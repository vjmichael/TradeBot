"""Synthetic fault drills only; no network, account or model access."""
import json
import tempfile
import unittest
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
import economic_memory as e
from tradebot import create_app

T=int(datetime(2026,6,1,tzinfo=timezone.utc).timestamp())

def cal(date='20260604T083000',uid='test-1',title='Employment Situation',zone='America/New_York',status='CONFIRMED'):
    return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:'+uid+'\r\nDTSTART;TZID='+zone+':'+date+'\r\nSUMMARY:'+title+'\r\nSTATUS:'+status+'\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n').encode()

def macro(value='4.0'):
    return json.dumps({'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':s,'data':[{'year':'2026','period':'M04','value':value if s=='LNS14000000' else '100','footnotes':[{}]},{'year':'2026','period':'M03','value':'-','footnotes':[{'text':'Not available'}]}]} for s in e.SERIES]}}).encode()

def rss(title='Policy statement',url='https://www.federalreserve.gov/newsevents/pressreleases/test.htm',published='Fri, 29 May 2026 14:00:00 GMT'):
    return f'<rss version="2.0"><channel><item><title>{title}</title><link>{url}</link><pubDate>{published}</pubDate><description>Ignored article body</description></item></channel></rss>'.encode()

class EconomicTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name);self.m=e.EconomicMemory(self.path)
    def put(self,s,b,t=T,fixture=False):return self.m.ingest(s,b,observed_at=t,fixture=fixture)
    def test_calendar_dst_reschedule_and_asof(self):
        self.put(e.CALENDAR,cal());self.put(e.CALENDAR,cal(date='20260605T083000'),T+1)
        old=self.m.view(e.CALENDAR,as_of=T)['rows'][0];new=e.EconomicMemory(self.path).view(e.CALENDAR,as_of=T+1)['rows'][0]
        self.assertEqual(datetime.fromtimestamp(old['event_at'],timezone.utc).hour,12)
        self.assertEqual(new['event_at']-old['event_at'],86400);self.assertNotEqual(old['evidence_id'],new['evidence_id'])
        self.assertIsNone(new['forecast']);self.assertIsNone(new['actual']);self.assertEqual(new['observed_at'],T+1)
        self.assertFalse(self.m.view(e.CALENDAR,as_of=T-1)['rows']);self.assertIsNone(self.m.evidence(new['evidence_id'],as_of=T));self.assertEqual(self.m.evidence(old['evidence_id'],as_of=T+1)['event_at'],old['event_at'])
        winter=e.parse_calendar(cal(date='20261204T083000'),T)[0];self.assertEqual(datetime.fromtimestamp(winter['event_at'],timezone.utc).hour,13)
    def test_calendar_disappearance_not_cancellation_and_revision_reappearance(self):
        self.put(e.CALENDAR,cal());old=self.m.view(e.CALENDAR,as_of=T)['rows'][0];self.put(e.CALENDAR,cal(uid='another'),T+1)
        self.assertEqual([r['key'] for r in self.m.view(e.CALENDAR,as_of=T+1)['rows']],['another'])
        self.put(e.CALENDAR,cal(),T+2);new=self.m.view(e.CALENDAR,as_of=T+2)['rows'][0]
        self.assertNotEqual(old['evidence_id'],new['evidence_id']);self.assertEqual(new['observed_at'],T+2)
    def test_rolling_feed_history_retains_disappeared_records_at_observation_cut(self):
        self.put(e.CALENDAR,cal());self.put(e.CALENDAR,cal(uid='later'),T+1)
        current=self.m.view(e.CALENDAR,as_of=T+1)
        self.assertEqual([r['key'] for r in current['rows']],['later'])
        self.assertEqual(self.m.history(e.CALENDAR,as_of=T)['total'],1)
        history=self.m.history(e.CALENDAR,as_of=T+1,limit=1)
        self.assertEqual((history['total'],history['distinct_records'],history['next_offset']),(2,2,1))
        self.assertEqual(self.m.history(e.CALENDAR,as_of=T+1,offset=1)['rows'][0]['key'],'test-1')
        self.assertFalse(history['release_time_vintages_verified'])
        self.put(e.CALENDAR,cal(uid='fixture'),T+2,fixture=True)
        self.assertEqual(self.m.history(e.CALENDAR,as_of=T+2)['total'],2)
    def test_extended_series_are_separate_and_current_vintage(self):
        raw=json.dumps({'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':s,'data':[{'year':'2026','period':'M04','value':'100','footnotes':[]}]} for s in e.EXT_SERIES]}}).encode()
        self.put(e.MACRO_EXT,raw)
        rows=self.m.view(e.MACRO_EXT,as_of=T)['rows']
        self.assertEqual({r['series'] for r in rows},set(e.EXT_SERIES))
        self.assertTrue(all(r['vintage']=='current_at_observation' and r['published_at'] is None for r in rows))
        self.assertFalse(self.m.view(e.MACRO,as_of=T)['rows'])
        self.assertFalse(self.m.history(e.MACRO_EXT,as_of=T-1)['rows'])
        with self.assertRaises(ValueError):self.put(e.MACRO_EXT,macro(),T+1)
    def test_calendar_malformed_rejected_atomic(self):
        self.put(e.CALENDAR,cal())
        for b in (cal(zone='Mars/Nowhere'),cal(date='20261101T013000'),cal(date='20260308T023000'),cal().replace(b'END:VEVENT',b'RRULE:FREQ=DAILY\r\nEND:VEVENT'),cal()[:-20]):
            with self.subTest(b=b),self.assertRaises(ValueError):self.put(e.CALENDAR,b,T+1)
        self.assertEqual(len(self.m.view(e.CALENDAR,as_of=T+1)['rows']),1)
    def test_macro_revisions_and_unknown_no_historical_leakage(self):
        self.put(e.MACRO,macro());self.put(e.MACRO,macro('4.2'),T+1);self.put(e.MACRO,macro(),T+2)
        def row(t):return next(r for r in self.m.view(e.MACRO,as_of=t)['rows'] if r['series']=='LNS14000000' and r['period']=='2026-04')
        self.assertEqual([row(t)['value'] for t in (T,T+1,T+2)],['4','4.2','4']);self.assertEqual(len({row(t)['evidence_id'] for t in (T,T+1,T+2)}),3)
        self.assertEqual(row(T)['vintage'],'current_at_observation');self.assertIsNone(row(T)['published_at']);self.assertTrue(any(r['value'] is None for r in self.m.view(e.MACRO,as_of=T)['rows']));self.assertFalse(self.m.view(e.MACRO,as_of=T-1)['rows'])
    def test_unchanged_and_fixture_isolation(self):
        a=self.put(e.MACRO,macro(),fixture=True);self.assertFalse(self.m.view(e.MACRO,as_of=T)['rows'])
        b=self.put(e.MACRO,macro(),T+1,fixture=True);self.assertEqual(a['snapshot'],b['snapshot']);self.assertEqual(b['new_revisions'],0)
        r=self.m.view(e.MACRO,as_of=T+1,fixture=True);self.assertEqual(r['last_success'],T+1);self.assertTrue(all(x['observed_at']==T for x in r['rows']))
    def test_fed_publication_observation_and_untrusted_xml(self):
        self.put(e.FED,rss());r=self.m.view(e.FED,as_of=T)['rows'][0];self.assertLess(r['published_at'],r['observed_at']);self.assertNotIn('description',r)
        for b in (rss(url='https://evil.example/test'),rss(published='Tue, 02 Jun 2026 14:00:00 GMT'),b'<!DOCTYPE x [<!ENTITY a "x">]>'+rss()):
            with self.assertRaises(ValueError):self.put(e.FED,b,T+1)
        self.assertEqual(self.m.view(e.FED,as_of=T+1)['rows'][0]['evidence_id'],r['evidence_id'])
    def test_source_snapshot_bound_pagination_and_retroactive_reject(self):
        self.put(e.MACRO,macro());a=self.m.view(e.MACRO,as_of=T,limit=2);b=self.m.view(e.MACRO,as_of=T,limit=2,offset=a['next_offset']);self.assertEqual((a['total'],a['next_offset'],b['next_offset']),(6,2,4));self.assertFalse(set(r['key'] for r in a['rows'])&set(r['key'] for r in b['rows']))
        for t in (T-1,T):
            with self.assertRaises(ValueError):self.put(e.MACRO,macro('4.5'),t)
        with self.assertRaises(ValueError):self.m.view(e.MACRO,as_of=T,limit=501)
    def test_pacing_failure_and_stale_state(self):
        # Stream with actual HTTPX transport and fixed URL, no external I/O.
        seen=[]
        def ok(req):
            seen.append(req);self.assertEqual(str(req.url),e.SOURCES[e.MACRO][0]);self.assertEqual(req.method,'POST');self.assertNotIn('authorization',req.headers);payload=json.loads(req.content);self.assertNotIn('registrationkey',payload);self.assertEqual(payload['startyear'],'2017');return httpx.Response(200,stream=httpx.ByteStream(macro()))
        with httpx.Client(transport=httpx.MockTransport(ok)) as c:
            self.assertEqual(self.m.collect(e.MACRO,client=c,clock=lambda:T)['state'],'observed');self.assertEqual(self.m.collect(e.MACRO,client=c,clock=lambda:T+1)['state'],'rate_limited')
        self.assertEqual(len(seen),1);self.assertEqual(self.m.view(e.MACRO,as_of=T+49*3600)['state'],'stale')
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(503))) as c,self.assertRaises(ValueError):self.m.collect(e.MACRO,client=c,clock=lambda:T+21600)
        self.assertEqual(self.m.view(e.MACRO,as_of=T+21600)['state'],'unavailable');self.assertEqual(len(self.m.view(e.MACRO,as_of=T+21600)['rows']),6)
        self.assertEqual(self.m.view(e.MACRO,as_of=T)['state'],'observed')
    def test_reject_bad_source_and_quota_and_checksum(self):
        for data in (b'bad',macro()[:-2],macro('101'),b'x'*(e.MAX_BYTES+1)):
            with self.assertRaises((ValueError,KeyError)):self.put(e.MACRO,data)
        with patch.object(e,'MAX_DB',0),self.assertRaises(e.MemoryUnavailable):self.put(e.MACRO,macro())
        self.put(e.MACRO,macro())
        with self.m.tx(True) as db:db.execute("UPDATE revisions SET body='{}'")
        with self.assertRaises(e.MemoryUnavailable):self.m.view(e.MACRO,as_of=T)
    def test_authenticated_read_only_endpoints(self):
        token='x'*40;auth={'Authorization':'Bearer '+token};self.put(e.MACRO,macro());self.put(e.CALENDAR,cal(date='20261204T083000'))
        with TestClient(create_app(self.path,token),base_url='http://localhost') as c:
            self.assertEqual(c.get('/api/economic/statistics').status_code,401);self.assertEqual(c.post('/api/economic/statistics',headers=auth,json={}).status_code,405)
            self.assertEqual(c.get('/api/economic/statistics',headers=auth,params={'as_of':T-1}).json()['rows'],[])
            self.assertEqual(c.get('/api/economic/statistics',headers=auth,params={'as_of':T}).json()['total'],6)
            self.assertEqual(c.get('/api/economic/statistics/history').status_code,401)
            self.assertEqual(c.get('/api/economic/statistics/history',headers=auth,params={'as_of':T}).json()['total'],6)
            self.assertEqual(c.get('/api/economic/statistics',headers=auth,params={'offset':-1}).status_code,422)
            self.assertEqual(c.get('/api/calendar',headers=auth).status_code,200)
