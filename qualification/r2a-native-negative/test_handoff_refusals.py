"""R2A source-level refusal matrix against the exact credential_handoff module.

Synthetic state only. No os session, GUI, provider account, secret, or trading
authorization is created. Run within the restored R2B R3 source checkout.
"""
import unittest

from credential_handoff import URI, handoff


class Events:
    def __init__(self, session, *, fail_at=None):
        self.session = session
        self.fail_at = fail_at
        self.checks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def check(self):
        self.checks += 1
        if self.fail_at == self.checks:
            raise RuntimeError('synthetic session revocation')


class HandoffRefusalTests(unittest.TestCase):
    def setUp(self):
        self.original = ('session-a', ':0', '/tmp/no-actual-Xauthority', b'synthetic-cookie-hash')
        self.terminated = []
        self.end = self.terminated.append

    def snapshot(self):
        return self.original

    def events(self, session):
        self.assertEqual(session, 'session-a')
        return Events(session)

    def run_handoff(self, ask, *, snapshot=None, events=None, args=(URI,)):
        return handoff(args, snapshot=snapshot or self.snapshot,
                       events_factory=events or self.events, ask=ask, end=self.end)

    def test_cancel_does_not_terminate_session(self):
        self.assertFalse(self.run_handoff(lambda: False))
        self.assertEqual(self.terminated, [])

    def test_dismissal_is_not_consent(self):
        for value in (None, 0, 1, 'yes', object()):
            with self.subTest(value=repr(value)):
                self.assertFalse(self.run_handoff(lambda v=value: v))
                self.assertEqual(self.terminated, [])

    def test_review_exception_fails_closed(self):
        def unavailable():
            raise RuntimeError('synthetic closed window')
        with self.assertRaises(RuntimeError):
            self.run_handoff(unavailable)
        self.assertEqual(self.terminated, [])

    def test_session_switch_while_prompt_open_refuses(self):
        current = [self.original]
        changed = ('session-b', ':0', self.original[2], self.original[3])
        def snapshot():
            return current[0]
        def approve_after_switch():
            current[0] = changed
            return True
        with self.assertRaises(Exception):
            self.run_handoff(approve_after_switch, snapshot=snapshot)
        self.assertEqual(self.terminated, [])

    def test_cookie_change_while_prompt_open_refuses(self):
        current = [self.original]
        def snapshot():
            return current[0]
        def approve_after_cookie_change():
            current[0] = (self.original[0], self.original[1], self.original[2], b'new-cookie')
            return True
        with self.assertRaises(Exception):
            self.run_handoff(approve_after_cookie_change, snapshot=snapshot)
        self.assertEqual(self.terminated, [])

    def test_revocation_before_review_refuses(self):
        called = []
        def ask():
            called.append(True)
            return True
        with self.assertRaises(RuntimeError):
            self.run_handoff(ask, events=lambda session: Events(session,fail_at=1))
        self.assertFalse(called)
        self.assertEqual(self.terminated, [])

    def test_revocation_after_review_refuses(self):
        called = []
        def ask():
            called.append(True)
            return True
        with self.assertRaises(RuntimeError):
            self.run_handoff(ask,events=lambda session: Events(session,fail_at=3))
        self.assertEqual(called,[True])
        self.assertEqual(self.terminated, [])

    def test_invalid_uri_never_asks_or_terminates(self):
        def ask():
            self.fail('invalid intent reached review')
        for bad in ((),('majvaj-account:open?key=x',),(URI,'extra')):
            with self.subTest(args=bad),self.assertRaises(Exception):
                self.run_handoff(ask,args=bad)
            self.assertEqual(self.terminated, [])

    def test_valid_confirmation_terminates_only_origin(self):
        self.assertTrue(self.run_handoff(lambda: True))
        self.assertEqual(self.terminated,['session-a'])


if __name__ == '__main__':
    unittest.main()
