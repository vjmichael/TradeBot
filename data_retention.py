"""Explicit bounded retirement of old sampled books after verified recovery copy.
No source candle, economic record, model, or credential removal.
"""
import tempfile
import time
import sqlite3
from pathlib import Path
from contextlib import closing
from market_archive import exclusive_job
from market_memory import MarketMemory, MemoryUnavailable
from market_quality import QualityMemory
from data_lifecycle import restore

MIN_AGE=30*86400
MAX_BATCH=1000

def retire_books(directory,snapshot,digest,*,apply=False,limit=MAX_BATCH,now=None):
    if type(limit) is not int or not 1<=limit<=MAX_BATCH or type(apply) is not bool:raise ValueError('invalid retention bounds')
    now=int(time.time()) if now is None else now
    if type(now) is not int or now<=MIN_AGE:raise ValueError('invalid retention time')
    m=MarketMemory(directory);QualityMemory(m)
    # Restore the exact verified snapshot into a private temporary location to
    # eliminate read-after-validation races against the operator's backup.
    with tempfile.TemporaryDirectory(prefix='retention-verify-',dir=Path(snapshot).absolute().parent) as tmp:
        target=Path(tmp)/'verified';restore(snapshot,target,digest)
        with closing(sqlite3.connect(target/'market-memory/catalog.sqlite3')) as old,exclusive_job(m.root,'quality-book'),m.tx() as db:
            old.row_factory=sqlite3.Row
            rows=db.execute("SELECT a.* FROM quality_attempts a WHERE a.slot<? AND a.finished IS NOT NULL AND a.state IN ('observed','unavailable') AND a.slot<(SELECT MAX(b.slot) FROM quality_attempts b WHERE b.pair=a.pair) ORDER BY a.slot,a.pair LIMIT ?",(now-MIN_AGE,limit)).fetchall()
            victims=[]
            for a in rows:
                key=(a['pair'],a['slot']);prior=old.execute('SELECT * FROM quality_attempts WHERE pair=? AND slot=?',key).fetchone();b=db.execute('SELECT * FROM quality_books WHERE pair=? AND slot=?',key).fetchone();saved=old.execute('SELECT * FROM quality_books WHERE pair=? AND slot=?',key).fetchone()
                if prior is None or tuple(prior)!=tuple(a) or (None if b is None else tuple(b))!=(None if saved is None else tuple(saved)):raise MemoryUnavailable('retention requires identical recovery copy')
                if (a['state']=='observed')!=(b is not None):raise MemoryUnavailable('inconsistent sample receipt')
                # Also preserve the latest successful observation of each pair.
                if b is not None and b['slot']==db.execute('SELECT MAX(slot) FROM quality_books WHERE pair=?',(a['pair'],)).fetchone()[0]:continue
                victims.append((key,b is not None))
            if apply:
                db.execute('CREATE TABLE IF NOT EXISTS quality_retired(pair TEXT NOT NULL,slot INTEGER NOT NULL,had_book INTEGER NOT NULL,retired_at INTEGER NOT NULL,manifest_sha256 TEXT NOT NULL,PRIMARY KEY(pair,slot))')
                for (pair,slot),had in victims:
                    db.execute('INSERT INTO quality_retired VALUES(?,?,?,?,?)',(pair,slot,int(had),now,digest));db.execute('DELETE FROM quality_books WHERE pair=? AND slot=?',(pair,slot));db.execute('DELETE FROM quality_attempts WHERE pair=? AND slot=?',(pair,slot))
            return {'state':'retired' if apply else 'preview','attempts':len(victims),'books':sum(had for _,had in victims),'before':now-MIN_AGE,'manifest_sha256':digest}
