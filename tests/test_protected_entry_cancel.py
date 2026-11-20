"""R2-R2: real Gate/SQLite boundary and signed zero transport negatives.

Driver side effects and checkpoint authority use explicit test fixtures; real
Freqtrade, split UID and signed snapshot integration run in the native script.
"""
import copy
import os
import signal
import unittest
from dataclasses import replace
from types import SimpleNamespace
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from financial_ledger import SCALE,replay,gate_reserve
from risk_gate import GateRefusal
from reserve_ipc import canonical
from reserve_zero_transport import DOMAIN,body,consume,acknowledged
from paper_execution import event_detail
from reserve_cancel_custody import local_transaction
import test_paper_execution as bridge_fixture
import test_risk_gate as gate_fixture


class EntryBoundaryTests(unittest.TestCase):
    def test_changed_market_or_scope_after_claim_blocks(self):
        f=gate_fixture.GateTests();f.setUp();self.addCleanup(f.doCleanups)
        intent=f.admit()['intent_id'];self.assertTrue(f.g.claim(intent,'paper-worker',1)['claimed'])
        with f.h.tx() as db:f.g.check_entry_boundary(db,db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone(),f.h.now)
        f.g.trusted_market(replace(f.m,receipt='market-changed'))
        with f.h.tx() as db:
            with self.assertRaisesRegex(GateRefusal,'market_stale_or_changed'):f.g.check_entry_boundary(db,db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone(),f.h.now)

    def test_changed_claim_context_blocks(self):
        f=gate_fixture.GateTests();f.setUp();self.addCleanup(f.doCleanups)
        intent=f.admit()['intent_id'];self.assertTrue(f.g.claim(intent,'paper-worker',1)['claimed'])
        with f.h.tx() as db:
            db.execute("UPDATE instances SET state='PAUSE' WHERE id='a'")
            with self.assertRaisesRegex(GateRefusal,'claim_context_changed'):f.g.check_entry_boundary(db,db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone(),f.h.now)

    @unittest.skipUnless(hasattr(os,'fork'),'Linux process death test')
    def test_entry_attempt_survives_kill_inside_side_effect(self):
        h=bridge_fixture.BridgeTests();h.setUp();self.addCleanup(h.doCleanups)
        h.d.entry=lambda row,guard:guard(lambda:os.kill(os.getpid(),signal.SIGKILL))
        child=os.fork()
        if child==0:
            try:h.submit()
            finally:os._exit(1)
        _,status=os.waitpid(child,0)
        self.assertTrue(os.WIFSIGNALED(status));self.assertEqual(os.WTERMSIG(status),signal.SIGKILL)
        with h.tx() as db:self.assertEqual(event_detail(db,'i','native_attempt'),{'binding':'b'*64})
        self.assertEqual(h.state(),'CLAIMED');self.assertFalse(h.submit()['submitted']);self.assertEqual(h.d.calls,0)

    def test_policy_change_between_precommit_and_effect_holds(self):
        h=bridge_fixture.BridgeTests();h.setUp();self.addCleanup(h.doCleanups)
        def gate(db,row,now):
            if event_detail(db,'i','native_attempt'):raise GateRefusal('goal_or_policy_changed')
        h.bridge.gate.check_entry_boundary=gate
        self.assertEqual(h.submit()['reason'],'native_outcome_unknown')
        self.assertEqual(h.state(),'UNKNOWN');self.assertEqual(h.d.calls,0)
        with h.tx() as db:self.assertIsNotNone(event_detail(db,'i','native_attempt'))


class ZeroTransportTests(unittest.TestCase):
    def setUp(self):
        h=bridge_fixture.BridgeTests();h.setUp();self.addCleanup(h.doCleanups);h.submit();self.h=h
        with h.tx() as db:
            db.execute('ALTER TABLE gate_intents ADD COLUMN resolution_receipt TEXT')
            _,_,self.count,self.head=replay(db)
        self.seed=Ed25519PrivateKey.generate();self.public=self.seed.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.store=SimpleNamespace(verify=lambda db:'fixed-test-witness')
    def sign(self,**changes):
        z=dict(order='a'*64,qty=SCALE//2,price=100*SCALE,quote=100*SCALE,at=self.h.now)|changes
        b=body('paper-test','a','i','b'*64,self.count,self.head,z)
        return {'body':b,'signature':self.seed.sign(DOMAIN+canonical(b)).hex()}
    def consume(self,p):
        return consume(self.h.tx,p,lambda d,i:'b'*64,self.public,'paper-test',store=self.store,clock=lambda:self.h.now)
    def test_exact_zero_and_duplicate_then_acknowledged(self):
        p=self.sign();self.assertEqual(self.consume(p),{'state':'NO_FILL','duplicate':False});self.assertTrue(self.consume(p)['duplicate'])
        with self.h.tx() as db:
            self.assertEqual(gate_reserve(db,'a'),0);self.assertTrue(acknowledged(db,p,lambda d,i:'b'*64,self.public,'paper-test'))
            self.assertEqual(replay(db)[3],self.head)
        self.assertEqual(self.h.h.snap()['active'],100*SCALE)
        with self.assertRaises(ValueError):self.consume(self.sign(order='c'*64))
    def test_wrong_wallet_oversize_future_and_tamper_hold(self):
        p=self.sign();p['signature']='0'*128
        for packet in (p,self.sign(quote=99*SCALE),self.sign(qty=2*SCALE),self.sign(at=self.h.now+1),self.sign(price=99*SCALE)):
            with self.assertRaises(ValueError):self.consume(packet)
        self.assertEqual(self.h.state(),'CLAIMED');self.assertEqual(self.h.h.snap()['gate_reserved'],100*SCALE)
    def test_prior_economic_event_prevents_zero_release(self):
        from paper_execution import append_event
        with self.h.tx() as db:append_event(db,'i','native_partial_accounted',{},self.h.now)
        with self.assertRaisesRegex(ValueError,'zero_prior_execution'):self.consume(self.sign())
        self.assertEqual(self.h.state(),'CLAIMED')
    def test_native_local_storage_refuses_symlink_and_foreign_schema(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cancel-receipts.sqlite';p.symlink_to('/dev/null')
            with self.assertRaises(OSError):
                with local_transaction(d):pass
            p.unlink()
            with local_transaction(d) as db:
                with self.assertRaises(Exception):db.execute("INSERT INTO gate_events(intent_id,action,at,detail) VALUES('i','wrong',1,'{}')")

if __name__=='__main__':unittest.main()
