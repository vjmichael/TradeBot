"""Actual Gate integration in an isolated SQLite RUN fixture; no venue calls."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from financial_ledger import FinancialLedger, SCALE, gate_projection, initialize
from risk_gate import Gate, GateRefusal
import test_risk_gate as fixture


class GateAccountingTests(unittest.TestCase):
    def setUp(self):
        self.base=fixture.GateTests('test_size_and_exact_idempotency')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.h=self.base.h
        with self.h.tx() as db:
            initialize(db)
        self.ledger=FinancialLedger(self.h.tx,clock=lambda:self.h.now)
        self.ledger.fund('fund-a','a')
        self.ledger.fund('fund-b','b')
        self.g=Gate(self.h.tx,clock=lambda:self.h.now,account_projection=gate_projection)

    def observe(self, ident):
        accounts={key:dict(active=60*SCALE,protected=0,pending=0,qty=0,covered_qty=0)
                  for key in ('a','b')}
        return self.ledger.reconcile_desk(ident,observed_at=self.h.now,
            evidence_digest='a'*64,accounts=accounts,open_orders=[])

    def propose(self, ident, instance='a'):
        if instance=='a':
            return self.g.admit(replace(self.base.req,proposal_id=ident))
        return self.g.admit(replace(self.base.req,proposal_id=ident,instance_id='b',
            pair='ETH/USD',evidence_id='market-eth'))

    def test_forged_account_path_denied_and_legacy_receipt_ignored(self):
        with self.assertRaisesRegex(GateRefusal,'ledger_projection_required'):
            self.g.trusted_account(self.base.a)
        self.assertEqual(self.propose('before')['reason'],'financial:fresh_desk_reconciliation_required')
        self.observe('whole-desk')
        receipt=self.propose('after')
        self.assertEqual((receipt['state'],receipt['notional_cents'],receipt['reserved_cents']),
                         ('AUTHORIZED',5000,5050))

    def test_financial_mutation_blocks_previously_authorized_claim(self):
        self.observe('before')
        receipt=self.propose('intent')
        self.ledger.mark('new-mark','a',20*SCALE,self.h.now)
        self.assertEqual(self.g.claim(receipt['intent_id'],'paper-worker',1)['reason'],
                         'financial:fresh_desk_reconciliation_required')
        self.assertEqual(self.propose('intent')['state'],'VOID')

    def test_claim_and_no_fill_require_fresh_whole_desk_observation(self):
        self.observe('first')
        receipt=self.propose('intent')
        claimed=self.g.claim(receipt['intent_id'],'paper-worker',1)
        self.assertTrue(claimed['claimed'])
        self.assertEqual(self.propose('during','b')['reason'],'financial:unresolved_execution')
        self.g.trusted_resolution(receipt['intent_id'],claimed['claim_token'],'verified-no-fill','NO_FILL')
        self.assertEqual(self.propose('after','b')['reason'],'financial:desk_execution_changed')
        self.observe('new-observation')
        self.assertEqual(self.propose('new','b')['state'],'AUTHORIZED')

    def test_store_wires_ledger_and_remains_paused(self):
        from tradebot import Store
        with tempfile.TemporaryDirectory() as name:
            store=Store(Path(name),12000)
            store.boot()
            self.assertIsNotNone(store.gate.account_projection)
            self.assertIsNot(store.gate.account_projection,gate_projection)
            with self.assertRaisesRegex(GateRefusal,'ledger_projection_required'):
                store.gate.trusted_account(self.base.a)
            self.assertTrue(store.status()['paused'])
            self.assertFalse(store.status()['live_available'])


if __name__=='__main__':
    unittest.main()
