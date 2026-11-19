"""Explicit, journaled rotation of one unreferenced recovery copy.

Two newer completed copies must restore successfully. Historic cleanup pins
and unrecognized filesystem entries are never eligible. An interrupted
approved removal can resume only against its recorded selection.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import closing
from pathlib import Path
from data_lifecycle import restore,_publish_directory,DATABASES,DIRECTORY_DB
from data_preparation import _db
from market_memory import MarketMemory,MemoryUnavailable
from market_lake import syncdir

ID=re.compile(r'[0-9a-f]{32}')
SHA=re.compile(r'[0-9a-f]{64}')

def _rows(directory):
    with closing(_db(directory/'preparation')) as db:
        rows=db.execute("""SELECT j.id,j.rowid AS sequence,m.result FROM jobs j JOIN maintenance_results m ON m.job=j.id
            WHERE j.mode='backup' AND j.state='complete' ORDER BY j.rowid DESC LIMIT 10000""").fetchall()
        return [dict(json.loads(r['result']),id=r['id'],sequence=r['sequence']) for r in rows]

def _protected(directory):
    m=MarketMemory(directory)
    with m.tx() as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        protected={r[0] for r in db.execute('SELECT DISTINCT manifest_sha256 FROM quality_retired')} if 'quality_retired' in tables else set()
    with closing(_db(directory/'preparation')) as db:
        # Preserve every historically reviewed cleanup copy, not merely latest.
        for row in db.execute("""SELECT m.result FROM maintenance_results m JOIN jobs j ON j.id=m.job
            WHERE j.mode IN ('preview-retention','retire')"""):
            value=json.loads(row[0]);digest=value.get('manifest_sha256')
            if digest:protected.add(digest)
    return protected

def _inventory(directory,root):
    rows=_rows(directory);by_id={r['id']:r for r in rows}
    if not root.exists():return []
    output=[]
    for p in root.iterdir():
        if p.name.startswith('.rotation-'):raise MemoryUnavailable('resume interrupted rotation before new review')
        if p.name not in by_id or not ID.fullmatch(p.name) or p.is_symlink() or not p.is_dir():
            raise MemoryUnavailable('unknown recovery entry; rotation withheld')
        row=by_id[p.name]
        if not SHA.fullmatch(row['manifest_sha256']):raise MemoryUnavailable('invalid backup receipt')
        output.append(row)
    return sorted(output,key=lambda x:x['sequence'],reverse=True)

def _receipt(row):
    return {k:row[k] for k in ('id','manifest_sha256','cutoff','bytes')}

def _verify(root,receipt):
    with tempfile.TemporaryDirectory(prefix='rotation-verify-',dir=root.parent) as tmp:
        result=restore(root/receipt['id'],Path(tmp)/'verified',receipt['manifest_sha256'])
        if result['bytes']!=receipt['bytes'] or result['cutoff']!=receipt['cutoff']:
            raise MemoryUnavailable('recovery copy receipt changed')

def _check_assets(root,victim,survivors):
    # Never erase the only verified copy of any catalog-bound CSV/Parquet
    # generation. The restore checks authenticate these manifests and assets.
    def files(receipt):
        p=root/receipt['id']/'manifest.json'
        if p.is_symlink() or not p.is_file() or p.stat().st_size>32*1024**2:
            raise MemoryUnavailable('rotation manifest unavailable')
        raw=p.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=receipt['manifest_sha256']:
            raise MemoryUnavailable('rotation manifest changed')
        return json.loads(raw)['files']
    remaining={}
    for receipt in survivors:
        for path,entry in files(receipt).items():
            remaining.setdefault(path,set()).add(entry['sha256'])
    for path,entry in files(victim).items():
        if path not in (*DATABASES,DIRECTORY_DB) and entry['sha256'] not in remaining.get(path,set()):
            raise MemoryUnavailable('older copy contains a unique catalog asset')

def preview(directory,root):
    directory,root=Path(directory),Path(root)
    rows=_inventory(directory,root)
    protected=_protected(directory)
    if len(rows)<3:return {'eligible':False,'reason':'two_newer_copies_required'}
    survivors=[_receipt(r) for r in rows[:2]]
    choices=[r for r in rows[2:] if r['manifest_sha256'] not in protected ]
    if not choices:return {'eligible':False,'reason':'all_older_copies_protected'}
    plan={'eligible':True,'victim':_receipt(choices[-1]),'survivors':survivors}
    for receipt in survivors+[plan['victim']]:_verify(root,receipt)
    _check_assets(root,plan['victim'],survivors)
    plan['selection_sha256']=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return plan

def apply(directory,root,plan):
    directory,root=Path(directory),Path(root)
    if not plan.get('eligible'):raise MemoryUnavailable('no reviewed rotation selection')
    victim=plan['victim'];ident=victim['id'];key=plan['selection_sha256']
    if not ID.fullmatch(ident) or not SHA.fullmatch(key) or hashlib.sha256(json.dumps({k:v for k,v in plan.items() if k!='selection_sha256'},sort_keys=True,separators=(',',':')).encode()).hexdigest()!=key:raise MemoryUnavailable('invalid rotation identity')
    source=root/ident;quarantine=root/('.rotation-'+ident)
    journal=directory/'preparation'
    with closing(_db(journal)) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS copy_rotations(id TEXT PRIMARY KEY,selection TEXT NOT NULL,state TEXT NOT NULL)')
        prior=db.execute('SELECT * FROM copy_rotations WHERE id=?',(ident,)).fetchone()
    if prior:
        if prior['selection']!=key:raise MemoryUnavailable('rotation selection changed')
        if prior['state']=='complete':return {'state':'complete','removed':ident}
    else:
        if preview(directory,root)!=plan:raise MemoryUnavailable('rotation selection changed; review again')
        with closing(_db(journal)) as db,db:
            db.execute("INSERT INTO copy_rotations VALUES(?,?,'approved')",(ident,key))
    if victim['manifest_sha256'] in _protected(directory):
        raise MemoryUnavailable('recovery copy became protected')
    for survivor in plan['survivors']:_verify(root,survivor)
    if quarantine.is_symlink() or source.is_symlink() or (source.exists() and quarantine.exists()):
        raise MemoryUnavailable('unsafe rotation custody')
    if source.exists():
        _verify(root,victim)
        _check_assets(root,victim,plan['survivors'])
        _publish_directory(source,quarantine)
    if not source.exists() and not quarantine.exists() and (not prior or prior['state']!='quarantined'):
        raise MemoryUnavailable('reviewed copy vanished outside recorded removal')
    if quarantine.exists():
        with closing(_db(journal)) as db,db:db.execute("UPDATE copy_rotations SET state='quarantined' WHERE id=?",(ident,))
        if not quarantine.is_dir():raise MemoryUnavailable('invalid rotation quarantine')
        shutil.rmtree(quarantine);syncdir(root)
    with closing(_db(journal)) as db,db:
        db.execute("UPDATE copy_rotations SET state='complete' WHERE id=?",(ident,))
    return {'state':'complete','removed':ident,'retained':[r['id'] for r in plan['survivors']]}
