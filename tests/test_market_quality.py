import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from market_memory import MarketMemory, MemoryUnavailable
from market_quality import QualityMemory, parse_depth, measure, realized_hours
from market_stream import exact_closed
from fastapi.testclient import TestClient
from tradebot import create_app

T=1704067200
NOW=T+3600+30
PAIR='BTC/USD'
TOKEN='synthetic-quality-access-'*3


def book(q='20',ask='101',bid='99'):
    return {'error':[],'result':{'XXBTZUSD':{'asks':[[ask,q,T]],'bids':[[bid,q,T]]}}}


def fetch(pair):return book(),T+30,'a'*64


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.m=MarketMemory(self.root);self.q=QualityMemory(self.m)
    def test_book_math_and_fee_scenario(self):
        b=parse_depth(PAIR,book(),NOW);m=measure(b,40)
        self.assertAlmostEqual(m['spread_bps'],200);self.assertAlmostEqual(m['bid_depth_1pct_usd'],1980)
        self.assertAlmostEqual(m['scenarios'][0]['book_cost_bps'],200);self.assertAlmostEqual(m['scenarios'][0]['fee_inclusive_bps'],280)
        self.assertIsNone(measure(b)['scenarios'][0]['fee_inclusive_bps'])
    def test_insufficient_depth(self):
        m=measure(parse_depth(PAIR,book('0.01'),NOW),0)
        self.assertTrue(all(s['state']=='insufficient_displayed_depth' and s['book_cost_bps'] is None for s in m['scenarios']))
    def test_multilevel_cost(self):
        raw=book('0.5');raw['result']['XXBTZUSD']['asks'].append(['102','10',T]);raw['result']['XXBTZUSD']['bids'].append(['98','10',T]);m=measure(parse_depth(PAIR,raw,NOW))
        self.assertAlmostEqual(m['scenarios'][0]['book_cost_bps'],300)
    def test_reject_bad_books(self):
        bad=[book(ask='98'),book(q='0'),book(q='-1'),book(ask='NaN'),{'error':[],'result':{'ETHUSD':{}}}]
        x=book();x['result']['XXBTZUSD']['asks'].append(['100','1',T]);bad.append(x)
        for b in bad:
            with self.subTest(b=b),self.assertRaises(ValueError):parse_depth(PAIR,b,NOW)
    def test_durable_pacing_cutoff_and_staleness(self):
        self.assertEqual(self.q.sample(PAIR,fetch=fetch,clock=lambda:T+30),'observed')
        q=QualityMemory(MarketMemory(self.root));self.assertEqual(q.sample(PAIR,fetch=lambda _:self.fail('paced'),clock=lambda:T+40),'paced')
        before=q.profile(PAIR,T-3600,T,T+20);self.assertIsNone(before['latest'])
        after=q.profile(PAIR,T,T+3600,NOW);self.assertEqual(after['book_samples'],1);self.assertEqual(after['latest']['state'],'stale')
        self.assertEqual(sum(b['books'] for b in after['bins']),1);self.assertEqual(after['rv_hours'],0)
    def test_failure_and_capacity(self):
        self.assertEqual(self.q.sample(PAIR,fetch=fetch,clock=lambda:T+30),'observed')
        with self.assertRaises(RuntimeError): self.q.sample(PAIR,fetch=lambda _:(_ for _ in ()).throw(RuntimeError('down')),clock=lambda:T+330)
        out=self.q.profile(PAIR,T-3600,T,T+331);self.assertEqual(out['source_state'],'unavailable');self.assertEqual(out['latest']['state'],'stale')
        with patch('market_quality.MAX_SAMPLES',2),self.assertRaises(MemoryUnavailable):self.q.sample(PAIR,fetch=lambda _:self.fail('capacity'),clock=lambda:T+630)
    def test_integrity(self):
        self.q.sample(PAIR,fetch=fetch,clock=lambda:T+30)
        with self.m.tx() as db:db.execute("UPDATE quality_books SET body='{}'")
        with self.assertRaises(MemoryUnavailable):self.q.profile(PAIR,T,T+3600,NOW)
    def test_realized_volatility_and_missing(self):
        rows=[{'ts':T+i*300,'c':str(100*1.001**(i+1)),'v':'2'} for i in range(-1,12)]
        view={'start':T-300,'end':T+3600,'interval_minutes':5,'rows':rows}
        out=realized_hours(view);self.assertEqual(len(out),1);self.assertAlmostEqual(out[0]['rv_pct'],100*math.sqrt(12)*math.log(1.001));self.assertEqual(out[0]['base_volume'],24)
        self.assertEqual(realized_hours(view|{'rows':rows[:4]+rows[5:]}),[])
    def test_exact_five_minute_excludes_forming(self):
        raw=[[T-300,'100','100','100','100','100','2.000000000001',2],[T,'100','100','100','100','100','1',1]]
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'error':[],'result':{'XXBTZUSD':raw,'last':T}}))) as c:
            a=exact_closed(PAIR,5,client=c,now=T+30)
        self.assertEqual(a['interval_minutes'],5);self.assertEqual(len(a['candles']),1);self.assertEqual(a['candles'][0][5],'2.000000000001')
    def test_profile_dst_and_api(self):
        # Fall-back local hour is counted twice, not dropped or fabricated.
        a=1730610000 # 2024-11-03 05 UTC
        out=self.q.profile(PAIR,a,a+7200,a+7201,'America/New_York')
        self.assertEqual(out['bins'][6*24+1]['expected_hours'],2)
        with TestClient(create_app(self.root,TOKEN),base_url='http://localhost') as c:
            path='/api/markets/BTC/quality';auth={'Authorization':'Bearer '+TOKEN}
            self.assertEqual(c.get(path).status_code,401);r=c.get(path,headers=auth);self.assertEqual(r.status_code,200);self.assertIsNone(r.json()['latest']);self.assertEqual(r.json()['rv_hours'],0)
            self.assertEqual(c.get(path+'?fee_bps=101',headers=auth).status_code,422)
            self.assertEqual(c.post(path,headers=auth).status_code,405)
