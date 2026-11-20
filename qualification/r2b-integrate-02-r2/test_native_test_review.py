import unittest
from native_test_review import review, LocalIntent

class Event:
    def __init__(self,session):self.session=session;self.checked=0
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def check(self):self.checked+=1

class Tests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.calls=[];self.identity_calls=0
        self.snapshot=('7',':0','/home/operator/.Xauthority',b'mock-hash')
    def ident(self):self.identity_calls+=1;return self.snapshot
    def ev(self,s):e=Event(s);self.events.append(e);return e
    def ask(self,role):self.calls.append(role);return True
    def attempt(self,role='kraken_read',**kw):
        a=dict(role=role, identity=self.ident,events=self.ev,ask=self.ask);a.update(kw);return review(**a)
    def test_allowed_review(self):self.assertEqual(self.attempt(),LocalIntent('kraken_read','7'))
    def test_desk_review(self):self.assertEqual(self.attempt('telegram_desk').role,'telegram_desk')
    def test_recovery_review(self):self.assertEqual(self.attempt('telegram_recovery').role,'telegram_recovery')
    def test_grok_optin(self):self.assertEqual(self.attempt('grok',opted_in=True).role,'grok')
    def test_grok_disabled(self):self.assertIsNone(self.attempt('grok'));self.assertEqual(self.identity_calls,0)
    def test_grok_true_only(self):self.assertIsNone(self.attempt('grok',opted_in=1))
    def test_cancel(self):self.assertIsNone(self.attempt(ask=lambda _:False))
    def test_nonstrict_true(self):self.assertIsNone(self.attempt(ask=lambda _:1))
    def test_unknown_role_no_side_effect(self):
        with self.assertRaises(ValueError):self.attempt(role='wallet_seed')
        self.assertEqual(self.calls,[])
    def test_identity_changed(self):
        def changed():self.identity_calls+=1;return self.snapshot if self.identity_calls==1 else ('8',)+self.snapshot[1:]
        with self.assertRaises(ValueError):self.attempt(identity=changed)
    def test_session_guard_checked(self):self.attempt();self.assertGreaterEqual(self.events[0].checked,4)
    def test_prompt_failure_not_approved(self):
        def bad(_):raise RuntimeError('GUI unavailable')
        with self.assertRaises(RuntimeError):self.attempt(ask=bad)
    def test_no_secret_properties(self):
        r=self.attempt();self.assertEqual(set(r.__dict__),{'role','session'})
    def test_missing_session(self):
        with self.assertRaises(ValueError):self.attempt(identity=lambda:())

if __name__=='__main__':unittest.main()