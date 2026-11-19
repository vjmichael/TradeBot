"""Actual Store v3->v4 integration; no operational execution."""
from pathlib import Path
import tempfile
import unittest

from financial_ledger import AccountingError, SCALE
from risk_gate import Proposal
from tradebot import Store, InstanceSpec


class FinancialStoreTests(unittest.TestCase):
    def test_migration_preserves_gate_and_funding_requires_explicit_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp),20000)
            store.boot()
            row=store.add(InstanceSpec(name='paper',pair='BTC/USD',virtual_cents=10000,
                                       provider='none',strategy_family='numerical-baseline'))
            instance=row['id']
            rejected=store.gate.admit(Proposal('prior',instance,'BTC/USD','missing',100,4_000_000_000))
            self.assertEqual(rejected['reason'],'desk_paused')
            store.pause()
            with store.transaction() as db:
                for name in ('fin_events','fin_conflicts','fin_head'):
                    db.execute("DROP TABLE "+name)
                db.execute("PRAGMA user_version=3")
            store.boot()
            self.assertEqual(store.status()['allocated_cents'],10000)
            self.assertTrue(store.status()['human_pause'])
            self.assertFalse(store.status()['live_available'])
            with store.transaction() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],4)
                self.assertEqual(db.execute("SELECT reason FROM gate_decisions WHERE proposal_id='prior'").fetchone()[0],'desk_paused')
                self.assertEqual(db.execute("SELECT COUNT(*) FROM fin_events").fetchone()[0],0)
            with self.assertRaisesRegex(AccountingError,'unfunded'):
                store.financial.snapshot(instance)
            store.financial.fund('first-paper-funding',instance)
            store.financial.reconcile('paper-balances',instance,active=100*SCALE,protected=0,pending=0,quantity=0,reserved=0)
            self.assertTrue(store.financial.snapshot(instance)['reconciled'])
            before=store.financial.snapshot(instance)
            store.boot()
            after=store.financial.snapshot(instance)
            self.assertEqual(after['active'],100*SCALE)
            self.assertEqual(after['journal_head'],before['journal_head'])
            self.assertFalse(after['reconciled'] or after['execution_ready'])
            with store.transaction() as db:
                db.execute("DROP TABLE fin_head")
            with self.assertRaisesRegex(AccountingError,'financial_ledger_missing'):
                store.boot()

    def test_store_startup_refuses_truncated_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp),20000)
            store.boot()
            row=store.add(InstanceSpec(name='paper',pair='BTC/USD',virtual_cents=10000))
            store.financial.fund('paper-funding',row['id'])
            with store.transaction() as db:
                db.execute("DELETE FROM fin_events")
            with self.assertRaisesRegex(AccountingError,'journal_head_mismatch'):
                store.boot()


if __name__ == '__main__':
    unittest.main()
