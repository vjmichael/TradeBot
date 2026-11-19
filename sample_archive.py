"""Lossless, bounded cold storage for reviewed retired sample records.

The original backup stays protected. This archive does not claim that a
historical sample was valid merely because the original bytes were preserved.
"""
import hashlib
import json
import sqlite3
import tempfile
import zlib
from contextlib import closing
from pathlib import Path
from market_memory import MemoryUnavailable,MarketMemory
from market_archive import exclusive_job

MAX_RECORD=256*1024
MAX_BATCH=1000
MAX_ARCHIVE_BYTES=2*1024**3

def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS quality_archive(
      pair TEXT NOT NULL,slot INTEGER NOT NULL,manifest_sha256 TEXT NOT NULL,
      raw_sha256 TEXT NOT NULL,raw_bytes INTEGER NOT NULL,compressed BLOB NOT NULL,
      PRIMARY KEY(pair,slot))""")

def archive_record(db,pair,slot,manifest,attempt,book,usage=None):
    schema(db)
    raw=json.dumps({'attempt':dict(attempt),'book':dict(book) if book is not None else None},sort_keys=True,separators=(',',':')).encode()
    if len(raw)>MAX_RECORD:raise MemoryUnavailable('sample archive record exceeds bound')
    sha=hashlib.sha256(raw).hexdigest()
    prior=db.execute('SELECT * FROM quality_archive WHERE pair=? AND slot=?',(pair,slot)).fetchone()
    if prior:
        if prior['manifest_sha256']!=manifest or prior['raw_sha256']!=sha or decode(prior)!=json.loads(raw):
            raise MemoryUnavailable('sample archive identity conflict')
        return
    compressed=zlib.compress(raw,9)
    if usage is None:usage=[db.execute('SELECT COALESCE(sum(length(compressed)),0) FROM quality_archive').fetchone()[0]]
    if usage[0]+len(compressed)>MAX_ARCHIVE_BYTES:
        raise MemoryUnavailable('sample archive capacity reached')
    db.execute('INSERT INTO quality_archive VALUES(?,?,?,?,?,?)',(pair,slot,manifest,sha,len(raw),compressed));usage[0]+=len(compressed)

def decode(row):
    size=row['raw_bytes']
    if type(size) is not int or not 0<size<=MAX_RECORD or len(row['compressed'])>MAX_RECORD+1024:
        raise MemoryUnavailable('invalid archive size')
    try:
        d=zlib.decompressobj()
        raw=d.decompress(row['compressed'],size+1)
        if len(raw)!=size or not d.eof or d.unused_data or d.unconsumed_tail or hashlib.sha256(raw).hexdigest()!=row['raw_sha256']:
            raise MemoryUnavailable('sample archive checksum mismatch')
        value=json.loads(raw)
        if value['attempt']['pair']!=row['pair'] or value['attempt']['slot']!=row['slot']:
            raise MemoryUnavailable('sample archive identity mismatch')
        return value
    except (zlib.error,ValueError,KeyError,TypeError) as exc:
        raise MemoryUnavailable('invalid sample archive record') from exc

def verify_archive(db):
    """Validate cold record bytes and their preserved retirement receipts."""
    total=0
    for row in db.execute('SELECT * FROM quality_archive ORDER BY pair,slot'):
        total+=len(row['compressed'])
        if total>MAX_ARCHIVE_BYTES:raise MemoryUnavailable('sample archive capacity exceeded')
        value=decode(row)
        receipt=db.execute('SELECT had_book,manifest_sha256 FROM quality_retired WHERE pair=? AND slot=?',(row['pair'],row['slot'])).fetchone()
        if not receipt or receipt['manifest_sha256']!=row['manifest_sha256'] or bool(receipt['had_book'])!=(value['book'] is not None):
            raise MemoryUnavailable('sample archive receipt mismatch')
        if value['book'] is not None and (value['book']['pair']!=row['pair'] or value['book']['slot']!=row['slot']):
            raise MemoryUnavailable('archived book identity mismatch')

def archived_sample(memory,pair,slot):
    with memory.tx() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quality_archive'").fetchone():return None
        row=db.execute('SELECT * FROM quality_archive WHERE pair=? AND slot=?',(pair,slot)).fetchone()
        return None if row is None else decode(row)|{'manifest_sha256':row['manifest_sha256'],'raw_sha256':row['raw_sha256']}

def status(memory):
    with memory.tx() as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        count,size=(0,0) if 'quality_archive' not in tables else tuple(db.execute('SELECT count(*),COALESCE(sum(length(compressed)),0) FROM quality_archive').fetchone())
        retired=db.execute('SELECT count(*) FROM quality_retired').fetchone()[0] if 'quality_retired' in tables else 0
        return {'archived_records':count,'compressed_bytes':size,'retired_receipts':retired,'unconsolidated':max(0,retired-count),'original_recovery_copies_protected':True}

def consolidate(directory,snapshot,manifest,*,limit=MAX_BATCH):
    """Recover old retired records from an independently verified exact copy."""
    from data_lifecycle import restore
    if type(limit) is not int or not 1<=limit<=MAX_BATCH:raise ValueError('invalid archive batch')
    m=MarketMemory(directory)
    with tempfile.TemporaryDirectory(prefix='archive-verify-',dir=Path(snapshot).absolute().parent) as tmp:
        target=Path(tmp)/'verified';restore(snapshot,target,manifest)
        with closing(sqlite3.connect(target/'market-memory/catalog.sqlite3')) as old,exclusive_job(m.root,'quality-book'),m.tx() as db:
            old.row_factory=sqlite3.Row;schema(db)
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quality_retired'").fetchone():return {'archived':0}
            victims=db.execute("""SELECT r.* FROM quality_retired r WHERE manifest_sha256=?
                AND NOT EXISTS(SELECT 1 FROM quality_archive a WHERE a.pair=r.pair AND a.slot=r.slot)
                ORDER BY r.slot,r.pair LIMIT ?""",(manifest,limit)).fetchall()
            usage=[db.execute('SELECT COALESCE(sum(length(compressed)),0) FROM quality_archive').fetchone()[0]]
            for r in victims:
                key=(r['pair'],r['slot'])
                a=old.execute('SELECT * FROM quality_attempts WHERE pair=? AND slot=?',key).fetchone()
                b=old.execute('SELECT * FROM quality_books WHERE pair=? AND slot=?',key).fetchone()
                if a is None or bool(b)!=bool(r['had_book']) or (a['state']=='observed')!=(b is not None):
                    raise MemoryUnavailable('original retired sample unavailable')
                archive_record(db,*key,manifest,a,b,usage)
            return {'archived':len(victims)}
