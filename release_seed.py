"""Private persistent release-seed workspace and immutable package repository.

This is NOT distribution authorization or a signed installer. Each version
references content-addressed data-only snapshot assets, never executable code
or model weights. Import requires an independently trusted manifest digest.
No network and no automatic activation/replacement of existing user data.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from data_lifecycle import (backup,restore,_private_dir,_safe,_copy,_entry,
                            _publish_directory,_sync_tree,DATABASES,DIRECTORY_DB,
                            MAX_BYTES,MAX_FILES,RESERVE,HEX)
from market_memory import MarketMemory,MemoryUnavailable,sha256_file
from market_directory import MarketDirectory,DEFAULTS
from economic_memory import EconomicMemory,SOURCES
from historical_readiness import historical_report,HOURS
from vintage_memory import coverage as vintage_coverage,URL as VINTAGE_URL
from market_archive import exclusive_job
from market_lake import syncdir

FORMAT=1
COMPATIBILITY='maj-vaj-data-v1'
MAX_STORE_BYTES=32*1024**3
MAX_OBJECTS=200000
MAX_VERSIONS=128
MAX_MANIFEST=64*1024**2
VERSION=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
TABLES={
    DATABASES[0]:set('sources candles collector exports archive_receipts market_archive_status stream_health stream_latest stream_gap_reports quality_books quality_attempts quality_retired quality_archive gap_jobs gap_candles gap_pages gap_trade_tail gap_overlap_receipts gap_attempts recovery_admission recovery_request_totals recovery_admission_clock recovery_cooldown lake_epoch lake_files lake_active lake_pins lake_policy sqlite_sequence'.split()),
    DATABASES[1]:set('snapshots revisions members attempts alfred_vintages alfred_attempts vintage_backfill_plan vintage_requests vintage_cooldown sqlite_sequence'.split()),
    DIRECTORY_DB:set('snapshots selections pinned_identity refresh_state sqlite_sequence'.split())}
REQUIRED={DATABASES[0]:{'sources','candles','collector','exports'},DATABASES[1]:{'snapshots','revisions','members','attempts'},DIRECTORY_DB:{'snapshots','selections','pinned_identity','refresh_state'}}

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def _reject(message):
    raise MemoryUnavailable(message)

def _json(path,limit):
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=limit:_reject('missing or unbounded seed manifest')
    def unique(items):
        out={}
        for k,v in items:
            if k in out:_reject('duplicate manifest field')
            out[k]=v
        return out
    try:return json.loads(path.read_bytes(),object_pairs_hook=unique)
    except (ValueError,UnicodeError):_reject('invalid seed manifest')

def _write(path,value):
    with path.open('xb') as f:
        f.write(canonical(value));f.flush();os.fsync(f.fileno())
    os.chmod(path,0o600)

def _version(value):
    if not isinstance(value,str) or not VERSION.fullmatch(value) or value in ('.','..'):raise ValueError('invalid seed version')
    return value

def _hex(value):
    if not isinstance(value,str) or not HEX.fullmatch(value):_reject('invalid seed digest')
    return value

def _root(path,create=False):
    path=Path(path).absolute()
    if create:
        _private_dir(path.parent)
        path.mkdir(mode=0o700,exist_ok=True)
    _private_dir(path)
    return path.resolve()

def _child(root,name,create=False):
    path=root/name
    if create:path.mkdir(mode=0o700,exist_ok=True)
    _private_dir(path)
    return path

def init(root,*,snapshot=None,snapshot_sha256=None):
    """Reopen the same private acquisition workspace; never reset it."""
    root=_root(root,True)
    with exclusive_job(root,'seed-store'):
        _child(root,'objects',True);_child(root,'versions',True)
        data=root/'workspace'
        if (snapshot is None)!=(snapshot_sha256 is None):raise ValueError('snapshot and external digest are required together')
        if snapshot is not None:
            if data.exists() or data.is_symlink():_reject('seed workspace already exists')
            snapshot=Path(snapshot).absolute();_private_dir(snapshot)
            manifest=snapshot/'manifest.json'
            if manifest.is_symlink() or not manifest.is_file() or not 0<manifest.stat().st_size<=32*1024**2 or sha256_file(manifest)!=_hex(snapshot_sha256):_reject('seed initialization manifest checksum mismatch')
            snap,_=_snapshot_manifest(snapshot);_bundle_inventory(snapshot,snap['files']);_schema_inventory(snapshot,snap['files'])
            with tempfile.TemporaryDirectory(prefix='.seed-init-',dir=root) as tmp:
                fresh=Path(tmp)/'restored';restore(snapshot,fresh,_hex(snapshot_sha256))
                _review(fresh,snap['cutoff']);_sync_tree(fresh);_publish_directory(fresh,data)
        else:
            data=_child(root,'workspace',True)
            MarketDirectory(data);MarketMemory(data);EconomicMemory(data)
        syncdir(root)
    return {'workspace':str(data),'state':'persistent_private_workspace','distribution_approved':False}

def rights_inventory():
    # Fixed conservative source review inventory; these are references, not
    # grants. Derived data/Parquet have the same unresolved source review.
    return [{'id':'kraken_public','references':['https://www.kraken.com/legal/global-terms',
             'https://api.kraken.com/0/public/OHLC','https://api.kraken.com/0/public/Trades',
             'https://api.kraken.com/0/public/Depth','https://api.kraken.com/0/public/AssetPairs',
             'https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data',
             'wss://ws.kraken.com/v2'],'state':'review_required'},
            *[{'id':source,'references':[info[0]],'state':'review_required'} for source,info in sorted(SOURCES.items())],
            {'id':'alfred','references':[VINTAGE_URL],'state':'review_required'}]

def _schema_inventory(snapshot,files):
    """Reject unexpected schema objects and fixture content; not a sandbox
    for untrusted arbitrary SQLite files. Only owner-controlled snapshots."""
    for rel in (*DATABASES,DIRECTORY_DB):
        if rel not in files:continue
        db_path=_safe(snapshot,rel)
        if not _asset_matches(db_path,files[rel]):_reject('seed database bytes do not match manifest')
        with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            rows=db.execute("SELECT type,name,tbl_name FROM sqlite_master").fetchall()
            tables={n for t,n,_ in rows if t=='table'}
            if not REQUIRED[rel]<=tables<=TABLES[rel] or any(t not in ('table','index') or table not in tables for t,n,table in rows):
                _reject('unrecognized seed database schema')
            if rel==DATABASES[0]:
                if db.execute("SELECT 1 FROM sources WHERE kind='fixture' LIMIT 1").fetchone():_reject('fixture market data cannot enter release seed')
                from market_memory import ARCHIVE_DOC,REST_DOC
                from market_gap import URL as TRADE_URL
                if db.execute("SELECT 1 FROM sources WHERE locator NOT IN (?,?,?) LIMIT 1",(ARCHIVE_DOC,REST_DOC,TRADE_URL)).fetchone():
                    _reject('unrecognized market evidence source')
                if any(p not in DEFAULTS for (p,) in db.execute('SELECT DISTINCT pair FROM sources')):_reject('nondefault seed market')
            elif rel==DATABASES[1]:
                for table in ('snapshots','revisions','attempts'):
                    if db.execute('SELECT 1 FROM '+table+' WHERE fixture!=0 LIMIT 1').fetchone():_reject('fixture economic evidence cannot enter release seed')
                    if any(s not in SOURCES for (s,) in db.execute('SELECT DISTINCT source FROM '+table)):_reject('unrecognized economic evidence source')

def _review(directory,cut):
    registry=MarketDirectory(directory);scope=registry.selected_symbols()
    if set(scope)!=set(DEFAULTS) or set(registry.retained_symbols())!=set(DEFAULTS):_reject('release seed requires exactly default market scope')
    m=MarketMemory(directory,symbols=scope);e=EconomicMemory(directory)
    markets=[];economic=None
    for pair in sorted(scope):
        r=historical_report(m,e,pair,as_of=cut)
        markets.append({k:r[k] for k in ('pair','start','end','expected','coverage','missing','conflicts','trade_count_conflicts','classification_hours','archive_retention','qualification')})
        view=m.query(pair,60,r['start'],r['end'],cut)
        if view['coverage']!=r['coverage'] or view['conflicts']!=r['conflicts']:_reject('seed hourly coverage disagrees with journal')
        markets[-1]['unknown_or_conflicting_count_hours']=sum(x['trades'] is None for x in view['rows'])
        markets[-1]['report_id']=r['report_id']
        markets[-1]['range_count']=r['range_count']
        if economic is None:economic=[{k:item[k] for k in ('source','state','total','snapshot_sha256','retained_revisions')} for item in r['economic_sources']]
    vintages=vintage_coverage(e,as_of=cut)
    vintage_targets=[]
    for offset in range(0,vintages['expected'],60):
        page=vintage_coverage(e,as_of=cut,offset=offset,limit=60,detail=True)
        vintage_targets.extend(page['rows'])
    if len(vintage_targets)!=vintages['expected'] or len({(x['series'],x['vintage']) for x in vintage_targets})!=vintages['expected'] or sum(x['state']=='saved' for x in vintage_targets)!=vintages['saved']:_reject('seed vintage target inventory did not reconcile')
    complete=all(r['coverage']==HOURS and not r['missing'] and not r['conflicts'] and not r['trade_count_conflicts'] and not r['unknown_or_conflicting_count_hours'] and r['archive_retention']['assets']>0 and r['archive_retention']['integrity'].get('verified',0)==r['archive_retention']['assets'] for r in markets)
    complete=complete and all(r['archive_retention']['provenance'].get('published',0)==r['archive_retention']['assets'] and r['archive_retention']['provenance'].get('no_publisher_receipt',0)==0 and r['archive_retention']['provenance'].get('https_range_member_crc32',0)+r['archive_retention']['provenance'].get('publisher_full_sha256',0)>=r['archive_retention']['assets'] for r in markets)
    complete=complete and all(r['state']=='observed' and r['total']>0 for r in economic) and vintages['missing']==0 and vintages['supplemental']['saved']==vintages['supplemental']['expected']
    return {'markets':markets,'economic':economic,'vintages':vintages,'vintage_targets':vintage_targets,'core_coverage_complete':bool(complete),
            'qualification_state':'review_required','training_ready':False,'paper_ready':False,
            'distribution_approved':False,'installer_ready':False}

def _snapshot_manifest(snapshot):
    v=_json(snapshot/'manifest.json',32*1024**2)
    if not isinstance(v,dict) or set(v)!={'version','kind','cutoff','files'} or v['version']!=1 or v['kind']!='tradebot-data-only':_reject('invalid seed snapshot format')
    if type(v['cutoff']) is not int or not 0<v['cutoff']<=int(time.time()):_reject('invalid seed snapshot cutoff')
    files=v['files']
    if not isinstance(files,dict) or not set(DATABASES)<=files.keys() or not 2<=len(files)<=MAX_FILES:_reject('invalid seed snapshot files')
    total=0
    for rel,entry in files.items():
        if not isinstance(rel,str) or not rel or '\\' in rel or not isinstance(entry,dict) or set(entry)!={'bytes','sha256'}:_reject('invalid seed asset receipt')
        path=Path(rel)
        if path.is_absolute() or path.as_posix()!=rel or '..' in path.parts or not path.parts:_reject('unsafe seed asset path')
        if rel not in (*DATABASES,DIRECTORY_DB) and not re.fullmatch(r'market-memory/(?:[0-9a-f]{64}\.csv|lake-v1/[0-9a-f]{64}\.parquet|parquet/[A-Z0-9]+_USD/(?:1|5|15|60|240|720|1440)/[0-9a-f]{64}\.parquet)',rel):_reject('unrecognized seed asset path')
        _hex(entry['sha256'])
        if type(entry['bytes']) is not int or not 0<entry['bytes']<=MAX_BYTES-total:_reject('seed snapshot size bound exceeded')
        total+=entry['bytes']
    return v,total

def _asset_matches(path,entry):
    return not path.is_symlink() and path.is_file() and path.stat().st_size==entry['bytes'] and sha256_file(path)==entry['sha256']

def _inventory(root):
    objects=_child(root,'objects');versions=_child(root,'versions')
    usage=0;count=0
    for p in objects.iterdir():
        count+=1
        if count>MAX_OBJECTS or not HEX.fullmatch(p.name) or p.is_symlink() or not p.is_file():_reject('unexpected seed object inventory')
        usage+=p.stat().st_size
        if usage>MAX_STORE_BYTES:_reject('seed object store capacity reached')
    n=0
    for p in versions.iterdir():
        n+=1
        if n>MAX_VERSIONS or not VERSION.fullmatch(p.name):_reject('unexpected seed version inventory')
        _private_dir(p)
    return usage,count,n

def capture(root,version):
    """Capture the existing persistent workspace, reusing unchanged objects.
    No previous versions or workspace data are removed; orphaned objects
    after an interrupted capture remain charged to the bounded store.
    """
    version=_version(version);root=_root(root)
    data=_child(root,'workspace');objects=_child(root,'objects');versions=_child(root,'versions')
    with exclusive_job(root,'seed-store'):
        target=versions/version
        if target.exists() or target.is_symlink():_reject('immutable seed version already exists')
        usage,count,n=_inventory(root)
        if n>=MAX_VERSIONS:_reject('seed version capacity reached')
        with tempfile.TemporaryDirectory(prefix='.seed-capture-',dir=root) as tmp:
            tmp=Path(tmp);snapshot=tmp/'snapshot'
            receipt=backup(data,snapshot)
            snap,total=_snapshot_manifest(snapshot)
            _schema_inventory(snapshot,snap['files'])
            confirmed=tmp/'confirmed'
            restore(snapshot,confirmed,receipt['manifest_sha256'])
            review=_review(confirmed,snap['cutoff'])
            # Stage bytes privately, fsync, then no-replace object publication.
            added=0;reused=0;object_count=0
            for rel,entry in sorted(snap['files'].items()):
                ident=entry['sha256'];obj=objects/ident
                if obj.exists() or obj.is_symlink():
                    if not _asset_matches(obj,entry):_reject('retained seed object checksum mismatch')
                    reused+=entry['bytes'];continue
                if count+object_count>=MAX_OBJECTS or usage+added+entry['bytes']>MAX_STORE_BYTES:_reject('seed object quota reached')
                stage=tmp/'object'
                _copy(_safe(snapshot,rel),stage,entry['bytes'])
                if _entry(stage)!=entry:_reject('seed object changed during capture')
                os.link(stage,obj);syncdir(objects);stage.unlink()
                added+=entry['bytes'];object_count+=1
            stage=tmp/'version';stage.mkdir(mode=0o700)
            # Exact snapshot manifest bytes must retain their original SHA.
            _copy(snapshot/'manifest.json',stage/'snapshot.json',32*1024**2)
            manifest={'format':FORMAT,'kind':'maj-vaj-private-release-seed','compatibility':COMPATIBILITY,
                      'version':version,'cutoff':snap['cutoff'],'snapshot_sha256':receipt['manifest_sha256'],
                      'bytes':total,'files':len(snap['files']),'review':review,'rights':rights_inventory(),
                      'distribution_approved':False,'installer_ready':False,'training_ready':False,'paper_ready':False}
            _write(stage/'manifest.json',manifest);_sync_tree(stage);_publish_directory(stage,target)
    return manifest|{'manifest_sha256':sha256_file(target/'manifest.json'),'new_object_bytes':added,'reused_object_bytes':reused}

def _package_manifest(manifest_path,expected,version=None):
    v=_json(manifest_path,MAX_MANIFEST)
    if sha256_file(manifest_path)!=_hex(expected):_reject('seed manifest checksum mismatch')
    if not isinstance(v,dict) or set(v)!={'format','kind','compatibility','version','cutoff','snapshot_sha256','bytes','files','review','rights','distribution_approved','installer_ready','training_ready','paper_ready'} or v.get('format')!=FORMAT or v.get('kind')!='maj-vaj-private-release-seed' or v.get('compatibility')!=COMPATIBILITY or v.get('rights')!=rights_inventory() or any(v.get(k) is not False for k in ('distribution_approved','installer_ready','training_ready','paper_ready')):
        _reject('incompatible or unauthorized seed manifest')
    _version(v['version']);_hex(v['snapshot_sha256'])
    if version is not None and v['version']!=version:_reject('seed version mismatch')
    if type(v['cutoff']) is not int or not 0<v['cutoff']<=int(time.time()) or type(v['bytes']) is not int or not 0<v['bytes']<=MAX_BYTES or type(v['files']) is not int or not 2<=v['files']<=MAX_FILES:_reject('invalid seed manifest totals')
    return v

def _read_version(root,version,expected):
    p=_child(_child(root,'versions'),_version(version))
    v=_package_manifest(p/'manifest.json',expected,version)
    if {x.name for x in p.iterdir()}!={'manifest.json','snapshot.json'}:_reject('unexpected seed version files')
    s=p/'snapshot.json'
    if s.is_symlink() or not s.is_file() or s.stat().st_size>32*1024**2 or sha256_file(s)!=_hex(v.get('snapshot_sha256')):_reject('seed snapshot manifest checksum mismatch')
    return p,v

def _assemble(root,version,expected,destination):
    p,v=_read_version(root,version,expected)
    destination.mkdir(mode=0o700)
    _copy(p/'snapshot.json',destination/'manifest.json',32*1024**2)
    snap,total=_snapshot_manifest(destination)
    if snap['cutoff']!=v['cutoff'] or total!=v['bytes'] or len(snap['files'])!=v['files']:_reject('seed snapshot totals disagree')
    objects=_child(root,'objects')
    for rel,entry in sorted(snap['files'].items()):
        src=objects/entry['sha256']
        if not _asset_matches(src,entry):_reject('seed object checksum mismatch')
        target=_safe(destination,rel);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        _copy(src,target,entry['bytes'])
        if _entry(target)!=entry:_reject('seed assembly checksum mismatch')
    _schema_inventory(destination,snap['files'])
    return v

def _verified(root,version,expected,tmp):
    snapshot=tmp/'snapshot';v=_assemble(root,version,expected,snapshot)
    fresh=tmp/'restored';restore(snapshot,fresh,v['snapshot_sha256'])
    review=_review(fresh,v['cutoff'])
    if review!=v['review']:_reject('seed coverage report does not reproduce')
    return v,snapshot,fresh

def verify(root,version,expected):
    root=_root(root)
    with exclusive_job(root,'seed-store'),tempfile.TemporaryDirectory(prefix='.seed-verify-',dir=root) as tmp:
        v,_,_=_verified(root,version,expected,Path(tmp))
    return v|{'manifest_sha256':expected}

def materialize(root,version,expected,destination,*,restore_data=False,bundle=False):
    """Export a standalone data-only snapshot or restore into NEW private
    destination. No overwrite, merge, model training or activation."""
    root=_root(root);destination=Path(destination).absolute()
    _private_dir(destination.parent);destination=destination.parent.resolve()/destination.name
    if destination.exists() or destination.is_symlink() or root==destination or root in destination.parents:_reject('seed destination must be new and outside repository')
    with exclusive_job(root,'seed-store'),tempfile.TemporaryDirectory(prefix='.seed-transfer-',dir=destination.parent) as tmp:
        v,snapshot,fresh=_verified(root,version,expected,Path(tmp))
        if restore_data and bundle:raise ValueError('choose bundle or fresh restore')
        if bundle:
            stage=Path(tmp)/'package';stage.mkdir(mode=0o700)
            os.rename(snapshot,stage/'snapshot')
            _copy(_child(_child(root,'versions'),version)/'manifest.json',stage/'manifest.json',MAX_MANIFEST)
            if sha256_file(stage/'manifest.json')!=expected:_reject('seed manifest changed during transfer')
        else:stage=fresh if restore_data else snapshot
        _sync_tree(stage);_publish_directory(stage,destination)
    return {'version':version,'manifest_sha256':expected,'snapshot_sha256':v['snapshot_sha256'],
            'state':'restored_private_data' if restore_data else 'materialized_private_bundle' if bundle else 'materialized_private_snapshot',
            'distribution_approved':False,'training_ready':False}

def _bundle_inventory(snapshot,files):
    seen=set();directories=0
    allowed_dirs={parent.as_posix() for rel in files for parent in Path(rel).parents if parent!=Path('.')}
    for dirname,dirs,names in os.walk(snapshot,followlinks=False):
        for name in dirs:
            path=Path(dirname)/name;directories+=1
            if directories>MAX_FILES or path.is_symlink() or path.relative_to(snapshot).as_posix() not in allowed_dirs:_reject('unsafe or unexpected portable seed directory')
        for name in names:
            path=Path(dirname)/name
            if path.is_symlink() or not path.is_file():_reject('unsafe portable seed file')
            seen.add(path.relative_to(snapshot).as_posix())
            if len(seen)>MAX_FILES+1:_reject('portable seed inventory bound reached')
    if seen!=set(files)|{'manifest.json'}:_reject('portable seed file inventory mismatch')

def restore_bundle(bundle,expected,destination):
    """Restore a standalone package without its original object repository."""
    bundle=Path(bundle).absolute();_private_dir(bundle)
    if {p.name for p in bundle.iterdir()}!={'manifest.json','snapshot'}:_reject('unexpected portable seed inventory')
    v=_package_manifest(bundle/'manifest.json',expected)
    snapshot=_child(bundle,'snapshot')
    snap,total=_snapshot_manifest(snapshot);_bundle_inventory(snapshot,snap['files'])
    if sha256_file(snapshot/'manifest.json')!=v['snapshot_sha256'] or snap['cutoff']!=v['cutoff'] or total!=v['bytes'] or len(snap['files'])!=v['files']:_reject('portable seed manifest mismatch')
    _schema_inventory(snapshot,snap['files'])
    destination=Path(destination).absolute();_private_dir(destination.parent)
    destination=destination.parent.resolve()/destination.name
    if destination.exists() or destination.is_symlink() or bundle.resolve() in destination.parents:_reject('portable seed requires a new destination')
    with tempfile.TemporaryDirectory(prefix='.seed-install-',dir=destination.parent) as tmp:
        fresh=Path(tmp)/'restored';restore(snapshot,fresh,v['snapshot_sha256'])
        if _review(fresh,v['cutoff'])!=v['review']:_reject('portable seed coverage does not reproduce')
        _sync_tree(fresh);_publish_directory(fresh,destination)
    return {'version':v['version'],'manifest_sha256':expected,'snapshot_sha256':v['snapshot_sha256'],
            'state':'restored_private_data','distribution_approved':False,'training_ready':False}

def status(root):
    root=_root(root)
    with exclusive_job(root,'seed-store'):
        usage,count,n=_inventory(root)
        versions=[]
        for p in sorted(_child(root,'versions').iterdir()):
            path=p/'manifest.json';v=_json(path,MAX_MANIFEST)
            versions.append({'version':p.name,'cutoff':v['cutoff'],'manifest_sha256':sha256_file(path),
                             'bytes':v['bytes'],'core_coverage_complete':v['review']['core_coverage_complete'],'verification':'not_checked'})
        return {'workspace':str(_child(root,'workspace')),'object_bytes':usage,'object_count':count,
                'version_count':n,'versions':versions,'distribution_approved':False}
