"""Exact restored PaperVault plus managed one-writer regression checks.
Synthetic credentials, no network or real provider/test execution.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from paper_credential_vault import PaperVault, dispatch, REFUSED, DOMAIN
from managed_paper_vault import ManagedPaperVault, Refused
from managed_rpc import ManagedVaultCommands
from vault_owner_hooks import OwnerHooks, ROLES

POLICIES = {r: format(i+11,'064x') for i,r in enumerate(ROLES)}
TAG = b'Z'*32


class ExactVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)
        self.uid=patch('managed_paper_vault._require_owner', return_value=None)
        self.uid.start()
        self.original=PaperVault(self.path,Fernet.generate_key())
        self.managed=ManagedPaperVault(self.original,TAG,POLICIES)
        self.managed.migrate_empty(100)
        self.rpc=ManagedVaultCommands(self.managed,clock=lambda:101)
    def tearDown(self):
        self.uid.stop();self.tmp.cleanup()
    def test_original_status_shape_preserved(self):
        out=self.rpc.status()
        self.assertEqual(set(out),{'ok','credentials','connections_verified','deployment_ready'})
        self.assertFalse(out['connections_verified'])
        self.assertFalse(out['deployment_ready'])
    def test_dispatch_pair_and_verification_unavailable(self):
        req={'op':'enroll','values':{'kraken_read_key':'synthetic-key','kraken_read_secret':'synthetic-secret'}}
        out=dispatch(self.rpc,req)
        self.assertTrue(out['ok'])
        self.assertEqual(out['credentials']['kraken_read_key'],'stored_unverified')
        self.assertEqual(out['credentials']['kraken_read_secret'],'stored_unverified')
        self.assertFalse(out['deployment_ready'])
        self.assertFalse(out['connections_verified'])
    def test_operator_credential_remove(self):
        dispatch(self.rpc,{'op':'enroll','values':{'telegram_desk':'synthetic-token'}})
        out=dispatch(self.rpc,{'op':'remove','slots':['telegram_desk']})
        self.assertEqual(out['credentials']['telegram_desk'],'not_configured')
    def test_refuse_partial_pair(self):
        self.assertEqual(dispatch(self.rpc,{'op':'enroll','values':{'kraken_read_key':'synthetic'}}),REFUSED)
        self.assertEqual(self.rpc.status()['credentials']['kraken_read_key'],'not_configured')
    def test_refuse_unreviewed_role(self):
        self.assertEqual(dispatch(self.rpc,{'op':'enroll','values':{'kraken_trade_key':'synthetic'}}),REFUSED)
    def test_refuse_arbitrary_test_request(self):
        self.assertEqual(dispatch(self.rpc,{'op':'test_connection','provider':'kraken'}),REFUSED)
        self.assertFalse(hasattr(self.rpc,'test_connection'))
    def test_untracked_legacy_write_fenced(self):
        self.rpc.enroll({'grok':'synthetic-one'})
        with self.original.transaction() as db:
            db.execute('UPDATE secrets SET ciphertext=? WHERE slot=?',
                       (self.original.cipher.encrypt(DOMAIN+b'grok\0synthetic-other'),'grok'))
        with self.assertRaises(Refused): self.rpc.status()
    def test_ciphertext_not_plaintext(self):
        self.rpc.enroll({'grok':'CANARY_REALM_FOR_TEST'})
        self.assertNotIn(b'CANARY_REALM_FOR_TEST', (self.path/'vault.sqlite3').read_bytes())
    def test_restart_invalidates_success(self):
        self.rpc.enroll({'grok':'synthetic'})
        with self.original.transaction() as db:
            owner=OwnerHooks(db,self.original.cipher,TAG)
            sealed={'grok':db.execute('SELECT ciphertext FROM secrets WHERE slot=?',('grok',)).fetchone()[0]}
            attempt=owner.begin('grok',sealed,POLICIES['grok'],102)
        with self.original.transaction() as db:
            owner=OwnerHooks(db,self.original.cipher,TAG)
            owner.finish(attempt,sealed,'metadata_verified',103)
        self.managed.startup_invalidate(104)
        with self.original.transaction() as db:
            owner=OwnerHooks(db,self.original.cipher,TAG)
            status=owner.status('grok',sealed,POLICIES['grok'],104)
        self.assertEqual(status,'stored_unverified')
    def test_double_enrollment_generation_changes(self):
        self.rpc.enroll({'grok':'synthetic-1'})
        with self.original.transaction() as db:
            first=OwnerHooks(db,self.original.cipher,TAG)._load()['roles']['grok']['generation']
        self.rpc.enroll({'grok':'synthetic-2'})
        with self.original.transaction() as db:
            second=OwnerHooks(db,self.original.cipher,TAG)._load()['roles']['grok']['generation']
        self.assertNotEqual(first,second)
    def test_missing_tag_key_refuses(self):
        with self.assertRaises(Refused):
            ManagedPaperVault(self.original,b'bad',POLICIES)
    def test_original_status_no_secret_export(self):
        self.rpc.enroll({'grok':'PRIVATE_CANARY'})
        self.assertNotIn('PRIVATE_CANARY',repr(self.rpc.status()))
    def test_other_role_unaffected(self):
        self.rpc.enroll({'telegram_desk':'synthetic-desk'})
        self.rpc.enroll({'telegram_recovery':'different'})
        self.assertEqual(self.rpc.status()['credentials']['telegram_recovery'],'stored_unverified')
    def test_unknown_fields_refused(self):
        self.assertEqual(dispatch(self.rpc,{'op':'enroll','values':{'wallet_seed':'synthetic'}}),REFUSED)

if __name__=='__main__':
    unittest.main()
