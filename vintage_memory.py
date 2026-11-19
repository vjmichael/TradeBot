"""Date-specific ALFRED evidence, separate from live BLS observations."""
import csv
import hashlib
import io
import re
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import httpx
from economic_memory import EconomicMemory, MAX_DB
from market_archive import exclusive_job
from market_memory import MemoryUnavailable,decimal_text

URL='https://alfred.stlouisfed.org/graph/alfredgraph.csv'
SERIES={'UNRATE':'Unemployment rate (seasonally adjusted)','PAYEMS':'Nonfarm payroll employment (seasonally adjusted)','CPIAUCNS':'Consumer price index (not seasonally adjusted)'}
MAX_BYTES=128*1024
MAX_ROWS=1500
MAX_SNAPSHOTS=2048
BATCH=6
PACE=5
RESERVE=256*1024**2

def day(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('invalid vintage date')
    return date.fromisoformat(value)

def cutoff(vintage):
    # After the entire named U.S. calendar day; no intraday release claim.
    return int(datetime.combine(day(vintage)+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc).timestamp())+43200

def parse(series,vintage,raw):
    d=day(vintage)
    if series not in SERIES or not isinstance(raw,bytes) or not 0<len(raw)<=MAX_BYTES:raise ValueError('invalid vintage source')
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

def verify_vintages(db):
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
            return {'snapshots':count,'series':rows,'target':'monthly dates over ten years; not all release dates','release_precision':'date_only','training_ready':False}

    def view(self,series,vintage,*,retrieved_by=None,replay_at=None):
        day(vintage)
        if series not in SERIES:raise ValueError('unknown vintage series')
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
            return {'state':'observed','series':series,'title':SERIES[series],'vintage':vintage,'retrieved_at':row['retrieved_at'],'available_after':row['available_after'],'sha256':row['sha256'],'source_url':URL,'release_precision':'date_only','rows':rows,'scope':'provider-dated historical snapshot, not locally observed on vintage date'}

    def collect(self,series,vintage,*,client=None,clock=time.time):
        day(vintage);now=int(clock())
        if series not in SERIES or cutoff(vintage)>now or day(vintage).year<datetime.fromtimestamp(now,timezone.utc).year-11:raise ValueError('vintage outside authorized scope')
        with exclusive_job(self.memory.root,'alfred-vintage'):
            with self.memory.tx(True) as db:
                if db.execute('SELECT 1 FROM alfred_vintages WHERE series=? AND vintage=?',(series,vintage)).fetchone():return {'state':'already_observed'}
                if db.execute('SELECT count(*) FROM alfred_vintages').fetchone()[0]>=MAX_SNAPSHOTS or sum(p.stat().st_size for p in self.memory.root.glob('evidence.sqlite3*'))>=MAX_DB or shutil.disk_usage(self.memory.root).free<RESERVE:raise MemoryUnavailable('vintage storage limit reached')
                prior=db.execute('SELECT max(last_attempt) FROM alfred_attempts').fetchone()[0]
                if prior is not None and now-prior<PACE:return {'state':'paced'}
                if db.execute('SELECT count(*) FROM alfred_attempts').fetchone()[0]>=MAX_SNAPSHOTS*2 and not db.execute('SELECT 1 FROM alfred_attempts WHERE series=? AND vintage=?',(series,vintage)).fetchone():raise MemoryUnavailable('vintage attempt capacity reached')
                db.execute('INSERT INTO alfred_attempts VALUES(?,?,?,1) ON CONFLICT(series,vintage) DO UPDATE SET last_attempt=excluded.last_attempt,attempts=attempts+1',(series,vintage,now))
            owned=client is None
            if owned:client=httpx.Client(timeout=httpx.Timeout(20,connect=8),follow_redirects=False,trust_env=False,headers={'User-Agent':'TradeBot-public-economic-research/1.0','Accept-Encoding':'identity'})
            params={'id':series,'vintage_date':vintage}
            try:
                with client.stream('GET',URL,params=params) as response:
                    if response.status_code==429:raise MemoryUnavailable('official vintage request throttled')
                    if response.status_code!=200 or response.url!=httpx.URL(URL,params=params):raise MemoryUnavailable('official vintage unavailable or redirected')
                    if response.headers.get('Content-Encoding','identity').lower()!='identity':raise ValueError('unexpected vintage encoding')
                    raw=bytearray()
                    for chunk in response.iter_raw():
                        if len(raw)+len(chunk)>MAX_BYTES:raise ValueError('vintage response too large')
                        raw.extend(chunk)
                raw=bytes(raw);rows=parse(series,vintage,raw);retrieved=int(clock())
                if retrieved<now:raise MemoryUnavailable('vintage clock moved backward')
                digest=hashlib.sha256(raw).hexdigest()
                with self.memory.tx(True) as db:db.execute('INSERT INTO alfred_vintages VALUES(?,?,?,?,?,?,?)',(series,vintage,retrieved,cutoff(vintage),digest,raw,len(rows)))
                return {'state':'observed','rows':len(rows),'sha256':digest}
            finally:
                if owned:client.close()

    def collect_batch(self):
        today=datetime.now(timezone.utc).date();month=today.year*12+today.month-1
        targets=[]
        for i in range(1,121):
            y,m=divmod(month-i,12);vintage=date(y,m+1,1).isoformat()
            for series in SERIES:targets.append((series,vintage))
        with self.memory.tx() as db:
            done={tuple(r) for r in db.execute('SELECT series,vintage FROM alfred_vintages')}
            attempts={(r[0],r[1]):r[2] for r in db.execute('SELECT series,vintage,last_attempt FROM alfred_attempts')}
        due=sorted((t for t in targets if t not in done),key=lambda t:attempts.get(t,-1))[:BATCH]
        failures=observed=0
        for series,vintage in due:
            try:
                result=self.collect(series,vintage)
                if result['state']=='observed':observed+=1
                elif result['state']!='already_observed':failures+=1
            except (MemoryUnavailable,ValueError,OSError,httpx.HTTPError,csv.Error):
                failures+=1
                # Failed network or parsing admission stops this burst; the
                # durable attempt is retained and next batch rotates dates.
                break
            time.sleep(PACE)
        return {'observed':observed,'unavailable':failures,'remaining':len(set(targets)-done)-observed,'state':'incomplete' if failures else 'batch_finished'}
