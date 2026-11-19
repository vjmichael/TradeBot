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
        saved=_latest(db,'backup');preview=_latest(db,'preview-retention');applied=_latest(db,'retire')
    # Do not present a missing copy or a preview bound to an older copy as valid.
    if saved and (not ID.fullmatch(saved['id']) or not (root/saved['id']).is_dir() or (root/saved['id']).is_symlink()):saved=None
    if preview and (not saved or preview['backup_id']!=saved['id'] or (applied and applied['preview_id']==preview['id'])):preview=None
    count=_count(root)
    return {'copies':count,'max_copies':MAX_COPIES,'latest_backup':saved,'cleanup_preview':preview,'last_cleanup':applied}


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
