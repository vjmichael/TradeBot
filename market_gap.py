"""Recover closed hourly gaps using bounded, checkpointed public trade pages.

Both adjacent hourly anchors must be independently known and reproduce exactly.
No missing trade or candle is manufactured. Page receipts are durable; the
candidate is not published until both boundary anchors match at current as-of.
"""
import hashlib
import json
import re
import time
from contextlib import closing
from decimal import Decimal, localcontext
import sqlite3

import httpx
from engine import SYMBOLS, PUBLIC_HEADERS
from market_memory import normalize, decimal_text, MAX_TIME, MemoryUnavailable
from market_archive import exclusive_job

URL='https://api.kraken.com/0/public/Trades'
NS=10**9
STEP=3600
MAX_SPAN=31*86400
MAX_PAGES=256


def fetch_page(pair,cursor):
    with httpx.Client(timeout=15,follow_redirects=False,trust_env=False) as c:
        with c.stream('GET',URL,params={'pair':SYMBOLS[pair][0],'since':str(cursor),'count':1000},headers=PUBLIC_HEADERS) as response:
            if response.status_code!=200:raise MemoryUnavailable('trade recovery HTTP unavailable')
            data=bytearray()
            for block in response.iter_bytes(chunk_size=65536):
                if len(data)+len(block)>1024*1024:raise ValueError('oversized trade response')
                data.extend(block)
    return json.loads(data,parse_float=Decimal)


def parse_page(pair,cursor,payload):
    if not isinstance(payload,dict) or payload.get('error')!=[]:raise MemoryUnavailable('trade recovery exchange unavailable')
    data=payload.get('result');alias=SYMBOLS[pair][1]
    if not isinstance(data,dict) or set(data)!={alias,'last'}:raise ValueError('incorrect trade market identity')
    value=data['last']
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,20}',value):raise ValueError('invalid trade cursor')
    nxt=int(value)
    if not cursor<nxt<MAX_TIME*NS:raise ValueError('non-advancing trade cursor')
    rows=data[alias]
    if not isinstance(rows,list) or not 1<=len(rows)<=1000:raise ValueError('empty or oversized trade page')
    clean=[]
    for row in rows:
        if not isinstance(row,list) or len(row)!=7 or type(row[6]) is not int or not 0<row[6]<2**63:raise ValueError('invalid trade identity')
        stamp=row[2]
        if isinstance(stamp,bool) or not isinstance(stamp,(int,Decimal,str)):raise ValueError('invalid trade timestamp')
        with localcontext() as ctx:
            ctx.prec=80
            ns=Decimal(str(stamp))*NS
            if not ns.is_finite() or ns!=ns.to_integral_value() or not 0<ns<MAX_TIME*NS:raise ValueError('invalid trade time')
            ts=int(ns)
        price,qty=decimal_text(row[0]),decimal_text(row[1])
        if Decimal(price)<=0 or Decimal(qty)<=0:raise ValueError('invalid trade price or amount')
        clean.append((ts,row[6],price,qty))
    if any(a[0]>b[0] or a[1]>=b[1] for a,b in zip(clean,clean[1:])):raise ValueError('reordered or duplicate trade IDs')
    # The opaque nanosecond cursor can differ slightly from JSON timestamps due
    # to source precision; require it not to precede the final row by >1 ms.
    if nxt+1_000_000<clean[-1][0]:raise ValueError('trade cursor precedes last observation')
    return nxt,clean


class GapBridge:
    def __init__(self,memory):
        self.memory=memory
        with memory.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS gap_jobs(id TEXT PRIMARY KEY,pair TEXT NOT NULL,start_ts INTEGER NOT NULL,end_ts INTEGER NOT NULL,first_digest TEXT NOT NULL,last_digest TEXT NOT NULL,cursor TEXT NOT NULL,last_trade INTEGER NOT NULL DEFAULT 0,last_stamp TEXT NOT NULL DEFAULT '0',pages INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL,observed_at INTEGER,source_id TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS gap_candles(job TEXT NOT NULL,ts INTEGER NOT NULL,o TEXT NOT NULL,h TEXT NOT NULL,l TEXT NOT NULL,c TEXT NOT NULL,v TEXT NOT NULL,trades INTEGER NOT NULL,PRIMARY KEY(job,ts))''')
            db.execute('''CREATE TABLE IF NOT EXISTS gap_pages(job TEXT NOT NULL,page INTEGER NOT NULL,old_cursor TEXT NOT NULL,new_cursor TEXT NOT NULL,sha256 TEXT NOT NULL,trade_count INTEGER NOT NULL,observed_at INTEGER NOT NULL,PRIMARY KEY(job,page))''')
    def anchors(self,pair,start,end,now):
        left=self.memory.query(pair,60,start-STEP,start,now);right=self.memory.query(pair,60,end,end+STEP,now)
        if left['coverage']!=1 or right['coverage']!=1 or left['conflicts'] or right['conflicts']:
            raise MemoryUnavailable('gap recovery requires known clean boundary candles')
        return left['rows'][0]['digest'],right['rows'][0]['digest']
    def run(self,pair,start,end,*,max_pages=64,fetch=fetch_page,pace=time.sleep,clock=time.time):
        now=int(clock())
        if pair not in SYMBOLS or any(type(x) is not int for x in (start,end,max_pages)) or not 0<start-STEP<end+STEP<=now or start%STEP or end%STEP or not 0<end-start<=MAX_SPAN or not 1<=max_pages<=MAX_PAGES:
            raise ValueError('invalid gap recovery bounds')
        with exclusive_job(self.memory.root,'trade-gap'):
            first,last=self.anchors(pair,start,end,now)
            key=hashlib.sha256(f'{pair}:{start}:{end}:{first}:{last}'.encode()).hexdigest()
            with self.memory.tx() as db:
                db.execute('INSERT OR IGNORE INTO gap_jobs(id,pair,start_ts,end_ts,first_digest,last_digest,cursor,state) VALUES(?,?,?,?,?,?,?,?)',(key,pair,start,end,first,last,str((start-STEP)*NS-1),'recovering'))
            for _ in range(max_pages):
                with closing(sqlite3.connect(self.memory.db)) as db:
                    db.row_factory=sqlite3.Row;job=dict(db.execute('SELECT * FROM gap_jobs WHERE id=?',(key,)).fetchone())
                if job['state']=='complete':return job
                if job['state']=='rejected':raise MemoryUnavailable('trade recovery rejected; independent review needed')
                if job['state']=='ready':return self.publish(key,pair,start,end,first,last,int(clock()))
                cursor=int(job['cursor']); nxt,rows=parse_page(pair,cursor,fetch(pair,cursor)); observed=int(clock())
                if rows[0][0]<(start-STEP)*NS or rows[0][0]+1_000_000<cursor or rows[-1][0]>observed*NS+NS:raise ValueError('trade response outside observation boundary')
                prior_id=job['last_trade'];prior_stamp=int(job['last_stamp'])
                if rows[0][1]<=prior_id or rows[0][0]<prior_stamp:raise ValueError('overlapping or reordered trade page; no partial publication')
                reached=rows[-1][0]>=(end+STEP)*NS
                digest=hashlib.sha256(json.dumps([cursor,nxt,rows],separators=(',',':')).encode()).hexdigest()
                with self.memory.tx() as db,localcontext() as ctx:
                    ctx.prec=100
                    for ns,trade_id,price,qty in rows:
                        ts=ns//(STEP*NS)*STEP
                        if ts>=end+STEP:break
                        old=db.execute('SELECT * FROM gap_candles WHERE job=? AND ts=?',(key,ts)).fetchone()
                        if old:
                            h=decimal_text(max(Decimal(old['h']),Decimal(price)));l=decimal_text(min(Decimal(old['l']),Decimal(price)));v=decimal_text(Decimal(old['v'])+Decimal(qty))
                            db.execute('UPDATE gap_candles SET h=?,l=?,c=?,v=?,trades=trades+1 WHERE job=? AND ts=?',(h,l,price,v,key,ts))
                        else:db.execute('INSERT INTO gap_candles VALUES(?,?,?,?,?,?,?,?)',(key,ts,price,price,price,price,qty,1))
                    db.execute('INSERT INTO gap_pages VALUES(?,?,?,?,?,?,?)',(key,job['pages']+1,str(cursor),str(nxt),digest,len(rows),observed))
                    db.execute('UPDATE gap_jobs SET cursor=?,last_trade=?,last_stamp=?,pages=pages+1,state=?,observed_at=? WHERE id=?',(str(nxt),rows[-1][1],str(rows[-1][0]),'ready' if reached else 'recovering',observed,key))
                if reached:return self.publish(key,pair,start,end,first,last,observed)
                pace(1.25) # conservative per-page request cadence; 429/error stops job, retry resumes
            with closing(sqlite3.connect(self.memory.db)) as db:
                db.row_factory=sqlite3.Row;return dict(db.execute('SELECT * FROM gap_jobs WHERE id=?',(key,)).fetchone())
    def publish(self,key,pair,start,end,first,last,now):
        if self.anchors(pair,start,end,now)!=(first,last):raise MemoryUnavailable('boundary changed before recovery publication')
        with self.memory.tx() as db:
            job=db.execute('SELECT * FROM gap_jobs WHERE id=?',(key,)).fetchone()
            if job['state']=='complete':return dict(job)
            if job['state']!='ready':raise MemoryUnavailable('trade recovery not complete')
            data=[normalize([r['ts'],r['o'],r['h'],r['l'],r['c'],r['v'],r['trades']],60,now) for r in db.execute('SELECT * FROM gap_candles WHERE job=? ORDER BY ts',(key,))]
            valid=bool(data and data[0][0]==start-STEP and data[-1][0]==end and data[0][-1]==first and data[-1][-1]==last)
            if not valid:
                db.execute("UPDATE gap_jobs SET state='rejected' WHERE id=?",(key,))
            else:
                # Recheck both anchors within the same write transaction so a
                # concurrent conflicting revision cannot race publication.
                for stamp,digest in ((start-STEP,first),(end,last)):
                    known=db.execute("SELECT DISTINCT c.digest,c.trades FROM candles c JOIN sources s ON s.id=c.source_id WHERE c.pair=? AND c.interval=60 AND c.ts=? AND c.available_at<=? AND s.published_at<=? AND s.kind!='fixture' AND s.state='complete'",(pair,stamp,now,now)).fetchall()
                    recovered_count=data[0][6] if stamp==start-STEP else data[-1][6]
                    if {r[0] for r in known}!={digest} or any(r[1] is not None and r[1]!=recovered_count for r in known):raise MemoryUnavailable('boundary value or trade count mismatch')
                rows=[r for r in data if start<=r[0]<end]
                if not rows:raise MemoryUnavailable('no trade-derived candles recovered')
                source=hashlib.sha256(('public-trades:'+key).encode()).hexdigest()
                db.execute('INSERT INTO sources(id,kind,pair,interval,locator,observed_at,cursor,last_ts,state,published_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(source,'rest',pair,60,URL,now,len(rows),rows[-1][0],'complete',now))
                self.memory._insert(db,source,pair,60,now,rows)
                db.execute("UPDATE gap_jobs SET state='complete',source_id=? WHERE id=?",(source,key))
        if not valid:raise MemoryUnavailable('trade-derived boundary does not match independent candles')
        with closing(sqlite3.connect(self.memory.db)) as db:
            db.row_factory=sqlite3.Row;return dict(db.execute('SELECT * FROM gap_jobs WHERE id=?',(key,)).fetchone())
