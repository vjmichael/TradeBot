"""Bounded public spot liquidity samples and realized five-minute volatility.
No order, account API, fee inference, historical book fabrication or signal.
"""
import hashlib
import json
import math
import shutil
import time
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

import httpx
from engine import SYMBOLS, PUBLIC_HEADERS
from market_memory import decimal_text, MemoryUnavailable, MAX_TIME, ZONES
from market_archive import exclusive_job
from market_stream import exact_closed

DEPTH_URL = 'https://api.kraken.com/0/public/Depth'
LEVELS = 25
SLOT = 300
MAX_DAYS = 7
MAX_SAMPLES = 3*288*30
MAX_BODY = 128*1024
RESERVE = 256*1024**2
NOTIONALS = (100, 500, 1000)


def parse_depth(pair, payload, observed):
    if pair not in SYMBOLS or type(observed) is not int or not 0 < observed < MAX_TIME:
        raise ValueError('invalid market or time')
    if not isinstance(payload, dict) or payload.get('error') != []:
        raise MemoryUnavailable('order book unavailable')
    result = payload.get('result'); alias = SYMBOLS[pair][1]
    if not isinstance(result, dict) or set(result) != {alias}:
        raise ValueError('incorrect market identity')
    book = result[alias]
    if not isinstance(book, dict) or set(book) != {'asks','bids'}:
        raise ValueError('invalid book')
    clean = {}
    for side in ('asks','bids'):
        rows = book[side]
        if not isinstance(rows,list) or not 1 <= len(rows) <= LEVELS:
            raise ValueError('invalid book depth')
        levels = []
        for r in rows:
            if not isinstance(r,list) or len(r)!=3 or type(r[2]) is not int or not 0 < r[2] <= observed+2:
                raise ValueError('invalid price level time')
            p,q = decimal_text(r[0]),decimal_text(r[1])
            if Decimal(p)<=0 or Decimal(q)<=0: raise ValueError('nonpositive price level')
            levels.append([p,q,r[2]])
        prices = [Decimal(r[0]) for r in levels]
        if any((a>=b if side=='asks' else a<=b) for a,b in zip(prices,prices[1:])):
            raise ValueError('unordered or duplicate price levels')
        clean[side]=levels
    if Decimal(clean['bids'][0][0]) >= Decimal(clean['asks'][0][0]):
        raise ValueError('crossed or locked order book')
    return clean


def fetch_depth(pair):
    if pair not in SYMBOLS: raise ValueError('unsupported market')
    start = time.monotonic()
    with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as c:
        with c.stream('GET',DEPTH_URL,params={'pair':SYMBOLS[pair][0],'count':LEVELS},headers=PUBLIC_HEADERS) as r:
            if r.status_code!=200: raise MemoryUnavailable('order book HTTP unavailable')
            body=bytearray()
            for chunk in r.iter_bytes(chunk_size=16384):
                if len(body)+len(chunk)>MAX_BODY: raise ValueError('oversized order book')
                body.extend(chunk)
    if time.monotonic()-start>15: raise MemoryUnavailable('order book sampling latency exceeded')
    return json.loads(body,parse_float=Decimal),int(time.time()),hashlib.sha256(body).hexdigest()


def measure(book, fee_bps=None):
    """Equal base quantity on both sides, sized by USD mid notional.
    No assumption of fill availability beyond returned displayed levels.
    """
    if fee_bps is not None and (type(fee_bps) is not int or not 0<=fee_bps<=100):
        raise ValueError('fee scenario outside supported range')
    with localcontext() as ctx:
        ctx.prec=64
        bid,ask=Decimal(book['bids'][0][0]),Decimal(book['asks'][0][0]);mid=(bid+ask)/2
        def walk(side,qty):
            amount=qty;usd=Decimal(0)
            for p,q,_ in book[side]:
                p,q=Decimal(p),Decimal(q); take=min(q,amount); usd+=take*p; amount-=take
                if amount==0:return usd
            return None
        def depth(side):
            return sum((Decimal(p)*Decimal(q) for p,q,_ in book[side] if abs(Decimal(p)-mid)<=mid/100),Decimal(0))
        scenarios=[]
        for n in NOTIONALS:
            qty=Decimal(n)/mid; buy=walk('asks',qty);sell=walk('bids',qty)
            if buy is None or sell is None:
                scenarios.append({'notional_usd':n,'state':'insufficient_displayed_depth','book_cost_bps':None,'fee_inclusive_bps':None});continue
            cost=(buy-sell)/n*10000
            fees=None if fee_bps is None else cost+(buy+sell)/n*fee_bps
            scenarios.append({'notional_usd':n,'state':'estimated','book_cost_bps':float(cost),'fee_inclusive_bps':None if fees is None else float(fees)})
        return {'spread_bps':float((ask-bid)/mid*10000),'bid_depth_1pct_usd':float(depth('bids')),'ask_depth_1pct_usd':float(depth('asks')),'scenarios':scenarios}


def realized_hours(snapshot):
    """12 closes plus prior close per hour; omit incomplete/conflicted hours."""
    if snapshot['interval_minutes']!=5:raise ValueError('five-minute closes required')
    rows={r['ts']:r for r in snapshot['rows']}
    start=((snapshot['start']+SLOT+3599)//3600)*3600;end=snapshot['end']//3600*3600
    hours=[]
    for t in range(start,end,3600):
        points=[rows.get(x) for x in range(t-SLOT,t+3600,SLOT)]
        if any(r is None for r in points):continue
        closes=[float(r['c']) for r in points]
        hours.append({'ts':t,'rv_pct':100*math.sqrt(sum(math.log(b/a)**2 for a,b in zip(closes,closes[1:]))),'base_volume':sum(float(r['v']) for r in points[1:])})
    return hours


class QualityMemory:
    def __init__(self,memory):
        self.memory=memory
        with memory.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS quality_books(pair TEXT NOT NULL, slot INTEGER NOT NULL, observed INTEGER NOT NULL, published INTEGER NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL, source_digest TEXT NOT NULL, PRIMARY KEY(pair,slot))''')
            db.execute('''CREATE TABLE IF NOT EXISTS quality_attempts(pair TEXT NOT NULL, slot INTEGER NOT NULL, attempted INTEGER NOT NULL, finished INTEGER, state TEXT NOT NULL, PRIMARY KEY(pair,slot))''')

    def sample(self,pair,*,fetch=fetch_depth,clock=lambda:int(time.time())):
        if pair not in SYMBOLS:raise ValueError('unsupported market')
        with exclusive_job(self.memory.root,'quality-book'):
            now=clock(); slot=now//SLOT*SLOT
            with self.memory.tx() as db:
                prior=db.execute('SELECT MAX(attempted) FROM quality_attempts WHERE pair=?',(pair,)).fetchone()
                if prior and prior[0] is not None and prior[0]//SLOT>=slot//SLOT:return 'paced'
                if db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0]>=MAX_SAMPLES or shutil.disk_usage(self.memory.root).free<RESERVE:
                    raise MemoryUnavailable('liquidity sample capacity reached')
                db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',(pair,slot,now,None,'pending'))
            try:
                raw,observed,source_digest=fetch(pair); published=clock()
                if type(observed) is not int or not now<=observed<=published<MAX_TIME or published-now>30 or observed//SLOT*SLOT!=slot or not isinstance(source_digest,str) or len(source_digest)!=64 or any(c not in '0123456789abcdef' for c in source_digest):raise ValueError('invalid sample receipt')
                body=json.dumps(parse_depth(pair,raw,observed),sort_keys=True,separators=(',',':'));digest=hashlib.sha256(body.encode()).hexdigest()
                with self.memory.tx() as db:
                    db.execute('INSERT INTO quality_books VALUES(?,?,?,?,?,?,?)',(pair,observed//SLOT*SLOT,observed,published,body,digest,source_digest))
                    db.execute('UPDATE quality_attempts SET state=?,finished=? WHERE pair=? AND slot=?',('observed',published,pair,slot))
            except Exception:
                with self.memory.tx() as db:db.execute('UPDATE quality_attempts SET state=?,finished=? WHERE pair=? AND slot=?',('unavailable',max(now,clock()),pair,slot))
                raise
            return 'observed'

    def profile(self,pair,start,end,as_of,zone='UTC',fee_bps=None):
        if pair not in SYMBOLS or zone not in ZONES or any(type(x) is not int for x in (start,end,as_of)) or not SLOT<=start<end<=as_of<MAX_TIME or start%3600 or end%3600 or end-start>MAX_DAYS*86400 or (fee_bps is not None and (type(fee_bps) is not int or not 0<=fee_bps<=100)):
            raise ValueError('invalid quality window')
        fine=self.memory.query(pair,5,start-SLOT,end,as_of);hours=realized_hours(fine)
        tz=ZoneInfo(zone)
        bins=[{'weekday':d,'hour':h,'expected_hours':0,'rv_hours':0,'rv_sum':0.,'expected_books':0,'books':0,'spread_sum':0.,'bid_sum':0.,'ask_sum':0.,'cost_sum':0.,'cost_samples':0,'fee_sum':0.} for d in range(7) for h in range(24)]
        def cell(t):
            dt=datetime.fromtimestamp(t,timezone.utc).astimezone(tz);return bins[dt.weekday()*24+dt.hour]
        for t in range(start,end,3600):cell(t)['expected_hours']+=1
        for t in range(start,end,SLOT):cell(t)['expected_books']+=1
        for r in hours:
            b=cell(r['ts']);b['rv_hours']+=1;b['rv_sum']+=r['rv_pct']
        with self.memory.tx() as db:
            samples=db.execute('SELECT * FROM quality_books WHERE pair=? AND slot>=? AND slot<? AND published<=? ORDER BY slot',(pair,start,end,as_of)).fetchall()
            last=db.execute('SELECT * FROM quality_books WHERE pair=? AND published<=? ORDER BY observed DESC LIMIT 1',(pair,as_of)).fetchone()
            attempt=db.execute('SELECT * FROM quality_attempts WHERE pair=? AND attempted<=? ORDER BY attempted DESC LIMIT 1',(pair,as_of)).fetchone()
            retired=0
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quality_retired'").fetchone():
                retired=db.execute('SELECT count(*) FROM quality_retired WHERE pair=? AND slot>=? AND slot<? AND had_book=1',(pair,start,end)).fetchone()[0]
        source_state = (attempt['state'] if attempt['finished'] is not None and attempt['finished']<=as_of else 'pending') if attempt else 'not_observed'
        def checked(row):
            if hashlib.sha256(row['body'].encode()).hexdigest()!=row['digest']:raise MemoryUnavailable('order book integrity failure')
            return measure(parse_depth(pair,{'error':[],'result':{SYMBOLS[pair][1]:json.loads(row['body'])}},row['observed']),fee_bps)
        for r in samples:
            m=checked(r); b=cell(r['slot']);b['books']+=1;b['spread_sum']+=m['spread_bps'];b['bid_sum']+=m['bid_depth_1pct_usd'];b['ask_sum']+=m['ask_depth_1pct_usd']
            scenario=m['scenarios'][0]
            if scenario['state']=='estimated':
                b['cost_samples']+=1;b['cost_sum']+=scenario['book_cost_bps'];b['fee_sum']+=scenario['fee_inclusive_bps'] or 0
        for b in bins:
            total=b.pop('rv_sum');b['mean_hourly_realized_vol_pct']=total/b['rv_hours'] if b['rv_hours'] else None
            for field,name,count in [('spread_sum','mean_spread_bps','books'),('bid_sum','mean_bid_depth_1pct_usd','books'),('ask_sum','mean_ask_depth_1pct_usd','books'),('cost_sum','mean_book_cost_100usd_bps','cost_samples'),('fee_sum','mean_fee_inclusive_100usd_bps','cost_samples')]:
                total=b.pop(field);b[name]=total/b[count] if b[count] and (field!='fee_sum' or fee_bps is not None) else None
        latest=None
        if last:
            latest=checked(last)|{'observed_at':last['observed'],'age_seconds':as_of-last['observed'],'state':'recent' if as_of-last['observed']<=2*SLOT and source_state=='observed' else 'stale','source_digest':last['source_digest']}
        return {'pair':pair,'zone':zone,'as_of':as_of,'start':start,'end':end,'expected_hours':(end-start)//3600,'rv_hours':len(hours),'book_samples':len(samples),'retired_book_samples':retired,'expected_books':(end-start)//SLOT,'fine_missing':fine['missing'],'fine_conflicts':fine['conflicts'],'bins':bins,'latest':latest,'fee_bps_per_side':fee_bps,'source_state':source_state}
