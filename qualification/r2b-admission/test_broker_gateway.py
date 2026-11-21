"""Broker gateway source tests: injected trusted callbacks are NOT native evidence.

The positive fixture verifies ordering and exactly-one consumption, never
authorizes actual installed provider execution. Wrong owner/operator
entrypoints fail closed before a prompt or state mutation.
"""
import socket
import tempfile
import unittest
from pathlib import Path
from approval_ledger import Context,Ledger,Refused,Witness
from broker_gateway import BrokerGateway,fixed_native_prompt

C=Context('provider_test','kraken_read','a'*32,'b'*64)
M=Context('legacy_migration','legacy',inventory='f'*64,roles=('kraken_read',))
W=Witness('c21',4312)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.clock=[1_000_000_000_000]
        self.boot='f'*8+'-'+'f'*4+'-'+'f'*4+'-'+'f'*4+'-'+'f'*12
        self.ledger=Ledger(Path(self.tmp.name)/'one-use.sqlite',
            clock=lambda:self.clock[0],boot=lambda:self.boot)
        self.owner,self.agent=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        self.calls=[]
        self.session=[W]
        def validate_owner(conn):
            if conn is not self.owner:
                raise Refused('vault_owner_peer_required')
        def attest(conn):
            if conn is not self.agent:
                raise Refused('operator_peer_refused')
            self.calls.append('attest')
            return self.session[0]
        def prompt(ctx):
            self.calls.append('prompt:'+ctx.role)
            return True
        self.gateway=BrokerGateway(self.ledger,owner=validate_owner,
            attest=attest,prompt=prompt,live=lambda w:self.session[0]==w)
    def tearDown(self):
        self.owner.close();self.agent.close();self.tmp.cleanup()

    def test_prepare_review_consume_exactly_once(self):
        rid=self.gateway.prepare_from_owner(self.owner,C)
        self.assertTrue(self.gateway.review_from_operator(self.agent,rid))
        self.assertEqual(self.gateway.consume_from_owner(self.owner,rid,C),C)
        with self.assertRaises(Refused):
            self.gateway.consume_from_owner(self.owner,rid,C)
        self.assertEqual(self.calls,['attest','attest','prompt:kraken_read','attest'])

    def test_wrong_owner_cannot_prepare(self):
        with self.assertRaises(Refused):
            self.gateway.prepare_from_owner(self.agent,C)
        self.assertFalse(self.calls)

    def test_operator_cannot_consume_owner_grant(self):
        rid=self.gateway.prepare_from_owner(self.owner,C)
        self.gateway.review_from_operator(self.agent,rid)
        with self.assertRaises(Refused):
            self.gateway.consume_from_owner(self.agent,rid,C)
        self.assertEqual(self.gateway.consume_from_owner(self.owner,rid,C),C)

    def test_owner_cannot_invoke_operator_prompt(self):
        rid=self.gateway.prepare_from_owner(self.owner,C)
        with self.assertRaises(Refused):
            self.gateway.review_from_operator(self.owner,rid)
        self.assertFalse(any(x.startswith('prompt:') for x in self.calls))

    def test_unknown_request_does_not_show_dialog(self):
        with self.assertRaises(Refused):
            self.gateway.review_from_operator(self.agent,'0'*32)
        self.assertFalse(any(x.startswith('prompt:') for x in self.calls))

    def test_live_pam_session_required_at_consume(self):
        rid=self.gateway.prepare_from_owner(self.owner,C)
        self.gateway.review_from_operator(self.agent,rid)
        self.session[0]=Witness('c22',W.uid)
        with self.assertRaises(Refused):
            self.gateway.consume_from_owner(self.owner,rid,C)

    def test_native_session_changes_during_review_refused(self):
        rid=self.gateway.prepare_from_owner(self.owner,C)
        def change(ctx):
            self.session[0]=Witness('c22',W.uid)
            return True
        self.gateway._prompt=change
        self.assertFalse(self.gateway.review_from_operator(self.agent,rid))
        self.assertEqual(self.ledger.state_for_test(rid),'cancelled')

    def test_provider_context_is_owner_held(self):
        rid=self.gateway.prepare_from_owner(self.owner,C)
        self.gateway.review_from_operator(self.agent,rid)
        alternate=Context('provider_test','kraken_read','d'*32,'b'*64)
        with self.assertRaises(Refused):
            self.gateway.consume_from_owner(self.owner,rid,alternate)
        self.assertEqual(self.gateway.consume_from_owner(self.owner,rid,C),C)

    def test_migration_binds_exact_private_ciphertext_inventory(self):
        rid=self.gateway.prepare_from_owner(self.owner,M)
        self.gateway.review_from_operator(self.agent,rid)
        changed=Context('legacy_migration','legacy',inventory='e'*64,roles=M.roles)
        with self.assertRaises(Refused):
            self.gateway.consume_from_owner(self.owner,rid,changed)
        self.assertEqual(self.gateway.consume_from_owner(self.owner,rid,M),M)

    def test_native_gateway_has_no_client_approved_parameter(self):
        rid=self.gateway.prepare_from_owner(self.owner,C)
        with self.assertRaises(TypeError):
            self.gateway.review_from_operator(self.agent,rid,approved=True)
        self.assertEqual(self.ledger.state_for_test(rid),'pending')

    def test_disallowed_provider_specific_preview_fails_before_gui(self):
        for role in ('telegram_desk','telegram_recovery','grok'):
            with self.subTest(role=role),self.assertRaises(Refused):
                fixed_native_prompt(Context('provider_test',role,'c'*32,'a'*64))
    def test_owner_cancel_refuses_future_approval(self):
        rid=self.gateway.prepare_from_owner(self.owner,C)
        self.assertTrue(self.gateway.cancel_from_owner(self.owner,rid))
        with self.assertRaises(Refused):self.gateway.review_from_operator(self.agent,rid)

if __name__=='__main__':unittest.main()
