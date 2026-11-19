"""Fixed-path, data-only graphical maintenance. No browser-supplied paths.

Results become eligible only after the owning worker has completed. Apply a
reviewed exact cleanup selection, never infer approval from a backup alone.
"""
import json
import os
import re
from contextlib import closing
from pathlib import Path

from data_preparation import _db
from data_lifecycle import backup
from data_retention import retire_books, MIN_AGE
from sample_archive import consolidate,status as archive_status
from recovery_rotation import preview as rotation_preview,apply as rotate
from market_memory import MarketMemory
from vintage_memory import VintageMemory
from market_memory import MemoryUnavailable

MAX_COPIES=4
ID=re.compile(r'[0-9a-f]{32}')


def _root(directory, *, create=False):
    directory=Path(directory).resolve()
    root=directory.parent/(directory.name+'-recovery-copies')
    if create:root.mkdir(mode=0o700,exist_ok=True)
    if root.is_symlink() or (root.exists() and (not root.is_dir() or root.stat().st_mode&0o077)):
        raise MemoryUnavailable('unsafe recovery copy directory')
    return root


def _count(root):
    if not root.exists():return 0
    # Include interrupted staging/orphan copies in the admission bound.
    count=0
    for _ in root.iterdir():
        count+=1
        if count>32:raise MemoryUnavailable('recovery copy inventory exceeds limit')
    return count


def _latest(db,mode):
    row=db.execute("SELECT j.id,m.result FROM jobs j JOIN maintenance_results m ON m.job=j.id WHERE j.mode=? AND j.state='complete' ORDER BY j.rowid DESC LIMIT 1",(mode,)).fetchone()
    return (dict(json.loads(row['result']),id=row['id']) if row else None)


def status(directory):
    directory=Path(directory).resolve();root=_root(directory)
    with closing(_db(directory/'preparation')) as db:
        saved=_latest(db,'backup');preview=_latest(db,'preview-retention');applied=_latest(db,'retire');rotation=_latest(db,'preview-rotation');rotated=_latest(db,'rotate');vintage=_latest(db,'vintages')
    # Do not present a missing copy or a preview bound to an older copy as valid.
    if saved and (not ID.fullmatch(saved['id']) or not (root/saved['id']).is_dir() or (root/saved['id']).is_symlink()):saved=None
    if preview and (not saved or preview['backup_id']!=saved['id'] or (applied and applied['preview_id']==preview['id'])):preview=None
    if rotation and rotated and rotated.get('preview_id')==rotation['id']:rotation=None
    count=_count(root)
    return {'copies':count,'max_copies':MAX_COPIES,'latest_backup':saved,'cleanup_preview':preview,'last_cleanup':applied,'rotation_preview':rotation,'last_rotation':rotated,'archive':archive_status(MarketMemory(directory)),'vintages':VintageMemory(directory).status(),'last_vintage_batch':vintage}


def run(directory,mode,job):
    directory=Path(directory).resolve();journal=directory/'preparation'
    if not ID.fullmatch(job):raise ValueError('invalid maintenance job')
    root=_root(directory,create=True)
    with closing(_db(journal)) as db:
        row=db.execute("SELECT mode,state FROM jobs WHERE id=?",(job,)).fetchone()
        if not row or row['mode']!=mode or row['state']!='running':raise MemoryUnavailable('maintenance job not authorized')
    if mode=='backup':
        if _count(root)>=MAX_COPIES:raise MemoryUnavailable('recovery copy capacity reached')
        result=backup(directory,root/job)
    elif mode=='vintages':
        result=VintageMemory(directory).collect_batch()
    elif mode=='preview-rotation':
        result=rotation_preview(directory,root)
    elif mode=='rotate':
        with closing(_db(journal)) as db:
            option=db.execute('SELECT preview_id FROM job_options WHERE job=?',(job,)).fetchone()
            reviewed=_latest(db,'preview-rotation')
        if not option or not reviewed or option['preview_id']!=reviewed['id']:raise MemoryUnavailable('review rotation first')
        plan={k:v for k,v in reviewed.items() if k!='id'}
        result=rotate(directory,root,plan)|{'preview_id':reviewed['id']}
    elif mode=='consolidate':
        with closing(_db(journal)) as db:
            copies=db.execute("SELECT j.id,m.result FROM jobs j JOIN maintenance_results m ON m.job=j.id WHERE j.mode='backup' AND j.state='complete' ORDER BY j.rowid DESC LIMIT 10000").fetchall()
        m=MarketMemory(directory)
        with m.tx() as db:
            pending=set(r[0] for r in db.execute('SELECT DISTINCT manifest_sha256 FROM quality_retired')) if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quality_retired'").fetchone() else set()
        result={'archived':0}
        if archive_status(m)['unconsolidated'] and not copies:raise MemoryUnavailable('original recovery copies unavailable')
        for copy in copies:
            digest=json.loads(copy['result'])['manifest_sha256']
            if digest in pending and ID.fullmatch(copy['id']) and (root/copy['id']).is_dir() and not (root/copy['id']).is_symlink():
                result=consolidate(directory,root/copy['id'],digest)
                if result['archived']:break
        if not result['archived'] and archive_status(m)['unconsolidated']:raise MemoryUnavailable('original retired sample archive unavailable')
    else:
        current=status(directory);saved=current['latest_backup']
        if not saved:raise MemoryUnavailable('create a verified recovery copy first')
        if mode=='preview-retention':
            result=retire_books(directory,root/saved['id'],saved['manifest_sha256']) | {'backup_id':saved['id']}
        elif mode=='retire':
            with closing(_db(journal)) as db:
                option=db.execute('SELECT preview_id FROM job_options WHERE job=?',(job,)).fetchone()
            preview=current['cleanup_preview']
            if not option or not preview or option['preview_id']!=preview['id'] or not preview['attempts']:
                raise MemoryUnavailable('review old sample cleanup again')
            result=retire_books(directory,root/saved['id'],saved['manifest_sha256'],apply=True,
                now=preview['before']+MIN_AGE,expected_selection=preview['selection_sha256']) | {'backup_id':saved['id'],'preview_id':preview['id']}
        else:raise ValueError('invalid maintenance operation')
    with closing(_db(journal)) as db,db:
        db.execute('INSERT INTO maintenance_results VALUES(?,?)',(job,json.dumps(result,sort_keys=True,separators=(',',':'))))
    return result
