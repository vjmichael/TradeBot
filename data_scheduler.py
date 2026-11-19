"""Explicit opt-in public-data scheduler. No trading, credentials or service installation."""
try:
    import fcntl
except ImportError:
    fcntl=None
import os
import time
import sys
from contextlib import closing, contextmanager
from data_preparation import Preparation, _db
from market_memory import MemoryUnavailable

REPAIR_INTERVAL=6*3600
RETRY=300
RESTART_RETRY=30
HEARTBEAT_MAX=15

class Schedule:
    def __init__(self,directory):
        self.controller=Preparation(directory);self.root=self.controller.root
        with self.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS schedule(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL, next_repair INTEGER NOT NULL, retry_at INTEGER NOT NULL, active_job TEXT, active_mode TEXT, last_outcome TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO schedule VALUES(1,0,0,0,NULL,NULL,'disabled')")
            db.execute("CREATE TABLE IF NOT EXISTS schedule_heartbeat(id INTEGER PRIMARY KEY CHECK(id=1),seen INTEGER NOT NULL)")
    @contextmanager
    def tx(self):
        with closing(_db(self.root)) as db:
            db.execute('BEGIN IMMEDIATE')
            with db:yield db
    def status(self):
        with self.tx() as db:return dict(db.execute('SELECT * FROM schedule WHERE id=1').fetchone())
    def public_status(self,now=None):
        """Persisted intent is not proof of a running service. Check lease + heartbeat."""
        now=int(time.time()) if now is None else now
        supported=fcntl is not None and sys.platform.startswith('linux')
        owned=False
        with self.command():
            s=self.status()
            if supported:
                try:
                    with self.lease():pass
                except MemoryUnavailable:owned=True
            with self.tx() as db:
                row=db.execute('SELECT seen FROM schedule_heartbeat WHERE id=1').fetchone()
        seen=row[0] if row else None
        fresh=owned and seen is not None and 0<=now-seen<=HEARTBEAT_MAX
        return {'enabled':bool(s['enabled']),'service':'running' if fresh else 'not_confirmed' if owned else 'stopped' if supported else 'unsupported',
                'last_check':seen,'next_repair':s['next_repair'] if s['enabled'] else None,'last_outcome':s['last_outcome']}
    def heartbeat(self,now=None):
        now=int(time.time()) if now is None else now
        with self.tx() as db:db.execute('INSERT INTO schedule_heartbeat VALUES(1,?) ON CONFLICT(id) DO UPDATE SET seen=excluded.seen',(now,))
    @contextmanager
    def command(self):
        """Serialize schedule intent and manual admission across processes."""
        if fcntl is None or not sys.platform.startswith('linux'):
            yield;return
        fd=os.open(self.root/'command.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX);yield
        finally:os.close(fd)
    def enable(self,enabled,now=None,*,require_idle=False):
        with self.command():
            if require_idle and enabled and not self.status()['enabled'] and self.controller.status()['state']=='running':
                raise MemoryUnavailable('stop manual work first')
            return self._enable(enabled,now)
    def _enable(self,enabled,now=None):
        if type(enabled) is not bool:raise ValueError('explicit boolean required')
        if enabled and (fcntl is None or not sys.platform.startswith('linux')):raise MemoryUnavailable('Linux scheduler unavailable')
        now=int(time.time()) if now is None else now
        with self.tx() as db:
            # An explicit opt-in persists, including across service restarts.
            if enabled:db.execute("UPDATE schedule SET enabled=1,next_repair=CASE WHEN enabled=0 THEN ? ELSE next_repair END,retry_at=CASE WHEN enabled=0 THEN 0 ELSE retry_at END WHERE id=1",(now+REPAIR_INTERVAL,))
            else:db.execute('UPDATE schedule SET enabled=0 WHERE id=1')
        return self.status()
    @contextmanager
    def lease(self):
        if fcntl is None or not sys.platform.startswith('linux'):raise MemoryUnavailable('Linux scheduler unavailable')
        fd=os.open(self.root/'schedule.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise MemoryUnavailable('scheduler already owned') from None
            yield
        finally:os.close(fd)
    def tick(self,now=None):
        """Caller holds scheduler lease; serialize admissions with manual work."""
        with self.command():return self._tick(now)
    def _tick(self,now=None):
        """Only cancel children owned by this instance."""
        now=int(time.time()) if now is None else now
        state=self.controller.status();s=self.status()
        if s['active_job'] and state.get('id')==s['active_job'] and state['state']!='running':
            outcome=state['state']; mode=s['active_mode']
            with self.tx() as db:
                db.execute('UPDATE schedule SET active_job=NULL,active_mode=NULL,last_outcome=?,retry_at=?,next_repair=CASE WHEN ? THEN ? ELSE next_repair END WHERE id=1',(outcome,now+(RESTART_RETRY if outcome=='interrupted' else RETRY) if mode=='collect' and outcome!='cancelled' else 0,mode=='repair',now+REPAIR_INTERVAL if outcome=='complete' else now+3600))
            s=self.status()
        elif s['active_job'] and state.get('id')!=s['active_job']:
            # A manual job may have superseded an old terminal row. Never adopt it.
            with self.tx() as db:db.execute("UPDATE schedule SET active_job=NULL,active_mode=NULL,last_outcome='interrupted',retry_at=? WHERE id=1",(now+RESTART_RETRY,))
            s=self.status()
        if not s['enabled']:
            if self.controller.child is not None and not self.controller.stopping:self.controller.cancel()
            return self.status()
        if state['state']=='running':
            if self.controller.child is not None and not self.controller.stopping and state['mode']=='collect' and now>=s['next_repair']:
                self.controller.cancel()
            return self.status()
        if now<s['retry_at']:return s
        mode='repair' if now>=s['next_repair'] else 'collect'
        try:job=self.controller.start(mode)
        except MemoryUnavailable:return self.status()
        with self.tx() as db:
            db.execute('UPDATE schedule SET active_job=?,active_mode=?,last_outcome=? WHERE id=1',(job['id'],mode,'running'))
        return self.status()
    def close(self):self.controller.close()
