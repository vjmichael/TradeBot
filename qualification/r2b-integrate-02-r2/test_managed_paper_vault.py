"""Real SQLite/Fernet and actual previous OwnerHooks logic, with minimal exact
PaperVault table/transaction/status contract copied from the frozen candidate.
Installed service and native operator not simulated as successful here.
"""
import contextlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet
from managed_paper_vault import ManagedPaperVault, Refused, DOMAIN
from vault_owner_hooks import OwnerHooks, ROLES

KEY=b'Z'*32
POLICIES={r:format(i+3,'064x') for i,r in enumerate(ROLES)}

class FaithfulPaperVault:
    """The staged PaperVault's actual table/transaction/_verify/_status shape."""
    def __init__(self,path):
        self.path=Path(path);self.cipher=Fernet(Fernet.generate_key())
        with self.transaction() as db:
            db.execute('CREATE TABLE secrets (slot TEXT PRIMARY KEY,ciphertext BLOB NOT NULL)')
            db.execute('CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK(id=1),verifier BLOB NOT NULL)')
            db.execute('INSERT INTO meta VALUES(1,?)',(self.cipher.encrypt(DOMAIN+b'verifier'),))
    @contextlib.contextmanager
    def transaction(self):
        db=sqlite3.connect(self.path,timeout=2,isolation_level=None)
        try:
            db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA secure_delete=ON');db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()
    def _verify(self,db):
        row=db.execute('SELECT verifier FROM meta WHERE id=1').fetchone()
        if row is None or self.cipher.decrypt(row[0])!=DOMAIN+b'verifier':raise ValueError('vault verification refused')
        slots=set()
        for slot,blob in db.execute('SELECT slot,ciphertext FROM secrets'):
            if slot not in {s for ss in ROLES.values() for s in ss} or not self.cipher.decrypt(blob).startswith(DOMAIN+slot.encode()+b'\0'):
                raise ValueError('vault verification refused')
            slots.add(slot)
        if slots&{'kraken_read_key','kraken_read_secret'} and not {'kraken_read_key','kraken_read_secret'}<=slots:
            raise ValueError('incomplete credential pair')
        return slots
    def _status(self,db):
        slots=self._verify(db)
        return {'ok':True,'credentials':{s:'stored_unverified' if s in slots else 'not_configured' for s in sorted({v for ss in ROLES.values() for v in ss})},'connections_verified':False,'deployment_ready':False}
    def status(self):
        with self.transaction() as db:return self._status(db)

class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'vault.db'
        self.v=FaithfulPaperVault(self.path)
        self.uid=patch('managed_paper_vault._require_owner',return_value=None);self.uid.start()
        self.m=ManagedPaperVault(self.v,KEY,POLICIES)
    def tearDown(self):self.uid.stop();self.tmp.cleanup()
    def save(self,role='kraken_read',prefix='mock',now=100):return self.m.enroll(role,{slot:prefix+'-'+slot for slot in ROLES[role]},now)
    def test_initial_migration(self):
        self.m.migrate_empty(100)
        with self.v.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM provider_metadata').fetchone()[0],1)
    def test_no_unreviewed_migration(self):
        with self.v.transaction() as db:
            db.execute('INSERT INTO secrets VALUES (?,?)',('telegram_desk',self.v.cipher.encrypt(DOMAIN+b'telegram_desk\0mock')))
        with self.assertRaises(Refused): self.m.migrate_empty(100)
        with self.v.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='provider_metadata'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM secrets').fetchone()[0],1)
    def test_exact_pair_and_status(self):
        self.m.migrate_empty(100);state=self.save()
        self.assertEqual(state['credentials']['kraken_read_key'],'stored_unverified')
        self.assertEqual(state['credentials']['kraken_read_secret'],'stored_unverified')
        self.assertFalse(state['connections_verified']);self.assertFalse(state['deployment_ready'])
    def test_cannot_partial_pair(self):
        self.m.migrate_empty(100)
        with self.assertRaises(Refused):self.m.enroll('kraken_read',{'kraken_read_key':'x'},100)
        self.assertEqual(self.v.status()['credentials']['kraken_read_key'],'not_configured')
    def test_unknown_slot(self):
        self.m.migrate_empty(100)
        with self.assertRaises(Refused):self.m.enroll('grok',{'wallet_seed':'x'},100)
    def test_invalid_secret(self):
        self.m.migrate_empty(100)
        for bad in ('','contains space','contains\nline'):
            with self.assertRaises(Refused):self.m.enroll('grok',{'grok':bad},100)
    def test_no_state_no_mutation(self):
        with self.assertRaises(Refused):self.save()
        self.assertEqual(self.v.status()['credentials']['kraken_read_key'],'not_configured')
    def test_replace_changes_generation(self):
        self.m.migrate_empty(100);self.save()
        with self.v.transaction() as db:
            old=OwnerHooks(db,self.v.cipher,KEY)._load()['roles']['kraken_read']['generation']
        self.save(now=101)
        with self.v.transaction() as db:
            new=OwnerHooks(db,self.v.cipher,KEY)._load()['roles']['kraken_read']['generation']
        self.assertNotEqual(new,old)
    def test_atomic_commit_rollback(self):
        self.m.migrate_empty(100);self.save()
        original=self.v.status()
        # Rejected identity collision must also roll back changed stored ciphertext.
        same='12345:SyntheticTokenWithEnoughLetters000000000'
        self.m.enroll('telegram_desk',{'telegram_desk':same},101)
        before=self.v.status()
        with self.assertRaises(Refused):self.m.enroll('telegram_recovery',{'telegram_recovery':same},102)
        self.assertEqual(self.v.status(),before)
        with self.v.transaction() as db:self.assertIsNone(db.execute('SELECT ciphertext FROM secrets WHERE slot="telegram_recovery"').fetchone())
    def test_removal_atomic_and_unverified(self):
        self.m.migrate_empty(100);self.save();state=self.m.remove('kraken_read',102)
        self.assertEqual(state['credentials']['kraken_read_key'],'not_configured')
        self.assertEqual(state['credentials']['kraken_read_secret'],'not_configured')
        with self.v.transaction() as db:self.assertEqual(OwnerHooks(db,self.v.cipher,KEY)._load()['roles'],{})
    def test_removal_rejects_unknown(self):
        self.m.migrate_empty(100)
        with self.assertRaises(Refused):self.m.remove('kraken_read',102)
    def test_no_untracked_deletion(self):
        self.m.migrate_empty(100);self.save()
        with self.v.transaction() as db:
            o=OwnerHooks(db,self.v.cipher,KEY);state=o._load();state['roles'].pop('kraken_read');o._put(state)
        with self.assertRaises(Refused):self.m.remove('kraken_read',102)
        self.assertEqual(self.v.status()['credentials']['kraken_read_key'],'stored_unverified')
    def test_disable_retains_secret(self):
        self.m.migrate_empty(100);self.save();self.m.disable('kraken_read',101)
        self.assertEqual(self.v.status()['credentials']['kraken_read_key'],'stored_unverified')
        with self.v.transaction() as db:self.assertTrue(OwnerHooks(db,self.v.cipher,KEY)._load()['roles']['kraken_read']['disabled'])
    def test_no_plaintext_database(self):
        self.m.migrate_empty(100);self.m.enroll('grok',{'grok':'SECRET_TEST_CANARY'},100)
        self.assertNotIn(b'SECRET_TEST_CANARY',self.path.read_bytes())
    def test_reject_root_other_uid(self):
        self.uid.stop()
        try:
            with patch('managed_paper_vault.pwd.getpwnam',return_value=type('User',(),{'pw_uid':23456})()):
                with patch('managed_paper_vault.os.getuid',return_value=0),patch('managed_paper_vault.os.geteuid',return_value=0):
                    self.assertRaises(Refused,__import__('managed_paper_vault')._require_owner)
        finally:
            self.uid.start()
    def test_reject_too_short_tag(self):self.assertRaises(Refused,ManagedPaperVault,self.v,b'xx',POLICIES)
    def test_reject_policy_variation(self):self.assertRaises(Refused,ManagedPaperVault,self.v,KEY,{'grok':'a'*64})
    def test_nonreplayable_migration(self):
        self.m.migrate_empty(100)
        with self.assertRaises(sqlite3.OperationalError):self.m.migrate_empty(101)
    def test_policies_do_not_mutate(self):
        src=POLICIES.copy();m=ManagedPaperVault(self.v,KEY,src);src['grok']='f'*64
        self.assertNotEqual(m._policies['grok'],src['grok'])
    def test_bypass_write_refused(self):
        self.m.migrate_empty(100); self.save()
        with self.v.transaction() as db:
            db.execute('UPDATE secrets SET ciphertext=? WHERE slot=?',(
                self.v.cipher.encrypt(DOMAIN+b'kraken_read_key\0changed'), 'kraken_read_key'))
        with self.assertRaises(Refused): self.m.enroll('grok',{'grok':'test'},102)
        self.assertEqual(self.v.status()['credentials']['grok'],'not_configured')
    def test_orphan_metadata_refused(self):
        self.m.migrate_empty(100);self.save()
        with self.v.transaction() as db:
            db.execute('DELETE FROM secrets WHERE slot IN (?,?)',ROLES['kraken_read'])
        with self.assertRaises(Refused): self.m.enroll('grok',{'grok':'test'},102)
    def test_untracked_role_refused(self):
        self.m.migrate_empty(100)
        with self.v.transaction() as db:
            db.execute('INSERT INTO secrets VALUES(?,?)',('grok',self.v.cipher.encrypt(DOMAIN+b'grok\0legacy')))
        with self.assertRaises(Refused):self.m.enroll('telegram_desk',{'telegram_desk':'test'},102)
    def test_restart_drops_successful_receipts(self):
        self.m.migrate_empty(100);self.save()
        with self.v.transaction() as db:
            o=OwnerHooks(db,self.v.cipher,KEY);a=o.begin('kraken_read',self.m._sealed(db,'kraken_read'),POLICIES['kraken_read'],101)
        with self.v.transaction() as db:
            o=OwnerHooks(db,self.v.cipher,KEY);o.finish(a,self.m._sealed(db,'kraken_read'),'permissions_verified',102)
        with self.v.transaction() as db:
            o=OwnerHooks(db,self.v.cipher,KEY)
            self.assertEqual(o.status('kraken_read',self.m._sealed(db,'kraken_read'),POLICIES['kraken_read'],102),'permissions_verified')
        self.m.startup_invalidate(103)
        with self.v.transaction() as db:
            o=OwnerHooks(db,self.v.cipher,KEY)
            self.assertEqual(o.status('kraken_read',self.m._sealed(db,'kraken_read'),POLICIES['kraken_read'],103),'stored_unverified')
    def test_restart_telegram_unknown_no_resend(self):
        self.m.migrate_empty(100);self.save(role='telegram_desk')
        with self.v.transaction() as db:
            o=OwnerHooks(db,self.v.cipher,KEY);o.begin('telegram_desk',self.m._sealed(db,'telegram_desk'),POLICIES['telegram_desk'],101)
        self.m.startup_invalidate(102)
        with self.v.transaction() as db:
            o=OwnerHooks(db,self.v.cipher,KEY)
            self.assertEqual(o.status('telegram_desk',self.m._sealed(db,'telegram_desk'),POLICIES['telegram_desk'],102),'delivery_unknown')
    def test_no_provider_execution_method(self):
        self.assertFalse(hasattr(self.m,'begin_test'));self.assertFalse(hasattr(self.m,'complete_from_protected_adapter'))

if __name__=='__main__':unittest.main()