"""Real SQLite, signed simulated wallet and full-fill transition fixtures."""
import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager, closing
import unittest

from financial_ledger import SCALE, AccountingError, canon, replay, sha, start
from financial_goals import DEFAULT, save, report
from goal_control import evaluate
from goal_evidence import capture_open, timing_packet, validate_time, GoalRecoveryDriver
from paper_custody import ReceiptJournal, packet
from paper_execution import append_event
import test_financial_goals as fixture
import test_paper_custody as native


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.GoalTests('test_loss_and_no_cap_change');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.clock=self.h.clock;self.cut=self.h.cut
        self.h.h.now=self.cut-8
        self.h.save({'target_kind':'points','target_value':200})
        self.key,self.binding=self.h.key_for('a')
        self.oid='order:'+sha(['BTC/USD','dry_run_buy_native'])
        self.rid='native:'+sha([self.binding,self.oid])
        with self.tx() as db:
            db.execute("UPDATE gate_intents SET state='CLAIMED'")
            append_event(db,'i','native_bound',{'binding':self.binding},self.clock())
            append_event(db,'i','native_attempt',{'binding':self.binding},self.clock())
        self.h.h.buy(self.rid,q=SCALE,gross=98*SCALE,fee=2*SCALE)
        with self.tx() as db:db.execute("UPDATE gate_intents SET state='FILLED_HELD'")
        o=dict(id=self.oid,tag='mv:i',side='buy',terminal=True,qty=SCALE,gross=98*SCALE,fee=2*SCALE,price=98*SCALE)
        self.v=dict(orders=[o],position=SCALE,closed=False,quote=2*SCALE)
        self.driver=SimpleNamespace(instance='a',binding=self.binding,key=self.key,trade=lambda row:SimpleNamespace(is_open=True,open_orders=[]),view=lambda row:self.v,bid=lambda row:(110*SCALE,self.clock()))

    @contextmanager
    def tx(self):
        with self.h.tx() as db:
            db.row_factory=sqlite3.Row;yield db

    def snap(self):capture_open(self.tx,self.driver,clock=self.clock)
    def after(self):self.h.h.now=self.cut+2
    def report(self):return self.h.report()

    def test_open_bid_equity_persisted_across_sell_and_restart(self):
        self.snap();self.after();a=self.report()
        self.assertEqual((a['day_start_active_usd'],a['baseline_state'],a['target_usd']),('110.00000000','verified_open_bid_snapshot','2.20000000'))
        with self.tx() as db:evaluate(db,'a',self.clock(),self.h.key_for);start(db)
        self.assertTrue(self.report()['baseline_persisted'])
        self.h.h.sell(gross=115*SCALE);self.h.h.f.settle('settle','a','sell',110*SCALE)
        b=self.report();self.assertEqual(b['baseline_evidence'],a['baseline_evidence']);self.assertEqual(b['day_start_active_usd'],'110.00000000');self.assertEqual(b['protected_usd'],'10.00000000')

    def test_position_wallet_mismatch_refused(self):
        self.v['quote']+=1
        with self.assertRaisesRegex(AccountingError,'opening_wallet_mismatch'):self.snap()
        self.after();self.assertIsNone(self.report()['day_start_active_usd'])

    def test_open_order_or_missing_fee_is_not_a_baseline(self):
        self.driver.trade=lambda row:SimpleNamespace(is_open=True,open_orders=[1])
        with self.assertRaisesRegex(AccountingError,'opening_order_unresolved'):self.snap()
        self.driver.trade=lambda row:SimpleNamespace(is_open=True,open_orders=[]);self.v['orders'][0]['fee']=None
        with self.assertRaisesRegex(AccountingError,'opening_order_unverified'):self.snap()

    def test_intervening_event_rejects_pre_midnight_snapshot(self):
        self.snap()
        with self.tx() as db:append_event(db,'i','new_event',{},self.clock()+1)
        self.after();self.assertIsNone(self.report()['day_start_active_usd'])

    def test_missed_cutoff_and_tamper_never_backfill(self):
        self.snap()
        with self.tx() as db:db.execute("UPDATE goal_open_snapshots SET body='{}'")
        self.after();self.snap();self.assertIsNone(self.report()['day_start_active_usd'])
        with self.tx() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM goal_open_snapshots').fetchone()[0],1)

    def sell_at(self,lo,hi,*,tamper=False):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        journal=ReceiptJournal(Path(d.name)/'receipts.sqlite',self.key,self.binding)
        raw=native.raw(side='sell',filled=1,fee=5.)|{'amount':1,'average':115.,'cost':115.}
        journal.capture(raw);self.driver.journal=journal
        signed=timing_packet(self.driver,raw,lo,hi);b=signed['body'];self.h.h.f.sell(b['id'],'a',b['order'],SCALE,115*SCALE,5*SCALE)
        with self.tx() as db:
            if tamper:signed['body']['start']-=1
            db.execute('INSERT INTO goal_fill_times VALUES(?,?,?,?)',(b['id'],'a',canon(signed),sha(signed)))
        return b['id']

    def test_execution_interval_prior_day_posting_today(self):
        self.after();self.sell_at(self.cut-3,self.cut-2);r=self.report()
        self.assertEqual(r['daily_realized_usd'],'10.00000000');self.assertEqual(r['execution_day_original_pl_usd'],'0.00000000');self.assertEqual(r['execution_day_unknown'],0)

    def test_execution_interval_today_and_separate_correction(self):
        self.after();sid=self.sell_at(self.cut,self.cut+1);r=self.report()
        self.assertEqual((r['execution_day_original_pl_usd'],r['daily_realized_usd']),('10.00000000','10.00000000'))
        self.h.h.f.settle('settle','a',sid,110*SCALE)
        self.h.h.f.late_correct('late','a',sid,114*SCALE,5*SCALE,settlement_received=109*SCALE,expected={'active':100*SCALE,'protected':9*SCALE,'pending':0,'qty':0},evidence_digest='c'*64)
        r=self.report();self.assertEqual((r['execution_day_original_pl_usd'],r['posting_day_adjustments_usd'],r['daily_realized_usd']),('10.00000000','-1.00000000','9.00000000'))

    def test_crossing_midnight_missing_or_tampered_time_not_invented(self):
        self.after();sid=self.sell_at(self.cut-1,self.cut+1);r=self.report();self.assertIsNone(r['execution_day_original_pl_usd']);self.assertEqual(r['execution_day_unknown'],1)
        with self.tx() as db:db.execute('DELETE FROM goal_fill_times')
        self.assertIsNone(self.report()['execution_day_original_pl_usd'])

    def test_tampered_time_refuses_attribution(self):
        self.after();self.sell_at(self.cut,self.cut+1,tamper=True);self.assertEqual(self.report()['execution_day_unknown'],1)

    def test_full_transition_only_no_partial_or_clock_rollback(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);self.driver.journal=ReceiptJournal(Path(d.name)/'receipts.sqlite',self.key,self.binding)
        raw=native.raw(side='sell')|{'amount':1}
        self.driver.journal.capture(raw)
        with self.assertRaisesRegex(AccountingError,'fill_time_not_full'):timing_packet(self.driver,raw,100,101)
        with self.assertRaisesRegex(AccountingError,'fill_time_window'):timing_packet(self.driver,raw|{'amount':.5},101,100)


if __name__=='__main__':unittest.main()
