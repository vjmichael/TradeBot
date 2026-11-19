import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import market_memory as mm
from tradebot import create_app

T=1704067200 # Monday 2024-01-01 00 UTC
NOW=T+10*86400
TOKEN='test-market-memory-token-'*2
AUTH={'Authorization':'Bearer '+TOKEN}

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.m=mm.MarketMemory(self.root)
    def archive(self, rows, name='XBTUSD_60.csv'):
        p=self.root/name;p.write_text(''.join(','.join(map(str,r))+'\n' for r in rows))
        return p,mm.sha256_file(p)
    def row(self,t,close='101'):
        return (t,'100','103','99',close,'2.00',4)
    def view(self,as_of=NOW,fixture=False):
        return self.m.query('BTC/USD',60,T,T+4*3600,as_of,fixture=fixture)
    def recent(self,t=T+3*3600, close=101):
        return {'exchange':'kraken','product':'spot','pair':'BTC/USD','interval_minutes':60,'observed_at':t+3600,'candles':[[t*1000,100,103,99,close,2]]}
    def test_import_resume_visibility_and_idempotence(self):
        p,d=self.archive([self.row(T),self.row(T+3600),self.row(T+3*3600)])
        with patch.object(mm,'BATCH',2):
            first=self.m.import_archive(p,'BTC/USD',60,d,fixture=True,now=NOW,max_batches=1)
            self.assertEqual((first['state'],first['cursor']),('importing',2))
            self.assertEqual(self.view(fixture=True)['coverage'],0)
            self.m=mm.MarketMemory(self.root)
            final=self.m.import_archive(p,'BTC/USD',60,d,fixture=True,now=NOW+10)
        self.assertEqual((final['state'],final['cursor']),('complete',3))
        self.assertEqual(self.view(NOW+9,fixture=True)['coverage'],0)
        v=self.view(NOW+10,fixture=True)
        self.assertEqual((v['coverage'],v['missing'],v['conflicts']),(3,1,0))
        self.assertEqual(v['rows'][0]['v'],'2')
        self.assertEqual(v['rows'][0]['available_at'],NOW+10)
        self.assertEqual(v['rows'][0]['source_ids'],[final['id']])
        self.assertEqual(self.view(NOW+10)['coverage'],0)
        self.assertEqual(self.m.import_archive(p,'BTC/USD',60,d,fixture=True,now=NOW+20), final)
    def test_invalid_source_hidden_and_checksum_identity(self):
        p,d=self.archive([self.row(T),self.row(T+3600),self.row(T+2*3600,'500')])
        with self.assertRaises(ValueError):self.m.import_archive(p,'ETH/USD',60,d,fixture=True,now=NOW)
        with self.assertRaises(ValueError):self.m.import_archive(p,'BTC/USD',60,'f'*64,fixture=True,now=NOW)
        with patch.object(mm,'BATCH',2),self.assertRaises(ValueError):self.m.import_archive(p,'BTC/USD',60,d,fixture=True,now=NOW)
        self.assertEqual(self.view(fixture=True)['coverage'],0)
        with self.assertRaises(mm.MemoryUnavailable):self.m.import_archive(p,'BTC/USD',60,d,fixture=True,now=NOW)
    def test_revision_conflict_asof_and_repeated_poll(self):
        # Synthetic transport records within a private test DB, never deployed.
        r=self.recent();self.assertEqual(self.m.record_recent('BTC/USD',60,r,now=r['observed_at']),1)
        self.assertEqual(self.m.record_recent('BTC/USD',60,r,now=r['observed_at']),0)
        r2=self.recent(close=102);r2['observed_at']+=10
        self.assertEqual(self.m.record_recent('BTC/USD',60,r2,now=r2['observed_at']),1)
        old=self.view(T+4*3600);self.assertEqual(old['coverage'],1)
        new=self.view();self.assertEqual((new['coverage'],new['conflicts'],new['missing']),(0,1,4))
    def test_poll_health_and_bad_payload(self):
        r=self.recent()
        with patch.object(mm.time,'time',return_value=r['observed_at']):
            self.assertEqual(self.m.poll('BTC/USD',60,fetch=lambda *_:r),1)
        with self.assertRaises(ValueError): self.m.record_recent('ETH/USD',60,r)
        r['candles'][0][0]+=1
        with self.assertRaises(ValueError): self.m.record_recent('BTC/USD',60,r,now=r['observed_at'])
        with patch.object(mm.time,'time',return_value=NOW-1),self.assertRaises(RuntimeError):
            self.m.poll('BTC/USD',60,fetch=lambda *_: (_ for _ in ()).throw(RuntimeError('offline')))
        self.assertEqual(self.view()['collector']['state'],'unavailable')
        self.assertEqual(self.view()['coverage'],1)
    def test_profile_and_dst(self):
        p,d=self.archive([self.row(T),self.row(T+3600),self.row(T+3*3600)])
        self.m.import_archive(p,'BTC/USD',60,d,fixture=True,now=NOW)
        v=mm.session_profile(self.view(fixture=True),'UTC');self.assertEqual(len(v['bins']),168);self.assertEqual(len(v['hourly']),24)
        self.assertEqual(v['bins'][0]['samples'],1);self.assertEqual(v['bins'][2]['expected'],1);self.assertIsNone(v['bins'][2]['mean_range_pct']);self.assertEqual(v['bins'][0]['mean_range_pct'],4)
        self.assertIsNone(v['spread']);self.assertIsNone(v['realized_subhour_volatility'])
        self.assertEqual(mm.session_profile(self.view(fixture=True),'Asia/Tokyo')['bins'][9]['samples'],1)
        # November US fall-back repeats local Sunday 01:00; count both UTC hours.
        start=int(datetime(2024,11,3,5,tzinfo=timezone.utc).timestamp())
        span={'interval_minutes':60,'start':start,'end':start+3*3600,'rows':[]}
        self.assertEqual(mm.session_profile(span,'America/New_York')['bins'][6*24+1]['expected'],2)
    def test_validation_and_query_bounds(self):
        for row in [self.row(T+1),self.row(NOW),self.row(T,'nan'),self.row(T,'0'),(T,'100','103','99','101','-1',4)]:
            with self.assertRaises(ValueError):mm.normalize(row,60,NOW)
        for args in [('BTC/EUR',60,T,T+3600,NOW),('BTC/USD',1,T,T+60*(mm.MAX_QUERY_ROWS+1),NOW),('BTC/USD',60,T+1,T+3600,NOW)]:
            with self.assertRaises(ValueError):self.m.query(*args)
        p,d=self.archive([self.row(T+3600),self.row(T)])
        with self.assertRaises(ValueError):self.m.import_archive(p,'BTC/USD',60,d,fixture=True,now=NOW)
    def test_profile_api_requires_auth_and_excludes_fixture(self):
        end=int(mm.time.time())//3600*3600
        p,d=self.archive([self.row(end-3600)])
        self.m.import_archive(p,'BTC/USD',60,d,fixture=True,now=end)
        with TestClient(create_app(self.root,TOKEN),base_url='http://localhost') as c:
            url='/api/markets/BTC/session-profile?days=30&zone=Asia/Tokyo'
            self.assertEqual(c.get(url).status_code,401)
            profile=c.get(url,headers=AUTH).json()
            self.assertEqual(profile['state'],'awaiting_history');self.assertEqual(profile['coverage'],0)
            self.assertEqual(c.get('/api/markets/BTC/session-profile?days=999999',headers=AUTH).status_code,422)
            self.assertEqual(c.get('/api/markets/XYZ/session-profile',headers=AUTH).status_code,422)
            self.assertEqual(c.get('/api/markets/BTC/session-profile?zone=bad',headers=AUTH).status_code,422)
            self.assertFalse(c.get('/api/status',headers=AUTH).json()['live_available'])
    def test_provider_free_instance_plan(self):
        with TestClient(create_app(self.root,TOKEN,cap=10000),base_url='http://localhost') as c:
            plan={'name':'Numerical desk','pair':'BTC/USD','virtual_cents':500,'provider':'none','strategy_family':'numerical-baseline'}
            r=c.post('/api/instances',headers=AUTH,json=plan)
            self.assertEqual(r.status_code,201);self.assertEqual(r.json()['provider'],'none')
            for fields in ({'strategy_family':'research-assisted'},{'model':'grok-test'},{'monthly_budget_cents':1}):
                self.assertEqual(c.post('/api/instances',headers=AUTH,json=plan|{'name':'Bad plan'}|fields).status_code,422)
            self.assertEqual(c.get('/api/status',headers=AUTH).json()['allocated_cents'],500)
    def test_export_native_if_available(self):
        try: import duckdb
        except ImportError:self.skipTest('DuckDB not installed; native Parquet round-trip required separately')
        self.m.record_recent('BTC/USD',60,self.recent(),now=NOW)
        result=mm.export_parquet(self.m,'BTC/USD',60,T,T+4*3600,NOW)
        path=self.m.root/result['path'];self.assertEqual(mm.sha256_file(path),result['sha256']);self.assertEqual(result['row_count'],1)
        self.assertEqual(mm.export_parquet(self.m,'BTC/USD',60,T,T+4*3600,NOW),result)
        path.write_bytes(b'corrupted')
        with self.assertRaises(mm.MemoryUnavailable):mm.export_parquet(self.m,'BTC/USD',60,T,T+4*3600,NOW)
