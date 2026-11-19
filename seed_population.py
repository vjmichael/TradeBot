"""Explicit bounded acquisition sessions for an operator-owned private seed.

No public artifact upload, auto-order, background cloud task or paid service.
The collection time bound excludes worker shutdown and snapshot verification.
A stopped or interrupted session retains all committed workspace observations.
"""
import os
import sqlite3
import time
from contextlib import closing
from data_scheduler import Schedule
from market_archive import RELEASE,exclusive_job
from market_directory import DEFAULTS,MarketDirectory
from market_memory import MarketMemory,MemoryUnavailable
from release_seed import _root,_child,_version,capture
from storage_telemetry import storage_growth

MIN_SECONDS=60
MAX_SECONDS=21600
MAX_EPOCHS=1024

def _journal(root):
    path=root/'population.sqlite3'
    if path.is_symlink() or (path.exists() and not path.is_file()):raise MemoryUnavailable('unsafe population journal')
    db=sqlite3.connect(path,timeout=10)
    db.row_factory=sqlite3.Row
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS epochs(version TEXT PRIMARY KEY,started INTEGER NOT NULL,
            finished INTEGER,state TEXT NOT NULL,job TEXT,mode TEXT,manifest_sha256 TEXT,detail TEXT)""")
        db.commit();os.chmod(path,0o600);return db
    except BaseException:
        db.close();raise

def _update(root,version,**fields):
    allowed={'finished','state','job','mode','manifest_sha256','detail'}
    if not fields or not set(fields)<=allowed:raise ValueError('invalid epoch update')
    with closing(_journal(root)) as db,db:
        db.execute('UPDATE epochs SET '+','.join(k+'=?' for k in fields)+' WHERE version=?',(*fields.values(),version))

def _scope(workspace):
    registry=MarketDirectory(workspace)
    selected=registry.selected_symbols()
    if set(selected)!=set(DEFAULTS) or set(registry.retained_symbols())!=set(DEFAULTS):
        raise MemoryUnavailable('seed population requires exactly default markets')
    return selected

def need_archive(workspace):
    memory=MarketMemory(workspace)
    with memory.tx() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_archive_status'").fetchone():return True
        done={r[0] for r in db.execute("SELECT pair FROM market_archive_status WHERE release=? AND state IN ('imported','not_in_pinned_archive')",(RELEASE,))}
    return bool(set(DEFAULTS)-done)

def population_status(root):
    root=_root(root);workspace=_child(root,'workspace')
    with closing(_journal(root)) as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM epochs ORDER BY rowid DESC LIMIT 20')]
    scope=_scope(workspace)
    return {'epochs':rows,'archive_preparation_pending':need_archive(workspace),
            'workspace':str(workspace),'storage':storage_growth(workspace,scope),
            'training_ready':False,'distribution_approved':False}

def _start(root,version,controller,mode):
    selected=controller.start(mode)
    if not selected.get('id') or selected.get('mode')!=mode:raise MemoryUnavailable('unexpected seed worker receipt')
    _update(root,version,job=selected['id'],mode=mode)
    return selected['id']

def _wait(controller,job,deadline,stop,clock,sleep):
    while True:
        current=controller.status()
        if current.get('id')!=job:raise MemoryUnavailable('seed worker identity changed')
        if current['state']!='running':return current['state']
        if stop() or clock()>=deadline:
            try:controller.cancel()
            except MemoryUnavailable:
                state=controller.status()
                if state.get('id')==job and state['state']!='running':return state['state']
                raise
            # Do not freeze while the child still has custody.
            limit=clock()+20
            while clock()<limit:
                state=controller.status()
                if state.get('id')!=job:raise MemoryUnavailable('seed worker identity changed')
                if state['state']!='running':return state['state']
                sleep(.2)
            raise MemoryUnavailable('seed worker termination unconfirmed')
        sleep(min(1,max(.01,deadline-clock())))

def run_epoch(root,version,seconds,*,stop=lambda:False,clock=time.monotonic,sleep=time.sleep):
    root=_root(root);version=_version(version)
    if type(seconds) is not int or not MIN_SECONDS<=seconds<=MAX_SECONDS:raise ValueError('invalid acquisition duration')
    workspace=_child(root,'workspace');versions=_child(root,'versions')
    with exclusive_job(root,'seed-population'):
        schedule=Schedule(workspace);started=False
        try:
            # Hold scheduler and command admission while owning the worker;
            # do not disable an independently enabled continuous service.
            with schedule.lease(),schedule.command():
                controller=schedule.controller
                if schedule.status()['enabled'] or controller.status()['state']=='running':
                    raise MemoryUnavailable('stop existing acquisition service before a seed epoch')
                _scope(workspace)
                with closing(_journal(root)) as db,db:
                    db.execute("UPDATE epochs SET state='interrupted',finished=?,detail='prior owner ended; workspace retained; inspect any saved version' WHERE state='running'",(int(time.time()),))
                    if db.execute('SELECT count(*) FROM epochs').fetchone()[0]>=MAX_EPOCHS:raise MemoryUnavailable('population journal capacity reached')
                    if db.execute('SELECT 1 FROM epochs WHERE version=?',(version,)).fetchone() or (versions/version).exists() or (versions/version).is_symlink():
                        raise MemoryUnavailable('use a new immutable epoch version')
                    db.execute("INSERT INTO epochs(version,started,state) VALUES(?,?,'running')",(version,int(time.time())))
                started=True
                deadline=clock()+seconds;outcome='not_started'
                if not stop() and need_archive(workspace):
                    job=_start(root,version,controller,'prepare')
                    outcome=_wait(controller,job,deadline,stop,clock,sleep)
                if not stop() and clock()<deadline and outcome in ('complete','not_started') and not need_archive(workspace):
                    job=_start(root,version,controller,'collect')
                    outcome=_wait(controller,job,deadline,stop,clock,sleep)
                if stop():
                    _update(root,version,finished=int(time.time()),state='stopped',detail='operator stopped; committed workspace retained')
                    return {'version':version,'state':'stopped','worker_outcome':outcome,'distribution_approved':False}
                # Preparation failure can leave useful committed observations.
                # Freeze a private checkpoint, never label that a complete run.
                fd=controller._lease()
                if fd is None:raise MemoryUnavailable('worker lease not released; checkpoint withheld')
                try:
                    _scope(workspace)
                    receipt=capture(root,version)
                finally:os.close(fd)
                state='checkpoint_saved_review_required' if receipt['review']['core_coverage_complete'] and outcome in ('complete','cancelled') else 'checkpoint_saved_incomplete'
                _update(root,version,finished=int(time.time()),state=state,manifest_sha256=receipt['manifest_sha256'],detail=outcome)
                return {'version':version,'state':state,'worker_outcome':outcome,'receipt':receipt,
                        'distribution_approved':False,'training_ready':False}
        except BaseException:
            if started:_update(root,version,finished=int(time.time()),state='interrupted',detail='epoch failed; committed workspace retained; inspect any saved version')
            raise
        finally:schedule.close()
