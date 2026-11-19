import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from market_memory import MarketMemory,MemoryUnavailable
from market_gap import RecoveryDeferred
from recovery_budget import RecoveryBudget,recovery_budget_status
from gap_schedule import repair_due
from storage_telemetry import sample_storage,storage_growth
from engine import SYMBOLS

T=1795000000000

class RecoveryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.memory=MarketMemory(self.root)
        self.ms=T
    def budget(self):
        def sleep(seconds):self.ms+=round(seconds*1000)
        return RecoveryBudget(MarketMemory(self.root),clock_ms=lambda:self.ms,sleep=sleep)
    def test_fresh_schema_and_reopen_use_distinct_sqlite_names(self):
        self.budget().reserve('BTC/USD')
        self.budget().reserve('ETH/USD')
        with self.memory.tx() as db:
            names=dict(db.execute("SELECT name,type FROM sqlite_master WHERE name LIKE 'recovery_%'"))
            self.assertEqual(names['recovery_admission_clock'],'table')
            self.assertEqual(names['recovery_admission_requested_ms_idx'],'index')
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_admission').fetchone()[0],2)
        self.assertEqual(recovery_budget_status(self.memory,self.ms)['total_requests'],2)
    def test_legacy_empty_index_repaired_and_reopen_preserves_accounting(self):
        with self.memory.tx() as db:
            db.execute('CREATE TABLE recovery_admission(id INTEGER PRIMARY KEY AUTOINCREMENT,pair TEXT NOT NULL,requested_ms INTEGER NOT NULL)')
            db.execute('CREATE INDEX recovery_admission_clock ON recovery_admission(requested_ms)')
        self.budget().reserve('BTC/USD')
        self.budget().reserve('SOL/USD')
        self.assertEqual(recovery_budget_status(self.memory,self.ms)['total_requests'],2)
        with self.memory.tx() as db:
            self.assertEqual(db.execute("SELECT type FROM sqlite_master WHERE name='recovery_admission_clock'").fetchone()[0],'table')
    def test_incomplete_nonempty_legacy_schema_refuses_to_reset_budget(self):
        with self.memory.tx() as db:
            db.execute('CREATE TABLE recovery_admission(id INTEGER PRIMARY KEY AUTOINCREMENT,pair TEXT NOT NULL,requested_ms INTEGER NOT NULL)')
            db.execute('CREATE INDEX recovery_admission_clock ON recovery_admission(requested_ms)')
            db.execute('INSERT INTO recovery_admission(pair,requested_ms) VALUES(?,?)',('BTC/USD',self.ms))
        with self.assertRaises(RecoveryDeferred):self.budget()
        with self.memory.tx() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_admission').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT type FROM sqlite_master WHERE name='recovery_admission_clock'").fetchone()[0],'index')
    def test_rolling_admission_restart_and_pacing(self):
        with patch('recovery_budget.HOURLY_LIMIT',3):
            self.budget().reserve('BTC/USD')
            self.budget().reserve('ETH/USD')
            self.budget().reserve('SOL/USD')
            self.assertEqual(self.ms,T+2500)
            with self.assertRaises(RecoveryDeferred):self.budget().reserve('BTC/USD')
            status=recovery_budget_status(self.memory,self.ms)
            self.assertEqual((status['window_requests'],status['total_requests'],status['state']),(3,3,'paced'))
            self.ms=T+3600000
            self.budget().reserve('BTC/USD')
            status=recovery_budget_status(self.memory,self.ms)
            self.assertEqual((status['window_requests'],status['total_requests']),(3,4))
            self.ms=T
            with self.assertRaises(RecoveryDeferred):self.budget().reserve('BTC/USD')
    def test_exchange_cooldown_persists_and_blocks_early_requests(self):
        b=self.budget();b.reserve('BTC/USD');b.throttle('600')
        self.ms=T+599999
        with self.assertRaises(RecoveryDeferred):self.budget().reserve('ETH/USD')
        self.assertEqual(recovery_budget_status(self.memory,self.ms)['state'],'exchange_cooldown')
        self.ms=T+600000;self.budget().reserve('ETH/USD')
        self.assertEqual(recovery_budget_status(self.memory,self.ms)['total_requests'],2)
        self.budget().throttle('invalid')
        self.ms+=86400000
        with self.assertRaises(RecoveryDeferred):self.budget().reserve('SOL/USD')
    def test_cumulative_slice_budget_and_market_rotation(self):
        calls=[]
        class Bridge:
            def run(self,pair,start,end,**kwargs):
                calls.append((pair,kwargs['max_pages']))
                return {'state':'recovering','pages':kwargs['max_pages']}
        reports=[{'pair':p,'ranges':[{'start':3600,'end':7200,'reason':'outside_rest_window'}]} for p in SYMBOLS]
        first=repair_due(self.memory,reports,now=100000,bridge=Bridge(),max_pages=4,total_pages=5,prefer_checkpoint=True)
        self.assertEqual(calls,[(list(SYMBOLS)[0],4),(list(SYMBOLS)[1],1)])
        repair_due(MarketMemory(self.root),reports,now=100301,bridge=Bridge(),max_pages=4,total_pages=4,prefer_checkpoint=True)
        self.assertEqual(calls[-1],(list(SYMBOLS)[2],4))
    def test_deferred_request_keeps_checkpoint(self):
        class Bridge:
            def run(self,*args,**kwargs):raise RecoveryDeferred('limit')
        report={'pair':'BTC/USD','ranges':[{'start':3600,'end':7200,'reason':'outside_rest_window'}]}
        self.assertEqual(repair_due(self.memory,[report],now=100000,bridge=Bridge())[0]['outcome'],'deferred')
    def test_measured_storage_is_scope_specific_and_bounded(self):
        self.assertEqual(storage_growth(self.root,SYMBOLS)['state'],'awaiting_samples')
        self.assertTrue(sample_storage(self.memory,SYMBOLS,now=100000))
        self.assertFalse(sample_storage(self.memory,SYMBOLS,now=100001))
        (self.memory.root/'sample.bin').write_bytes(b'x'*4096)
        self.assertTrue(sample_storage(MarketMemory(self.root),SYMBOLS,now=100300))
        result=storage_growth(self.root,SYMBOLS)
        self.assertEqual(result['growth']['elapsed_seconds'],300)
        self.assertGreaterEqual(result['growth']['logical_bytes_delta'],4096)
        self.assertEqual(result['sample_count'],2)
        self.assertEqual(len(result['windows']),3);self.assertEqual(result['windows'][0]['elapsed_seconds'],300)
        self.assertEqual(result['windows'][0]['missed_nominal_slots'],0)
        self.assertEqual(storage_growth(self.root,{'BTC/USD':SYMBOLS['BTC/USD']})['state'],'awaiting_samples')
        with self.assertRaises(MemoryUnavailable):sample_storage(self.memory,SYMBOLS,now=99999)
    def test_long_windows_separate_recovery_copies_and_gap_reporting(self):
        root=self.root.parent/(self.root.name+'-recovery-copies');root.mkdir(mode=0o700);self.addCleanup(lambda:__import__('shutil').rmtree(root))
        self.assertTrue(sample_storage(self.memory,SYMBOLS,now=100000))
        (root/'copy.bin').write_bytes(b'x'*8192)
        self.assertTrue(sample_storage(self.memory,SYMBOLS,now=100600))
        result=storage_growth(self.root,SYMBOLS)
        self.assertGreaterEqual(result['latest']['recovery_copies']['logical_bytes'],8192)
        self.assertGreaterEqual(result['windows'][0]['recovery_allocated_delta'],8192)
        self.assertEqual(result['windows'][0]['missed_nominal_slots'],1)

    def test_verified_checkpoint_resumes_early_but_new_markets_rotate(self):
        calls=[]
        class Bridge:
            def run(self,pair,start,end,**kwargs):
                calls.append(pair);return {'state':'recovering','pages':1,'cursor':'12'}
        reports=[{'pair':p,'ranges':[{'start':3600,'end':7200,'reason':'verified_boundary_gap','recovery':'checkpoint_saved'}]} for p in SYMBOLS]
        for offset in (0,1,2):
            repair_due(self.memory,reports,now=100000+offset,bridge=Bridge(),max_pages=1,total_pages=1,prefer_checkpoint=True)
        self.assertEqual(calls,list(SYMBOLS))
        self.assertEqual(repair_due(self.memory,reports,now=100029,bridge=Bridge(),max_pages=1,total_pages=1,prefer_checkpoint=True),[])
        repair_due(MarketMemory(self.root),reports,now=100030,bridge=Bridge(),max_pages=1,total_pages=1,prefer_checkpoint=True)
        self.assertEqual(calls[-1],list(SYMBOLS)[0])
