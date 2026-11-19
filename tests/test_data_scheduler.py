import os
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from data_scheduler import Schedule,REPAIR_INTERVAL
from gap_schedule import repair_due
from market_memory import MarketMemory,MemoryUnavailable
from data_preparation import _db
from data_retention import retire_books,MIN_AGE
from data_lifecycle import backup
from economic_memory import EconomicMemory
from market_quality import QualityMemory

T=1704067200

@unittest.skipUnless(os.name=='posix','Linux custody')
class ScheduleTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.s=Schedule(self.root);self.addCleanup(self.s.close)
        self.script=self.root/'worker.py';self.script.write_text('import sys,time\ntime.sleep(.1 if sys.argv[2]=="repair" else 100)\n')
    def wait(self,expected):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if self.s.controller.status()['state']==expected:return
            time.sleep(.02)
        self.fail(str(self.s.controller.status()))
    def test_default_off_and_reopen_enabled(self):
        self.assertEqual(self.s.tick(T)['enabled'],0);self.assertIsNone(self.s.controller.child)
        self.s.enable(True,T);other=Schedule(self.root);self.addCleanup(other.close)
        self.assertEqual(other.status()['next_repair'],T+REPAIR_INTERVAL)
        with self.s.lease(),self.assertRaises(MemoryUnavailable):
            with other.lease():pass
        with patch('data_preparation.SCRIPT',self.script),self.s.lease():
            self.s.tick(T);self.assertEqual(self.s.controller.status()['mode'],'collect');other.enable(False,T);self.s.tick(T);self.wait('cancelled');self.s.tick(T)
        self.assertEqual(self.s.status()['enabled'],0);self.assertIsNone(self.s.controller.child)
    def test_repair_cycle_and_return_to_collection(self):
        self.s.enable(True,T)
        with patch('data_preparation.SCRIPT',self.script),self.s.lease():
            self.s.tick(T);due=T+REPAIR_INTERVAL;self.s.tick(due);self.wait('cancelled');self.s.tick(due)
            self.assertEqual(self.s.controller.status()['mode'],'repair');self.wait('complete');self.s.tick(due+2)
            self.assertEqual(self.s.controller.status()['mode'],'collect');self.assertEqual(self.s.status()['next_repair'],due+2+REPAIR_INTERVAL)
    def test_old_crashed_job_reconciled_without_adopting(self):
        self.s.enable(True,T)
        with _db(self.s.root) as db:
            db.execute("INSERT INTO jobs VALUES('old','collect','running','starting',1,1)")
            db.execute("UPDATE schedule SET active_job='old',active_mode='collect' WHERE id=1")
        self.s.tick(T);self.assertEqual(self.s.status()['last_outcome'],'interrupted');self.assertIsNone(self.s.controller.child)
        with patch('data_preparation.SCRIPT',self.script),self.s.lease():self.s.tick(T+301);self.assertEqual(self.s.controller.status()['mode'],'collect')
    def test_new_selected_market_prepared_before_scheduled_collection(self):
        from market_directory import MarketDirectory, parse_directory
        from market_archive import RELEASE
        from test_market_directory import payload
        registry=MarketDirectory(self.root)
        result=registry.refresh(now=T,fetch=lambda:parse_directory(payload()))
        registry.select('ADA/USD',True,result['version'],now=T)
        m=MarketMemory(self.root)
        self.assertTrue(self.s.needs_selected_archive())
        self.s.enable(True,T)
        with patch('data_preparation.SCRIPT',self.script),self.s.lease():
            self.s.tick(T)
            self.assertEqual(self.s.controller.status()['mode'],'prepare')
            self.s.controller.cancel();self.wait('cancelled')
            with m.tx() as db:
                db.execute('CREATE TABLE market_archive_status(pair TEXT PRIMARY KEY,checked INTEGER,state TEXT,release TEXT)')
                db.execute('INSERT INTO market_archive_status VALUES(?,?,?,?)',('ADA/USD',T,'imported',RELEASE))
            self.s.tick(T+1)
            self.assertEqual(self.s.controller.status()['mode'],'collect')
            self.s.enable(False,T+2);self.s.tick(T+2);self.wait('cancelled')
    def test_no_cancel_foreign_manual_worker(self):
        other=Schedule(self.root);self.addCleanup(other.close)
        with patch('data_preparation.SCRIPT',self.script):
            other.controller.start('collect');self.s.enable(True,T);self.s.tick(T+REPAIR_INTERVAL);self.s.enable(False,T);self.s.tick(T+REPAIR_INTERVAL)
            self.assertEqual(other.controller.status()['state'],'running');self.assertIsNone(self.s.controller.child)

class GapAndRetentionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.data=self.root/'data';self.data.mkdir(mode=0o700);self.m=MarketMemory(self.data);EconomicMemory(self.data)
    def test_durable_gap_rotation_and_retry(self):
        class Bridge:
            def run(self,pair,start,end,**kw):return {'state':'recovering'}
        report={'pair':'BTC/USD','ranges':[{'start':T,'end':T+7200,'reason':'outside_rest_window'},{'start':T+10800,'end':T+14400,'reason':'outside_rest_window'}]}
        r=repair_due(self.m,[report],now=T+86400,bridge=Bridge());self.assertEqual(r[0]['start'],T)
        r=repair_due(MarketMemory(self.data),[report],now=T+86401,bridge=Bridge());self.assertEqual(r[0]['start'],T+10800)
        self.assertEqual(repair_due(self.m,[report],now=T+86402,bridge=Bridge()),[])
        self.assertEqual(repair_due(self.m,[report],now=T+86701,bridge=Bridge())[0]['start'],T)
    def test_backup_gated_retirement_and_preview(self):
        QualityMemory(self.m);pair='BTC/USD'
        with self.m.tx() as db:
            for t in (T,T+300):
                db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',(pair,t,t,t,'observed'))
                db.execute('INSERT INTO quality_books VALUES(?,?,?,?,?,?,?)',(pair,t,t,t,'{}','hash','source'))
        snap=self.root/'backup';r=backup(self.data,snap);now=T+MIN_AGE+600
        self.assertEqual(retire_books(self.data,snap,r['manifest_sha256'],now=now)['books'],1)
        with self.m.tx() as db:self.assertEqual(db.execute('SELECT count(*) FROM quality_books').fetchone()[0],2)
        with self.assertRaises(MemoryUnavailable):retire_books(self.data,snap,'0'*64,apply=True,now=now)
        self.assertEqual(retire_books(self.data,snap,r['manifest_sha256'],apply=True,now=now)['books'],1)
        with self.m.tx() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quality_books').fetchone()[0],1);self.assertEqual(db.execute('SELECT count(*) FROM quality_retired').fetchone()[0],1)
    def test_changed_book_refused(self):
        QualityMemory(self.m);pair='BTC/USD'
        with self.m.tx() as db:
            for t in (T,T+300):db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',(pair,t,t,t,'unavailable'))
        snap=self.root/'backup';r=backup(self.data,snap)
        with self.m.tx() as db:db.execute('UPDATE quality_attempts SET finished=finished+1 WHERE slot=?',(T,))
        with self.assertRaises(MemoryUnavailable):retire_books(self.data,snap,r['manifest_sha256'],apply=True,now=T+MIN_AGE+600)
