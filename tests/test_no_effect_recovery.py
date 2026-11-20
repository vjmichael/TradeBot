"""Positive native phase evidence and restrictive reviewed recovery checks."""
import tempfile
import unittest
from pathlib import Path
from reserve_outage_protection import save_grant
from reserve_no_effect import prepared, possible, seal, _name, _body, _signed, verify_native, consume, NATIVE_DOMAIN, HUMAN_DOMAIN
from financial_ledger import sha
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from risk_gate import GateRefusal
import test_risk_gate as gate_fixture


class PhaseTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.work=Path(tmp.name)
        self.row=dict(id='i',instance_id='a',pair='BTC/USD',entry=100,stop=90,target=120,notional=500,reserved=510,expires=200)
        self.binding='a'*64;self.nonce='b'*32;self.key=b'x'*32;self.ready=sha([self.row,self.nonce,self.binding])
        save_grant(self.work,self.key,self.row,self.nonce,self.binding,100,102)
    def prepare(self):prepared(self.work,self.key,self.row,self.binding,self.ready)
    def seal(self):return seal(self.work,self.key,self.row,self.binding,400)
    def mark(self):possible(self.work,self.key,self.row,self.binding,self.ready)
    def test_missing_phase_is_not_nonexecution_proof(self):
        with self.assertRaises(FileNotFoundError):self.seal()
    def test_prepared_can_seal_once_and_cannot_execute_after_seal(self):
        self.prepare();self.assertEqual(self.seal(),(self.row,self.ready));self.assertEqual(self.seal(),(self.row,self.ready))
        with self.assertRaises(ValueError):self.mark()
        with self.assertRaises(FileExistsError):self.prepare()
    def test_possible_phase_can_never_seal_or_reset(self):
        self.prepare();self.mark()
        with self.assertRaisesRegex(ValueError,'effect_possible_or_unproven'):self.seal()
        with self.assertRaises(ValueError):self.mark()
        with self.assertRaises(FileExistsError):self.prepare()
    def test_tampered_phase_and_changed_scope_refused(self):
        self.prepare();p=self.work/_name(self.binding,'i');raw=p.read_bytes()
        with self.assertRaises(ValueError):seal(self.work,self.key,self.row|{'entry':101},self.binding,400)
        p.write_bytes(raw.replace(b'PREPARED',b'POSSIBLE'))
        with self.assertRaises(ValueError):self.seal()
        p.unlink();p.symlink_to('/dev/null')
        with self.assertRaises(OSError):self.seal()
    def test_native_and_human_signatures_are_domain_separated(self):
        seed=b'y'*32;public=Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        b=_body('paper',self.row,self.binding,self.ready,1,'c'*64,1,'d'*64,1000,100)
        p=_signed(seed,NATIVE_DOMAIN,b);self.assertEqual(verify_native(public,'paper',p),b)
        with self.assertRaises(ValueError):verify_native(public,'paper',_signed(seed,HUMAN_DOMAIN,b))
        with self.assertRaises(ValueError):consume(None,{'proof':p,'approval':p},None,public,public,'paper',store=None)


class LegacyResolutionTests(unittest.TestCase):
    def test_native_bound_claim_cannot_use_generic_no_fill(self):
        from paper_execution import append_event
        f=gate_fixture.GateTests();f.setUp();self.addCleanup(f.doCleanups)
        intent=f.admit()['intent_id'];token=f.g.claim(intent,'paper-worker',1)['claim_token']
        with f.h.tx() as db:append_event(db,intent,'native_bound',{'binding':'a'*64},f.h.now)
        with self.assertRaisesRegex(GateRefusal,'protected_native_resolution_required'):f.g.trusted_resolution(intent,token,'not-proof','NO_FILL')
        with f.h.tx() as db:self.assertEqual(db.execute('SELECT state FROM gate_intents WHERE id=?',(intent,)).fetchone()[0],'CLAIMED')
