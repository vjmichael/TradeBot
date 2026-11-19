"""Build immutable research-only feature datasets from a verified data snapshot.

Upstream snapshot is restored privately, and native lake reads must agree with
the SQLite journal. Only fixed hourly complete windows are accepted. No live
connectivity or model execution. All recorded costs are hypothetical.
"""
import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing
from data_lifecycle import restore,_private_dir,_publish_directory,_sync_tree
from market_memory import MarketMemory,MemoryUnavailable,normalize,sha256_file
from market_lake import HistoricalLake
from economic_memory import EconomicMemory
from vintage_memory import parse,cutoff,MAX_SNAPSHOTS
from training_handoff import validate_window,canonical,HEX
from numerical_features import (Plan,STEP,LOOKBACK,SCHEMA,FEATURES,TARGETS,MACRO,
                                technical,outcome,split,model_columns,known_at)

MAX_BYTES=512*1024**2
MAX_LINE=128*1024
MAX_MANIFEST=1024*1024
RESERVE=256*1024**2
MIN_ROWS={'train':48,'validation':24,'test':24}
SPLITS=tuple(MIN_ROWS)
MACRO_MAX_AGE=62*86400
QUALIFICATION={'training_ready':False,'paper_ready':False,'distribution_approved':False,'model_promoted':False}

def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def checked_rows(memory,pair,plan,cut):
    journal=memory.query(pair,60,plan.start,plan.end,cut)
    lake=HistoricalLake(memory).query(pair,60,plan.start,plan.end,cut)
    if {k:v for k,v in lake.items() if k!='storage'}!=journal:raise MemoryUnavailable('dataset lake and journal disagree')
    if journal['fixture'] or journal['missing'] or journal['conflicts'] or journal['coverage']!=(plan.end-plan.start)//STEP:
        raise MemoryUnavailable('dataset requires requested complete common hourly window')
    # A later source may supply a count for an otherwise identical candle.
    # The journal's earliest price timestamp cannot release that later evidence.
    with closing(sqlite3.connect(memory.db)) as db:
        availability={(ts,digest):latest for ts,digest,latest in db.execute("""
            SELECT c.ts,c.digest,MAX(MAX(c.available_at,s.published_at))
            FROM candles c JOIN sources s ON s.id=c.source_id
            WHERE c.pair=? AND c.interval=60 AND c.ts>=? AND c.ts<?
              AND c.available_at<=? AND s.published_at<=?
              AND s.kind!='fixture' AND s.state='complete'
            GROUP BY c.ts,c.digest
            """,(pair,plan.start,plan.end,cut,cut))}
    for i,r in enumerate(journal['rows']):
        ts=r['ts'];values=[r[k] for k in ('o','h','l','c','v')]
        if ts!=plan.start+i*STEP or r['trades'] is None or not ts+STEP<=r['available_at']<=cut or not r['source_ids'] or any(not HEX.fullmatch(s) for s in r['source_ids']):
            raise MemoryUnavailable('invalid dataset candle lineage')
        exact=normalize([ts,*values,r['trades']],60,cut)
        if exact[-1]!=r['digest'] or exact[1:6]!=tuple(values):raise MemoryUnavailable('dataset candle digest mismatch')
        latest=availability.get((ts,r['digest']))
        if latest is None or not r['available_at']<=latest<=cut:raise MemoryUnavailable('invalid contributing observation availability')
        r['contributing_available_at']=latest
    return journal['rows']

def vintage_records(economic,cut):
    """Use only date-specific verified ALFRED bodies, never latest macro snapshots."""
    records={s:[] for s in MACRO}
    with economic.tx() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alfred_vintages'").fetchone():return records
        for i,r in enumerate(db.execute("SELECT * FROM alfred_vintages WHERE retrieved_at<=? ORDER BY series,vintage",(cut,))):
            if i>=MAX_SNAPSHOTS or r['series'] not in records or r['available_after']!=cutoff(r['vintage']) or r['retrieved_at']<r['available_after'] or hashlib.sha256(r['body']).hexdigest()!=r['sha256']:
                raise MemoryUnavailable('invalid frozen macro vintage')
            rows=parse(r['series'],r['vintage'],r['body'])
            if len(rows)!=r['rows']:raise MemoryUnavailable('macro vintage row count mismatch')
            latest=next(x for x in reversed(rows) if x['value'] is not None)
            value=float(latest['value'])
            if not math.isfinite(value):raise MemoryUnavailable('nonfinite macro value')
            records[r['series']].append({'series':r['series'],'vintage':r['vintage'],'available_after':r['available_after'],
                'retrieved_at':r['retrieved_at'],'sha256':r['sha256'],'period':latest['period'],'value':value})
    return records

def macro_features(records,decision,mode):
    values={};lineage={}
    for series in MACRO:
        eligible=[r for r in records[series] if r['available_after']<=decision and
                  (mode=='provider-retrospective' or r['retrieved_at']<=decision)]
        latest=max(eligible,key=lambda r:r['available_after'],default=None)
        age=decision-latest['available_after'] if latest else None
        usable=age is not None and age<=MACRO_MAX_AGE
        prefix='%-macro_'+series
        values.update({prefix:latest['value'] if usable else None,prefix+'_missing':int(not usable),
                       prefix+'_age_days':age/86400 if usable else None})
        lineage[series]=latest|{'used':bool(usable)} if latest else None
    return values,lineage

def iter_rows(rows,records,plan,pair):
    """All features use the closed window ending at decision.

    Entry is the *following* hourly open (one full bar execution delay).
    Exit open follows the explicit holding horizon. Outcome is withheld until
    both closes and recorded label evidence are available under the chosen mode.
    """
    for i in range(LOOKBACK-1,len(rows)):
        decision=rows[i]['ts']+STEP
        entry_index=i+2;exit_index=entry_index+plan.horizon_hours
        if exit_index>=len(rows):break
        entry,exit=rows[entry_index],rows[exit_index]
        label_available=exit['ts']+STEP
        if plan.availability_mode=='local-observed':
            label_available=max(label_available,known_at(entry),known_at(exit))
        fold=split(plan,decision,label_available)
        if fold is None:continue
        window=rows[i-LOOKBACK+1:i+1]
        row=technical(window,decision,plan.availability_mode)
        if row is None:continue
        macro,lineage=macro_features(records,decision,plan.availability_mode)
        yield fold,{'pair':pair,'decision':decision,'feature_start':window[0]['ts'],
                    'entry_at':entry['ts'],'exit_at':exit['ts'],'label_available':label_available,
                    'features':row|macro,'targets':outcome(plan,entry,exit),
                    'evidence':{'window_sha256':fingerprint(window),'latest_price_available':max(known_at(x) for x in window),
                                'entry':{k:entry[k] for k in ('ts','digest','available_at','contributing_available_at','source_ids')},
                                'exit':{k:exit[k] for k in ('ts','digest','available_at','contributing_available_at','source_ids')},'macro':lineage}}

def build(snapshot,destination,snapshot_sha256,pairs,plan):
    plan.validate();validate_window(pairs,plan.start,plan.end)
    snapshot,destination=Path(snapshot).absolute(),Path(destination).absolute()
    _private_dir(destination.parent);destination=destination.parent.resolve()/destination.name
    if destination.exists() or destination.is_symlink() or snapshot.resolve() in destination.parents:
        raise MemoryUnavailable('dataset requires new destination outside snapshot')
    stage=Path(tempfile.mkdtemp(prefix='.numerical-',dir=destination.parent))
    try:
        with tempfile.TemporaryDirectory(prefix='.numerical-restore-',dir=destination.parent) as tmp:
            restored=Path(tmp)/'data'
            receipt=restore(snapshot,restored,snapshot_sha256);cut=receipt['cutoff']
            if plan.end>cut//STEP*STEP:raise ValueError('dataset window exceeds snapshot')
            memory=MarketMemory(restored)
            if any(p not in memory.symbols for p in pairs):raise ValueError('market outside snapshot')
            vintages=vintage_records(EconomicMemory(restored),cut)
            inventory=[];total=0
            for pair in sorted(pairs):
                rows=checked_rows(memory,pair,plan,cut)
                counts=dict.fromkeys(SPLITS,0);missing=dict.fromkeys(MACRO,0)
                boundaries={k:None for k in SPLITS}
                base=pair.replace('/','_')
                from contextlib import ExitStack
                with ExitStack() as stack:
                    handles={k:stack.enter_context((stage/(base+'-'+k+'.jsonl')).open('xb')) for k in SPLITS}
                    for fold,row in iter_rows(rows,vintages,plan,pair):
                        data=(canonical(row)+'\n').encode()
                        if len(data)>MAX_LINE or total+len(data)>MAX_BYTES or shutil.disk_usage(stage).free<RESERVE+len(data):
                            raise MemoryUnavailable('numerical dataset capacity reached')
                        handles[fold].write(data);total+=len(data);counts[fold]+=1
                        for s in MACRO:missing[s]+=row['features']['%-macro_'+s+'_missing']
                        old=boundaries[fold]
                        boundaries[fold]={'first_decision':old['first_decision'] if old else row['decision'],
                            'last_decision':row['decision'],'max_label_available':max(row['label_available'],old['max_label_available'] if old else 0)}
                    for h in handles.values():h.flush();os.fsync(h.fileno())
                if any(counts[k]<MIN_ROWS[k] for k in SPLITS):raise MemoryUnavailable('insufficient eligible rows in declared splits; do not narrow or backfill')
                inventory.append({'pair':pair,'counts':counts,'macro_missing':missing,'boundaries':boundaries,
                    'price_window_sha256':fingerprint(rows),
                    'files':{k:{'name':base+'-'+k+'.jsonl','bytes':(stage/(base+'-'+k+'.jsonl')).stat().st_size,
                        'sha256':sha256_file(stage/(base+'-'+k+'.jsonl'))} for k in SPLITS}})
            manifest={'format':1,'kind':'maj-vaj-numerical-research-dataset','schema':SCHEMA,'snapshot_sha256':snapshot_sha256,
                'snapshot_cutoff':cut,'plan':plan.as_dict(),'markets':inventory,'bytes':total,'features':list(FEATURES),'targets':list(TARGETS),
                'cohorts':{'numerical_only':list(model_columns(include_macro=False)),'no_sessions':list(model_columns(False,False)),
                           'macro_exploratory':list(model_columns())},
                'semantics':{'price_availability':plan.availability_mode,'macro_age_limit_days':62,
                    'sessions':'weekday local-hour anchors; DST-aware; no exchange holiday or exact opening claim',
                    'labels':'hypothetical long-only net return; next following hourly open; no fill claim',
                    'split':'shared UTC boundaries, label-completion purge and embargo; no fitting/transforms',
                    'retrospective_caveat':'frozen provider history and revisions at snapshot cut; not proof of contemporaneous local knowledge; strict mode is a conservative final-clean subset, not unbiased historical membership',
                    'model_interface':'FreqAI percent-prefixed features and ampersand-prefixed targets; no automatic dispatch'},
                **QUALIFICATION}
            with (stage/'manifest.json').open('x') as h:h.write(canonical(manifest));h.flush();os.fsync(h.fileno())
        _sync_tree(stage);_publish_directory(stage,destination)
        return manifest|{'manifest_sha256':sha256_file(destination/'manifest.json')}
    finally:
        if stage.exists():shutil.rmtree(stage)

def verify(directory,expected_digest):
    """Verify fixed file inventory and actual row/schema/chronology, not approval."""
    directory=Path(directory).absolute();_private_dir(directory)
    manifest=directory/'manifest.json'
    if not isinstance(expected_digest,str) or not HEX.fullmatch(expected_digest) or manifest.is_symlink() or not manifest.is_file() or not 0<manifest.stat().st_size<=MAX_MANIFEST or sha256_file(manifest)!=expected_digest:
        raise MemoryUnavailable('dataset manifest checksum mismatch')
    m=json.loads(manifest.read_text())
    if m.get('format')!=1 or m.get('schema')!=SCHEMA or m.get('kind')!='maj-vaj-numerical-research-dataset' or any(m.get(k) is not v for k,v in QUALIFICATION.items()) or m.get('features')!=list(FEATURES) or m.get('targets')!=list(TARGETS):
        raise MemoryUnavailable('invalid numerical dataset manifest')
    plan=Plan(**m['plan']).validate();validate_window([a['pair'] for a in m['markets']],plan.start,plan.end)
    if not HEX.fullmatch(m['snapshot_sha256']) or plan.end>m['snapshot_cutoff'] or not 0<m['bytes']<=MAX_BYTES:raise MemoryUnavailable('invalid dataset source receipt')
    if m.get('cohorts')!={'numerical_only':list(model_columns(include_macro=False)),'no_sessions':list(model_columns(False,False)),'macro_exploratory':list(model_columns())}:raise MemoryUnavailable('unexpected model feature schema')
    names={'manifest.json'};total=0
    for asset in m['markets']:
        base=asset['pair'].replace('/','_');missing=dict.fromkeys(MACRO,0)
        for fold in SPLITS:
            file=asset['files'][fold];name=base+'-'+fold+'.jsonl';path=directory/name;names.add(name)
            if file['name']!=name or path.is_symlink() or not path.is_file() or not 0<file['bytes']==path.stat().st_size<=MAX_BYTES-total or sha256_file(path)!=file['sha256']:
                raise MemoryUnavailable('dataset file checksum or inventory mismatch')
            total+=file['bytes'];count=0;first=last=None;maximum=0
            with path.open('rb') as h:
                while line:=h.readline(MAX_LINE+1):
                    if len(line)>MAX_LINE or not line.endswith(b'\n'):raise MemoryUnavailable('invalid dataset row bound')
                    row=json.loads(line);decision=row['decision']
                    if row['pair']!=asset['pair'] or type(decision) is not int or decision%STEP or (last is not None and decision<=last) or split(plan,decision,row['label_available'])!=fold or row['entry_at']!=decision+STEP or row['exit_at']!=row['entry_at']+plan.horizon_hours*STEP or row['label_available']<row['exit_at']+STEP or row['feature_start']!=decision-LOOKBACK*STEP or row['feature_start']<plan.start or set(row['features'])!=set(FEATURES) or set(row['targets'])!=set(TARGETS):
                        raise MemoryUnavailable('invalid feature or label chronology')
                    if any(v is not None and (type(v) not in (int,float) or not math.isfinite(v)) for v in row['features'].values()) or any(type(v) not in (int,float) or not math.isfinite(v) for v in row['targets'].values()) or row['targets']['&-net_positive'] not in (0,1) or row['targets']['&-net_positive']!=int(row['targets']['&-net_return']>0):
                        raise MemoryUnavailable('invalid numerical values')
                    if plan.availability_mode=='local-observed' and row['evidence']['latest_price_available']>decision:raise MemoryUnavailable('future local price feature')
                    ev=row['evidence']
                    if not HEX.fullmatch(ev['window_sha256']) or not decision<=ev['latest_price_available']<=m['snapshot_cutoff'] or set(ev['macro'])!=set(MACRO):
                        # Earliest feature may be exactly at close; latest contribution can never precede it.
                        raise MemoryUnavailable('invalid window evidence')
                    for name in ('entry','exit'):
                        e=ev[name]
                        if e['ts']!=row[name+'_at'] or not HEX.fullmatch(e['digest']) or not e['ts']+STEP<=e['available_at']<=e['contributing_available_at']<=m['snapshot_cutoff'] or not e['source_ids'] or any(not HEX.fullmatch(v) for v in e['source_ids']):
                            raise MemoryUnavailable('invalid label evidence')
                        if plan.availability_mode=='local-observed' and e['contributing_available_at']>row['label_available']:raise MemoryUnavailable('late outcome evidence')
                    for series,evidence in ev['macro'].items():
                        prefix='%-macro_'+series
                        flag=row['features'][prefix+'_missing']
                        if evidence and (evidence['series']!=series or evidence['available_after']!=cutoff(evidence['vintage']) or not evidence['available_after']<=decision or not evidence['available_after']<=evidence['retrieved_at']<=m['snapshot_cutoff'] or not HEX.fullmatch(evidence['sha256']) or plan.availability_mode=='local-observed' and evidence['retrieved_at']>decision):raise MemoryUnavailable('future or invalid macro evidence')
                        usable=bool(evidence and decision-evidence['available_after']<=MACRO_MAX_AGE)
                        if flag!=int(not usable) or (evidence and evidence['used'] is not usable) or row['features'][prefix]!=(evidence['value'] if usable else None) or row['features'][prefix+'_age_days']!=((decision-evidence['available_after'])/86400 if usable else None):raise MemoryUnavailable('macro feature disagrees with evidence')
                        missing[series]+=flag
                    count+=1
                    if first is None:first=decision
                    last=decision;maximum=max(maximum,row['label_available'])
            if count<MIN_ROWS[fold] or count!=asset['counts'][fold] or asset['boundaries'][fold]!={'first_decision':first,'last_decision':last,'max_label_available':maximum}:
                raise MemoryUnavailable('numerical split receipt mismatch')
        if missing!=asset['macro_missing']:raise MemoryUnavailable('macro missing receipt mismatch')
    if total!=m['bytes'] or {p.name for p in directory.iterdir()}!=names:raise MemoryUnavailable('numerical dataset inventory mismatch')
    return m|{'manifest_sha256':expected_digest}

def reproduce(directory,expected_digest,snapshot):
    """Independently regenerate from the referenced data-only snapshot."""
    m=verify(directory,expected_digest)
    with tempfile.TemporaryDirectory(prefix='.numerical-reproduction-',dir=Path(directory).absolute().parent) as tmp:
        new=build(snapshot,Path(tmp)/'dataset',m['snapshot_sha256'],[a['pair'] for a in m['markets']],Plan(**m['plan']))
        if new!=m:raise MemoryUnavailable('dataset does not reproduce from source snapshot')
    return {'manifest_sha256':expected_digest,'snapshot_sha256':m['snapshot_sha256'],'reproduced':True,**QUALIFICATION}

def model_rows(directory,expected_digest,pair,fold,cohort,target):
    """Verified adapter for a subsequent trainer: only exact feature columns,
    target separate, and one declared fold. Never passes evidence/metadata as X.
    No model fitting or final-test access policy is implemented in TB-06A.
    """
    manifest=verify(directory,expected_digest)
    if fold not in SPLITS or cohort not in manifest['cohorts'] or target not in TARGETS:raise ValueError('invalid model input selection')
    asset=next((a for a in manifest['markets'] if a['pair']==pair),None)
    if asset is None:raise ValueError('market outside dataset')
    columns=tuple(manifest['cohorts'][cohort])
    path=Path(directory)/asset['files'][fold]['name']
    # Hash while reading the same bounded bytes used to populate the matrix.
    # Keep only numeric X/y and decisions in memory, not raw evidence.
    entry=asset['files'][fold];digest=hashlib.sha256();size=0
    X=[];y=[];decisions=[]
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as handle:
        while line:=handle.readline(MAX_LINE+1):
            size+=len(line);digest.update(line)
            if size>entry['bytes'] or len(line)>MAX_LINE or len(X)>=asset['counts'][fold]:raise MemoryUnavailable('model input exceeded receipt')
            row=json.loads(line)
            X.append(tuple(row['features'][k] for k in columns));y.append(row['targets'][target]);decisions.append(row['decision'])
    if size!=entry['bytes'] or digest.hexdigest()!=entry['sha256'] or len(X)!=asset['counts'][fold]:raise MemoryUnavailable('model input changed after verification')
    return {'columns':columns,'pair':pair,'fold':fold,'target':target,'cohort':cohort,
        'decisions':tuple(decisions),'X':tuple(X),'y':tuple(y),
        'manifest_sha256':expected_digest}
