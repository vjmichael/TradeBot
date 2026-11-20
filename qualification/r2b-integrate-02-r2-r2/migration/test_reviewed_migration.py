"""Source-locked tests run inside reconstructed R2B checkout.
No network; synthetic Fernet SQLite vault only.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from cryptography.fernet import Fernet
from paper_credential_vault import PaperVault, DOMAIN
from managed_paper_vault import ManagedPaperVault, Refused
from reviewed_migration import Inventory, inspect_legacy, migrate_reviewed

POLICIES={r:format(i+1,'064x') for i,r in enumerate(('kraken_read','telegram_desk','telegram_recovery','grok'))}
TAG=b'R'*32

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)
        self.uid1=patch('managed_paper_vault._require_owner',return_value=None)
        self.uid2=patch('reviewed_migration._require_owner',return_value=None)
        self.uid1.start();self.uid2.start()
        self.old=PaperVault(self.path,Fernet.generate_key())
        self.managed=ManagedPaperVault(self.old,TAG,POLICIES)
    def tearDown(self):
        self.uid2.stop();self.uid1.stop();self.tmp.cleanup()
    def old_rows(self):
        with self.old.transaction() as db:
            return dict(db.execute('SELECT slot,ciphertext FROM secrets'))
    def preview(self):return inspect_legacy(self.managed)
    def migrate(self,plan=None,consume=None):
        return migrate_reviewed(self.managed,plan or self.preview(),consume or (lambda inventory,now:True),100)
    def test_preserves_ciphertext_and_versions(self):
        self.old.enroll({'grok':'synthetic-canary-value','telegram_desk':'synthetic-bot-A'})
        before=self.old_rows();plan=self.preview()
        self.assertEqual(set(plan.roles),{'grok','telegram_desk'})
        self.migrate(plan)
        self.assertEqual(self.old_rows(),before)
        self.assertEqual(self.managed.status()['credentials']['grok'],'stored_unverified')
        self.assertFalse(self.managed.status()['connections_verified'])
        self.assertNotIn(b'synthetic-canary-value',self.old.path.read_bytes())
    def test_atomic_on_collision(self):
        self.old.enroll({'telegram_desk':'same-token','telegram_recovery':'same-token'})
        before=self.old_rows()
        with self.assertRaises(Refused):self.migrate()
        with self.old.transaction() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='provider_metadata'").fetchone())
        self.assertEqual(before,self.old_rows())
    def test_declined_migration_no_mutation(self):
        self.old.enroll({'grok':'synthetic'})
        with self.assertRaises(Refused):self.migrate(consume=lambda *_:False)
        with self.old.transaction() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='provider_metadata'").fetchone())
    def test_review_altered_ciphertext_refused(self):
        self.old.enroll({'grok':'version-one'});p=self.preview()
        self.old.enroll({'grok':'version-two'})
        called=Mock(return_value=True)
        with self.assertRaises(Refused):self.migrate(p,consume=called)
        called.assert_not_called()
    def test_review_changed_role_refused(self):
        self.old.enroll({'grok':'synthetic'});p=self.preview()
        self.old.enroll({'telegram_desk':'second'})
        with self.assertRaises(Refused):self.migrate(p)
    def test_replay_refused(self):
        self.old.enroll({'grok':'synthetic'});p=self.preview()
        self.migrate(p)
        with self.assertRaises(Refused):self.migrate(p)
    def test_existing_state_refused_at_preview(self):
        self.old.enroll({'grok':'synthetic'});self.migrate()
        with self.assertRaises(Refused):self.preview()
    def test_empty_refused(self):
        with self.assertRaises(Refused):self.preview()
    def test_trusted_review_required(self):
        self.old.enroll({'grok':'synthetic'})
        with self.assertRaises(Refused):migrate_reviewed(self.managed,self.preview(),None,100)
    def test_invalid_plan(self):
        self.old.enroll({'grok':'synthetic'})
        with self.assertRaises(Refused):self.migrate(Inventory(('grok',),'x'))
    def test_unsupported_role_rejected(self):
        self.old.enroll({'grok':'synthetic'})
        with self.old.transaction() as db:
            db.execute('INSERT INTO secrets VALUES(?,?)',('unknown-slot',self.old.cipher.encrypt(DOMAIN+b'unknown-slot\0test')))
        with self.assertRaises((ValueError,Refused)):self.preview()
    def test_incomplete_pair_rejected(self):
        self.old.enroll({'kraken_read_key':'synthetic','kraken_read_secret':'synthetic'})
        with self.old.transaction() as db:db.execute("DELETE FROM secrets WHERE slot='kraken_read_secret'")
        with self.assertRaises((ValueError,Refused)):self.preview()
    def test_bad_ciphertext_rejected(self):
        self.old.enroll({'grok':'synthetic'})
        with self.old.transaction() as db:db.execute("UPDATE secrets SET ciphertext=X'12' WHERE slot='grok'")
        with self.assertRaises(Exception):self.preview()
    def test_review_called_once(self):
        self.old.enroll({'grok':'synthetic'});check=Mock(return_value=True)
        self.migrate(consume=check)
        check.assert_called_once()
    def test_nonce_highwater_initialized(self):
        self.old.enroll({'kraken_read_key':'synthetic-key','kraken_read_secret':'synthetic-secret'})
        self.migrate()
        with self.old.transaction() as db:
            from vault_owner_hooks import OwnerHooks
            state=OwnerHooks(db,self.old.cipher,TAG)._load()
            self.assertEqual(len(state['nonces']),1)
            self.assertEqual(list(state['nonces'].values()),[0])

if __name__=='__main__':unittest.main()