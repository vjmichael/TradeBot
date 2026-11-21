"""R2B operator agent source qualification; no installed approval claim.

The positive prompt response is a synthetic test fixture. Same-UID and
non-leader negative checks exercise kernel/session attestation policy,
not a simulated actual PAM login. Real native QEMU acceptance is separate.
"""
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

from approval_ledger import Ledger,Context,Refused,Witness
from broker_gateway import BrokerGateway
from agent_broker_channel import AgentHandler,next_unambiguous,attest_exact_gui,_one_line

W=Witness('c42',4312)
CTX=Context('provider_test','kraken_read','a'*32,'b'*64)
BOOT='f'*8+'-'+'f'*4+'-'+'f'*4+'-'+'f'*4+'-'+'f'*12


class Events:
    def __init__(self,session):
        self.session=session
        self.invalid=False
        self.closed=False
    def check(self):
        if self.invalid:raise Refused('session_event_invalidated')
    def close(self):
        self.closed=True


class AgentNativeSource(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.ledger=Ledger(Path(self.tmp.name)/'ledger.sqlite',
               clock=lambda:1_000_000_000_000,boot=lambda:BOOT)
        self.gateway=BrokerGateway(self.ledger,
            attest=lambda _:W,owner=lambda _:None,live=lambda _:True)
        self.server=Mock()
        self.server.gateway=self.gateway
        self.server.review_lock=threading.RLock()
        self.server.active=False
        self.server.latches={}
        self.events=[]
        def new_event(s):
            ev=Events(s)
            self.events.append(ev)
            return ev
        self.event_patch=patch('agent_broker_channel.SessionEvents',new_event)
        self.peer_patch=patch('agent_broker_channel.attest_exact_gui',lambda _:W)
        self.event_patch.start();self.peer_patch.start()
    def tearDown(self):
        self.peer_patch.stop();self.event_patch.stop()
        self.tmp.cleanup()

    def start(self,rid=None,response='approve', *,before_answer=None):
        if rid is None:rid=self.ledger.prepare(CTX)
        left,right=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        thread=threading.Thread(target=lambda:AgentHandler(left,None,self.server),daemon=True)
        thread.start()
        right.settimeout(3)
        try:
            right.sendall(b'{"op":"review_next"}\n')
            def recv():
                data=bytearray()
                while not data.endswith(b'\n'):
                    chunk=right.recv(128)
                    if not chunk:break
                    data.extend(chunk)
                return json.loads(data)
            first=recv()
            if 'request_id' in first:
                if before_answer:before_answer()
                reply={'op':'answer','request_id':first['request_id'],
                       'choice':response}
                right.sendall((json.dumps(reply)+'\n').encode())
                right.shutdown(socket.SHUT_WR)
                last=recv()
            else:
                last=first
            thread.join(4)
            self.assertFalse(thread.is_alive())
            return rid,first,last
        finally:
            right.close();left.close()

    def test_native_pinned_connection_one_use_yes(self):
        rid,preview,last=self.start()
        self.assertEqual(preview['role'],'kraken_read')
        self.assertEqual(last,{'ok':True})
        self.assertEqual(self.ledger.state_for_test(rid),'approved')
        self.assertEqual(len(self.server.latches),1)
        self.assertFalse(self.events[0].closed)

    def test_native_user_decline_is_not_approval(self):
        rid,preview,last=self.start(response='decline')
        self.assertEqual(last,{'ok':False})
        self.assertEqual(self.ledger.state_for_test(rid),'cancelled')
        self.assertFalse(self.server.latches)
        self.assertTrue(self.events[0].closed)

    def test_session_lock_during_prompt_denies(self):
        rid,preview,last=self.start(before_answer=lambda:setattr(self.events[0],'invalid',True))
        self.assertEqual(last,{'ok':False})
        self.assertNotEqual(self.ledger.state_for_test(rid),'approved')
        self.assertTrue(self.events[0].closed)

    def test_unknown_truthy_response_not_approved(self):
        rid,preview,last=self.start(response='yes please')
        self.assertEqual(last,{'ok':False})
        self.assertNotEqual(self.ledger.state_for_test(rid),'approved')

    def test_race_cancel_before_answer_denies(self):
        rid=self.ledger.prepare(CTX)
        _,first,last=self.start(rid,before_answer=lambda:self.ledger.cancel(rid))
        self.assertEqual(last,{'ok':False})
        self.assertEqual(self.ledger.state_for_test(rid),'cancelled')

    def test_ambiguous_pending_refused(self):
        self.ledger.prepare(CTX);self.ledger.prepare(CTX)
        with self.assertRaises(Refused):next_unambiguous(self.ledger)

    def test_wrong_process_refused_before_preview(self):
        self.peer_patch.stop()
        with patch('agent_broker_channel.attest_exact_gui',side_effect=Refused('not_native_session_leader')):
            rid,preview,last=self.start()
            self.assertEqual(last,{'ok':False})
            self.assertEqual(self.ledger.state_for_test(rid),'pending')
        self.peer_patch.start()

    def test_restart_revokes_approval(self):
        rid,_,last=self.start()
        self.assertTrue(last['ok'])
        restarted=Ledger(Path(self.tmp.name)/'ledger.sqlite',
                         clock=lambda:1_000_000_000_000,boot=lambda:BOOT)
        self.assertEqual(restarted.state_for_test(rid),'revoked')

    def test_genuine_lightdm_direct_child_passes(self):
        from operator_session import Peer
        with patch('agent_broker_channel.attest_operator_peer',return_value=W),patch(
                'agent_broker_channel.peer_from_socket',return_value=Peer(2041,4312,4312)),patch(
                'agent_broker_channel._session_leader',return_value=2042),patch(
                'agent_broker_channel._kernel_parent',return_value=2042):
            self.assertEqual(attest_exact_gui(None),W)

    def test_non_child_same_uid_refused(self):
        from operator_session import Peer
        with patch('agent_broker_channel.attest_operator_peer',return_value=W),patch(
                'agent_broker_channel.peer_from_socket',return_value=Peer(2041,4312,4312)),patch(
                'agent_broker_channel._session_leader',return_value=2042),patch(
                'agent_broker_channel._kernel_parent',return_value=9999):
            with self.assertRaises(Refused):attest_exact_gui(None)

    def test_kernel_proc_parent_unavailable_refused(self):
        from agent_broker_channel import _kernel_parent
        with patch('pathlib.Path.read_bytes',side_effect=PermissionError):
            with self.assertRaises(Refused):_kernel_parent(2041)
        for value in (b'not-a-proc-stat',b'2041 (python) R foo'):
            with patch('pathlib.Path.read_bytes',return_value=value):
                with self.assertRaises(Refused):_kernel_parent(2041)

    def test_kernel_proc_parent_parser(self):
        from agent_broker_channel import _kernel_parent
        with patch('pathlib.Path.read_bytes',return_value=
                b'873 (python3) S 833 833 833 0 -1 0 0 0 0 0 0 0 0'):
            self.assertEqual(_kernel_parent(873),833)

if __name__=='__main__':unittest.main()
