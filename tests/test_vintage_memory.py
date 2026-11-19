import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from pathlib import Path
import httpx
from vintage_memory import VintageMemory,parse,cutoff,URL,coverage,ensure_plan,targets
from market_memory import MemoryUnavailable
V='2020-05-01'
RAW=b'observation_date,UNRATE_20200501\n2020-02-01,3.5\n2020-03-01,4.4\n'
class VintageTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.m=VintageMemory(self.root)
    def test_qualified_identity_and_no_lookahead(self):
        calls=[]
        def fetch(req):
            calls.append(req);self.assertEqual(req.url,httpx.URL(URL,params={'id':'UNRATE','vintage_date':V}))
            return httpx.Response(200,stream=httpx.ByteStream(RAW))
        with httpx.Client(transport=httpx.MockTransport(fetch)) as client:
            self.assertEqual(self.m.collect('UNRATE',V,client=client)['state'],'observed')
            self.assertEqual(self.m.collect('UNRATE',V,client=client)['state'],'already_observed')
        self.assertEqual(len(calls),1)
        v=VintageMemory(self.root).view('UNRATE',V,replay_at=cutoff(V))
        self.assertEqual(len(v['rows']),2)
        self.assertFalse(self.m.view('UNRATE',V,replay_at=cutoff(V)-1)['rows'])
        self.assertFalse(self.m.view('UNRATE',V,retrieved_by=v['retrieved_at']-1)['rows'])
        with self.m.memory.tx(True) as db:db.execute("UPDATE alfred_vintages SET body=X'31'")
        with self.assertRaises(MemoryUnavailable):self.m.view('UNRATE',V)
    def test_wrong_header_future_row_duplicate_and_invalid_value(self):
        for raw in (RAW.replace(b'20200501',b'20200601'),RAW.replace(b'2020-03-01',b'2020-06-01'),RAW.replace(b'2020-03-01',b'2020-02-01'),RAW.replace(b'4.4',b'101')):
            with self.assertRaises(ValueError):parse('UNRATE',V,raw)
    def test_failed_request_paced_across_reopen(self):
        now=int(time.time())
        with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(503))) as client:
            with self.assertRaises(MemoryUnavailable):self.m.collect('UNRATE',V,client=client,clock=lambda:now)
            self.assertEqual(VintageMemory(self.root).collect('PAYEMS',V,client=client,clock=lambda:now+1)['state'],'paced')
        self.assertEqual(self.m.status()['snapshots'],0)

    def test_frozen_360_targets_resume_and_asof_counts(self):
        now=int(datetime(2026,10,15,tzinfo=timezone.utc).timestamp())
        def clock():return now
        def sleep(seconds):
            nonlocal now
            now+=seconds
        def fetch(req):
            series=req.url.params['id'];vintage=req.url.params['vintage_date']
            return httpx.Response(200,stream=httpx.ByteStream(f'observation_date,{series}_{vintage.replace("-","")}\n{vintage},3.5\n'.encode()))
        first=now
        with httpx.Client(transport=httpx.MockTransport(fetch)) as client:
            self.assertEqual(self.m.collect_batch(client=client,clock=clock,sleep=sleep)['observed'],6)
            self.assertEqual(coverage(self.m.memory,as_of=first-1)['saved'],0)
            self.assertEqual(coverage(self.m.memory,as_of=now)['saved'],6)
            self.assertTrue(all(len(s['missing_preview'])==6 for s in coverage(self.m.memory,as_of=now)['series']))
            now+=32*86400 # month boundary does not replace plan
            self.assertEqual(VintageMemory(self.root).collect_batch(client=client,clock=clock,sleep=sleep)['observed'],6)
            self.assertEqual(coverage(self.m.memory,as_of=now)['anchor_month'],'2026-10-01')
            for _ in range(58):
                now+=3600
                VintageMemory(self.root).collect_batch(client=client,clock=clock,sleep=sleep)
            result=coverage(self.m.memory,as_of=now,detail=True)
            self.assertEqual((result['saved'],result['missing'],result['expected']),(360,0,360))
            self.assertTrue(all(s['saved']==120 for s in result['series']))
            self.assertEqual(self.m.collect_batch(client=client,clock=clock,sleep=sleep)['state'],'complete')
            self.assertEqual(coverage(self.m.memory,as_of=now,offset=300,detail=True)['next_offset'],None)
    def test_throttle_and_budget_persist(self):
        now=int(time.time())
        def fetch(req):return httpx.Response(429,headers={'Retry-After':'1800'})
        with httpx.Client(transport=httpx.MockTransport(fetch)) as c:
            with self.assertRaises(MemoryUnavailable):self.m.collect('UNRATE',V,client=c,clock=lambda:now)
            self.assertEqual(VintageMemory(self.root).collect('PAYEMS',V,client=c,clock=lambda:now+1799)['state'],'cooldown')
        with patch('vintage_memory.HOUR_LIMIT',1):
            with httpx.Client(transport=httpx.MockTransport(lambda req:self.fail('budget should stop request'))) as c:
                self.assertEqual(self.m.collect('PAYEMS',V,client=c,clock=lambda:now+1800)['state'],'budget_exhausted')

    def test_supplemental_policy_preserves_core_target(self):
        from vintage_memory import coverage,ensure_plan,supplemental_targets
        now=int(time.time());anchor=ensure_plan(self.m.memory,now)
        series,vintage=supplemental_targets(anchor)[0]
        self.assertEqual(self.m.collect_supplemental()['state'],'core_incomplete')
        raw=f'observation_date,{series}_{vintage.replace("-","")}\n{vintage},3.5\n'.encode()
        with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,stream=httpx.ByteStream(raw)))) as c:
            self.assertEqual(self.m.collect(series,vintage,client=c)['state'],'observed')
        status=coverage(self.m.memory)
        self.assertEqual((status['expected'],status['saved'],status['supplemental']['saved']),(360,0,1))
        self.assertEqual(self.m.view(series,vintage)['state'],'observed')
