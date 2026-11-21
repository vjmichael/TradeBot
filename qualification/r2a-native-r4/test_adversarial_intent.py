"""R4 independent source-locked adversarial IPC regressions.

No real users, credentials, console session or production daemon.
The only patched guard is the temporary local directory used by the
test fixture; production frame parsing, socket peer credentials and
listener inode cleanup execute unmodified.
"""
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from credential_handoff import INTENT, ACK, URI, validate_uri, intent_listener, receive_intent


class AdversarialIntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.runtime=Path(self.tmp.name)
        os.chmod(self.runtime,0o700)
        self.uid=os.getuid()
        self.owner_patch=patch('credential_handoff.protected_dir')
        self.owner_patch.start()

    def tearDown(self):
        self.owner_patch.stop()
        self.tmp.cleanup()

    def test_incomplete_frame_without_eof_cannot_ack(self):
        result=[]
        def sender(path):
            with socket.socket(socket.AF_UNIX) as peer:
                peer.settimeout(3.0)
                peer.connect(str(path))
                peer.sendall(INTENT)  # no shutdown; receiver requires EOF
                try:
                    result.append(peer.recv(16))
                except (OSError,TimeoutError):
                    result.append(b'')
        with intent_listener(self.runtime,self.uid) as listener:
            path=self.runtime/'maj-vaj-account-handoff-intent.sock'
            worker=threading.Thread(target=sender,args=(path,))
            worker.start()
            self.assertFalse(receive_intent(listener,self.uid))
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertNotIn(ACK,result)
        self.assertFalse(path.exists())

    def test_failed_frame_does_not_poison_next_valid_intent(self):
        responses=[]
        def sender(path,payload):
            try:
                with socket.socket(socket.AF_UNIX) as peer:
                    peer.settimeout(3)
                    peer.connect(str(path))
                    peer.sendall(payload)
                    peer.shutdown(socket.SHUT_WR)
                    responses.append(peer.recv(16))
            except (ConnectionResetError,BrokenPipeError):
                responses.append(b'')
        with intent_listener(self.runtime,self.uid) as listener:
            path=self.runtime/'maj-vaj-account-handoff-intent.sock'
            for payload,expected in [(b'OPEN\\nEXTRA',False),(INTENT,True)]:
                t=threading.Thread(target=sender,args=(path,payload))
                t.start()
                self.assertEqual(receive_intent(listener,self.uid),expected)
                t.join(5)
                self.assertFalse(t.is_alive())
        self.assertNotEqual(responses[0],ACK)
        self.assertEqual(responses[1],ACK)

    def test_listener_cleanup_refuses_inode_replacement(self):
        with intent_listener(self.runtime,self.uid):
            path=self.runtime/'maj-vaj-account-handoff-intent.sock'
            original=path.lstat()
            self.assertTrue(stat.S_ISSOCK(original.st_mode))
            path.unlink()
            path.write_text('synthetic replacement; not an intent socket')
        self.assertEqual(path.read_text(),'synthetic replacement; not an intent socket')

    def test_exact_uri_cannot_be_changed_by_encoding_or_extra_arguments(self):
        for invalid in (
            ('MAJVaj-account:open',),
            ('majvaj-account:%6Fpen',),
            ('majvaj-account:open?ticket=any',),
            ('majvaj-account:open#replay',),
            (URI,URI),
            ('',),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_uri(invalid)

if __name__=='__main__':
    unittest.main()
