"""Preparation, durable operator preferences, graphical maintenance and recovery."""
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from data_preparation import Preparation, _db
from data_scheduler import Schedule, HEARTBEAT_MAX
from data_retention import MIN_AGE, retire_books
from storage_management import status as maintenance_status
from data_lifecycle import backup
from gap_schedule import repair_due, recovery_progress
from market_gap import GapBridge, NS
from market_memory import MarketMemory, MemoryUnavailable
from market_quality import QualityMemory
from economic_memory import EconomicMemory
from tradebot import create_app

ROOT=Path(__file__).resolve().parents[1]
TOKEN='data-operations-test-token-0123456789'
AUTH={'Authorization':'Bearer '+TOKEN}
T=1704067200


def wait(predicate,seconds=20):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        v=predicate()
        if v:return v
        time.sleep(.05)
    raise AssertionError('operation did not reach required state')


@unittest.skipUnless(sys.platform.startswith('linux'),'Linux worker custody')
class DataOperationsTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.data=self.root/'data';self.data.mkdir(mode=0o700)
        self.memory=MarketMemory(self.data);EconomicMemory(self.data);QualityMemory(self.memory)
    def test_schedule_status_is_not_activation(self):
        s=Schedule(self.data);self.addCleanup(s.close)
        self.assertEqual(s.public_status(T)['service'],'stopped')
        s.enable(True,T)
        self.assertTrue(s.public_status(T)['enabled']);self.assertIsNone(s.controller.child)
        with s.lease():
            self.assertEqual(s.public_status(T)['service'],'not_confirmed')
            s.heartbeat(T);self.assertEqual(s.public_status(T)['service'],'running')
            self.assertEqual(s.public_status(T+HEARTBEAT_MAX+1)['service'],'not_confirmed')
        self.assertEqual(s.public_status(T)['service'],'stopped')
    def test_authenticated_schedule_and_maintenance_admission(self):
        with TestClient(create_app(self.data,TOKEN),base_url='http://localhost') as c:
            self.assertEqual(c.post('/api/data-schedule',json={'enabled':True}).status_code,401)
            self.assertEqual(c.post('/api/data-schedule',headers=AUTH|{'Origin':'https://evil.invalid'},json={'enabled':True}).status_code,403)
            self.assertEqual(c.post('/api/data-schedule',headers=AUTH,json={'enabled':'true'}).status_code,422)
            v=c.post('/api/data-schedule',headers=AUTH,json={'enabled':True});self.assertEqual(v.status_code,200);self.assertEqual(v.json()['service'],'stopped');self.assertTrue(v.json()['enabled'])
            for path in ('/api/data-maintenance/backup','/api/data-preparation/collect'):
                self.assertEqual(c.post(path,headers=AUTH).status_code,409)
            self.assertEqual(c.post('/api/data-schedule',headers=AUTH,json={'enabled':False}).status_code,200)
            self.assertEqual(c.post('/api/data-maintenance/retire',headers=AUTH,json={'preview_id':'0'*32}).status_code,409)
            self.assertEqual(c.post('/api/data-maintenance/retire',headers=AUTH,json={'preview_id':'../../unsafe'}).status_code,422)
            self.assertEqual(c.get('/api/status',headers=AUTH).json()['mode'],'PAPER')
        self.assertFalse(Schedule(self.data).status()['enabled'])
    def test_actual_maintenance_workers_and_reviewed_cleanup(self):
        with self.memory.tx() as db:
            for slot in (T,T+300):
                db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',('BTC/USD',slot,slot,slot,'observed'))
                db.execute('INSERT INTO quality_books VALUES(?,?,?,?,?,?,?)',('BTC/USD',slot,slot,slot,'{}','hash','source'))
        with TestClient(create_app(self.data,TOKEN),base_url='http://localhost') as c:
            def job(path,body=None):
                r=c.post('/api/data-maintenance/'+path,headers=AUTH,json=body);self.assertEqual(r.status_code,200,r.text)
                prep=c.app.state.preparation
                result=wait(lambda:(v if (v:=prep.status())['state']!='running' else None))
                self.assertEqual(result['state'],'complete',result)
                return c.get('/api/market-storage',headers=AUTH).json()['maintenance']
            a=job('backup');self.assertEqual(a['copies'],1);self.assertTrue(a['latest_backup']['manifest_sha256'])
            p=job('preview-retention')['cleanup_preview'];self.assertEqual((p['attempts'],p['books']),(1,1))
            self.assertEqual(c.post('/api/data-maintenance/retire',headers=AUTH,json={'preview_id':'0'*32}).status_code,409)
            b=job('retire',{'preview_id':p['id']});self.assertEqual(b['last_cleanup']['books'],1);self.assertIsNone(b['cleanup_preview'])
            self.assertEqual(c.post('/api/data-maintenance/retire',headers=AUTH,json={'preview_id':p['id']}).status_code,409)
        with self.memory.tx() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quality_books').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM quality_retired').fetchone()[0],1)
    def test_exact_preview_rejects_selection_change(self):
        with self.memory.tx() as db:
            for slot in (T,T+300,T+600):db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',('BTC/USD',slot,slot,slot,'unavailable'))
        target=self.root/'snapshot';r=backup(self.data,target);now=T+MIN_AGE+900
        p=retire_books(self.data,target,r['manifest_sha256'],now=now)
        with self.assertRaises(MemoryUnavailable):retire_books(self.data,target,r['manifest_sha256'],now=now,limit=1,apply=True,expected_selection=p['selection_sha256'])
        with self.memory.tx() as db:self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0],3)
    def test_maintenance_copy_capacity_and_symlink_refused(self):
        p=Preparation(self.data);self.addCleanup(p.close)
        root=self.root/'data-recovery-copies';root.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(MemoryUnavailable):maintenance_status(self.data)
        root.unlink();root.mkdir(mode=0o700)
        for i in range(4):(root/str(i)).mkdir()
        self.assertEqual(maintenance_status(self.data)['copies'],4)
        p.start('backup');res=wait(lambda:(v if (v:=p.status())['state']!='running' else None));self.assertEqual(res['state'],'failed')
    def test_independent_scheduler_parent_death_and_persisted_intent(self):
        s=Schedule(self.data);self.addCleanup(s.close);s.enable(True)
        fixture=self.root/'worker.py'
        fixture.write_text('import sys,os,time\nsys.path.insert(0,'+repr(str(ROOT))+')\nfrom data_preparation import parent_guard\nparent_guard(int(sys.argv[4]));os.fstat(int(sys.argv[5]))\ntime.sleep(100)\n')
        # Run the shipped scheduler entry point with a controlled worker only.
        code='import sys;from pathlib import Path;import data_preparation as p;p.SCRIPT=Path(sys.argv[1]);sys.argv=["schedule_data",sys.argv[2],"run"];from scripts.schedule_data import main;main()'
        child=subprocess.Popen([sys.executable,'-c',code,str(fixture),str(self.data)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(lambda:child.kill() if child.poll() is None else None)
        j=wait(lambda:(v if (v:=s.controller.status())['state']=='running' and s.public_status()['service']=='running' else None))
        child.kill();child.wait(timeout=5)
        wait(lambda:s.controller.status()['state']=='interrupted');self.assertTrue(s.status()['enabled']);self.assertEqual(s.public_status()['service'],'stopped')
        # Restart under a distinct owner after the first process and its worker die.
        # Force only the test clock's recovery delay due; never modify production policy.
        child2=subprocess.Popen([sys.executable,'-c',code,str(fixture),str(self.data)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            wait(lambda:s.public_status()['service']=='running' and s.status()['last_outcome']=='interrupted')
            with s.tx() as db:db.execute('UPDATE schedule SET retry_at=? WHERE id=1',(int(time.time())-1,))
            new=wait(lambda:(v if (v:=s.controller.status())['state']=='running' else None));self.assertNotEqual(j['id'],new['id'])
            s.enable(False);wait(lambda:s.controller.status()['state']=='cancelled');self.assertFalse(s.status()['enabled'])
        finally:
            child2.terminate();child2.wait(timeout=10)


class GapProgressTests(unittest.TestCase):
    def test_durable_pages_without_publication_then_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=MarketMemory(Path(tmp));start=T+3600;end=start+7200;now=end+7200;pair='BTC/USD'
            def snapshot(ts):return {'exchange':'kraken','product':'spot','pair':pair,'interval_minutes':60,'observed_at':ts+3600,'candles':[[ts*1000,'100','100','100','100','2',1]]}
            for ts in (T,end):m.record_recent(pair,60,snapshot(ts),now=ts+3600)
            def payload(stamps,ids):return {'error':[],'result':{'XXBTZUSD':[['100','2',t,'b','m','',i] for t,i in zip(stamps,ids)],'last':str(stamps[-1]*NS)}}
            a=payload([T,start],[11,12]);b=payload([start,start+3600],[12,13]);c=payload([start+3600,end,end+3600],[13,14,15]);pages=[a,b,c]
            class Bridge:
                def run(self,pair,start,end,**kw):return GapBridge(m).run(pair,start,end,max_pages=kw['max_pages'],fetch=lambda *_:pages.pop(0),pace=lambda _:None,clock=lambda:now+1000)
            report={'pair':pair,'ranges':[{'start':start,'end':end,'reason':'outside_rest_window'}]}
            for i in (1,2):
                r=repair_due(m,[report],now=now+i*301,bridge=Bridge(),max_pages=1)[0]
                self.assertEqual((r['outcome'],r['pages']),('checkpointed',i))
                self.assertEqual(recovery_progress(MarketMemory(Path(tmp)))[0]['pages'],i)
                self.assertEqual(m.query(pair,60,start,end,now+1000)['coverage'],0)
            r=repair_due(m,[report],now=now+903,bridge=Bridge(),max_pages=1)[0]
            self.assertEqual((r['outcome'],r['pages']),('complete',3));self.assertEqual(m.query(pair,60,start,end,now+1000)['coverage'],2)
