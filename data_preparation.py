"""Public-data-only job custody for the local dashboard, Linux candidate."""
import ctypes
try:
    import fcntl
except ImportError:  # Linux worker custody; other hosts retain read-only dashboard.
    fcntl=None
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from market_memory import MemoryUnavailable

MODES=('prepare','repair','collect')
SCRIPT=Path(__file__).resolve().parent/'scripts'/'prepare_data.py'


def _db(root):
    db=sqlite3.connect(root/'jobs.sqlite3',timeout=10)
    db.row_factory=sqlite3.Row
    return db


def progress(root,job,stage):
    with closing(_db(root)) as db,db:
        db.execute("UPDATE jobs SET stage=?,updated=? WHERE id=? AND state='running'",(stage,int(time.time()),job))


def parent_guard(parent):
    # Set before opening network or starting a thread. Race check after prctl.
    if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGTERM,0,0,0)!=0 or os.getppid()!=parent:
        raise SystemExit(70)


class Preparation:
    def __init__(self,directory):
        self.directory=Path(directory).resolve();self.root=self.directory/'preparation'
        self.root.mkdir(mode=0o700,exist_ok=True)
        if self.root.is_symlink() or (os.name=='posix' and self.root.stat().st_mode&0o077) or (self.root/'jobs.sqlite3').is_symlink():raise MemoryUnavailable('unsafe preparation storage')
        self.lock=threading.RLock();self.child=None;self.job=None;self.stopping=False;self.closed=False
        with closing(_db(self.root)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,mode TEXT,state TEXT,stage TEXT,started INTEGER,updated INTEGER)')
        os.chmod(self.root/'jobs.sqlite3',0o600)

    def _lease(self):
        fd=os.open(self.root/'worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:os.close(fd);return None
        return fd

    def _interrupt_old(self):
        with closing(_db(self.root)) as db,db:
            db.execute("UPDATE jobs SET state='interrupted',updated=? WHERE state='running'",(int(time.time()),))

    def status(self):
        with self.lock:
            if fcntl is None or not sys.platform.startswith('linux'):return {'state':'unavailable','mode':None,'stage':None}
            fd=self._lease()
            if fd is not None:
                try:self._interrupt_old()
                finally:os.close(fd)
            with closing(_db(self.root)) as db:
                row=db.execute('SELECT * FROM jobs ORDER BY rowid DESC LIMIT 1').fetchone()
            return dict(row) if row else {'state':'idle','mode':None,'stage':None}

    def start(self,mode):
        if mode not in MODES:raise ValueError('invalid preparation operation')
        if fcntl is None or not sys.platform.startswith('linux'):raise MemoryUnavailable('Linux data worker unavailable')
        with self.lock:
            if self.closed:raise MemoryUnavailable('data job controller closed')
            if self.child is not None:raise MemoryUnavailable('data job already running')
            fd=self._lease()
            if fd is None:raise MemoryUnavailable('data job already running')
            job=uuid4().hex;now=int(time.time())
            try:
                self._interrupt_old()
                with closing(_db(self.root)) as db,db:
                    if db.execute('SELECT count(*) FROM jobs').fetchone()[0]>=10000:raise MemoryUnavailable('data job history limit reached')
                    db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(job,mode,'running','starting',now,now))
                # No inherited API credentials, shell, arbitrary command, or client-provided paths.
                env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONUNBUFFERED':'1'}
                self.child=subprocess.Popen([sys.executable,str(SCRIPT),str(self.directory),mode,job,str(os.getpid()),str(fd)],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=(fd,),start_new_session=True)
                self.job=job;self.stopping=False
                # Parent retains the exclusive lease until terminal state is committed.
                threading.Thread(target=self._watch,args=(self.child,job,fd,mode),daemon=True).start()
            except BaseException:
                with closing(_db(self.root)) as db,db:db.execute("UPDATE jobs SET state='failed',updated=? WHERE id=?",(int(time.time()),job))
                if self.child is not None:self.child.kill();self.child.wait();self.child=None
                os.close(fd);raise
            return self.status()

    def _watch(self,child,job,fd,mode):
        timed_out=False
        try:rc=child.wait(timeout=None if mode=='collect' else 1200)
        except subprocess.TimeoutExpired:
            timed_out=True;child.terminate()
            try:rc=child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();rc=child.wait()
        with self.lock:
            state='cancelled' if self.stopping else 'failed' if timed_out or rc!=0 else 'complete'
            try:
                with closing(_db(self.root)) as db,db:db.execute('UPDATE jobs SET state=?,updated=? WHERE id=?',(state,int(time.time()),job))
            finally:
                self.child=None;self.job=None;os.close(fd)

    def cancel(self):
        with self.lock:
            child=self.child
            if child is None:raise MemoryUnavailable('no locally owned data job')
            self.stopping=True
            try:child.terminate()
            except ProcessLookupError:pass
            # Do not claim cancellation until the actual worker has exited.
            progress(self.root,self.job,'stopping')
            threading.Thread(target=self._cancel_deadline,args=(child,),daemon=True).start()
            return self.status()

    @staticmethod
    def _cancel_deadline(child):
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:child.kill();child.wait()
            except ProcessLookupError:pass

    def close(self):
        with self.lock:
            self.closed=True
            child=self.child
            if child is not None:
                self.stopping=True
                child.terminate()
        if child is not None:
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        # Watcher owns terminal commit; ensure it releases custody before app closes.
        deadline=time.monotonic()+5
        while self.child is not None and time.monotonic()<deadline:time.sleep(.02)
