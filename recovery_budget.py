"""Durable conservative admission of public historical trade requests.

All production repair bursts use this shared sliding budget. Reserve before
network I/O, including failed requests and process crashes. No faster request
cadence than the independently qualified bridge. No private API or trading.
"""
import shutil
import time
from email.utils import parsedate_to_datetime
from market_gap import GapBridge, RecoveryDeferred, RecoveryThrottled, MIN_FREE_BYTES
from gap_schedule import repair_due

WINDOW_MS=3600000
MIN_SPACING_MS=1250
HOURLY_LIMIT=480
BACKGROUND_PAGES=16
BACKGROUND_TOTAL=48

def _schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS recovery_admission(id INTEGER PRIMARY KEY AUTOINCREMENT,pair TEXT NOT NULL,requested_ms INTEGER NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS recovery_admission_clock ON recovery_admission(requested_ms)')
    db.execute('CREATE TABLE IF NOT EXISTS recovery_request_totals(pair TEXT PRIMARY KEY,requests INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS recovery_admission_clock(id INTEGER PRIMARY KEY CHECK(id=1),last_ms INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS recovery_cooldown(id INTEGER PRIMARY KEY CHECK(id=1),until_ms INTEGER NOT NULL)')

class RecoveryBudget:
    def __init__(self,memory,*,clock_ms=lambda:time.time_ns()//1000000,sleep=time.sleep):
        self.memory=memory;self.clock_ms=clock_ms;self.sleep=sleep
        with memory.tx() as db:_schema(db)

    def reserve(self,pair):
        if pair not in self.memory.symbols:raise ValueError('market outside recovery scope')
        if shutil.disk_usage(self.memory.root).free<=MIN_FREE_BYTES:
            raise RecoveryDeferred('recovery disk reserve reached')
        for _ in range(5):
            now=self.clock_ms()
            if type(now) is not int or now<=0:raise RecoveryDeferred('invalid recovery clock')
            with self.memory.tx() as db:
                last=db.execute('SELECT last_ms FROM recovery_admission_clock WHERE id=1').fetchone()
                if last and now<last[0]:raise RecoveryDeferred('recovery clock moved backward')
                cooldown=db.execute('SELECT until_ms FROM recovery_cooldown WHERE id=1').fetchone()
                if cooldown and now<cooldown[0]:raise RecoveryDeferred('exchange recovery cooldown remains active')
                db.execute('DELETE FROM recovery_admission WHERE requested_ms<=?',(now-WINDOW_MS,))
                if db.execute('SELECT count(*) FROM recovery_admission').fetchone()[0]>=HOURLY_LIMIT:
                    raise RecoveryDeferred('rolling public recovery request budget reached')
                delay=max(0,MIN_SPACING_MS-(now-last[0])) if last else 0
                if not delay:
                    db.execute('INSERT INTO recovery_admission(pair,requested_ms) VALUES(?,?)',(pair,now))
                    db.execute('INSERT INTO recovery_admission_clock VALUES(1,?) ON CONFLICT(id) DO UPDATE SET last_ms=excluded.last_ms',(now,))
                    db.execute('INSERT INTO recovery_request_totals VALUES(?,1) ON CONFLICT(pair) DO UPDATE SET requests=requests+1',(pair,))
                    return
            self.sleep(delay/1000)
        raise RecoveryDeferred('shared recovery pacing unavailable')

    def throttle(self,retry_after=None):
        now=self.clock_ms();until=now+300000
        if retry_after is not None:
            try:
                if retry_after.isascii() and retry_after.isdigit() and len(retry_after)<=10:
                    requested=now+1000*int(retry_after)
                else:
                    date=parsedate_to_datetime(retry_after)
                    if date.tzinfo is None:raise ValueError('missing timezone')
                    requested=int(date.timestamp()*1000)
                until=max(until,requested)
            except (TypeError,ValueError,OverflowError,OSError,AttributeError):
                # Unusable explicit Retry-After: fail closed for operator review
                # rather than guess a shorter permissible waiting period.
                until=2**62
        with self.memory.tx() as db:
            db.execute('INSERT INTO recovery_cooldown VALUES(1,?) ON CONFLICT(id) DO UPDATE SET until_ms=MAX(until_ms,excluded.until_ms)',(until,))

def recovery_budget_status(memory,now_ms=None):
    now=time.time_ns()//1000000 if now_ms is None else now_ms
    with memory.tx() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_admission'").fetchone():
            return {'window_requests':0,'window_limit':HOURLY_LIMIT,'total_requests':0,'markets':[],'state':'not_started','next_available_at':None}
        last=db.execute('SELECT last_ms FROM recovery_admission_clock WHERE id=1').fetchone()
        cooldown=db.execute('SELECT until_ms FROM recovery_cooldown WHERE id=1').fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_cooldown'").fetchone() else None
        count,earliest=db.execute('SELECT count(*),min(requested_ms) FROM recovery_admission WHERE requested_ms>?',(now-WINDOW_MS,)).fetchone()
        totals=[dict(r) for r in db.execute('SELECT pair,requests FROM recovery_request_totals ORDER BY pair')]
        state='clock_review' if last and now<last[0] else 'exchange_cooldown' if cooldown and now<cooldown[0] else 'paced' if count>=HOURLY_LIMIT else 'available'
        next_ms=max(earliest+WINDOW_MS if count>=HOURLY_LIMIT else 0,cooldown[0] if cooldown and now<cooldown[0] else 0)
        return {'window_requests':count,'window_limit':HOURLY_LIMIT,'total_requests':sum(r['requests'] for r in totals),
                'markets':totals,'state':state,'next_available_at':(next_ms+999)//1000 if 0<next_ms<2**62 else None}

def recover_batch(memory,reports,*,scope_check=lambda:None,max_pages=BACKGROUND_PAGES,total_pages=BACKGROUND_TOTAL):
    budget=RecoveryBudget(memory)
    class BudgetedBridge:
        def run(self,pair,start,end,**kwargs):
            # Guard again after shared pacing; if scope changes, the reserved
            # request remains charged and no request leaves the process.
            def admit(p):
                scope_check();budget.reserve(p);scope_check()
            try:return GapBridge(memory).run(pair,start,end,before_page=admit,**kwargs)
            except RecoveryThrottled as exc:
                budget.throttle(exc.retry_after)
                raise
    return repair_due(memory,reports,bridge=BudgetedBridge(),max_pages=max_pages,
                      total_pages=total_pages,prefer_checkpoint=True)
