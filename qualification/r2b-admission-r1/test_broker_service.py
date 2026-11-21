"""R2B R1 restricted broker socket source qualification.

A test fixture may inject an owner-UID guard because source tests have no
maj-vaj-vault account. Real installed Debian acceptance MUST exercise actual
SO_PEERCRED and service UID through systemd; no source fixture is authority.
"""
import grp
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

from approval_ledger import Context,Ledger,Refused
from broker_gateway import BrokerGateway
from operator_session import peer_from_socket
from broker_service import parse_frame,context_from_request,dispatch,Server

CTX=Context('provider_test','kraken_read','a'*32,'b'*64)
CANON={
 'operation':'provider_test','role':'kraken_read',
 'generation':'a'*32,'policy':'b'*64,'inventory':'','roles':[]
}


class FrameAndRouting(unittest.TestCase):
    def test_exact_owner_context_validates(self):
        self.assertEqual(context_from_request(CANON),CTX)
    def test_unknown_context_field_refused(self):
        with self.assertRaises(Refused):
            context_from_request(dict(CANON,endpoint='https://bad.invalid'))
    def test_no_approved_or_mutation_over_ipc(self):
        p={'op':'review','request_id':'a'*32,'approved':True}
        mock=Mock()
        with self.assertRaises(Refused):dispatch(mock,None,p)
        mock.assert_not_called()
        self.assertFalse(mock.method_calls)
    def test_unknown_operation_refused(self):
        with self.assertRaises(Refused):
            dispatch(Mock(),None,{'op':'execute_provider','role':'kraken_read'})
    def test_exact_ascii_one_line(self):
        self.assertEqual(parse_frame(b'{"op":"cancel","request_id":"'+b'a'*32+b'"}\n')['op'],'cancel')
    def test_duplicate_json_fields_refused(self):
        with self.assertRaises(Refused):parse_frame(b'{"op":"cancel","op":"prepare"}\n')
    def test_incomplete_overlong_and_trailing_refused(self):
        for frame in (b'',b'{}',b'{}\n{}\n',b'a'*1025+b'\n',b'{"op":"a"}\nEXTRA',b'{bad}\n',b'{"op":"cancel"}\x00\n'):
            with self.subTest(frame=frame[:25]),self.assertRaises(Refused):
                parse_frame(frame)
    def test_unknown_or_boolean_role_refused(self):
        with self.assertRaises(Refused):
            context_from_request(dict(CANON,role=True))
    def test_owner_prepare_requires_correct_source(self):
        gateway=Mock()
        gateway.prepare_from_owner.side_effect=Refused('vault_owner_peer_required')
        with self.assertRaises(Refused):
            dispatch(gateway,None,{'op':'prepare','context':CANON})
    def test_owner_cancel_uses_only_opaque_id(self):
        gateway=Mock()
        gateway.cancel_from_owner.return_value=True
        response=dispatch(gateway,None,{'op':'cancel','request_id':'a'*32})
        self.assertEqual(response,{'ok':True})
        with self.assertRaises(Refused):
            dispatch(gateway,None,{'op':'cancel','request_id':'a'*32,'approved':True})


class GenuineLocalAFUnix(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.ledger=Ledger(self.root/'approval.sqlite')
        def owner(conn):
            peer=peer_from_socket(conn)
            if peer.uid!=os.getuid():raise Refused('owner_peer_refused')
        self.gate=BrokerGateway(self.ledger,owner=owner,
              attest=lambda conn:(_ for _ in ()).throw(Refused('operator_review_disabled')))
        self.path=self.root/'r2b.sock'
        self.gid=os.getgid()
    def tearDown(self):self.tmp.cleanup()
    def test_prepare_cancel_restart_refuses_consumption(self):
        with patch('grp.getgrnam') as group,patch('broker_service.os.getgroups',return_value=[self.gid]):
            group.return_value.gr_gid=self.gid
            with Server(self.path,self.gate) as srv:
                thread=threading.Thread(target=srv.serve_forever,kwargs={'poll_interval':.01})
                thread.start()
                def rpc(payload):
                    data=json.dumps(payload,separators=(',',':')).encode()+b'\n'
                    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
                        s.settimeout(2);s.connect(str(self.path))
                        s.sendall(data);s.shutdown(socket.SHUT_WR)
                        return json.loads(s.recv(1024))
                try:
                    r=rpc({'op':'prepare','context':CANON})
                    self.assertTrue(r['ok'])
                    rid=r['request_id']
                    self.assertFalse(rpc({'op':'consume','request_id':rid,'context':CANON})['ok'])
                    self.assertFalse(rpc({'op':'review','request_id':rid,'approved':True})['ok'])
                    self.assertTrue(rpc({'op':'cancel','request_id':rid})['ok'])
                    self.assertFalse(rpc({'op':'cancel','request_id':rid})['ok'])
                    self.assertFalse(rpc({'op':'consume','request_id':rid,'context':CANON})['ok'])
                finally:
                    srv.shutdown();thread.join(3)
                self.assertFalse(thread.is_alive())
        self.assertFalse(self.path.exists())
        rid2=self.ledger.prepare(CTX)
        self.ledger=Ledger(self.root/'approval.sqlite')
        self.assertEqual(self.ledger.state_for_test(rid2),'revoked')
        with self.assertRaises(Refused):
            self.ledger.consume(rid2,CTX,live_session=lambda _:True)

    def test_refuses_preexisting_file_without_unlink(self):
        self.path.write_text('no untrusted replacement unlink')
        with self.assertRaises(Refused):
            Server(self.path,self.gate)
        self.assertEqual(self.path.read_text(),'no untrusted replacement unlink')

if __name__=='__main__':unittest.main()
