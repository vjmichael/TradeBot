"""TB-04F1: bounded historical coverage and verified data-only snapshots.

No credentials, execution ledger, external paths from the dashboard, or live
trading authority. Backup locks both catalogs at one joint consistency cut.
"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from engine import SYMBOLS
from market_memory import MarketMemory, MemoryUnavailable, sha256_file
from economic_memory import EconomicMemory
from market_lake import syncdir

DATABASES = ('market-memory/catalog.sqlite3', 'economic-memory/evidence.sqlite3')
MAX_BYTES = 8 * 1024**3
MAX_FILES = 100000
RESERVE = 256 * 1024**2
HEX = re.compile('[0-9a-f]{64}')


def coverage(memory, now=None):
    now = int(time.time()) if now is None else now
    end = now // 3600 * 3600
    start = end - 1825 * 86400
    result = []
    for pair in SYMBOLS:
        view = memory.query(pair, 60, start, end, now)
        result.append({k: view[k] for k in ('pair','start','end','expected','coverage','missing','conflicts')})
    # Missing can include legitimate no-trade hours. Never infer readiness.
    return {'as_of': now, 'markets': result, 'state': 'review_required', 'training_ready': False}


def _safe(root, rel):
    path = Path(rel)
    if path.is_absolute() or any(p in ('..','.') for p in path.parts):
        raise MemoryUnavailable('unsafe snapshot path')
    dest = root
    for part in path.parts:
        dest = dest / part
        if dest.is_symlink(): raise MemoryUnavailable('unsafe snapshot symlink')
    return dest


def _catalog_files(db):
    """Only catalog-bound files, including retired generations and import checkpoints."""
    def ident(s):
        if not isinstance(s,str) or not HEX.fullmatch(s): raise MemoryUnavailable('invalid catalog identity')
        return s
    files = {}
    for source, digest in db.execute("SELECT id,sha256 FROM sources WHERE kind IN ('archive','fixture')"):
        files['market-memory/'+ident(source)+'.csv'] = ident(digest)
    for path,digest,pair,interval,key in db.execute('SELECT path,sha256,pair,interval,id FROM exports'):
        expected = f'parquet/{pair.replace("/","_")}/{interval}/{ident(key)}.parquet'
        if pair not in SYMBOLS or path != expected or type(interval) is not int or interval not in (1,5,15,60,240,720,1440):
            raise MemoryUnavailable('invalid export reference')
        files['market-memory/'+expected] = ident(digest)
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lake_files'").fetchone():
        for key,digest in db.execute('SELECT id,sha256 FROM lake_files'):
            files['market-memory/lake-v1/'+ident(key)+'.parquet'] = ident(digest)
    if len(files)>MAX_FILES-2:raise MemoryUnavailable('snapshot inventory exceeds bound')
    return files


def _private_dir(path):
    if path.is_symlink() or not path.is_dir() or (os.name=='posix' and path.stat().st_mode & 0o077):
        raise MemoryUnavailable('snapshot directory must be private')


def _check_db(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise MemoryUnavailable('SQLite snapshot integrity failure')


def _copy(source, target, budget):
    if not source.is_file() or source.is_symlink():raise MemoryUnavailable('snapshot asset missing or unsafe')
    size=source.stat().st_size
    if size>budget or shutil.disk_usage(target.parent).free < size+RESERVE:
        raise MemoryUnavailable('snapshot storage limit reached')
    with source.open('rb') as src,target.open('xb') as out:
        total=0
        for chunk in iter(lambda:src.read(1024*1024),b''):
            total+=len(chunk)
            if total>size:raise MemoryUnavailable('snapshot source changed')
            out.write(chunk)
        if total!=size:raise MemoryUnavailable('snapshot source changed')
        out.flush();os.fsync(out.fileno())
    os.chmod(target,0o600)
    return size


def _entry(path):return {'bytes':path.stat().st_size,'sha256':sha256_file(path)}


def _publish_directory(stage, target):
    # Linux no-replace rename; never clobber even an independently created empty directory.
    import ctypes
    libc=ctypes.CDLL(None,use_errno=True)
    if not hasattr(libc,'renameat2'):raise MemoryUnavailable('atomic no-replace publication unavailable')
    if libc.renameat2(-100,os.fsencode(stage),-100,os.fsencode(target),1)!=0:
        raise OSError(ctypes.get_errno(),'snapshot publication refused')
    syncdir(target.parent)


def _sync_tree(root):
    for dirname,dirs,files in os.walk(root,topdown=False):syncdir(Path(dirname))


def backup(directory, destination):
    """Copy immutable assets under writer locks and SQLite online backup, then publish atomically."""
    directory=Path(directory).resolve();destination=Path(destination).absolute()
    _private_dir(directory);_private_dir(destination.parent)
    destination=destination.parent.resolve()/destination.name
    for rel in DATABASES:
        if not _safe(directory,rel).is_file():raise MemoryUnavailable('database missing')
    if directory in destination.parents or destination==directory or destination.exists() or destination.is_symlink():
        raise MemoryUnavailable('backup requires a new directory outside the data store')
    m=MarketMemory(directory);e=EconomicMemory(directory)
    stage=Path(tempfile.mkdtemp(prefix='.snapshot-',dir=destination.parent));entries={};used=0
    try:
        with m.tx() as market_db,e.tx(write=True):
            files=_catalog_files(market_db);cut=int(time.time())
            for rel in DATABASES:
                src=_safe(directory,rel);target=_safe(stage,rel);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                if not src.is_file():raise MemoryUnavailable('database missing')
                with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as reader,closing(sqlite3.connect(target)) as writer:
                    estimate=reader.execute('PRAGMA page_count').fetchone()[0]*reader.execute('PRAGMA page_size').fetchone()[0]
                    if estimate>MAX_BYTES-used or shutil.disk_usage(stage).free<estimate+RESERVE:raise MemoryUnavailable('snapshot storage limit reached')
                    reader.backup(writer,pages=256)
                    writer.execute('PRAGMA journal_mode=DELETE')
                os.chmod(target,0o600);_check_db(target)
                with target.open('rb') as f:os.fsync(f.fileno())
                entries[rel]=_entry(target);used+=entries[rel]['bytes']
            for rel,digest in sorted(files.items()):
                target=_safe(stage,rel);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                used+=_copy(_safe(directory,rel),target,MAX_BYTES-used);entries[rel]=_entry(target)
                if entries[rel]['sha256']!=digest:raise MemoryUnavailable('catalog asset checksum mismatch')
        manifest={'version':1,'kind':'tradebot-data-only','cutoff':cut,'files':entries}
        with (stage/'manifest.json').open('x') as f:
            json.dump(manifest,f,sort_keys=True,separators=(',',':'));f.flush();os.fsync(f.fileno())
        os.chmod(stage/'manifest.json',0o600);_sync_tree(stage);_publish_directory(stage,destination)
        return {'files':len(entries),'bytes':used,'cutoff':cut,'manifest_sha256':sha256_file(destination/'manifest.json')}
    finally:
        if stage.exists():shutil.rmtree(stage)


def restore(snapshot, destination, expected_manifest_sha256):
    """Verify external manifest digest and catalog asset set; restore only to a fresh directory."""
    snapshot=Path(snapshot).absolute();destination=Path(destination).absolute()
    _private_dir(snapshot);_private_dir(destination.parent)
    snapshot=snapshot.resolve();destination=destination.parent.resolve()/destination.name
    if destination.exists() or destination.is_symlink() or snapshot in destination.parents:
        raise MemoryUnavailable('restore requires a new destination')
    path=_safe(snapshot,'manifest.json')
    if not path.is_file() or path.stat().st_size>32*1024**2 or not isinstance(expected_manifest_sha256,str) or not HEX.fullmatch(expected_manifest_sha256) or sha256_file(path)!=expected_manifest_sha256:
        raise MemoryUnavailable('snapshot manifest checksum mismatch')
    manifest=json.loads(path.read_text());files=manifest['files']
    if manifest['version']!=1 or manifest['kind']!='tradebot-data-only' or not isinstance(files,dict) or not 2<=len(files)<=MAX_FILES or not set(DATABASES)<=files.keys():raise MemoryUnavailable('invalid snapshot manifest')
    stage=Path(tempfile.mkdtemp(prefix='.restore-',dir=destination.parent));used=0
    try:
        # Copy only DBs first, derive the only permitted relative asset names from the catalog.
        def copy(rel):
            nonlocal used
            entry=files[rel];src=_safe(snapshot,rel);dst=_safe(stage,rel);dst.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if type(entry['bytes']) is not int or not 0<=entry['bytes']<=MAX_BYTES-used or src.stat().st_size!=entry['bytes']:raise MemoryUnavailable('invalid snapshot file size')
            used+=_copy(src,dst,MAX_BYTES-used)
            if _entry(dst)!=entry:raise MemoryUnavailable('snapshot file checksum mismatch')
        for rel in DATABASES:copy(rel);_check_db(stage/rel)
        with closing(sqlite3.connect(stage/DATABASES[0])) as db:
            bound=_catalog_files(db)
            if set(files)!=set(DATABASES)|set(bound):raise MemoryUnavailable('snapshot asset inventory mismatch')
            for rel,digest in bound.items():
                if files[rel]['sha256']!=digest:raise MemoryUnavailable('snapshot catalog checksum mismatch')
                copy(rel)
            # A restored process cannot inherit a previous live connection.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stream_health'").fetchone():
                db.execute("UPDATE stream_health SET connected=0,acked=0,state='offline'");db.commit()
        _sync_tree(stage);_publish_directory(stage,destination)
        return {'files':len(files),'bytes':used,'cutoff':manifest['cutoff'],'state':'restored_data_only'}
    finally:
        if stage.exists():shutil.rmtree(stage)
