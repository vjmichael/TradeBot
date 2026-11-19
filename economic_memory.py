"""TB-04D: bounded official evidence snapshots, no credentials or model calls.

Current-vintage statistics are NOT historical point-in-time release vintages.
A schedule is not proof a release occurred. No inference of consensus or orders.
"""
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx
from market_memory import decimal_text, MAX_TIME, MemoryUnavailable
from market_archive import exclusive_job

CALENDAR='bls_calendar'
MACRO='bls_statistics'
FED='fed_monetary'
SOURCES={CALENDAR:('https://www.bls.gov/schedule/news_release/bls.ics',3600,36*3600,'Bureau of Labor Statistics','BLS public-domain text/data; attribution'),
         MACRO:('https://api.bls.gov/publicAPI/v1/timeseries/data/',21600,48*3600,'Bureau of Labor Statistics','BLS public API v1; attribution'),
         FED:('https://www.federalreserve.gov/feeds/press_monetary.xml',3600,36*3600,'Federal Reserve','Official RSS headline and link reference only')}
SERIES={'CUUR0000SA0':('Consumer price index (not seasonally adjusted)','index'),
        'LNS14000000':('Unemployment rate (seasonally adjusted)','percent'),
        'CES0000000001':('Nonfarm payroll employment (seasonally adjusted)','thousands of jobs')}
MAX_BYTES=2*1024**2
MAX_ROWS=1500
MAX_QUERY=500
MAX_DB=256*1024**2  # admission watermark, not a hard filesystem quota


def digest(data):return hashlib.sha256(data).hexdigest()
def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def text(value,limit=512):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 for c in value):raise ValueError('invalid evidence text')
    return value.strip()
def stamp(value):
    if type(value) is not int or not 0<value<MAX_TIME:raise ValueError('invalid timestamp')
    return value

def safe_link(value,host):
    value=text(value,1024);u=urlsplit(value)
    if u.scheme!='https' or u.netloc!=host or not u.path.startswith('/') or u.fragment:raise ValueError('invalid official link')
    return value


def ics_time(key,value):
    # BLS publishes release times in Eastern time. No guessing for other TZIDs.
    if not isinstance(value,str):raise ValueError('missing calendar time')
    if re.fullmatch(r'\d{8}',value):
        return int(datetime.strptime(value,'%Y%m%d').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()),'date'
    if value.endswith('Z'):
        if 'TZID=' in key:raise ValueError('ambiguous calendar timezone')
        return int(datetime.strptime(value,'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()),'time'
    dt=datetime.strptime(value,'%Y%m%dT%H%M%S');params=key.split(';')[1:]
    tz=[p.split('=',1)[1].strip('"') for p in params if p.startswith('TZID=')]
    if len(tz)>1 or (tz and tz[0] not in ('America/New_York','US/Eastern','Eastern Standard Time','US-Eastern')):raise ValueError('unsupported calendar timezone')
    zone=ZoneInfo('America/New_York')
    a,b=dt.replace(tzinfo=zone,fold=0),dt.replace(tzinfo=zone,fold=1)
    if a.utcoffset()!=b.utcoffset():raise ValueError('ambiguous/nonexistent local release time')
    return int(a.timestamp()),'time'


def parse_calendar(raw,now):
    s=raw.decode('utf-8-sig');s=re.sub(r'\r?\n[ \t]','',s);lines=s.splitlines()
    if not lines or lines[0]!='BEGIN:VCALENDAR' or lines[-1]!='END:VCALENDAR':raise ValueError('not a complete calendar')
    out=[];event=None;alarm=False
    for line in lines:
        if len(line)>32768:raise ValueError('oversize calendar line')
        if line=='BEGIN:VALARM' and event is not None and not alarm:alarm=True;continue
        if alarm:
            if line=='END:VALARM':alarm=False
            elif line.startswith(('BEGIN:','END:')):raise ValueError('nested calendar alarm')
            continue
        if event is not None and line.startswith(('BEGIN:','END:')) and line not in ('BEGIN:VEVENT','END:VEVENT'):raise ValueError('nested calendar component unsupported')
        if line=='BEGIN:VEVENT':
            if event is not None:raise ValueError('nested calendar event')
            event={};continue
        if line=='END:VEVENT':
            if event is None:raise ValueError('unmatched calendar event')
            def get(k):return event.get(k,('',None))
            if any(k in event for k in ('RRULE','RDATE','RECURRENCE-ID','EXDATE')):raise ValueError('recurrence unsupported')
            uid=text(get('UID')[1],512);key,val=get('DTSTART');at,precision=ics_time(key,val)
            title=text(re.sub(r'\\([,;\\])',r'\1',get('SUMMARY')[1] or ''),512)
            status=(get('STATUS')[1] or 'SCHEDULED').upper()
            if status not in ('CONFIRMED','TENTATIVE','CANCELLED','SCHEDULED'):raise ValueError('unknown schedule status')
            out.append({'key':uid,'title':title,'event_at':stamp(at),'precision':precision,'status':status.lower(),'published_at':None,'forecast':None,'previous':None,'actual':None,'importance':None,'url':SOURCES[CALENDAR][0]});event=None;continue
        if event is not None and ':' in line:
            key,val=line.split(':',1);name=key.split(';',1)[0].upper()
            if name in ('UID','DTSTART','SUMMARY','STATUS','RRULE','RDATE','RECURRENCE-ID','EXDATE'):
                if name in event:raise ValueError('duplicate calendar field')
                event[name]=(key.upper().replace('AMERICA/NEW_YORK','America/New_York').replace('US/EASTERN','US/Eastern').replace('EASTERN STANDARD TIME','Eastern Standard Time').replace('US-EASTERN','US-Eastern'),val)
    if event is not None or alarm or not out:raise ValueError('incomplete/empty calendar')
    return out


def parse_macro(raw,now):
    obj=json.loads(raw);out=[];seen=set();today=datetime.fromtimestamp(now,timezone.utc)
    if obj.get('status')!='REQUEST_SUCCEEDED':raise ValueError('BLS request failed')
    for series in obj['Results']['series']:
        sid=series['seriesID']
        if sid not in SERIES or sid in seen:raise ValueError('unexpected series')
        seen.add(sid);count=0
        for r in series['data']:
            period=r['period'];year=int(r['year'])
            if not re.fullmatch(r'M(?:0[1-9]|1[0-3])',period):raise ValueError('unexpected period')
            if period=='M13':continue # annual averages are not months
            month=int(period[1:]);date=datetime(year,month,1,tzinfo=timezone.utc)
            if year<today.year-10 or (year,month)>(today.year,today.month):raise ValueError('invalid macro period')
            val=r['value'];value=None if val in ('-','.') else decimal_text(val)
            if value is not None and float(value)<0:raise ValueError('invalid negative level')
            if sid=='LNS14000000' and value is not None and float(value)>100:raise ValueError('invalid unemployment rate')
            footnotes=[text(f['text'],512) for f in r.get('footnotes',[]) if f.get('text')]
            out.append({'key':f'{sid}:{year:04d}-{month:02d}','series':sid,'title':SERIES[sid][0],'unit':SERIES[sid][1],'period':f'{year:04d}-{month:02d}','event_at':int(date.timestamp()),'value':value,'published_at':None,'footnotes':footnotes,'vintage':'current_at_observation','url':SOURCES[MACRO][0]});count+=1
        if not count:raise ValueError('missing series data')
    if seen!=set(SERIES):raise ValueError('incomplete macro response')
    return out


def parse_fed(raw,now):
    # No DTD, entities, HTML evaluation, article downloads or external links.
    if re.search(br'<!\s*(DOCTYPE|ENTITY)',raw,re.I) or b'\x00' in raw:raise ValueError('unsafe XML')
    root=ET.fromstring(raw)
    if root.tag!='rss' or root.find('channel') is None:raise ValueError('not RSS')
    out=[]
    for item in root.findall('./channel/item'):
        link=safe_link(item.findtext('link'),'www.federalreserve.gov');title=text(item.findtext('title'),512)
        dt=parsedate_to_datetime(item.findtext('pubDate'))
        if dt.tzinfo is None:raise ValueError('publication timezone missing')
        at=stamp(int(dt.timestamp()))
        if at>now:raise ValueError('future publication')
        out.append({'key':link,'title':title,'event_at':at,'published_at':at,'url':link,'kind':'official_release_reference'})
    if not out:raise ValueError('empty monetary feed')
    return out


PARSERS={CALENDAR:parse_calendar,MACRO:parse_macro,FED:parse_fed}

class EconomicMemory:
    def __init__(self,directory):
        self.root=Path(directory)/'economic-memory';self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.root.is_symlink() or (os.name=='posix' and self.root.stat().st_mode&0o077):raise PermissionError('economic memory must be private')
        self.db=self.root/'evidence.sqlite3'
        if self.db.is_symlink():raise PermissionError('unsafe evidence database')
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,source TEXT NOT NULL,fixture INTEGER NOT NULL,observed_at INTEGER NOT NULL,sha256 TEXT NOT NULL,row_count INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS economic_snapshot_cutoff ON snapshots(source,fixture,observed_at,id);
CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,source TEXT NOT NULL,fixture INTEGER NOT NULL,record_key TEXT NOT NULL,observed_at INTEGER NOT NULL,content_sha256 TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(snapshot INTEGER NOT NULL REFERENCES snapshots(id),record_key TEXT NOT NULL,revision TEXT NOT NULL REFERENCES revisions(id),event_at INTEGER NOT NULL,PRIMARY KEY(snapshot,record_key));
CREATE INDEX IF NOT EXISTS economic_member_time ON members(snapshot,event_at,record_key);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,source TEXT NOT NULL,fixture INTEGER NOT NULL,attempted_at INTEGER NOT NULL,state TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS economic_attempt_cutoff ON attempts(source,fixture,attempted_at,id);''')
        if os.name=='posix':os.chmod(self.db,0o600)

    @contextmanager
    def tx(self,write=False):
        with closing(sqlite3.connect(self.db,timeout=10,isolation_level=None)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:yield db;db.commit()
            except BaseException:db.rollback();raise

    def _latest(self,db,source,fixture,as_of):
        return db.execute('SELECT * FROM snapshots WHERE source=? AND fixture=? AND observed_at<=? ORDER BY observed_at DESC,id DESC LIMIT 1',(source,int(fixture),as_of)).fetchone()

    def ingest(self,source,raw,*,observed_at=None,fixture=False):
        now=stamp(int(time.time()) if observed_at is None else observed_at)
        if source not in SOURCES or not isinstance(raw,bytes) or not 0<len(raw)<=MAX_BYTES:raise ValueError('invalid/bounded evidence source')
        rows=PARSERS[source](raw,now)
        if not 0<len(rows)<=MAX_ROWS or len({r['key'] for r in rows})!=len(rows):raise ValueError('invalid or duplicate evidence identity')
        with self.tx(True) as db:
            prev=self._latest(db,source,fixture,MAX_TIME-1)
            if prev and now<prev['observed_at']:raise ValueError('nonmonotonic observation')
            if prev and now==prev['observed_at'] and prev['sha256']!=digest(raw):raise ValueError('ambiguous same-second revision')
            if sum(p.stat().st_size for p in self.root.glob('evidence.sqlite3*'))>=MAX_DB:raise MemoryUnavailable('economic memory storage watermark')
            # Store one complete snapshot membership, referring to immutable
            # revision records; unchanged repeats reuse the prior revision ID.
            if prev and prev['sha256']==digest(raw):
                db.execute('INSERT INTO attempts(source,fixture,attempted_at,state) VALUES(?,?,?,?)',(source,int(fixture),now,'ok'))
                return {'snapshot':prev['id'],'rows':prev['row_count'],'new_revisions':0,'repeated':True}
            cur=db.execute('INSERT INTO snapshots(source,fixture,observed_at,sha256,row_count) VALUES(?,?,?,?,?)',(source,int(fixture),now,digest(raw),len(rows))).lastrowid
            prior={} if prev is None else {r['record_key']:r for r in db.execute('SELECT m.record_key,r.id,r.content_sha256 FROM members m JOIN revisions r ON r.id=m.revision WHERE m.snapshot=?',(prev['id'],))}
            new=0
            for r in rows:
                body=canonical(r);sha=digest(body.encode());old=prior.get(r['key'])
                if old and old['content_sha256']==sha:rid=old['id']
                else:
                    rid=digest(canonical([source,int(fixture),cur,r['key'],sha]).encode());new+=1
                    db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?)',(rid,source,int(fixture),r['key'],now,sha,body))
                db.execute('INSERT INTO members VALUES(?,?,?,?)',(cur,r['key'],rid,r['event_at']))
            db.execute('INSERT INTO attempts(source,fixture,attempted_at,state) VALUES(?,?,?,?)',(source,int(fixture),now,'ok'))
            return {'snapshot':cur,'rows':len(rows),'new_revisions':new,'repeated':False}

    def _failure(self,source,now):
        with self.tx(True) as db:db.execute('INSERT INTO attempts(source,fixture,attempted_at,state) VALUES(?,0,?,?)',(source,now,'unavailable'))

    def collect(self,source,*,client=None,clock=time.time):
        if source not in SOURCES:raise ValueError('unsupported source')
        url,interval,*_=SOURCES[source]
        with exclusive_job(self.root,'economic-'+source):
            now=stamp(int(clock()))
            with self.tx() as db:
                last=db.execute('SELECT max(attempted_at) FROM attempts WHERE source=? AND fixture=0',(source,)).fetchone()[0]
            if last is not None and now-last<interval:return {'state':'rate_limited'}
            # Record start before network I/O; crashes cannot bypass pacing.
            self._failure(source,now)
            own=client is None
            if own:client=httpx.Client(timeout=httpx.Timeout(20,connect=8),follow_redirects=False,trust_env=False,headers={'User-Agent':'TradeBot-public-economic-research/1.0','Accept-Encoding':'identity'})
            try:
                args={};method='GET'
                if source==MACRO:
                    year=datetime.fromtimestamp(now,timezone.utc).year;method='POST';args={'json':{'seriesid':list(SERIES),'startyear':str(year-9),'endyear':str(year)}}
                with client.stream(method,url,**args) as response:
                    if response.status_code!=200 or response.url!=httpx.URL(url):raise ValueError('official feed unavailable/redirected')
                    if response.headers.get('Content-Encoding','identity').lower()!='identity':raise ValueError('unexpected feed encoding')
                    buf=bytearray()
                    for chunk in response.iter_raw():
                        if len(buf)+len(chunk)>MAX_BYTES:raise ValueError('feed exceeds byte limit')
                        buf.extend(chunk)
                result=self.ingest(source,bytes(buf),observed_at=stamp(int(clock())))
                return {'state':'observed',**result}
            finally:
                if own:client.close()

    def view(self,source,*,as_of=None,start=1,end=MAX_TIME,limit=MAX_QUERY,offset=0,fixture=False):
        now=stamp(int(time.time()) if as_of is None else as_of)
        if source not in SOURCES or not 0<start<end<=MAX_TIME or type(limit)is not int or not 1<=limit<=MAX_QUERY or type(offset)is not int or not 0<=offset<=MAX_ROWS:raise ValueError('invalid evidence query')
        url,_,ttl,name,terms=SOURCES[source]
        with self.tx() as db:
            shot=self._latest(db,source,fixture,now)
            attempt=db.execute('SELECT * FROM attempts WHERE source=? AND fixture=? AND attempted_at<=? ORDER BY attempted_at DESC,id DESC LIMIT 1',(source,int(fixture),now)).fetchone()
            success=db.execute("SELECT max(attempted_at) FROM attempts WHERE source=? AND fixture=? AND attempted_at<=? AND state='ok'",(source,int(fixture),now)).fetchone()[0]
            state='not_connected' if attempt is None else 'unavailable' if attempt['state']!='ok' or shot is None else 'stale' if now-success>ttl else 'observed'
            out=[];total=0
            if shot:
                total=db.execute('SELECT count(*) FROM members WHERE snapshot=? AND event_at>=? AND event_at<?',(shot['id'],start,end)).fetchone()[0]
                for r in db.execute('SELECT r.*,m.event_at FROM members m JOIN revisions r ON r.id=m.revision WHERE m.snapshot=? AND m.event_at>=? AND m.event_at<? ORDER BY m.event_at, m.record_key LIMIT ? OFFSET ?',(shot['id'],start,end,limit,offset)):
                    if digest(r['body'].encode())!=r['content_sha256']:raise MemoryUnavailable('evidence revision checksum mismatch')
                    out.append(json.loads(r['body'])|{'evidence_id':r['id'],'observed_at':r['observed_at'],'ingested_at':r['observed_at'],'content_sha256':r['content_sha256'],'source':name,'reliability':'official_primary','data_use':terms})
            return {'state':state,'source':name,'source_url':url,'as_of':now,'last_success':success,'snapshot':shot['id'] if shot else None,'snapshot_sha256':shot['sha256'] if shot else None,'total':total,'next_offset':offset+len(out) if offset+len(out)<total else None,'rows':out}

    def evidence(self,evidence_id,*,as_of=None):
        now=stamp(int(time.time()) if as_of is None else as_of)
        if not isinstance(evidence_id,str) or not re.fullmatch(r'[0-9a-f]{64}',evidence_id):raise ValueError('invalid evidence ID')
        with self.tx() as db:
            r=db.execute('SELECT * FROM revisions WHERE id=? AND fixture=0 AND observed_at<=?',(evidence_id,now)).fetchone()
            if r is None:return None
            if digest(r['body'].encode())!=r['content_sha256']:raise MemoryUnavailable('evidence revision checksum mismatch')
            _,_,_,name,terms=SOURCES[r['source']]
            return json.loads(r['body'])|{'evidence_id':r['id'],'observed_at':r['observed_at'],'ingested_at':r['observed_at'],'content_sha256':r['content_sha256'],'source':name,'reliability':'official_primary','data_use':terms}

    def calendar(self,now=None,offset=0):
        now=stamp(int(time.time()) if now is None else now)
        return self.view(CALENDAR,as_of=now,start=max(1,now-86400),end=min(MAX_TIME,now+90*86400),limit=100,offset=offset)
