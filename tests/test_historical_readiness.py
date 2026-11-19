import tempfile
import time
import unittest
from pathlib import Path
from market_memory import MarketMemory,sha256_file
from economic_memory import EconomicMemory
from historical_readiness import historical_report,HOURS,STEP
from market_gap import GapBridge
from tradebot import create_app
from fastapi.testclient import TestClient

PAIR='BTC/USD'
TOKEN='historical-readiness-test-'*3
AUTH={'Authorization':'Bearer '+TOKEN}

class HistoricalReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.m=MarketMemory(self.root);self.e=EconomicMemory(self.root)
        self.now=int(time.time());self.end=self.now//STEP*STEP
    def archive(self,stamps,price='100',fixture=False):
        path=self.root/'XBTUSD_60.csv'
        path.write_text(''.join(f'{s},{price},102,99,{price},2,1\n' for s in stamps))
        return self.m.import_archive(path,PAIR,60,sha256_file(path),fixture=fixture,now=self.now)
    def report(self,**kwargs):
        return historical_report(self.m,self.e,PAIR,as_of=self.now,**kwargs)
    def test_empty_history_and_fixture_not_counted(self):
        self.archive([self.end-STEP],fixture=True)
        report=self.report()
        self.assertEqual(report['classification_hours'],{'no_observed_history':HOURS})
        self.assertEqual(report['coverage'],0);self.assertEqual(report['missing'],HOURS)
        self.assertEqual(report['ranges'][0]['recovery'],'independent_anchors_missing')
        self.assertFalse(report['training_ready']);self.assertFalse(report['paper_ready'])
        self.assertEqual((report['vintage_coverage']['saved'],report['vintage_coverage']['missing']),(0,360))
        self.assertTrue(all(len(s['missing_preview'])==6 for s in report['vintage_coverage']['series']))
    def test_classification_conflict_and_retention(self):
        a=self.end-10*STEP
        src=self.archive([a,a+2*STEP,a+3*STEP])
        self.archive([a+3*STEP],price='101')
        result=self.report()
        self.assertEqual((result['coverage'],result['conflicts'],result['missing']),(2,1,HOURS-2))
        self.assertEqual(sum(result['classification_hours'].values()),result['missing'])
        self.assertEqual(result['classification_hours']['inside_observed_archive_span'],1)
        self.assertEqual(result['classification_hours']['conflicting_observations'],1)
        self.assertEqual(result['archive_retention']['integrity'],{'verified':2})
        gap=next(x for x in result['ranges'] if x['reason']=='inside_observed_archive_span')
        self.assertEqual(gap['recovery'],'bounded_recovery_possible')
        (self.m.root/(src['id']+'.csv')).write_bytes(b'corrupted')
        self.assertEqual(self.report()['archive_retention']['integrity']['checksum_mismatch'],1)
    def test_pagination_never_combines_changed_report(self):
        self.archive([self.end-i*STEP for i in range(150,0,-2)])
        first=self.report()
        self.assertEqual(len(first['ranges']),50)
        self.assertEqual(first['next_offset'],50)
        second=self.report(offset=50,report_id=first['report_id'])
        self.assertEqual(second['report_id'],first['report_id'])
        self.assertGreater(second['ranges'][0]['start'],first['ranges'][-1]['start'])
        self.archive([self.end-3*STEP])
        with self.assertRaises(ValueError):self.report(offset=50,report_id=first['report_id'])
    def test_checkpoint_does_not_resolve_a_gap(self):
        first=self.end-4*STEP;last=first+2*STEP
        self.archive([first,last])
        GapBridge(self.m)
        with self.m.tx() as db:
            db.execute("INSERT INTO gap_jobs(id,pair,start_ts,end_ts,first_digest,last_digest,cursor,pages,state,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ('checkpoint',PAIR,first+STEP,last,'a','b','1',8,'recovering',self.now))
        report=self.report()
        gap=next(x for x in report['ranges'] if x['start']==first+STEP)
        self.assertEqual((gap['recovery'],gap['pages']),('checkpoint_saved',8))
        self.assertEqual(report['coverage'],2)
        self.assertFalse(report['training_ready'])
    def test_contiguous_recovery_spans_reporting_boundary(self):
        first=self.end-722*STEP;last=self.end-719*STEP
        self.archive([first])
        self.m.record_recent(PAIR,60,{'exchange':'kraken','product':'spot','pair':PAIR,'interval_minutes':60,
            'observed_at':last+STEP,'candles':[[last*1000,'100','102','99','100','2',1]]},now=self.now)
        report=self.report()
        gaps=[g for g in report['ranges'] if first<g['start']<last]
        self.assertEqual(len(gaps),2)
        self.assertTrue(all((g['recovery_start'],g['recovery_end'],g['recovery'])==(first+STEP,last,'bounded_recovery_possible') for g in gaps))
    def test_endpoint_auth_and_selected_scope(self):
        with TestClient(create_app(self.root,TOKEN),base_url='http://localhost') as c:
            route='/api/markets/BTC/readiness'
            self.assertEqual(c.get(route).status_code,401)
            self.assertEqual(c.get('/api/markets/BOGUS/readiness',headers=AUTH).status_code,422)
            response=c.get(route,headers=AUTH)
            self.assertEqual(response.status_code,200,response.text)
            self.assertFalse(response.json()['training_ready'])
            self.assertEqual(c.get('/api/economic/vintage-coverage').status_code,401)
            vintage=c.get('/api/economic/vintage-coverage',headers=AUTH)
            self.assertEqual(vintage.status_code,200,vintage.text)
            v=vintage.json();self.assertEqual(v['expected'],360);self.assertEqual(len(v['rows']),60)
            self.assertEqual(c.get(route+'?offset=50',headers=AUTH).status_code,409)
            self.assertFalse(c.get('/api/status',headers=AUTH).json()['live_available'])

    def test_vintage_coverage_literal_route_precedes_dynamic_source(self):
        with TestClient(create_app(self.root,TOKEN),base_url='http://localhost') as c:
            route='/api/economic/vintage-coverage'
            self.assertEqual(c.get(route).status_code,401)
            self.assertEqual(c.get(route,headers={'Authorization':'Bearer invalid'}).status_code,401)
            keys=[]
            for offset in range(0,360,60):
                response=c.get(route,headers=AUTH,params={'as_of':self.now,'offset':offset})
                self.assertEqual(response.status_code,200,response.text)
                v=response.json()
                self.assertEqual((v['expected'],v['saved'],v['missing']),(360,0,360))
                self.assertEqual(v['as_of'],self.now)
                self.assertEqual(len(v['rows']),60)
                self.assertEqual(v['next_offset'],offset+60 if offset<300 else None)
                self.assertFalse(v['training_ready'])
                keys.extend((row['series'],row['vintage']) for row in v['rows'])
            self.assertEqual((len(keys),len(set(keys))),(360,360))
            for params in ({'offset':361},{'offset':-1},{'as_of':0},{'as_of':int(time.time())+3600}):
                response=c.get(route,headers=AUTH,params=params)
                self.assertEqual(response.status_code,422,response.text)
                self.assertEqual(response.json()['detail'],'Invalid vintage coverage query')
            for source in ('statistics','monetary','extended'):
                response=c.get('/api/economic/'+source,headers=AUTH)
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('state',response.json())
                history=c.get('/api/economic/'+source+'/history',headers=AUTH)
                self.assertEqual(history.status_code,200,history.text)
                self.assertIn('rows',history.json())
            self.assertEqual(c.get('/api/economic/invalid',headers=AUTH).status_code,422)
