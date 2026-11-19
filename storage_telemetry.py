"""Bounded host-local measurements of the shared public-data store.

Logical file sizes and allocated disk blocks are measured, not projected.
The host-local telemetry is deliberately outside data-only recovery copies:
restoring data on another host starts a new measurement series.
"""
import json
import os
import shutil
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path
from market_memory import MemoryUnavailable

ROOTS=('market-memory','market-directory','economic-memory')
SAMPLE_SECONDS=300
MAX_SAMPLES=30*288+1
MAX_ENTRIES=100000
MAX_BODY=65536

def _path(directory,create=False):
    root=Path(directory)/'preparation'
    if create:root.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=root/'storage-measurements.sqlite3'
    if root.is_symlink() or path.is_symlink() or (root.exists() and (not root.is_dir() or (os.name=='posix' and root.stat().st_mode&0o077))):
        raise MemoryUnavailable('unsafe storage measurement directory')
    return path

def _inventory(directory,names=ROOTS):
    totals={};count=0
    for name in names:
        root=Path(directory)/name
        if root.is_symlink():raise MemoryUnavailable('unsafe public storage root')
        logical=allocated=files=0
        if root.exists():
            if not root.is_dir():raise MemoryUnavailable('invalid public storage root')
            for base,dirs,names in os.walk(root,followlinks=False,onerror=lambda err: (_ for _ in ()).throw(err)):
                for entry in dirs+names:
                    count+=1
                    if count>MAX_ENTRIES:raise MemoryUnavailable('storage measurement inventory exceeds bound')
                    p=Path(base)/entry
                    try:info=p.lstat()
                    except FileNotFoundError:
                        # WAL/temp publication may race a sample. Do not
                        # publish a partial inventory as a complete sample.
                        raise MemoryUnavailable('storage changed during measurement') from None
                    if stat.S_ISLNK(info.st_mode):raise MemoryUnavailable('unexpected public storage symlink')
                    if stat.S_ISREG(info.st_mode):
                        logical+=info.st_size
                        allocated+=getattr(info,'st_blocks',(info.st_size+511)//512)*512
                        files+=1
                    elif not stat.S_ISDIR(info.st_mode):raise MemoryUnavailable('unexpected public storage entry')
        totals[name]={'logical_bytes':logical,'allocated_bytes':allocated,'files':files}
    return totals

def _read_metrics(memory,symbols):
    counts=[]
    with memory.tx() as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for pair in symbols:
            # Rows include retained revisions; not a unique-hour coverage claim.
            row=db.execute("""SELECT count(*) FROM candles c JOIN sources s ON s.id=c.source_id
                WHERE c.pair=? AND s.kind!='fixture' AND s.state='complete'""",(pair,)).fetchone()[0]
            book=db.execute('SELECT count(*) FROM quality_books WHERE pair=?',(pair,)).fetchone()[0] if 'quality_books' in tables else 0
            pages=db.execute('SELECT COALESCE(sum(pages),0) FROM gap_jobs WHERE pair=?',(pair,)).fetchone()[0] if 'gap_jobs' in tables else 0
            archived=db.execute('SELECT count(*) FROM quality_archive WHERE pair=?',(pair,)).fetchone()[0] if 'quality_archive' in tables else 0
            recent=db.execute('SELECT last_success FROM collector WHERE pair=? AND interval=5',(pair,)).fetchone()
            counts.append({'pair':pair,'published_candle_rows':row,'retained_books':book,'archived_samples':archived,'saved_recovery_pages':pages,'latest_five_minute_observation':recent[0] if recent else None})
    return counts

def sample_storage(memory,symbols,*,now=None):
    now=int(time.time()) if now is None else now
    if type(now) is not int or now<=0 or not symbols or len(symbols)>32:raise ValueError('invalid storage sample scope')
    known=memory.symbols
    if any(known.get(p)!=v for p,v in symbols.items()):raise ValueError('unknown storage sample identity')
    scope=json.dumps(sorted(symbols.items()),separators=(',',':'))
    path=_path(memory.directory,True)
    with closing(sqlite3.connect(path,timeout=10)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS samples(observed INTEGER PRIMARY KEY,scope TEXT NOT NULL,body TEXT NOT NULL)')
        db.commit()
        last=db.execute('SELECT max(observed) FROM samples').fetchone()[0]
        if last is not None and now<last:raise MemoryUnavailable('storage sample clock moved backward')
        if last is not None and now-last<SAMPLE_SECONDS:return False
        counts=_read_metrics(memory,symbols)
        roots=_inventory(memory.directory)
        recovery_name=memory.directory.name+'-recovery-copies'
        recovery=_inventory(memory.directory.parent,(recovery_name,))[recovery_name]
        evidence_db=memory.directory/'economic-memory/evidence.sqlite3'
        vintage_count=vintage_bytes=0
        if evidence_db.is_file():
            with closing(sqlite3.connect(evidence_db.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as edb:
                if edb.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alfred_vintages'").fetchone():
                    vintage_count,vintage_bytes=edb.execute('SELECT count(*),COALESCE(sum(length(body)),0) FROM alfred_vintages').fetchone()
        result={'observed_at':now,'markets':counts,'roots':roots,
                'logical_bytes':sum(r['logical_bytes'] for r in roots.values()),
                'allocated_bytes':sum(r['allocated_bytes'] for r in roots.values()),
                'free_bytes':shutil.disk_usage(memory.directory).free,'recovery_copies':recovery,'vintage_snapshots':vintage_count,'vintage_payload_bytes':vintage_bytes}
        body=json.dumps(result,sort_keys=True,separators=(',',':'))
        if len(body)>MAX_BODY:raise MemoryUnavailable('storage sample exceeds bound')
        with db:
            db.execute('BEGIN IMMEDIATE')
            last=db.execute('SELECT max(observed) FROM samples').fetchone()[0]
            if last is not None and now-last<SAMPLE_SECONDS:return False
            db.execute('INSERT INTO samples VALUES(?,?,?)',(now,scope,body))
            db.execute('DELETE FROM samples WHERE observed NOT IN (SELECT observed FROM samples ORDER BY observed DESC LIMIT ?)',(MAX_SAMPLES,))
    os.chmod(path,0o600)
    return True

def storage_growth(directory,symbols):
    path=_path(directory)
    if not path.is_file():return {'state':'awaiting_samples','sample_count':0,'latest':None,'growth':None}
    scope=json.dumps(sorted(symbols.items()),separators=(',',':'))
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='samples'").fetchone():
            return {'state':'awaiting_samples','sample_count':0,'latest':None,'growth':None}
        latest=db.execute('SELECT observed,body FROM samples WHERE scope=? ORDER BY observed DESC LIMIT 1',(scope,)).fetchone()
        if not latest:return {'state':'awaiting_samples','sample_count':0,'latest':None,'growth':None}
        first=db.execute('SELECT observed,body FROM samples WHERE scope=? AND observed>=? ORDER BY observed LIMIT 1',(scope,latest[0]-86400)).fetchone()
        count=db.execute('SELECT count(*) FROM samples WHERE scope=? AND observed BETWEEN ? AND ?',(scope,first[0],latest[0])).fetchone()[0]
        windows=[]
        for days in (1,7,30):
            records=[r[0] for r in db.execute('SELECT observed FROM samples WHERE scope=? AND observed BETWEEN ? AND ? ORDER BY observed',(scope,latest[0]-days*86400,latest[0]))]
            first_at=records[0];span=latest[0]-first_at
            initial=json.loads(db.execute('SELECT body FROM samples WHERE observed=?',(first_at,)).fetchone()[0]);final=json.loads(latest[1])
            gaps=[b-a for a,b in zip(records,records[1:])]
            windows.append({'days':days,'sample_count':len(records),'elapsed_seconds':span,'max_sample_gap_seconds':max(gaps,default=0),'missed_nominal_slots':max(0,span//SAMPLE_SECONDS+1-len(records)),
                            'logical_bytes_delta':final['logical_bytes']-initial['logical_bytes'],'allocated_bytes_delta':final['allocated_bytes']-initial['allocated_bytes'],
                            'recovery_allocated_delta':final['recovery_copies']['allocated_bytes']-initial['recovery_copies']['allocated_bytes'] if 'recovery_copies' in initial and 'recovery_copies' in final else None})
    a,b=(json.loads(first[1]),json.loads(latest[1]))
    elapsed=latest[0]-first[0]
    growth={'elapsed_seconds':elapsed,'logical_bytes_delta':b['logical_bytes']-a['logical_bytes'],'allocated_bytes_delta':b['allocated_bytes']-a['allocated_bytes']} if elapsed else None
    return {'state':'measured' if growth else 'awaiting_second_sample','sample_count':count,'latest':b,'growth':growth,
            'windows':windows,'scope':'public market and economic storage; recovery copies measured separately; credentials excluded; sampled elapsed time is not verified uptime'}
