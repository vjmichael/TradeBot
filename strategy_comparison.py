"""Verified snapshot + externally pinned model-evaluation receipts -> replay.

No fitting or model deserialization here. Native evaluation verification is
performed in the pinned model worker before those digests are handed over.
Reproduce the dataset from the source snapshot, bind exact test outcomes and
timestamps, and record immutable comparison files. No order authority.
"""
import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from data_lifecycle import restore,_private_dir,_publish_directory,_sync_tree
from market_memory import MarketMemory,MemoryUnavailable,sha256_file
from numerical_dataset import verify,reproduce,checked_rows,QUALIFICATION
from numerical_features import Plan,STEP
from training_handoff import canonical,HEX
from strategy_replay import Costs,STRATEGIES,SCENARIOS,SEED,signals,replay,aggregate,metrics

MAX_BYTES=512*1024**2
MAX_LINE=128*1024
MAX_MANIFEST=1024*1024
COHORTS=('numerical_only','no_sessions')

def require(ok,message):
    if not ok:raise MemoryUnavailable(message)

def read_pinned(path,digest,limit):
    require(isinstance(digest,str) and HEX.fullmatch(digest) and not path.is_symlink() and path.is_file() and 0<path.stat().st_size<=limit,'invalid pinned artifact')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as h:data=h.read(limit+1)
    require(0<len(data)<=limit and hashlib.sha256(data).hexdigest()==digest,'artifact checksum mismatch')
    return data

def read_rows(path,entry,maximum):
    require(type(entry['bytes']) is int and 0<entry['bytes']<=MAX_BYTES,'invalid receipt size')
    require(not path.is_symlink() and path.is_file() and path.stat().st_size==entry['bytes'],'missing row artifact')
    digest=hashlib.sha256();rows=[];size=0
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as h:
        while line:=h.readline(MAX_LINE+1):
            digest.update(line);size+=len(line)
            require(size<=entry['bytes'] and len(line)<=MAX_LINE and line.endswith(b'\n') and len(rows)<maximum,'row artifact exceeded receipt')
            rows.append(json.loads(line))
    require(size==entry['bytes'] and digest.hexdigest()==entry['sha256'],'row artifact checksum mismatch')
    return rows

def predictions(root,ref,pair,cohort,dataset_sha,rows):
    """Digest custody and test-row binding; does not itself verify inference."""
    stem=pair.replace('/','_')+'-'+cohort
    model=root/stem;ev=root/(stem+'-test')
    require(not model.is_symlink() and model.is_dir() and not ev.is_symlink() and ev.is_dir(),'missing native evaluation')
    require(set(ref)=={'model_sha256','evaluation_sha256'},'explicit model and evaluation digests required')
    m=json.loads(read_pinned(model/'manifest.json',ref['model_sha256'],MAX_MANIFEST))
    e=json.loads(read_pinned(ev/'manifest.json',ref['evaluation_sha256'],MAX_MANIFEST))
    require(m.get('kind')=='maj-vaj-frozen-numerical-model' and m.get('dataset_sha256')==dataset_sha and m.get('pair')==pair and m.get('cohort')==cohort and m.get('final_test_evaluated') is False and all(m.get(k) is v for k,v in QUALIFICATION.items()),'invalid native model receipt')
    require(e.get('kind')=='maj-vaj-frozen-test-evaluation' and e.get('dataset_sha256')==dataset_sha and e.get('model_sha256')==ref['model_sha256'] and e.get('pair')==pair and e.get('cohort')==cohort and e.get('evaluation_only') is True and all(e.get(k) is v for k,v in QUALIFICATION.items()),'invalid frozen evaluation receipt')
    require(set(m['artifacts'])=={'regressor.txt','classifier.txt'} and {p.name for p in model.iterdir()}=={'manifest.json','regressor.txt','classifier.txt'} and {p.name for p in ev.iterdir()}=={'manifest.json','predictions.jsonl'},'unexpected model or evaluation inventory')
    for name,entry in m['artifacts'].items():
        data=read_pinned(model/name,entry['sha256'],16*1024**2)
        require(len(data)==entry['bytes'] and data.startswith(b'tree\n'),'invalid native model artifact')
    parsed=read_rows(ev/'predictions.jsonl',e['predictions'],len(rows))
    require(len(parsed)==len(rows)==e['metrics']['rows'] and e['first_decision']==rows[0]['decision'] and e['last_decision']==rows[-1]['decision'],'evaluation coverage mismatch')
    for a,b in zip(parsed,rows):
        require(a['decision']==b['decision'] and a['actual_scenario_return']==b['targets']['&-net_return'] and a['actual_positive']==b['targets']['&-net_positive'],'evaluation outcomes differ from frozen test')
        require(all(type(a[k]) in (int,float) and math.isfinite(a[k]) for k in ('predicted_net_return','raw_positive_probability','calibrated_positive_probability')) and all(0<=a[k]<=1 for k in ('raw_positive_probability','calibrated_positive_probability')),'invalid native predictions')
    return {r['decision']:r for r in parsed}

def compare(snapshot,dataset,dataset_sha,models,refs,destination,capital_cents):
    snapshot,dataset,models=map(lambda p:Path(p).absolute(),(snapshot,dataset,models))
    dest=Path(destination).absolute();_private_dir(dest.parent);dest=dest.parent.resolve()/dest.name
    require(not dest.exists() and not dest.is_symlink() and all(dest!=p.resolve() and p.resolve() not in dest.parents for p in (snapshot,dataset,models)),'comparison requires a fresh private output')
    require(type(capital_cents) is int and 100<=capital_cents<=10**11,'invalid starting capital')
    manifest=verify(dataset,dataset_sha)
    require(reproduce(dataset,dataset_sha,snapshot)['reproduced'],'dataset reproduction failed')
    plan=Plan(**manifest['plan']).validate()
    assets={a['pair']:a for a in manifest['markets']};pairs=sorted(assets)
    require(set(refs)==set(pairs) and all(set(refs[p])==set(COHORTS) for p in pairs),'model evaluation scope differs')
    stage=Path(tempfile.mkdtemp(prefix='.strategy-replay-',dir=dest.parent))
    try:
        with tempfile.TemporaryDirectory(prefix='.strategy-source-',dir=dest.parent) as tmp:
            mem=Path(tmp)/'restored'
            receipt=restore(snapshot,mem,manifest['snapshot_sha256'])
            require(receipt['cutoff']==manifest['snapshot_cutoff'],'snapshot cutoff differs')
            prices={};features={};pred={};decisions={};labels={}
            market=MarketMemory(mem)
            for p in pairs:
                raw=checked_rows(market,p,plan,receipt['cutoff'])
                prices[p]={r['ts']:r['o'] for r in raw}
                item=assets[p];entry=item['files']['test']
                rows=read_rows(dataset/entry['name'],entry,item['counts']['test'])
                require(len(rows)==item['counts']['test'],'test extent mismatch')
                decisions[p]=[r['decision'] for r in rows]
                features[p]={r['decision']:r['features'] for r in rows}
                labels[p]={r['decision']:r['targets']['&-net_return'] for r in rows}
                pred[p]={k:predictions(models,refs[p][k],p,k,dataset_sha,rows) for k in COHORTS}
            # Same decision timestamps and mark clock for every sleeve and cohort.
            common=sorted(set.intersection(*(set(v) for v in decisions.values())))
            require(len(common)>=24,'insufficient common test decisions')
            first=common[0];last=common[-1]+(1+plan.horizon_hours)*STEP
            require(first>=plan.validation_end+plan.gap_hours*STEP and last+STEP<plan.end,'replay clock exceeds frozen test')
            allocations={p:capital_cents//len(pairs)+int(i<capital_cents%len(pairs)) for i,p in enumerate(pairs)}
            require(min(allocations.values())>0,'insufficient sleeve capital')
            costs={k:Costs(plan.fee_bps*m,plan.spread_bps*m,plan.slippage_bps*m) for k,m in zip(SCENARIOS,(0,1,2))}
            outputs={};summary={};total=0;schedules={}
            def write(path,records):
                nonlocal total
                with path.open('xb') as h:
                    for row in records:
                        data=(canonical(row)+'\n').encode()
                        require(len(data)<=MAX_LINE and total+len(data)<=MAX_BYTES and shutil.disk_usage(stage).free>256*1024**2+len(data),'comparison capacity reached')
                        h.write(data);total+=len(data)
                    h.flush();os.fsync(h.fileno())
                outputs[path.name]={'bytes':path.stat().st_size,'sha256':sha256_file(path)}
            for scenario,cost in costs.items():
                summary[scenario]={}
                for strategy in STRATEGIES:
                    sleeves=[];per_market={}
                    for p in pairs:
                        take=signals(strategy,p,common,features[p],pred[p])
                        sleeve=replay(prices[p],common,take,plan.horizon_hours,cost,allocations[p],strategy=='buy_hold')
                        slots=tuple((t['decision'],t['entry_at'],t['exit_at']) for t in sleeve['trades'])
                        key=(strategy,p)
                        if key not in schedules:schedules[key]=slots
                        else:require(schedules[key]==slots,'cost sensitivity altered execution schedule')
                        if scenario=='base' and strategy!='buy_hold':
                            require(all(math.isclose(t['net_return'],labels[p][t['decision']],rel_tol=1e-12,abs_tol=1e-14) for t in sleeve['trades']),'base replay return differs from frozen target')
                        sleeves.append(sleeve)
                        per_market[p]=metrics(sleeve['marks'],sleeve['initial'],sleeve['trades'],sleeve['fees'],sleeve['friction'],sleeve['turnover'])
                    marks,stats=aggregate(sleeves)
                    summary[scenario][strategy]={'portfolio':stats,'markets':per_market}
                    require(abs(stats['initial_equity']-capital_cents/100)<1e-7,'portfolio capital mismatch')
                    stem=scenario+'-'+strategy
                    write(stage/(stem+'-equity.jsonl'),({'ts':t,'portfolio':v,'sleeves':{p:sleeves[i]['marks'][j][1] for i,p in enumerate(pairs)}} for j,(t,v) in enumerate(marks)))
                    write(stage/(stem+'-trades.jsonl'),({'pair':p,**trade} for p,s in zip(pairs,sleeves) for trade in s['trades']))
            out={'format':1,'kind':'maj-vaj-matched-research-replay','dataset_sha256':dataset_sha,'snapshot_sha256':manifest['snapshot_sha256'],
                 'model_evaluations':refs,'source_contract':{p:sha256_file(Path(__file__).resolve().parent/p) for p in ('strategy_comparison.py','strategy_replay.py')},
                 'capital_cents':capital_cents,'sleeves_cents':allocations,'common_decisions':len(common),
                 'excluded_noncommon':{p:len(decisions[p])-len(common) for p in pairs},
                 'first_decision':first,'last_exit':last,'horizon_hours':plan.horizon_hours,
                 'scenarios':{k:{'fee_bps_per_side':v.fee,'full_spread_bps':v.spread,'slippage_bps_per_side':v.slippage} for k,v in costs.items()},
                 'policy':{'cash':'no positions','buy_hold':'first delayed entry through common last exit','random_seed':SEED,
                           'momentum':'positive 6h AND 24h return','model':'predicted base-cost net return >0 AND calibrated probability >=0.5',
                           'sizing':'equal funded independent fractional spot sleeves; one position per sleeve; no borrowing or reinvestment across sleeves',
                           'marks':'hourly executable-price scenario liquidation marks; no intrabar drawdown claim',
                           'sensitivity':'fixed signals and model calibration from base-cost targets; no retraining for costs'},
                 'limitations':['simulated fractional fills at hourly opens; no order book fill evidence or minimum size/precision model',
                    'costs are scenarios not observed fees or liquidity; no data/AI service cost included',
                    'descriptive risk statistics only; overlapping horizons and limited samples; no significance or outperformance claim',
                    'previous short qualification-window metrics were inspected; this replay is functional acceptance, not a fresh prospectively sealed independent holdout',
                    'externally pinned native evaluation receipts checked, no inference recomputation in replay',
                    'Grok/fundamental cohort deferred until TB-05 and matched forward evidence'], 
                 'results':summary,'files':outputs,'bytes':total,**QUALIFICATION}
            payload=canonical(out).encode();require(len(payload)<=MAX_MANIFEST,'comparison manifest too large')
            with (stage/'manifest.json').open('xb') as h:h.write(payload);h.flush();os.fsync(h.fileno())
        _sync_tree(stage);_publish_directory(stage,dest)
        return out|{'manifest_sha256':sha256_file(dest/'manifest.json')}
    finally:
        if stage.exists():shutil.rmtree(stage)

def verify_comparison(directory,expected,snapshot,dataset,dataset_sha,models,refs,capital_cents):
    directory=Path(directory).absolute();_private_dir(directory)
    manifest=json.loads(read_pinned(directory/'manifest.json',expected,MAX_MANIFEST))
    require(manifest.get('kind')=='maj-vaj-matched-research-replay','invalid comparison receipt')
    with tempfile.TemporaryDirectory(prefix='.comparison-verify-',dir=directory.parent) as tmp:
        new=compare(snapshot,dataset,dataset_sha,models,refs,Path(tmp)/'replay',capital_cents)
        require(new==manifest|{'manifest_sha256':expected},'comparison does not reproduce')
        require({p.name for p in directory.iterdir()}=={'manifest.json',*new['files']},'unexpected comparison inventory')
        for name,entry in new['files'].items():
            p=directory/name
            require(not p.is_symlink() and p.is_file() and p.stat().st_size==entry['bytes'] and sha256_file(p)==entry['sha256'],'comparison artifact mismatch')
    return new
