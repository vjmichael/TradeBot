"""Source-only authoritative owner synthetic review lifecycle tests.

All positive worker and approval callbacks are *injected fixtures*, not
native authorization evidence. Existing vault encrypted transactions and
credential generation are real temporary SQLite/Fernet records.
"""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from paper_credential_vault import PaperVault
from managed_paper_vault import ManagedPaperVault,Refused
from vault_owner_hooks import ROLES,Attempt
from approval_ledger import Context
from owner_rpc_extension import FixedReadOnlyCoordinator


class OwnerCoordinator(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        path=Path(self.tmp.name)
        self.owner1=patch('managed_paper_vault._require_owner',return_value=None)
        self.owner2=patch('owner_rpc_extension._require_owner',return_value=None)
        self.owner1.start();self.owner2.start()
        root=PaperVault(path,Fernet.generate_key())
        policies={role:hashlib.sha256(
            ('maj-vaj:r2b:stored-unverified:v1:'+role).encode()).hexdigest() for role in ROLES}
        self.managed=ManagedPaperVault(root,b't'*32,policies)
        self.managed.migrate_empty(100)
        self.coordinator=FixedReadOnlyCoordinator(self.managed,clock=lambda:104)
        self.broker=patch('owner_rpc_extension.broker_request',return_value='f'*32)
        self.mock_broker=self.broker.start()
    def tearDown(self):
        self.broker.stop();self.owner2.stop();self.owner1.stop();self.tmp.cleanup()
    def enroll(self):
        self.managed.enroll('kraken_read',
            {'kraken_read_key':'local-only-synthetic','kraken_read_secret':'synthetic-secret'},101)

    def test_empty_vault_cannot_prepare(self):
        with self.assertRaises(Refused):self.coordinator.prepare()
        self.mock_broker.assert_not_called()
    def test_exact_generation_and_policy_prepared_from_ciphertext(self):
        self.enroll()
        result=self.coordinator.prepare()
        self.assertEqual(result,{'ok':True,'status':'awaiting_native_review'})
        op,rid,ctx=self.mock_broker.call_args.args
        self.assertEqual(op,'prepare');self.assertIsNone(rid)
        self.assertIs(type(ctx),Context)
        self.assertEqual(ctx.role,'kraken_read')
        self.assertEqual(len(ctx.generation),32)
        self.assertEqual(ctx.policy,self.managed._policies['kraken_read'])
        with self.assertRaises(Refused):self.coordinator.prepare()
    def test_cancel_invalidates_local_pending_even_on_broker_failure(self):
        self.enroll();self.coordinator.prepare()
        self.mock_broker.side_effect=Refused('broker_unavailable')
        self.assertEqual(self.coordinator.cancel(),
                         {'ok':True,'status':'review_cancelled'})
        self.assertEqual(self.coordinator.cancel()['ok'],False)
        with self.assertRaises(Refused):self.coordinator.finish()
    def test_finish_without_review_refused(self):
        with self.assertRaises(Refused):self.coordinator.finish()
    def test_finish_invokes_reserved_attempt_once_with_no_client_fields(self):
        self.enroll();self.coordinator.prepare()
        attempt=Attempt('kraken_read','a'*32,'b'*32,
                        self.managed._policies['kraken_read'],104,12345)
        with patch('owner_rpc_extension.reserve_after_approval',return_value=attempt) as reserve,patch(
                'owner_rpc_extension.finish_fixed_synthetic',return_value='provider_refused') as finish:
            result=self.coordinator.finish()
            self.assertEqual(result,{'ok':True,'status':'stored_unverified'})
            self.assertEqual(reserve.call_args.args[0],self.managed)
            self.assertEqual(reserve.call_args.args[1],'f'*32)
            self.assertEqual(finish.call_args.args[:2],(self.managed,attempt))
            with self.assertRaises(Refused):self.coordinator.finish()
            finish.assert_called_once()
    def test_failed_worker_response_never_retries(self):
        self.enroll();self.coordinator.prepare()
        with patch('owner_rpc_extension.reserve_after_approval',
                return_value=Attempt('kraken_read','a'*32,'b'*32,'c'*64,104,1)),patch(
                'owner_rpc_extension.finish_fixed_synthetic',
                side_effect=Refused('worker_result_refused')):
            with self.assertRaises(Refused):self.coordinator.finish()
            with self.assertRaises(Refused):self.coordinator.finish()
    def test_unsupported_clock_refused_before_worker(self):
        self.enroll();self.coordinator.prepare()
        self.coordinator._clock=lambda:True
        with patch('owner_rpc_extension.reserve_after_approval') as reserve:
            with self.assertRaises(Refused):self.coordinator.finish()
            reserve.assert_not_called()
            with self.assertRaises(Refused):self.coordinator.finish()

if __name__=='__main__':unittest.main()
