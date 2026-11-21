"""Offline R2B worker provenance verifier: genuine HMAC and SQLite tests.

All provider reports are synthetic. These tests do not prove worker UID,
native operator approval, OS key provisioning or real provider permissions.
"""
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from vault_owner_hooks import Attempt, Refused, OwnerHooks
from worker_receipt_verifier import _signed_bytes, WORKER, verify_receipt

KEY=b'unit-test-independent-worker-receipt-key-64-bytes------'
ATTEMPT=Attempt('kraken_read','a'*32,'b'*32,'c'*64,1000,1000000)

def signed(attempt=ATTEMPT, result='permissions_verified', key=KEY, **updates):
    obj={
        'version':1, 'worker':WORKER[attempt.role], 'role':attempt.role,
        'generation':attempt.generation, 'attempt_id':attempt.attempt_id,
        'policy':attempt.policy,'started':attempt.started,'nonce':attempt.nonce,
        'result':result,'observed':attempt.started+1,'mac':'0'*64,
    }
    obj.update(updates)
    obj['mac']=hmac.new(key,_signed_bytes(obj),hashlib.sha256).hexdigest()
    return obj

class WorkerReceiptTests(unittest.TestCase):
    def ok(self,receipt,attempt=ATTEMPT,now=1003,key=KEY):
        return verify_receipt(receipt,attempt,key,now)

    def test_exact_hmac_attested_result(self):
        self.assertEqual(self.ok(signed()),'permissions_verified')

    def test_wrong_key_refused(self):
        with self.assertRaises(Refused):self.ok(signed(),key=b'A'*32)

    def test_modified_result_refused_even_if_valid_enum(self):
        row=signed();row['result']='provider_refused'
        with self.assertRaises(Refused):self.ok(row)

    def test_wrong_attempt_id_even_if_signed(self):
        with self.assertRaises(Refused):self.ok(signed(attempt_id='f'*32))

    def test_wrong_credential_generation_even_if_signed(self):
        with self.assertRaises(Refused):self.ok(signed(generation='d'*32))

    def test_wrong_policy_even_if_signed(self):
        with self.assertRaises(Refused):self.ok(signed(policy='d'*64))

    def test_wrong_nonce_even_if_signed(self):
        with self.assertRaises(Refused):self.ok(signed(nonce=9))

    def test_wrong_worker_even_if_signed(self):
        with self.assertRaises(Refused):self.ok(signed(worker='telegram-desk-adapter-v1'))

    def test_wrong_role_even_if_signed(self):
        with self.assertRaises(Refused):self.ok(signed(role='grok'))

    def test_unknown_result_refused(self):
        with self.assertRaises(Refused):self.ok(signed(result='funds_withdrawal_authorized'))

    def test_future_observation_refused(self):
        with self.assertRaises(Refused):self.ok(signed(observed=2000))

    def test_stale_receipt_refused(self):
        with self.assertRaises(Refused):self.ok(signed(),now=1300)

    def test_extra_field_refused(self):
        row=signed();row['url']='https://untrusted.invalid'
        with self.assertRaises(Refused):self.ok(row)

    def test_boolean_time_refused(self):
        with self.assertRaises(Refused):self.ok(signed(nonce=True))

    def test_short_key_refused(self):
        with self.assertRaises(Refused):self.ok(signed(),key=b'x'*16)

    def test_other_worker_role_verifiable_only_under_exact_attempt(self):
        a=Attempt('telegram_desk','a'*32,'b'*32,'c'*64,1000,0)
        row=signed(a,'test_accepted_by_telegram')
        self.assertEqual(self.ok(row,a),'test_accepted_by_telegram')
        with self.assertRaises(Refused):self.ok(row,ATTEMPT)

    def test_real_sqlite_owner_attempt_consumes_receipt_once(self):
        # Direct internal OwnerHooks calls are isolated test-only. A normal
        # browser/operator has no owner process access. This does not implement
        # the separate native one-use authenticated review authority.
        from paper_credential_vault import PaperVault
        from managed_paper_vault import ManagedPaperVault
        with tempfile.TemporaryDirectory(prefix='r2b-receipt-') as d:
            path=Path(d)
            path.chmod(0o700)
            with patch('managed_paper_vault._require_owner',return_value=None):
                original=PaperVault(path,Fernet.generate_key())
                policies={r:format(i+1,'064x') for i,r in enumerate(
                    ('kraken_read','telegram_desk','telegram_recovery','grok'))}
                managed=ManagedPaperVault(original,b't'*32,policies)
                managed.migrate_empty(100)
                values={'kraken_read_key':'synthetic-key','kraken_read_secret':'synthetic-secret'}
                managed.enroll('kraken_read',values,101)
                with original.transaction() as db:
                    owner=managed._owner(db)
                    att=owner.begin('kraken_read',managed._sealed(db,'kraken_read'),
                                    policies['kraken_read'],102)
                payload=signed(att,observed=103)
                result=verify_receipt(payload,att,KEY,103)
                with original.transaction() as db:
                    owner=managed._owner(db)
                    owner.finish(att,managed._sealed(db,'kraken_read'),result,103)
                with original.transaction() as db:
                    owner=managed._owner(db)
                    with self.assertRaises(Refused):
                        owner.finish(att,managed._sealed(db,'kraken_read'),result,104)
                status=managed.status()
                self.assertEqual(status['credentials']['kraken_read_key'],'stored_unverified')
                self.assertFalse(status['connections_verified'])
                self.assertFalse(status['deployment_ready'])

if __name__=='__main__':unittest.main()
