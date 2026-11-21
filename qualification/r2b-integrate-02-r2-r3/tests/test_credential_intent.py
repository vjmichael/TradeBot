"""Synthetic same-UID handoff-intent tests; no native authorization or secrets."""
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from credential_handoff import (
    INTENT, ACK, intent_listener, receive_intent, relay_intent,
    URI, validate_uri
)


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tmp.name)
        os.chmod(self.runtime, 0o700)
        self.uid = os.getuid()
        # Synthetic sockets live under /tmp, unlike installed /run/user/<uid>.
        # Only the parent-path custody prerequisite is mocked here; real peer
        # credentials, exact frames, socket modes and cleanup remain exercised.
        self._custody_patch = patch('credential_handoff.protected_dir')
        self._custody_patch.start()
    def tearDown(self):
        self._custody_patch.stop()
        self.tmp.cleanup()

    def _client(self, path, data, response, errors):
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(2)
                conn.connect(str(path))
                conn.sendall(data)
                conn.shutdown(socket.SHUT_WR)
                response.append(conn.recv(16))
        except OSError as exc:
            errors.append(type(exc).__name__)

    def _exercise(self, data, accepted=True, uid=None):
        responses, errors = [], []
        with intent_listener(self.runtime, self.uid) as listener:
            path = self.runtime/'maj-vaj-account-handoff-intent.sock'
            st = path.lstat()
            self.assertTrue(stat.S_ISSOCK(st.st_mode))
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
            self.assertEqual(st.st_uid, self.uid)
            worker=threading.Thread(target=self._client,args=(path,data,responses,errors))
            worker.start()
            value=receive_intent(listener, self.uid if uid is None else uid)
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(value,accepted)
            if accepted:
                self.assertFalse(errors)
                self.assertEqual(responses,[ACK])
            else:
                # Linux can reset a Unix stream when the server rejects an
                # oversized/malformed frame without draining unread bytes.
                # A rejected request must never receive a positive ACK.
                self.assertNotIn(ACK,responses)
                self.assertTrue(
                    (responses == [b''] and not errors) or
                    (not responses and errors in (['ConnectionResetError'], ['BrokenPipeError'])),
                    (responses,errors)
                )
        self.assertFalse(path.exists())
        return value

    def test_exact_message(self):
        self.assertEqual(INTENT,b'OPEN\n')
        self.assertEqual(ACK,b'QUEUED\n')
        self._exercise(INTENT)
    def test_bad_request_rejected(self):
        for bad in (b'', b'OPEN', b'OPEN\nEXTRA', b'OPEN\r\n', b'RESET\n',
                    b'OPEN\n\0', b'ENROLL\n', b'OPEN\n'*4):
            with self.subTest(bad=repr(bad)):
                self._exercise(bad,False)
    def test_peer_uid_refused(self):
        self._exercise(INTENT,False,self.uid+1)
    def test_stale_socket_refused_no_unlink(self):
        path=self.runtime/'maj-vaj-account-handoff-intent.sock'
        path.write_text('untrusted')
        with self.assertRaises(ValueError):
            with intent_listener(self.runtime,self.uid):
                pass
        self.assertEqual(path.read_text(),'untrusted')
    def test_cancelled_listener_cleanup(self):
        path=self.runtime/'maj-vaj-account-handoff-intent.sock'
        with self.assertRaises(RuntimeError):
            with intent_listener(self.runtime,self.uid):
                raise RuntimeError('fixture cancelled')
        self.assertFalse(path.exists())
    def test_no_external_authority(self):
        for value in ([],[URI,'something'],['majvaj-account:open?token=x'],['https://example.com/']):
            with self.assertRaises(ValueError):
                validate_uri(value)
    def test_relay_is_separate_from_review(self):
        # A desktop URI handler must call relay, never invoke handoff itself.
        from credential_handoff import main
        with patch('credential_handoff.harden_process'),patch('credential_handoff.relay_intent') as relay,patch('credential_handoff.handoff') as handoff,patch('credential_handoff.sys.argv',['app',URI]):
            main()
            relay.assert_called_once_with()
            handoff.assert_not_called()
    def test_local_socket_is_not_a_key_export(self):
        self.assertNotIn(b'secret',INTENT.lower())
        self.assertNotIn(b'credential',ACK.lower())
        self.assertLessEqual(len(INTENT),8)
        self.assertLessEqual(len(ACK),8)

if __name__=='__main__':
    unittest.main()
