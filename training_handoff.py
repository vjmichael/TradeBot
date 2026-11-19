"""TB-04F4: frozen public-data input handoff, not an approved training run.

Only export an explicit complete common hourly window from a manifest-verified
data-only restore. Do not fill gaps, silently narrow the window, include
economic features, or imply observation at historic decision time.
"""
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from data_lifecycle import restore,_private_dir,_publish_directory,_sync_tree
from market_memory import MarketMemory,MemoryUnavailable,normalize,sha256_file
from market_lake import HistoricalLake

STEP=3600
MAX_HOURS=43800
MAX_BYTES=512*1024**2
MAX_LINE=256*1024
RESERVE=256*1024**2
HEX=re.compile(r'[0-9a-f]{64}')
PAIR=re.compile(r'[A-Z0-9]{1,32}/USD')

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)

def validate_window(pairs,start,end):
    if not isinstance(pairs,(list,tuple)) or not 1<=len(pairs)<=32 or any(not isinstance(p,str) or not PAIR.fullmatch(p) for p in pairs) or len(set(pairs))!=len(pairs):
        raise ValueError('invalid export market scope')
    if any(type(n) is not int or n<=0 or n%STEP for n in (start,end)) or not 0<end-start<=MAX_HOURS*STEP:
        raise ValueError('invalid hourly export window')

def write_input(memory,pair,start,end,cut,path,sidecar,remaining):
    # Check the lake's active generation integrity and match authoritative
    # SQLite, including exact decimal strings, availability and lineage.
    view=HistoricalLake(memory).query(pair,60,start,end,cut)
    journal=memory.query(pair,60,start,end,cut)
    if {k:v for k,v in view.items() if k!='storage'}!=journal:
        raise MemoryUnavailable('training input catalog and partition disagree')
    expected=(end-start)//STEP
    if journal['fixture'] or journal['coverage']!=expected or journal['conflicts'] or journal['missing']:
        raise MemoryUnavailable('requested training input window is incomplete')
    written=0;checked_at=0
    if shutil.disk_usage(path.parent).free<RESERVE:raise MemoryUnavailable('training input disk reserve reached')
    def put(file,text):
        nonlocal written,checked_at
        data=text.encode('utf-8')
        if len(data)>MAX_LINE or written+len(data)>remaining:
            raise MemoryUnavailable('training input export storage bound reached')
        if written+len(data)-checked_at>=1024*1024:
            if shutil.disk_usage(path.parent).free<RESERVE+1024*1024:raise MemoryUnavailable('training input disk reserve reached')
            checked_at=written
        file.write(data);written+=len(data)
    with path.open('xb') as out,sidecar.open('xb') as evidence:
        put(out,'[')
        for i,row in enumerate(journal['rows']):
            ts=row['ts'];vals=[row[k] for k in ('o','h','l','c','v')]
            if ts!=start+i*STEP or row['trades'] is None or not ts+STEP<=row['available_at']<=cut or not row['source_ids'] or any(not HEX.fullmatch(x) for x in row['source_ids']):
                raise MemoryUnavailable('incomplete candle provenance or trade count')
            exact=normalize([ts,*vals,row['trades']],60,cut)
            if exact[-1]!=row['digest'] or tuple(vals)!=exact[1:6]:
                raise MemoryUnavailable('training input candle checksum mismatch')
            # Numeric JSON with exact decimal lexical values. Freqtrade's
            # loader may subsequently convert to binary floating point.
            put(out,(',' if i else '')+'['+str(ts*1000)+','+','.join(vals)+']')
            put(evidence,canonical(row)+'\n')
        put(out,']\n')
        for file in (out,evidence):file.flush();os.fsync(file.fileno())
    if shutil.disk_usage(path.parent).free<RESERVE:raise MemoryUnavailable('training input disk reserve reached')
    return {'pair':pair,'rows':expected,'input':path.name,'provenance':sidecar.name,
            'input_sha256':sha256_file(path),'provenance_sha256':sha256_file(sidecar),'bytes':written,
            'partition_reads':view['storage']['parquet_partitions']}

def export(snapshot,destination,snapshot_sha256,pairs,start,end):
    validate_window(pairs,start,end)
    snapshot,destination=Path(snapshot).absolute(),Path(destination).absolute()
    _private_dir(destination.parent)
    destination=destination.parent.resolve()/destination.name
    if destination.exists() or destination.is_symlink() or snapshot.resolve() in destination.parents:
        raise MemoryUnavailable('training input requires a new directory outside snapshot')
    stage=Path(tempfile.mkdtemp(prefix='.training-input-',dir=destination.parent))
    try:
        with tempfile.TemporaryDirectory(prefix='.training-verify-',dir=destination.parent) as work:
            private=Path(work)/'restored'
            receipt=restore(snapshot,private,snapshot_sha256);cut=receipt['cutoff']
            if end>cut//STEP*STEP:raise ValueError('export window exceeds snapshot cut')
            memory=MarketMemory(private)
            if any(pair not in memory.symbols for pair in pairs):raise ValueError('market outside snapshot scope')
            data=stage/'data'/'kraken';data.mkdir(mode=0o700,parents=True)
            output=[];used=0
            for pair in sorted(pairs):
                base=pair.replace('/','_')+'-1h'
                item=write_input(memory,pair,start,end,cut,data/(base+'.json'),data/(base+'.provenance.jsonl'),MAX_BYTES-used)
                used+=item['bytes'];output.append(item)
            manifest={'version':1,'kind':'maj-vaj-frozen-ohlcv-input','snapshot_sha256':snapshot_sha256,
                'as_of':cut,'start':start,'end':end,'markets':output,'bytes':used,'interval_minutes':60,
                'availability_policy':'retrospective provider history at snapshot cut, not proof of local historical knowledge',
                'features_included':False,'labels_included':False,'economic_features_included':False,
                'training_ready':False,'paper_ready':False}
            with (stage/'manifest.json').open('x') as file:
                file.write(canonical(manifest));file.flush();os.fsync(file.fileno())
        _sync_tree(stage);_publish_directory(stage,destination)
        return manifest|{'manifest_sha256':sha256_file(destination/'manifest.json')}
    finally:
        if stage.exists():shutil.rmtree(stage)

def verify(directory,manifest_sha256):
    directory=Path(directory);_private_dir(directory)
    path=directory/'manifest.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024 or not isinstance(manifest_sha256,str) or not HEX.fullmatch(manifest_sha256) or sha256_file(path)!=manifest_sha256:
        raise MemoryUnavailable('training input manifest checksum mismatch')
    v=json.loads(path.read_text())
    if v.get('version')!=1 or v.get('kind')!='maj-vaj-frozen-ohlcv-input' or v.get('interval_minutes')!=60 or any(v.get(k) is not False for k in ('training_ready','paper_ready','features_included','labels_included','economic_features_included')):
        raise MemoryUnavailable('invalid input manifest')
    validate_window([m['pair'] for m in v['markets']],v['start'],v['end'])
    if not HEX.fullmatch(v['snapshot_sha256']) or v['end']>v['as_of'] or not 0<v['bytes']<=MAX_BYTES:raise MemoryUnavailable('invalid input receipt')
    data=directory/'data'/'kraken'
    if (directory/'data').is_symlink() or data.is_symlink() or not data.is_dir() or {p.name for p in directory.iterdir()}!={'manifest.json','data'} or {p.name for p in (directory/'data').iterdir()}!={'kraken'}:raise MemoryUnavailable('unsafe input data directory or inventory')
    names=set();total=0
    for m in v['markets']:
        base=m['pair'].replace('/','_')+'-1h'
        if m['rows']!=(v['end']-v['start'])//STEP or m['input']!=base+'.json' or m['provenance']!=base+'.provenance.jsonl':raise MemoryUnavailable('invalid input extent')
        size=0
        for key in ('input','provenance'):
            p=data/m[key];names.add(p.name)
            if p.is_symlink() or not p.is_file() or p.stat().st_size<=0 or p.stat().st_size>MAX_BYTES-total or sha256_file(p)!=m[key+'_sha256']:
                raise MemoryUnavailable('training input asset checksum mismatch')
            total+=p.stat().st_size;size+=p.stat().st_size
        if size!=m['bytes']:raise MemoryUnavailable('training input asset size mismatch')
    if total!=v['bytes'] or {p.name for p in data.iterdir()}!=names:raise MemoryUnavailable('training input asset inventory mismatch')
    return v|{'manifest_sha256':manifest_sha256}
