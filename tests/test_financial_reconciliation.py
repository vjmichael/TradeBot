"""Independent PAPER observation and correction contracts (real SQLite)."""
import unittest
from financial_ledger import AccountingError, SCALE, projection_values, start
import test_financial_ledger as fixture


class ReconciliationTests(unittest.TestCase):
    # Reuse the existing real SQLite fixture without inheriting its tests.
    def setUp(self):
        self.h=fixture.LedgerTests('test_profit_only_on_settlement_and_fee_basis')
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.f=self.h.f

    def observe(self, name, *, accounts=None, orders=None, when=None):
        if accounts is None:
            accounts={'a':dict(active=100*SCALE,protected=0,pending=0,qty=0,covered_qty=0),
                      'b':dict(active=100*SCALE,protected=0,pending=0,qty=0,covered_qty=0)}
        return self.f.reconcile_desk(name,observed_at=self.h.now if when is None else when,
            evidence_digest='a'*64,accounts=accounts,open_orders=[] if orders is None else orders)

    def project(self):
        with self.h.tx() as db:
            return projection_values(db,self.h.now)

    def clear_fixture_claim(self):
        with self.h.tx() as db:
            db.execute("DELETE FROM gate_intents WHERE id='i'")

    def sell_settle(self):
        self.h.buy()
        self.h.sell()
        self.f.settle('settle','a','sell',200*SCALE)

    def test_late_settled_sell_fee_correction_rebuilds_and_protects(self):
        self.sell_settle()
        self.f.late_correct('late','a','sell',205*SCALE,6*SCALE,settlement_received=199*SCALE,
            expected=dict(active=100*SCALE,protected=99*SCALE,pending=0,qty=0),evidence_digest='b'*64)
        s=self.h.snap()
        self.assertEqual((s['active'],s['protected'],s['settled_pl'],s['fees']),
                         (100*SCALE,99*SCALE,99*SCALE,8*SCALE))
        self.assertFalse(s['correction_locked'])
        with self.h.tx() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM fin_events WHERE kind='sell'").fetchone()[0],1)
            start(db)
        self.assertEqual(self.h.snap()['protected'],99*SCALE)

    def test_late_buy_fee_after_dependent_partial_sales(self):
        self.h.buy('buy',q=2,gross=90*SCALE,fee=0)
        self.h.sell('part',q=1,gross=70*SCALE,fee=0)
        self.f.settle('part-settle','a','part',70*SCALE)
        self.f.late_correct('late','a','buy',90*SCALE,2*SCALE,settlement_received=None,
            expected=dict(active=54*SCALE,protected=24*SCALE,pending=0,qty=1),evidence_digest='b'*64)
        s=self.h.snap()
        self.assertEqual((s['active'],s['inventory_basis'],s['protected']),(54*SCALE,46*SCALE,24*SCALE))
        self.assertFalse(s['correction_locked'])

    def test_mismatch_is_journaled_and_locks_not_rewrites(self):
        self.sell_settle()
        self.f.late_correct('bad','a','sell',205*SCALE,6*SCALE,settlement_received=199*SCALE,
            expected=dict(active=100*SCALE,protected=100*SCALE,pending=0,qty=0),evidence_digest='b'*64)
        self.assertEqual(self.h.snap()['protected'],100*SCALE)
        self.assertTrue(self.h.snap()['correction_locked'])
        self.assertEqual(self.h.snap()['correction_reason'],'independent_balance_mismatch')
        self.clear_fixture_claim()
        obs={'a':dict(active=100*SCALE,protected=100*SCALE,pending=0,qty=0,covered_qty=0),
             'b':dict(active=100*SCALE,protected=0,pending=0,qty=0,covered_qty=0)}
        self.observe('obs',accounts=obs)
        with self.assertRaisesRegex(AccountingError,'unresolved_correction'):
            self.project()
        with self.h.tx() as db:
            start(db)
        self.assertTrue(self.h.snap()['correction_locked'])

    def test_correction_cannot_restore_depleted_active_cash(self):
        self.h.buy()
        self.h.sell(gross=70*SCALE,fee=0)
        self.f.settle('settle','a','sell',70*SCALE)
        self.f.late_correct('refund','a','sell',80*SCALE,0,settlement_received=80*SCALE,
            expected=dict(active=80*SCALE,protected=0,pending=0,qty=0),evidence_digest='c'*64)
        self.assertTrue(self.h.snap()['correction_locked'])
        self.assertEqual(self.h.snap()['active'],70*SCALE)

    def test_desk_observation_requires_all_accounts_and_independent_values(self):
        self.clear_fixture_claim()
        with self.assertRaisesRegex(AccountingError,'fresh_desk'):
            self.project()
        self.observe('partial',accounts={'a':dict(active=100*SCALE,protected=0,pending=0,qty=0,covered_qty=0)})
        with self.assertRaisesRegex(AccountingError,'scope_mismatch'):
            self.project()
        self.observe('unknown',accounts={'unknown':dict(active=0,protected=0,pending=0,qty=0,covered_qty=0)})
        with self.assertRaisesRegex(AccountingError,'scope_mismatch'):
            self.project()
        self.observe('empty',accounts={})
        with self.assertRaisesRegex(AccountingError,'scope_mismatch'):
            self.project()
        self.observe('full')
        account=self.project()
        self.assertEqual(account['active_by_instance'],{'a':10000,'b':10000})
        self.assertEqual(account['settled_cash_cents'],20000)
        self.assertEqual(account['protected_cash_cents'],0)
        self.assertTrue(account['reconciled'])
        self.h.now+=61
        with self.assertRaisesRegex(AccountingError,'observation_stale'):
            self.project()

    def test_claim_ambiguity_and_open_orders_block_even_if_balances_match(self):
        self.observe('claim')
        with self.assertRaisesRegex(AccountingError,'unresolved_execution'):
            self.project()
        self.clear_fixture_claim()
        self.observe('orders',orders=['outside-order'])
        with self.assertRaisesRegex(AccountingError,'unresolved_open_orders'):
            self.project()
        self.observe('clean')
        self.project()
        with self.h.tx() as db:
            db.execute("INSERT INTO gate_intents VALUES('a-new','a','BTC/USD',900,1000,'AUTHORIZED')")
        self.assertEqual(self.project()['active_by_instance']['a'],10000)
        with self.h.tx() as db:
            db.execute("UPDATE gate_intents SET state='CLAIMED' WHERE id='a-new'")
        with self.assertRaisesRegex(AccountingError,'unresolved_execution'):
            self.project()
        with self.h.tx() as db:
            db.execute("UPDATE gate_intents SET state='NO_FILL' WHERE id='a-new'")
        with self.assertRaisesRegex(AccountingError,'execution_changed'):
            self.project()
        self.observe('fresh-after-no-fill')
        self.project()

    def test_projected_protected_cash_never_expands_active_limit(self):
        self.sell_settle()
        self.clear_fixture_claim()
        self.observe('profit',accounts={'a':dict(active=100*SCALE,protected=100*SCALE,pending=0,qty=0,covered_qty=0),
                                       'b':dict(active=100*SCALE,protected=0,pending=0,qty=0,covered_qty=0)})
        account=self.project()
        self.assertEqual((account['settled_cash_cents'],account['protected_cash_cents']),(30000,10000))
        self.assertEqual(account['active_by_instance'],{'a':10000,'b':10000})
        with self.h.tx() as db:
            start(db)
        with self.assertRaisesRegex(AccountingError,'reconciliation_restart'):
            self.project()

    def test_old_observation_cannot_replace_current(self):
        self.clear_fixture_claim()
        self.observe('clean')
        before=self.project()
        with self.assertRaisesRegex(AccountingError,'out_of_order_desk'):
            self.observe('old',when=self.h.now-1)
        self.assertEqual(self.project(),before)

    def test_later_financial_event_invalidates_observation(self):
        self.clear_fixture_claim()
        self.observe('clean')
        self.project()
        self.f.mark('mark','a',10*SCALE,self.h.now)
        with self.assertRaisesRegex(AccountingError,'fresh_desk'):
            self.project()


if __name__=='__main__':
    unittest.main()
