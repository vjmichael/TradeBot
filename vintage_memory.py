"""Date-specific ALFRED evidence, separate from live BLS observations."""
import csv
import hashlib
import io
import re
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import httpx
from economic_memory import EconomicMemory, MAX_DB
from market_archive import exclusive_job
from market_memory import MemoryUnavailable,decimal_text

URL='https://alfred.stlouisfed.org/graph/alfredgraph.csv'
SERIES={'UNRATE':'Unemployment rate (seasonally adjusted)','PAYEMS':'Nonfarm payroll employment (seasonally adjusted)','CPIAUCNS':'Consumer price index (not seasonally adjusted)'}
SUPPLEMENTAL={'FEDFUNDS':'Effective federal funds rate (monthly, percent)'}
ALL_SERIES=SERIES|SUPPLEMENTAL
MAX_BYTES=128*1024
MAX_ROWS=1500
MAX_SNAPSHOTS=2048
BATCH=6
PACE=5
HOUR_LIMIT=36
DAY_LIMIT=144
MAX_REQUESTS=8192
RETRY_SECONDS=6*3600
BACKGROUND_INTERVAL=600
RESERVE=256*1024**2

def day(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('invalid vintage date')
    return date.fromisoformat(value)

def cutoff(vintage):
    # After the entire named U.S. calendar day; no intraday release claim.
    return int(datetime.combine(day(vintage)+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc).timestamp())+43200

def parse(series,vintage,raw):
    d=day(vintage)
    if series not in ALL_SERIES or not isinstance(raw,bytes) or not 0<len(raw)<=MAX_BYTES:raise ValueError('invalid vintage source')
    reader=csv.reader(io.StringIO(raw.decode('utf-8-sig')),strict=True)
    header=next(reader,None)
    if not header or len(header)!=2 or header[0] not in ('observation_date','DATE') or header[1]!=series+'_'+vintage.replace('-',''):raise ValueError('vintage identity not verified')
    out=[];last=None
    for row in reader:
        if len(row)!=2 or len(out)>=MAX_ROWS:raise ValueError('invalid vintage record count')
        p=day(row[0])
        if p.day!=1 or p>d or last is not None and p<=last:raise ValueError('invalid observation period')
        value=None if row[1] in ('','.','NA') else decimal_text(row[1])
        if value is not None and (Decimal(value)<0 or series=='UNRATE' and Decimal(value)>100):raise ValueError('invalid vintage observation')
        out.append({'period':p.isoformat(),'value':value});last=p
    if not out or not any(r['value'] is not None for r in out):raise ValueError('empty vintage')
    return out

def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS alfred_vintages(series TEXT NOT NULL,vintage TEXT NOT NULL,
        retrieved_at INTEGER NOT NULL,available_after INTEGER NOT NULL,sha256 TEXT NOT NULL,body BLOB NOT NULL,
        rows INTEGER NOT NULL,PRIMARY KEY(series,vintage))""")
    db.execute("""CREATE TABLE IF NOT EXISTS alfred_attempts(series TEXT NOT NULL,vintage TEXT NOT NULL,
        last_attempt INTEGER NOT NULL,attempts INTEGER NOT NULL,PRIMARY KEY(series,vintage))""")

    db.execute("""CREATE TABLE IF NOT EXISTS vintage_backfill_plan(
        id INTEGER PRIMARY KEY CHECK(id=1),anchor_month TEXT NOT NULL,created_at INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS vintage_requests(
        id INTEGER PRIMARY KEY,series TEXT NOT NULL,vintage TEXT NOT NULL,started INTEGER NOT NULL,
        finished INTEGER,state TEXT NOT NULL)""")
    db.execute('CREATE INDEX IF NOT EXISTS vintage_request_time_idx ON vintage_requests(started)')
    db.execute("""CREATE TABLE IF NOT EXISTS vintage_cooldown(
        id INTEGER PRIMARY KEY CHECK(id=1),until_at INTEGER NOT NULL)""")

def targets(anchor):
    d=day(anchor)
    if d.day!=1:raise MemoryUnavailable('invalid frozen backfill anchor')
    month=d.year*12+d.month-1
    return [(series,date(y,m+1,1).isoformat()) for i in range(1,121)
            for y,m in [divmod(month-i,12)] for series in SERIES]

def supplemental_targets(anchor):
    return [('FEDFUNDS',vintage) for series,vintage in targets(anchor) if series=='UNRATE'][:12]

def current_anchor(now):
    return datetime.fromtimestamp(now,timezone.utc).date().replace(day=1).isoformat()

def ensure_plan(memory,now):
    with memory.tx(True) as db:
        schema(db)
        db.execute('INSERT OR IGNORE INTO vintage_backfill_plan VALUES(1,?,?)',(current_anchor(now),now))
        row=db.execute('SELECT anchor_month,created_at FROM vintage_backfill_plan WHERE id=1').fetchone()
        if row['created_at']>now:raise MemoryUnavailable('vintage plan clock moved backward')
        if row['anchor_month']!=current_anchor(row['created_at']):raise MemoryUnavailable('invalid frozen backfill plan')
        return row['anchor_month']

def coverage(memory,*,as_of=None,offset=0,limit=60,detail=False):
    now=int(time.time()) if as_of is None else as_of
    if type(now) is not int or now<=0 or type(offset) is not int or not 0<=offset<=360 or type(limit) is not int or not 1<=limit<=120:raise ValueError('invalid vintage coverage query')
    with memory.tx() as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        plan=db.execute('SELECT * FROM vintage_backfill_plan WHERE created_at<=?',(now,)).fetchone() if 'vintage_backfill_plan' in tables else None
        anchor=plan['anchor_month'] if plan else current_anchor(now)
        if plan and anchor!=current_anchor(plan['created_at']):raise MemoryUnavailable('invalid frozen backfill plan')
        wanted=targets(anchor)
        saved={(r['series'],r['vintage']):dict(r) for r in db.execute('SELECT series,vintage,retrieved_at,sha256,rows FROM alfred_vintages WHERE retrieved_at<=?',(now,))} if 'alfred_vintages' in tables else {}
        latest={}
        if 'vintage_requests' in tables:
            for row in db.execute('SELECT * FROM vintage_requests WHERE started<=? ORDER BY id',(now,)):
                latest[(row['series'],row['vintage'])]=dict(row)
        old={(r['series'],r['vintage']):r['last_attempt'] for r in db.execute('SELECT series,vintage,last_attempt FROM alfred_attempts WHERE last_attempt<=?',(now,))} if 'alfred_attempts' in tables else {}
        cooldown=db.execute('SELECT until_at FROM vintage_cooldown WHERE id=1').fetchone() if 'vintage_cooldown' in tables else None
        # Current admission metadata is deliberately excluded from historical
        # views; its mutable state is not a historical evidence timestamp.
        admission=None
        if as_of is None and 'vintage_requests' in tables:
            h,d=db.execute('SELECT COALESCE(sum(started>?),0),count(*) FROM vintage_requests WHERE started>?',(now-3600,now-86400)).fetchone()
            until=cooldown[0] if cooldown and cooldown[0]>now else None
            admission={'hour_requests':h,'hour_limit':HOUR_LIMIT,'day_requests':d,'day_limit':DAY_LIMIT,'cooldown_until':until if until and until<2**62 else None,'blocked':bool(until or h>=HOUR_LIMIT or d>=DAY_LIMIT)}
        supplemental=[{'series':series,'vintage':vintage,'saved':(series,vintage) in saved} for series,vintage in supplemental_targets(anchor)]
        data=[];series_rows=[];counts={'saved':0,'not_requested':0,'attempted_unavailable':0,'attempt_pending':0}
        for series in SERIES:
            sc=dict.fromkeys(counts,0);missing_preview=[]
            for key in wanted:
                if key[0]!=series:continue
                record=saved.get(key);attempt=latest.get(key)
                last=attempt['started'] if attempt else old.get(key)
                if record:state='saved'
                elif attempt and (attempt['finished'] is None or attempt['finished']>now):state='attempt_pending'
                elif last is not None:state='attempted_unavailable'
                else:state='not_requested'
                counts[state]+=1;sc[state]+=1
                if state!='saved' and len(missing_preview)<6:missing_preview.append({'vintage':key[1],'state':state})
                data.append({'series':series,'vintage':key[1],'state':state,'last_attempt':last,'retrieved_at':record['retrieved_at'] if record else None,
                             'sha256':record['sha256'] if record else None,'rows':record['rows'] if record else None})
            series_rows.append({'series':series,'title':SERIES[series],'expected':120,**sc,'missing':120-sc['saved'],'missing_preview':missing_preview})
    result={'state':'complete' if counts['saved']==len(wanted) else 'incomplete' if plan else 'not_started',
            'plan_created_at':plan['created_at'] if plan else None,'anchor_month':anchor,'expected':len(wanted),**counts,
            'missing':len(wanted)-counts['saved'],'series':series_rows,'supplemental':{'series':'FEDFUNDS','expected':len(supplemental),'saved':sum(r['saved'] for r in supplemental),'dates':supplemental},'release_precision':'date_only',
            'scope':'monthly provider-dated snapshots; saved receipt counts, not all release dates or intraday vintages',
            'training_ready':False}
    if as_of is None:result['admission']=admission
    if detail:
        result.update(rows=data[offset:offset+limit],next_offset=offset+limit if offset+limit<len(data) else None,as_of=now)
    return result

def reserve(db,series,vintage,now):
    prior=db.execute('SELECT max(last_attempt) FROM alfred_attempts').fetchone()[0]
    if prior is not None and now-prior<PACE:return None,'paced'
    last=db.execute('SELECT max(started) FROM vintage_requests').fetchone()[0]
    if last is not None and now<last:raise MemoryUnavailable('vintage request clock moved backward')
    cool=db.execute('SELECT until_at FROM vintage_cooldown WHERE id=1').fetchone()
    if cool and now<cool[0]:return None,'cooldown'
    h,d=db.execute('SELECT COALESCE(sum(started>?),0),count(*) FROM vintage_requests WHERE started>?',(now-3600,now-86400)).fetchone()
    if h>=HOUR_LIMIT or d>=DAY_LIMIT:return None,'budget_exhausted'
    if db.execute('SELECT count(*) FROM vintage_requests').fetchone()[0]>=MAX_REQUESTS:raise MemoryUnavailable('vintage request history capacity reached')
    # Exclusive per-source custody: unresolved older requests were interrupted.
    db.execute("UPDATE vintage_requests SET finished=?,state='interrupted' WHERE finished IS NULL",(now,))
    key=db.execute("INSERT INTO vintage_requests(series,vintage,started,finished,state) VALUES(?,?,?,NULL,'pending')",(series,vintage,now)).lastrowid
    return key,None

def retry_until(now,value):
    until=now+900
    if value is not None:
        try:
            if value.isascii() and value.isdigit() and len(value)<=10:other=now+int(value)
            else:
                parsed=parsedate_to_datetime(value)
                if parsed.tzinfo is None:raise ValueError('missing timezone')
                other=int(parsed.timestamp())
            until=max(until,other)
        except (TypeError,ValueError,OverflowError,OSError,AttributeError):until=2**62
    return until

def verify_vintages(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'vintage_backfill_plan' in tables:
        plan=db.execute('SELECT * FROM vintage_backfill_plan WHERE id=1').fetchone()
        if plan and (plan['anchor_month']!=current_anchor(plan['created_at']) or len(targets(plan['anchor_month']))!=360):raise MemoryUnavailable('invalid vintage backfill plan')
    if 'vintage_requests' in tables:
        if db.execute('SELECT count(*) FROM vintage_requests').fetchone()[0]>MAX_REQUESTS:raise MemoryUnavailable('vintage request history capacity exceeded')
        if db.execute("SELECT 1 FROM vintage_requests WHERE started<=0 OR (finished IS NULL AND state!='pending') OR (finished IS NOT NULL AND (finished<started OR state NOT IN ('observed','unavailable','interrupted'))) LIMIT 1").fetchone():raise MemoryUnavailable('invalid vintage request receipt')
    for i,row in enumerate(db.execute('SELECT * FROM alfred_vintages ORDER BY series,vintage')):
        if i>=MAX_SNAPSHOTS or row['available_after']!=cutoff(row['vintage']) or row['retrieved_at']<row['available_after'] or hashlib.sha256(row['body']).hexdigest()!=row['sha256'] or len(parse(row['series'],row['vintage'],row['body']))!=row['rows']:
            raise MemoryUnavailable('vintage snapshot integrity failure')

class VintageMemory:
    def __init__(self,directory):
        self.memory=EconomicMemory(directory)
        with self.memory.tx(True) as db:schema(db)

    def status(self):
        with self.memory.tx() as db:
            count=db.execute('SELECT count(*) FROM alfred_vintages').fetchone()[0]
            rows=[dict(r) for r in db.execute('SELECT series,count(*) AS snapshots,min(vintage) AS first_vintage,max(vintage) AS last_vintage FROM alfred_vintages GROUP BY series')]
            return {'snapshots':count,'series':rows,'target':'monthly dates over ten years; not all release dates','release_precision':'date_only','training_ready':False,'coverage':coverage(self.memory)}

    def view(self,series,vintage,*,retrieved_by=None,replay_at=None):
        day(vintage)
        if series not in ALL_SERIES:raise ValueError('unknown vintage series')
        now=int(time.time()) if retrieved_by is None else retrieved_by
        replay=now if replay_at is None else replay_at
        if type(now) is not int or type(replay) is not int or not 0<replay<=now<=int(time.time()):raise ValueError('invalid vintage cutoff')
        with self.memory.tx() as db:
            row=db.execute('SELECT * FROM alfred_vintages WHERE series=? AND vintage=? AND retrieved_at<=? AND available_after<=?',(series,vintage,now,replay)).fetchone()
            if not row:return {'state':'not_observed','series':series,'vintage':vintage,'rows':[]}
            raw=row['body']
            if hashlib.sha256(raw).hexdigest()!=row['sha256'] or row['available_after']!=cutoff(vintage):raise MemoryUnavailable('vintage checksum mismatch')
            rows=parse(series,vintage,raw)
            if len(rows)!=row['rows']:raise MemoryUnavailable('vintage row count changed')
            return {'state':'observed','series':series,'title':ALL_SERIES[series],'vintage':vintage,'retrieved_at':row['retrieved_at'],'available_after':row['available_after'],'sha256':row['sha256'],'source_url':URL,'release_precision':'date_only','rows':rows,'scope':'provider-dated historical snapshot, not locally observed on vintage date'}

    def collect(self,series,vintage,*,client=None,clock=time.time):
        day(vintage);now=int(clock())
        if series not in ALL_SERIES or cutoff(vintage)>now:raise ValueError('vintage outside authorized scope')
        with self.memory.tx() as db:
            plan=db.execute('SELECT anchor_month FROM vintage_backfill_plan WHERE id=1').fetchone()
        if day(vintage).year<datetime.fromtimestamp(now,timezone.utc).year-11 and (not plan or (series,vintage) not in targets(plan['anchor_month'])+supplemental_targets(plan['anchor_month'])):raise ValueError('vintage outside authorized scope')
        with exclusive_job(self.memory.root,'alfred-vintage'):
            with self.memory.tx(True) as db:
                if db.execute('SELECT 1 FROM alfred_vintages WHERE series=? AND vintage=?',(series,vintage)).fetchone():return {'state':'already_observed'}
                if db.execute('SELECT count(*) FROM alfred_vintages').fetchone()[0]>=MAX_SNAPSHOTS or sum(p.stat().st_size for p in self.memory.root.glob('evidence.sqlite3*'))>=MAX_DB or shutil.disk_usage(self.memory.root).free<RESERVE:raise MemoryUnavailable('vintage storage limit reached')
                if db.execute('SELECT count(*) FROM alfred_attempts').fetchone()[0]>=MAX_SNAPSHOTS*2 and not db.execute('SELECT 1 FROM alfred_attempts WHERE series=? AND vintage=?',(series,vintage)).fetchone():raise MemoryUnavailable('vintage attempt capacity reached')
                request_id,deferred=reserve(db,series,vintage,now)
                if deferred:return {'state':deferred}
                db.execute('INSERT INTO alfred_attempts VALUES(?,?,?,1) ON CONFLICT(series,vintage) DO UPDATE SET last_attempt=excluded.last_attempt,attempts=attempts+1',(series,vintage,now))
            owned=client is None
            if owned:client=httpx.Client(timeout=httpx.Timeout(20,connect=8),follow_redirects=False,trust_env=False,headers={'User-Agent':'TradeBot-public-economic-research/1.0','Accept-Encoding':'identity'})
            params={'id':series,'vintage_date':vintage}
            try:
                with client.stream('GET',URL,params=params) as response:
                    if response.status_code==429:
                        with self.memory.tx(True) as db:db.execute('INSERT INTO vintage_cooldown VALUES(1,?) ON CONFLICT(id) DO UPDATE SET until_at=MAX(until_at,excluded.until_at)',(retry_until(int(clock()),response.headers.get('Retry-After')),))
                        raise MemoryUnavailable('official vintage request throttled')
                    if response.status_code!=200 or response.url!=httpx.URL(URL,params=params):raise MemoryUnavailable('official vintage unavailable or redirected')
                    if response.headers.get('Content-Encoding','identity').lower()!='identity':raise ValueError('unexpected vintage encoding')
                    raw=bytearray()
                    for chunk in response.iter_raw():
                        if len(raw)+len(chunk)>MAX_BYTES:raise ValueError('vintage response too large')
                        raw.extend(chunk)
                raw=bytes(raw);rows=parse(series,vintage,raw);retrieved=int(clock())
                if retrieved<now:raise MemoryUnavailable('vintage clock moved backward')
                digest=hashlib.sha256(raw).hexdigest()
                with self.memory.tx(True) as db:
                    db.execute('INSERT INTO alfred_vintages VALUES(?,?,?,?,?,?,?)',(series,vintage,retrieved,cutoff(vintage),digest,raw,len(rows)))
                    db.execute("UPDATE vintage_requests SET finished=?,state='observed' WHERE id=?",(retrieved,request_id))
                return {'state':'observed','rows':len(rows),'sha256':digest}
            except BaseException:
                with self.memory.tx(True) as db:db.execute("UPDATE vintage_requests SET finished=?,state='unavailable' WHERE id=? AND finished IS NULL",(max(now,int(clock())),request_id))
                raise
            finally:
                if owned:client.close()

    def collect_batch(self,*,client=None,clock=time.time,sleep=time.sleep,scope_check=lambda:None):
        """One bounded resumable burst for a frozen ten-year monthly plan."""
        with exclusive_job(self.memory.root,'alfred-backfill'):
            now=int(clock());anchor=ensure_plan(self.memory,now)
            with self.memory.tx() as db:
                done={tuple(r) for r in db.execute('SELECT series,vintage FROM alfred_vintages')}
                attempts={(r[0],r[1]):r[2] for r in db.execute('SELECT series,vintage,last_attempt FROM alfred_attempts')}
            wanted=set(targets(anchor))
            due=sorted((t for t in targets(anchor) if t not in done and (t not in attempts or now-attempts[t]>=RETRY_SECONDS)),key=lambda t:attempts.get(t,-1))[:BATCH]
            failures=observed=0;deferred=None
            for i,(series,vintage) in enumerate(due):
                if i:sleep(PACE)
                scope_check()
                try:
                    result=self.collect(series,vintage,client=client,clock=clock)
                    if result['state']=='observed':observed+=1
                    elif result['state']!='already_observed':
                        deferred=result['state'];break
                except (MemoryUnavailable,ValueError,OSError,httpx.HTTPError,csv.Error):
                    failures+=1;break
            with self.memory.tx() as db:
                saved={tuple(r) for r in db.execute('SELECT series,vintage FROM alfred_vintages')}
            remaining=len(wanted-saved)
            return {'observed':observed,'unavailable':failures,'remaining':remaining,
                    'state':'complete' if not remaining else deferred or ('incomplete' if failures else 'batch_finished' if due else 'waiting_retry')}

    def collect_supplemental(self,*,client=None,clock=time.time,scope_check=lambda:None):
        """One separately counted rate snapshot, after the core plan completes."""
        with exclusive_job(self.memory.root,'alfred-backfill'):
            now=int(clock());anchor=ensure_plan(self.memory,now)
            with self.memory.tx() as db:
                done={tuple(r) for r in db.execute('SELECT series,vintage FROM alfred_vintages')}
                attempts={(r[0],r[1]):r[2] for r in db.execute('SELECT series,vintage,last_attempt FROM alfred_attempts')}
            if not set(targets(anchor))<=done:return {'state':'core_incomplete'}
            due=[t for t in supplemental_targets(anchor) if t not in done and (t not in attempts or now-attempts[t]>=RETRY_SECONDS)]
            if not due:return {'state':'complete' if set(supplemental_targets(anchor))<=done else 'waiting_retry'}
            scope_check()
            return self.collect(*min(due,key=lambda t:attempts.get(t,-1)),client=client,clock=clock)
