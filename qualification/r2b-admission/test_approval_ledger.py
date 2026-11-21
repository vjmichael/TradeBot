"""R2B native admission candidate: durable SQLite one-use and kernel checks.

All native prompt callbacks and logind data here are synthetic for source
qualification only. A production installed broker must supply the actual
PAM/LightDM native UI, SO_PEERCRED and protected signing service.
"""
import os
from pathlib import Path
import socket
import stat
import tempfile
import unittest
from unittest.mock import patch

from approval_ledger import Context,Ledger,Refused,Witness
from operator_session import Peer, peer_from_socket, check_live_session, attest_operator_peer

POLICY='a'*64
GEN='b'*32
INV='c'*64
W=Witness('c42',4312)
PROVIDER=Context('provider_test','kraken_read',GEN,POLICY)
MIGRATION=Context('legacy_migration','legacy',inventory=INV,roles=('kraken_read','telegram_desk'))


class LedgerAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'admission.sqlite'
        self.t=[100_000_000_000]
        self.boot=['f'*8+'-'+'f'*4+'-'+'f'*4+'-'+'f'*4+'-'+'f'*12]
        self.ledger=Ledger(self.path,clock=lambda:self.t[0],boot=lambda:self.boot[0])
    def tearDown(self):
        self.tmp.cleanup()
    def prepare(self,c=PROVIDER,ttl=120):
        return self.ledger.prepare(c,ttl_seconds=ttl)
    def approve(self,rid,yes=True,after=W):
        return self.ledger.review_with_native_prompt(rid,witness=lambda:W,prompt=lambda:yes,reattest=lambda:after)
    def consume(self,rid,c=PROVIDER,live=True):
        return self.ledger.consume(rid,c,live_session=lambda w:live if w==W else False)
    def test_exact_one_use(self):
        rid=self.prepare();self.assertTrue(self.approve(rid))
        self.assertEqual(self.consume(rid),PROVIDER)
        self.assertEqual(self.ledger.state_for_test(rid),'consumed')
        with self.assertRaises(Refused):self.consume(rid)
    def test_pending_cannot_consume(self):
        with self.assertRaises(Refused):self.consume(self.prepare())
    def test_cancel_refuses_and_cannot_resurrect(self):
        rid=self.prepare();self.assertTrue(self.ledger.cancel(rid))
        self.assertFalse(self.ledger.cancel(rid))
        with self.assertRaises(Refused):self.approve(rid)
    def test_declined_prompt_refused(self):
        rid=self.prepare();self.assertFalse(self.approve(rid,yes=False))
        self.assertEqual(self.ledger.state_for_test(rid),'cancelled')
        with self.assertRaises(Refused):self.consume(rid)
    def test_truthy_prompt_not_boolean_refused(self):
        rid=self.prepare();self.assertFalse(self.approve(rid,yes='yes'))
    def test_session_change_during_prompt_refused(self):
        rid=self.prepare();self.assertFalse(self.approve(rid,after=Witness('c43',W.uid)))
    def test_revocation_prevents_consumption(self):
        rid=self.prepare();self.approve(rid)
        with self.assertRaises(Refused):self.consume(rid,live=False)
        self.assertEqual(self.ledger.state_for_test(rid),'approved')
    def test_credential_generation_change_refused(self):
        rid=self.prepare();self.approve(rid)
        with self.assertRaises(Refused):self.consume(rid,Context('provider_test','kraken_read','d'*32,POLICY))
        self.assertEqual(self.consume(rid),PROVIDER)
    def test_policy_change_refused(self):
        rid=self.prepare();self.approve(rid)
        with self.assertRaises(Refused):self.consume(rid,Context('provider_test','kraken_read',GEN,'d'*64))
    def test_broker_restart_cannot_replay(self):
        rid=self.prepare();self.approve(rid)
        self.ledger=Ledger(self.path,clock=lambda:self.t[0],boot=lambda:self.boot[0])
        self.assertEqual(self.ledger.state_for_test(rid),'revoked')
        with self.assertRaises(Refused):self.consume(rid)
    def test_boot_change_cannot_replay(self):
        rid=self.prepare();self.approve(rid)
        self.boot[0]='e'*8+'-'+'e'*4+'-'+'e'*4+'-'+'e'*4+'-'+'e'*12
        with self.assertRaises(Refused):self.consume(rid)
    def test_monotonic_clock_rollback_refused(self):
        rid=self.prepare();self.approve(rid)
        self.t[0]-=10
        with self.assertRaises(Refused):self.consume(rid)
    def test_expiry_before_confirmation_refused(self):
        rid=self.prepare(ttl=1);self.t[0]+=1_000_000_000
        with self.assertRaises(Refused):self.approve(rid)
    def test_expiry_after_confirmation_refused(self):
        rid=self.prepare(ttl=1);self.approve(rid)
        self.t[0]+=1_000_000_000
        with self.assertRaises(Refused):self.consume(rid)
    def test_invalid_ttl_and_request_id_refused(self):
        for x in (0,121,True,'120',None):
            with self.subTest(x=x),self.assertRaises(Refused):self.prepare(ttl=x)
        with self.assertRaises(Refused):self.ledger.cancel('../credential')
    def test_migration_exact_inventory(self):
        rid=self.prepare(MIGRATION);self.approve(rid)
        with self.assertRaises(Refused):
            self.consume(rid,Context('legacy_migration','legacy',inventory='d'*64,roles=MIGRATION.roles))
        self.assertEqual(self.consume(rid,MIGRATION),MIGRATION)
    def test_partial_or_unknown_role_refused(self):
        bad=[
          Context('provider_test','kraken_trade',GEN,POLICY),
          Context('provider_test','kraken_read','x'*32,POLICY),
          Context('legacy_migration','legacy',inventory=INV,roles=('telegram_desk','kraken_read')),
          Context('legacy_migration','legacy',inventory=INV,roles=('kraken_read','trade')),
          Context('legacy_migration','legacy',inventory=INV,roles=()),
        ]
        for c in bad:
            with self.subTest(c=c), self.assertRaises(Refused):self.prepare(c)
    def test_db_mode_and_symlink_refused(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o600)
        os.chmod(self.path,0o644)
        with self.assertRaises(Refused):self.prepare()
        os.chmod(self.path,0o600)
        self.path.unlink()
        self.path.symlink_to(Path(self.tmp.name)/'other.sqlite')
        with self.assertRaises(Refused):self.prepare()
    def test_prepare_does_not_approve(self):
        rid=self.prepare()
        self.assertEqual(self.ledger.state_for_test(rid),'pending')


class KernelSessionGuard(unittest.TestCase):
    def test_actual_af_unix_peer_credentials(self):
        one,two=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            peer=peer_from_socket(one)
            self.assertEqual(peer.uid,os.getuid())
            self.assertEqual(peer.pid,os.getpid())
        finally:one.close();two.close()
    def test_wrong_peer_uid_refused_before_session(self):
        one,two=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            with patch('operator_session.pwd.getpwnam') as p:
                p.return_value.pw_uid=os.getuid()+17
                with self.assertRaises(Refused):attest_operator_peer(one)
        finally:one.close();two.close()
    def test_session_must_be_genuine_active_local_lightdm(self):
        fields=dict(User=str(W.uid),Name='maj-vaj-credential-operator',
            Seat='seat0',Service='lightdm',Type='x11',Class='user',
            Active='yes',Remote='no',LockedHint='no')
        with patch('operator_session._login_properties',return_value=fields.copy()):
            self.assertTrue(check_live_session(W))
        for key,value in (('Seat','seat1'),('Active','no'),('Remote','yes'),
                          ('LockedHint','yes'),('User','9999'),('Type','tty'),
                          ('Service','ssh'),('Name','maj-vaj-console')):
            bad=fields.copy();bad[key]=value
            with self.subTest(key=key),patch('operator_session._login_properties',return_value=bad):
                self.assertFalse(check_live_session(W))
    def test_invalid_session_never_invokes_logind(self):
        with patch('operator_session._login_properties') as p:
            with self.assertRaises(Refused):
                check_live_session(Witness('../broken',W.uid))
            p.assert_not_called()

if __name__=='__main__':unittest.main()
