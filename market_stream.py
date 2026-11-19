"""TB-04B public Kraken WS v2 + authoritative REST close/gap reconciliation.

Streaming observations are *indicative*, never published as closed candles on
silence, timer expiry, or disconnect. REST supplies closed data; unresolved
out-of-window gaps stay visible. No account or LLM dependency.
"""
import asyncio
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from decimal import Decimal

import httpx
from engine import SYMBOLS, _public_ohlc
from market_memory import MarketMemory, MAX_TIME, MAX_QUERY_ROWS, normalize

WS_URL='wss://ws.kraken.com/v2'
STEP=3600
MIN_REPAIR_SECONDS=60
MAX_REPAIR_SECONDS=300
MAX_GAPS=MAX_QUERY_ROWS
RECONNECT=(5,10,20,40,60)


def exact_closed(pair, interval=60, *, client=None, now=None):
    """Same qualified public endpoint, but retain exact source decimals/count."""
    if pair not in SYMBOLS or interval!=60: raise ValueError('unsupported repair market')
    if client is None:
        with httpx.Client(timeout=12,follow_redirects=False,trust_env=False) as c:
            return exact_closed(pair,interval,client=c,now=now)
    symbol,alias=SYMBOLS[pair]; payload=_public_ohlc(client,symbol,interval)
    observed=int(time.time()) if now is None else now
    data=payload.get('result')
    if not isinstance(data,dict) or set(data)!={alias,'last'}: raise ValueError('incorrect REST identity')
    raw=data[alias]
    if not isinstance(raw,list) or not 2<=len(raw)<=721: raise ValueError('invalid REST count')
    rows=[];last=-1
    for r in raw:
        if not isinstance(r,list) or len(r)!=8 or type(r[0]) is not int or r[0]<=last: raise ValueError('malformed REST candle')
        last=r[0]
        if r[0]%STEP or r[0]>observed: raise ValueError('invalid REST timestamp')
        # Forming candle is validated using an artificial close bound, never emitted.
        normalized=normalize([r[0],r[1],r[2],r[3],r[4],r[6],r[7]],60,max(observed,r[0]+STEP))
        rows.append([r[0]*1000,*normalized[1:6],normalized[6]])
    if rows[-1][0]//1000!=observed//STEP*STEP: raise ValueError('REST forming hour missing; retry')
    rows=rows[:-1]
    if observed-rows[-1][0]//1000>2*STEP: raise ValueError('stale REST observation')
    return {'exchange':'kraken','product':'spot','pair':pair,'interval_minutes':60,'observed_at':observed,'candles':rows}


class StreamJournal:
    def __init__(self,memory):
        self.memory=memory
        with memory.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS stream_health(pair TEXT PRIMARY KEY,connected INTEGER NOT NULL DEFAULT 0,acked INTEGER NOT NULL DEFAULT 0,last_message INTEGER,last_update INTEGER,last_repair INTEGER,state TEXT NOT NULL DEFAULT 'offline',repair_state TEXT NOT NULL DEFAULT 'pending')''')
            db.execute('''CREATE TABLE IF NOT EXISTS stream_latest(pair TEXT PRIMARY KEY,ts INTEGER NOT NULL,observed_at INTEGER NOT NULL,payload TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS stream_gap_reports(pair TEXT PRIMARY KEY,start_ts INTEGER NOT NULL,end_ts INTEGER NOT NULL,as_of INTEGER NOT NULL,expected INTEGER NOT NULL,covered INTEGER NOT NULL,conflicts INTEGER NOT NULL,missing INTEGER NOT NULL,older_than_rest INTEGER NOT NULL,ranges TEXT NOT NULL)''')
            for pair in SYMBOLS: db.execute('INSERT OR IGNORE INTO stream_health(pair) VALUES(?)',(pair,))
    def start(self):
        with self.memory.tx() as db:
            db.execute("UPDATE stream_health SET connected=1,acked=0,last_message=NULL,state='subscribing',repair_state='pending'")
    def stop(self):
        with self.memory.tx() as db:
            db.execute("UPDATE stream_health SET connected=0,acked=0,state='offline'")
    def ack(self,pair,now):
        with self.memory.tx() as db: db.execute("UPDATE stream_health SET acked=1,last_message=?,state='awaiting_update' WHERE pair=? AND connected=1",(now,pair))
    def touch(self,now):
        # Transport health is separate from per-pair observation freshness.
        with self.memory.tx() as db: db.execute('UPDATE stream_health SET last_message=? WHERE connected=1 AND acked=1',(now,))
    def updates(self,rows,now):
        # Validate the entire message before making any writes; older snapshots
        # cannot replace newer observations of an interval.
        with self.memory.tx() as db:
            for pair,ts,row in rows:
                health=db.execute('SELECT connected,acked FROM stream_health WHERE pair=?',(pair,)).fetchone()
                if not health['connected'] or not health['acked']: raise ValueError('unacknowledged symbol')
                old=db.execute('SELECT ts,observed_at,payload FROM stream_latest WHERE pair=?',(pair,)).fetchone()
                if old and (ts<old['ts'] or now<old['observed_at']): continue
                if old and ts==old['ts']:
                    prior=json.loads(old['payload'])
                    if row['trades']<prior['trades']: continue
                    if row['trades']==prior['trades'] and row!=prior:
                        raise ValueError('conflicting stream update at same trade count')
                db.execute('INSERT INTO stream_latest VALUES(?,?,?,?) ON CONFLICT(pair) DO UPDATE SET ts=excluded.ts,observed_at=excluded.observed_at,payload=excluded.payload',(pair,ts,now,json.dumps(row,sort_keys=True)))
                db.execute("UPDATE stream_health SET last_message=?,last_update=?,state='observed' WHERE pair=?",(now,now,pair))
    def repair(self,pair,*,fetch=exact_closed,now=None):
        """Publish verified REST rows and a bounded explicit gap report.

        Gaps outside the REST response remain marked, never zero-filled. Scope
        starts at earliest observed closed candle (up to ~5 years), not invented
        coverage before a market listed. Report includes unresolved recent gaps.
        """
        try:
            snapshot=fetch(pair,60)
            observed=snapshot['observed_at'];published=int(time.time()) if now is None else now
            count=self.memory.record_recent(pair,60,snapshot,now=published)
            end=observed//STEP*STEP
            with closing(sqlite3.connect(self.memory.db)) as db:
                row=db.execute("SELECT MIN(c.ts) FROM candles c JOIN sources s ON s.id=c.source_id WHERE c.pair=? AND c.interval=60 AND s.kind!='fixture' AND s.state='complete' AND s.published_at<=?",(pair,published)).fetchone()
            start=max(end-MAX_QUERY_ROWS*STEP,row[0] if row[0] is not None else end-STEP)
            view=self.memory.query(pair,60,start,end,published)
            stamps={r['ts'] for r in view['rows']};ranges=[];older=0;floor=snapshot['candles'][0][0]//1000
            for ts in range(start,end,STEP):
                if ts in stamps: continue
                outside=ts<floor;older+=outside
                label='outside_rest_window' if outside else 'not_confirmed_by_rest'
                if ranges and ranges[-1]['end']==ts and ranges[-1]['reason']==label:ranges[-1]['end']=ts+STEP
                else:ranges.append({'start':ts,'end':ts+STEP,'reason':label})
            with self.memory.tx() as db:
                db.execute('INSERT INTO stream_gap_reports VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(pair) DO UPDATE SET start_ts=excluded.start_ts,end_ts=excluded.end_ts,as_of=excluded.as_of,expected=excluded.expected,covered=excluded.covered,conflicts=excluded.conflicts,missing=excluded.missing,older_than_rest=excluded.older_than_rest,ranges=excluded.ranges',(pair,start,end,published,view['expected'],view['coverage'],view['conflicts'],view['missing'],older,json.dumps(ranges,separators=(',',':'))))
                db.execute('UPDATE stream_health SET last_repair=?,repair_state=? WHERE pair=?',(published,'gaps_remaining' if view['missing'] else 'reconciled',pair))
            return {'pair':pair,'inserted':count,'coverage':view['coverage'],'missing':view['missing'],'outside_rest_window':older,'conflicts':view['conflicts']}
        except Exception:
            with self.memory.tx() as db: db.execute("UPDATE stream_health SET repair_state='unavailable' WHERE pair=?",(pair,))
            raise
    def status(self):
        with closing(sqlite3.connect(self.memory.db)) as db:
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute('SELECT h.*,l.ts AS latest_interval FROM stream_health h LEFT JOIN stream_latest l ON l.pair=h.pair ORDER BY h.pair')]
            now=int(time.time())
            for r in rows:
                r['transport_fresh']=bool(r['connected'] and r['acked'] and r['last_message'] is not None and 0<=now-r['last_message']<=30)
                r['market_update_fresh']=bool(r['transport_fresh'] and r['last_update'] is not None and 0<=now-r['last_update']<=60 and r['latest_interval']==now//STEP*STEP)
            return rows

    def gap_reports(self):
        with closing(sqlite3.connect(self.memory.db)) as db:
            db.row_factory=sqlite3.Row
            return [dict(r)|{'ranges':json.loads(r['ranges'])} for r in db.execute('SELECT * FROM stream_gap_reports ORDER BY pair')]


def decode_message(text,now):
    if not isinstance(text,str) or len(text)>1024*1024: raise ValueError('invalid WS message size')
    m=json.loads(text,parse_float=Decimal)
    if not isinstance(m,dict):raise ValueError('invalid WS message')
    if m.get('method')=='subscribe':
        r=m.get('result')
        if m.get('success') is not True or not isinstance(r,dict) or r.get('channel')!='ohlc' or r.get('symbol') not in SYMBOLS or r.get('interval')!=60: raise ValueError('subscription rejected')
        return 'ack',r['symbol']
    if m.get('channel') in ('heartbeat','status'): return 'transport',None
    if m.get('channel')!='ohlc' or m.get('type') not in ('snapshot','update'): raise ValueError('unexpected WS channel')
    data=m.get('data')
    if not isinstance(data,list) or not 1<=len(data)<=100:raise ValueError('invalid WS candle count')
    rows=[]
    for r in data:
        if not isinstance(r,dict) or r.get('symbol') not in SYMBOLS or r.get('interval')!=60:raise ValueError('invalid stream identity')
        stamp=r.get('interval_begin')
        if not isinstance(stamp,str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.0{1,9})?Z',stamp):raise ValueError('invalid stream timestamp')
        ts=int(datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp())
        if ts>now or not 0<ts<MAX_TIME: raise ValueError('future stream candle')
        v=normalize([ts,r['open'],r['high'],r['low'],r['close'],r['volume'],r['trades']],60,max(now,ts+STEP))
        if v[6] is None: raise ValueError('stream trade count missing')
        rows.append((r['symbol'],ts,dict(zip(('o','h','l','c','v','trades'),v[1:7]))))
    return 'updates',rows


async def session(journal,ws,*,duration=None,fetch=exact_closed,repair_interval=MAX_REPAIR_SECONDS):
    if not MIN_REPAIR_SECONDS<=repair_interval<=MAX_REPAIR_SECONDS:raise ValueError('invalid repair cadence')
    journal.start();started=time.monotonic();last_repair=None;last_hour=None;acks=set();seen=set();task=None;task_pairs=set();last_touch=0
    async def repair_all():
        out=[]
        for pair in SYMBOLS:
            try:out.append(await asyncio.to_thread(journal.repair,pair,fetch=fetch))
            except Exception as exc:out.append({'pair':pair,'error_type':type(exc).__name__})
        return out
    try:
        await ws.send(json.dumps({'method':'subscribe','params':{'channel':'ohlc','symbol':list(SYMBOLS),'interval':60,'snapshot':True},'req_id':1}))
        while duration is None or time.monotonic()-started<duration:
            if len(acks)<len(SYMBOLS) and time.monotonic()-started>20: raise TimeoutError('missing subscription acknowledgements')
            try:raw=await asyncio.wait_for(ws.recv(),timeout=15)
            except asyncio.TimeoutError:raise TimeoutError('no WS messages; reconnect') from None
            now=int(time.time());typ,payload=decode_message(raw,now)
            if typ=='ack':journal.ack(payload,now);acks.add(payload)
            elif typ=='updates':journal.updates(payload,now);seen.update(r[0] for r in payload)
            if now-last_touch>=5:journal.touch(now);last_touch=now
            if task is not None and task.done():
                result=await task;task_pairs.update(r['pair'] for r in result if 'error_type' not in r);task=None
            # One bounded batch per connection start, hourly rollover, or periodic repair.
            mono=time.monotonic();hour=now//STEP
            if len(acks)==len(SYMBOLS) and task is None and (last_repair is None or (mono-last_repair>=MIN_REPAIR_SECONDS and (hour!=last_hour or mono-last_repair>=repair_interval))):
                task=asyncio.create_task(repair_all());last_repair=mono;last_hour=hour
        if task is not None:
            result=await task;task_pairs.update(r['pair'] for r in result if 'error_type' not in r);task=None
        return {'acknowledged':sorted(acks),'stream_observed':sorted(seen),'rest_repaired':sorted(task_pairs)}
    finally:
        if task is not None:
            # Do not leave a detached writer running after disconnect; let the
            # bounded public REST operation finish before starting another session.
            try:await asyncio.shield(task)
            finally:journal.stop()
        else:journal.stop()


async def _run_stream(memory,*,duration=None):
    """Native public connection; finite mode for qualification, continuous for developer use."""
    from websockets.asyncio.client import connect
    journal=StreamJournal(memory);journal.stop();failures=0
    while True:
        try:
            async with connect(WS_URL,proxy=None,open_timeout=15,close_timeout=5,ping_interval=20,ping_timeout=15,max_size=1024*1024,max_queue=16) as ws:
                result=await session(journal,ws,duration=duration)
            if duration is not None:return result
        except asyncio.CancelledError:raise
        except Exception:
            journal.stop()
            if duration is not None:raise
            await asyncio.sleep(RECONNECT[min(failures,len(RECONNECT)-1)]);failures+=1


async def run_stream(memory,*,duration=None):
    from market_archive import exclusive_job
    with exclusive_job(memory.root,'market-stream'):
        return await _run_stream(memory,duration=duration)
