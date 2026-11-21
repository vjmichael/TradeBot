"""R4 authoritative owner adversarial unit tests.

These verify real encrypted local records and fixed source methods only.
Injected broker/worker callbacks are NOT independent native acceptance.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from approval_ledger import Refused
from test_owner_rpc_extension import OwnerCoordinator
from vault_owner_hooks import Attempt
from owner_synthetic_bridge import reserve_after_approval,finish_fixed_synthetic
from synthetic_worker_protocol import sign_denial,verify_denial


class R4OwnerAdversarial(unittest.TestCase):
    def setUp(self):
        self.fixture=OwnerCoordinator('test_empty_vault_cannot_prepare')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def prepare(self):
        f=self.fixture
        f.enroll()
        self.assertTrue(f.coordinator.prepare()['ok'])
        op,rid,context=f.mock_broker.call_args.args
        self.assertEqual(op,'prepare')
        return context

    def test_replacement_between_review_and_consume_refuses(self):
        f=self.fixture
        old=self.prepare()
        f.managed.enroll('kraken_read',{
            'kraken_read_key':'replaced-local-only',
            'kraken_read_secret':'replaced-secret'},102)
        with patch('owner_synthetic_bridge.broker_request') as consume:
            with self.assertRaises(Refused):
                f.coordinator.finish()
            consume.assert_not_called()
        self.assertEqual(f.managed.status()['credentials']['kraken_read_key'],
                         'stored_unverified')
        with self.assertRaises(Refused):
            f.coordinator.finish()
        self.assertNotEqual(old.generation,
                            f.managed._owner_generation if hasattr(f.managed,'_owner_generation') else
                            self._generation())

    def _generation(self):
        f=self.fixture
        with f.managed._vault.transaction() as db:
            owner=f.managed._owner(db)
            return f.managed._consistent(db,owner)['roles']['kraken_read']['generation']

    def test_removal_between_review_and_consume_refuses(self):
        f=self.fixture
        self.prepare()
        f.managed.remove('kraken_read',102)
        with patch('owner_synthetic_bridge.broker_request') as consume:
            with self.assertRaises(Refused):
                f.coordinator.finish()
            consume.assert_not_called()
        self.assertEqual(f.managed.status()['credentials']['kraken_read_key'],
                         'not_configured')

    def test_worker_crash_after_reserved_attempt_never_retries(self):
        f=self.fixture
        context=self.prepare()
        with patch('owner_synthetic_bridge.broker_request',return_value=True) as consume:
            attempt=reserve_after_approval(f.managed,'f'*32,context,104)
            self.assertIs(type(attempt),Attempt)
            consume.assert_called_once()
            with patch('owner_synthetic_bridge.pwd.getpwnam',
                       return_value=SimpleNamespace(pw_uid=4202)),patch(
                       'owner_synthetic_bridge.read_owner_receipt_key',
                       return_value=b'T'*32),patch(
                       'owner_synthetic_bridge._private_socket',
                       side_effect=OSError('synthetic-worker-dead')):
                with self.assertRaises(OSError):
                    finish_fixed_synthetic(f.managed,attempt,now=104)
            with self.assertRaises(Refused):
                reserve_after_approval(f.managed,'f'*32,context,105)
            consume.assert_called_once()
        f.managed.startup_invalidate(106)
        self.assertEqual(f.managed.status()['credentials']['kraken_read_key'],
                         'stored_unverified')

    def test_signed_wrong_key_cannot_finish(self):
        attempt=Attempt('kraken_read','a'*32,'b'*32,'c'*64,1000,47)
        receipt=sign_denial(attempt,b'A'*32,1001)
        with self.assertRaises(Refused):
            verify_denial(receipt,attempt,b'B'*32,1001)


if __name__=='__main__':
    unittest.main()
