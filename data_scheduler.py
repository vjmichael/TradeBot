"""Explicit opt-in public-data scheduler. No trading, credentials or service installation."""
import fcntl
import os
import time
from contextlib import closing, contextmanager
from data_preparation import Preparation, _db
from market_memory import MemoryUnavailable

REPAIR_INTERVAL=6*3600
RETRY=300

class Schedule:
    def __init__(self,directory):
        self.controller=Preparation(directory);self.root=self.controller.root
        with self.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS schedule(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL, next_repair INTEGER NOT NULL, retry_at INTEGER NOT NULL, active_job TEXT, active_mode TEXT, last_outcome TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO schedule VALUES(1,0,0,0,NULL,NULL,'disabled')")
    @contextmanager
    def tx(self):
        with closing(_db(self.root)) as db:
            db.execute('BEGIN IMMEDIATE')
            with db:yield db
    def status(self):
        with self.tx() as db:return dict(db.execute('SELECT * FROM schedule WHERE id=1').fetchone())
    def enable(self,enabled,now=None):
        if type(enabled) is not bool:raise ValueError('explicit boolean required')
        now=int(time.time()) if now is None else now
        with self.tx() as db:
            # An explicit opt-in persists, including across service restarts.
            if enabled:db.execute("UPDATE schedule SET enabled=1,next_repair=CASE WHEN enabled=0 THEN ? ELSE next_repair END,retry_at=CASE WHEN enabled=0 THEN 0 ELSE retry_at END WHERE id=1",(now+REPAIR_INTERVAL,))
            else:db.execute('UPDATE schedule SET enabled=0 WHERE id=1')
        return self.status()
    @contextmanager
    def lease(self):
        fd=os.open(self.root/'schedule.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise MemoryUnavailable('scheduler already owned') from None
            yield
        finally:os.close(fd)
    def tick(self,now=None):
        """Caller holds scheduler lease; only cancel children owned by this instance."""
        now=int(time.time()) if now is None else now
        state=self.controller.status();s=self.status()
        if s['active_job'] and state.get('id')==s['active_job'] and state['state']!='running':
            outcome=state['state']; mode=s['active_mode']
            with self.tx() as db:
                db.execute('UPDATE schedule SET active_job=NULL,active_mode=NULL,last_outcome=?,retry_at=?,next_repair=CASE WHEN ? THEN ? ELSE next_repair END WHERE id=1',(outcome,now+RETRY if mode=='collect' and outcome!='cancelled' else 0,mode=='repair',now+REPAIR_INTERVAL if outcome=='complete' else now+3600))
            s=self.status()
        elif s['active_job'] and state.get('id')!=s['active_job']:
            # A manual job may have superseded an old terminal row. Never adopt it.
            with self.tx() as db:db.execute("UPDATE schedule SET active_job=NULL,active_mode=NULL,last_outcome='interrupted',retry_at=? WHERE id=1",(now+RETRY,))
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
