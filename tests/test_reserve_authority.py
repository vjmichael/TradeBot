"""Keyless attestation qualification using real encrypted approval fixtures."""
import unittest
from financial_ledger import AccountingError, SCALE
from reserve_runtime import unlock, tick_all
from reserve_authority import public_key, verify_active, check, sign
from reserve_policy import DAY
import test_reserve_graphical as fixture


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        h=fixture.GraphicalTests('test_atomic_adoption_and_keyless_cycle_with_profit_protection');h.setUp();self.addCleanup(h.doCleanups);self.h=h
        self.public=public_key(unlock(h.creds,fixture.PASSWORD));h.b.authority_public=self.public
    def tick(self,key):
        return tick_all(self.h.tx,clock=self.h.clock,verify=self.h.b.verify,verify_authority=lambda db,i,s,now:verify_active(db,i,s,now,key))
    def ready(self):
        self.h.h.profitable('new');self.h.h.h.now+=DAY;self.h.h.observe('cycle')
    def test_keyless_signature_and_release(self):
        self.assertEqual(self.h.activate()['state'],'on');self.ready()
        self.assertEqual(self.tick(self.public)['a']['release'],5*SCALE)
        self.assertEqual(self.h.snap()['protected'],95*SCALE)
    def test_wrong_anchor_stops_cycle(self):
        self.h.activate();self.ready()
        self.assertEqual(self.tick(public_key(b'x'*32))['a']['release'],0)
        self.assertEqual(self.h.snap()['protected'],100*SCALE)
    def test_legacy_hmac_cannot_release_under_public_verifier(self):
        self.h.h.activate();self.ready()
        self.assertEqual(self.tick(self.public)['a']['release'],0)
    def test_wrong_pin_refuses_activation(self):
        self.h.b.authority_public=public_key(b'x'*32)
        with self.assertRaisesRegex(AccountingError,'approval_anchor_mismatch'):self.h.activate()
        self.assertIsNone(self.h.snap()['reserve_policy']['active'])
    def test_message_scope_and_missing_signature(self):
        seed=b'z'*32;sig=sign(seed,b'original');check(public_key(seed),b'original',sig)
        for msg,s in ((b'changed',sig),(b'original',None),(b'original','0'*128)):
            with self.assertRaises(AccountingError):check(public_key(seed),msg,s)
