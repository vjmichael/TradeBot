"""Real SQLite accounting rehearsal. No venue calls or runtime activation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest

from financial_ledger import FinancialLedger, AccountingError, SCALE, initialize, replay, start


class LedgerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)/'desk.sqlite3'
        self.now = 1_800_000_000
        with self.tx() as db:
            db.execute('CREATE TABLE desk(id INTEGER PRIMARY KEY, virtual_cap_cents INTEGER)')
            db.execute('INSERT INTO desk VALUES(1,20000)')
            db.execute('CREATE TABLE instances(id TEXT PRIMARY KEY,pair TEXT,virtual_cents INTEGER,mode TEXT)')
            db.executemany('INSERT INTO instances VALUES(?,?,10000,\'PAPER\')',[('a','BTC/USD'),('b','ETH/USD')])
            db.execute('CREATE TABLE gate_intents(id TEXT PRIMARY KEY,instance_id TEXT,pair TEXT,notional INTEGER,reserved INTEGER,state TEXT)')
            db.execute("INSERT INTO gate_intents VALUES('i','a','BTC/USD',9900,10000,'CLAIMED')")
            initialize(db)
        self.f = FinancialLedger(self.tx,clock=lambda:self.now)
        self.f.fund('fund-a','a')
        self.f.fund('fund-b','b')

    @contextmanager
    def tx(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def snap(self):
        return self.f.snapshot('a')

    def buy(self,receipt='buy',q=SCALE,gross=98*SCALE,fee=2*SCALE):
        return self.f.buy(receipt,'a','i',q,gross,fee)

    def sell(self,receipt='sell',q=SCALE,gross=205*SCALE,fee=5*SCALE):
        return self.f.sell(receipt,'a','exit',q,gross,fee)

    def test_profit_only_on_settlement_and_fee_basis(self):
        self.buy()
        self.assertEqual((self.snap()['active'],self.snap()['inventory_basis']),(0,100*SCALE))
        self.sell()
        s=self.snap()
        self.assertEqual((s['pending'],s['realized_pl'],s['settled_pl'],s['protected']),(200*SCALE,100*SCALE,0,0))
        self.f.settle('settle','a','sell',200*SCALE)
        s=self.snap()
        self.assertEqual((s['active'],s['protected'],s['settled_pl'],s['fees']),(100*SCALE,100*SCALE,100*SCALE,7*SCALE))
        self.assertEqual(self.f.snapshot('b')['active'],100*SCALE)
        self.assertFalse(s['execution_ready'])
        with self.assertRaisesRegex(AccountingError,'already_settled'):
            self.f.settle('settle-again','a','sell',200*SCALE)

    def test_fifo_partial_fills_rounding_and_loss_isolation(self):
        self.buy('b1',3,40*SCALE,1)
        self.buy('b2',2,50*SCALE,1)
        self.sell('s1',1,20*SCALE,1)
        self.f.settle('t1','a','s1',20*SCALE-1)
        protected=self.snap()['protected']
        self.assertGreater(protected,0)
        self.sell('s2',4,25*SCALE,1)
        self.f.settle('t2','a','s2',25*SCALE-1)
        s=self.snap()
        self.assertEqual((s['qty'],s['inventory_basis'],s['protected']),(0,0,protected))
        self.assertEqual(s['settled_pl'],45*SCALE-2-(90*SCALE+2))
        self.assertEqual(s['active']+s['protected'],100*SCALE+s['settled_pl'])
        self.assertLess(s['active'],100*SCALE)

    def test_duplicate_conflict_quarantine_and_restart(self):
        self.buy()
        self.assertTrue(self.buy()['duplicate'])
        with self.assertRaisesRegex(AccountingError,'receipt_conflict'):
            self.buy(fee=SCALE)
        self.f=FinancialLedger(self.tx,clock=lambda:self.now)
        s=self.snap()
        self.assertEqual((s['qty'],s['conflicts']),(SCALE,1))
        self.f.reconcile('reconcile','a',active=0,protected=0,pending=0,quantity=SCALE,reserved=100*SCALE)
        self.assertFalse(self.snap()['reconciled'])

    def test_intent_bound_and_no_other_instance_funding(self):
        with self.assertRaisesRegex(AccountingError,'unverified_intent'):
            self.f.buy('wrong','b','i',1,SCALE,0)
        self.buy('b1',1,60*SCALE,0)
        with self.assertRaisesRegex(AccountingError,'intent_budget'):
            self.buy('b2',1,40*SCALE,0)
        with self.assertRaisesRegex(AccountingError,'already_funded'):
            self.f.fund('another','a')
        with self.assertRaisesRegex(AccountingError,'oversell'):
            self.sell(q=2)
        self.assertEqual(self.snap()['qty'],1)

    def test_correction_appends_and_does_not_double_count(self):
        self.buy()
        self.sell()
        self.f.correct_last_fill('fee-correction','a','sell',205*SCALE,6*SCALE)
        self.assertEqual(self.snap()['realized_pl'],99*SCALE)
        self.assertEqual(self.snap()['fees'],8*SCALE)
        self.f.settle('settle','a','sell',199*SCALE)
        self.assertEqual(self.snap()['protected'],99*SCALE)
        with self.assertRaisesRegex(AccountingError,'dependent_or_unknown'):
            self.f.correct_last_fill('too-late','a','sell',205*SCALE,0)
        with self.tx() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM fin_events WHERE kind='sell'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM fin_events WHERE kind='correct'").fetchone()[0],1)

    def test_mark_unrealized_and_reconciliation_expiry(self):
        self.buy()
        self.assertIsNone(self.snap()['unrealized_pl'])
        self.f.mark('mark','a',120*SCALE,self.now)
        self.assertEqual(self.snap()['unrealized_pl'],20*SCALE)
        self.assertEqual(self.snap()['protected'],0)
        self.f.reconcile('mismatch','a',active=1,protected=0,pending=0,quantity=SCALE,reserved=100*SCALE)
        self.assertFalse(self.snap()['reconciled'])
        self.f.reconcile('match','a',active=0,protected=0,pending=0,quantity=SCALE,reserved=100*SCALE)
        self.assertTrue(self.snap()['reconciled'])
        self.now+=61
        self.assertIsNone(self.snap()['unrealized_pl'])
        self.assertFalse(self.snap()['reconciled'])
        with self.assertRaisesRegex(AccountingError,'future_mark'):
            self.f.mark('future','a',120*SCALE,self.now+1)

    def test_startup_invalidates_reconciliation_without_moving_money(self):
        self.f.reconcile('match','a',active=100*SCALE,protected=0,pending=0,quantity=0,reserved=100*SCALE)
        self.assertTrue(self.snap()['reconciled'])
        before=self.snap()
        with self.tx() as db:
            start(db)
        self.assertFalse(self.snap()['reconciled'])
        self.assertEqual(self.snap()['active'],before['active'])
        self.assertEqual(self.snap()['journal_head'],before['journal_head'])
        self.f.reconcile('new-match','a',active=100*SCALE,protected=0,pending=0,quantity=0,reserved=100*SCALE)
        self.assertTrue(self.snap()['reconciled'])

    def test_settlement_mismatch_and_negative_fee_atomic(self):
        self.buy()
        self.sell()
        before=self.snap()
        with self.assertRaisesRegex(AccountingError,'settlement_amount'):
            self.f.settle('bad','a','sell',199*SCALE)
        with self.assertRaises(AccountingError):
            self.f.sell('negative','a','exit',1,SCALE,-1)
        self.assertEqual(self.snap(),before)

    def test_concurrent_duplicate_and_partial_budget(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.buy(),range(12)))
        self.assertEqual(sum(not r['duplicate'] for r in results),1)
        self.assertEqual(self.snap()['inventory_basis'],100*SCALE)
        self.assertEqual(self.snap()['gate_reserved'],100*SCALE)
        self.assertEqual(self.snap()['unencumbered_upper_bound'],0)

    def test_journal_tamper_and_tail_truncation_fail_closed(self):
        self.buy()
        with self.tx() as db:
            db.execute("DELETE FROM fin_events WHERE id='buy'")
        with self.assertRaisesRegex(AccountingError,'journal_head_mismatch'):
            self.snap()
        with self.tx() as db:
            db.execute("UPDATE fin_events SET body='{}' WHERE id='fund-a'")
        with self.assertRaisesRegex(AccountingError,'journal_integrity'):
            self.snap()

    def test_replay_ledger_balances_and_scope_change(self):
        self.buy()
        self.sell()
        with self.tx() as db:
            states,_,_,_=replay(db)
            self.assertEqual(sum(states['a'][k] for k in ('active','inventory','pending','protected','capital','pl')),0)
            db.execute("UPDATE instances SET pair='ETH/USD' WHERE id='a'")
        with self.assertRaisesRegex(AccountingError,'instance_scope_changed'):
            self.f.settle('bad-scope','a','sell',200*SCALE)


if __name__ == '__main__':
    unittest.main()
