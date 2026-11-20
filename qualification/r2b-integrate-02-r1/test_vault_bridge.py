"""Staged bridge contract tests; existing OwnerHooks own suite is separate.
Test fake implements the same owner-only interface and fail-closed transaction.
"""
import contextlib
import importlib
import sqlite3
import socket
import struct
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet

class Refused(ValueError): pass
ROLES = {'kraken_read':('kraken_read_key','kraken_read_secret'), 'telegram_desk':('telegram_desk',), 'telegram_recovery':('telegram_recovery',), 'grok':('grok',)}
class FakeOwnerHooks:
    def __init__(self, db, cipher, tag_key):
        assert db.in_transaction
        self.db=db
    def initialize(self):
        self.db.execute('CREATE TABLE metadata (role TEXT PRIMARY KEY, generation INTEGER, status TEXT)')
    def replace(self, role, sealed, policy, now):
        assert set(sealed)==set(ROLES[role]); assert self.db.in_transaction
        self.db.execute('INSERT INTO metadata VALUES (?,1,?) ON CONFLICT(role) DO UPDATE SET generation=generation+1,status=excluded.status',(role,'stored_unverified'))
    def remove(self, role, now): self.db.execute('DELETE FROM metadata WHERE role=?',(role,))
    def disable(self, role, now): self.db.execute('UPDATE metadata SET status=? WHERE role=?',('disabled',role))
    def begin(self, role, sealed, policy, now):
        self.db.execute('UPDATE metadata SET status=? WHERE role=?',('pending',role))
        return types.SimpleNamespace(role=role, generation='1',policy=policy,started=now)
    def finish(self, attempt, sealed, result, now):
        self.db.execute('UPDATE metadata SET status=? WHERE role=?',(result,attempt.role))
    def recover_interrupted(self, now): self.db.execute("UPDATE metadata SET status='stored_unverified' WHERE status='pending'")
    def status(self, role, sealed, policy, now):
        row=self.db.execute('SELECT status FROM metadata WHERE role=?',(role,)).fetchone()
        return row[0] if row else 'not_configured'

fake_mod=types.ModuleType('vault_owner_hooks')
fake_mod.OwnerHooks=FakeOwnerHooks; fake_mod.Refused=Refused; fake_mod.ROLES=ROLES
sys.modules.setdefault('vault_owner_hooks',fake_mod)
from vault_bridge import VaultBridge

class Vault:
    def __init__(self, path):
        self.path=path;self.cipher=Fernet(Fernet.generate_key());
        with self.transaction() as db:
            db.execute('CREATE TABLE secrets (slot TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)')
    @contextlib.contextmanager
    def transaction(self):
        db=sqlite3.connect(self.path,isolation_level=None);db.execute('BEGIN IMMEDIATE')
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def _verify(self,db):
        rows={k:self.cipher.decrypt(v) for k,v in db.execute('SELECT slot,ciphertext FROM secrets')}
        if len(set(rows).intersection(ROLES['kraken_read']))==1:raise ValueError('incomplete pair')
        for k,v in rows.items():
            assert v.startswith(b'maj-vaj:PAPER:credential-vault:v1\0'+k.encode()+b'\0')
        return set(rows)
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.v=Vault(str(Path(self.tmp.name)/'state.sqlite3'))
        self.guard=patch('vault_bridge._require_owner')
        self.guard.start()
        self.bridge=VaultBridge(self.v,b'k'*32);self.bridge.migrate_empty()
    def tearDown(self):self.guard.stop();self.tmp.cleanup()
    def test_owner_identity_required(self):
        with patch('vault_bridge._require_owner',side_effect=Refused('vault_owner_required')):
            with self.assertRaises(Refused):VaultBridge(self.v,b'k'*32)
    def test_saved_pair_and_unverified(self):
        self.bridge.save('kraken_read',{'kraken_read_key':'synthetic','kraken_read_secret':'secret'},'a'*64,100)
        self.assertEqual(self.bridge.status_local('kraken_read','a'*64,101),'stored_unverified')
        with self.v.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM secrets').fetchone()[0],2)
    def test_missing_pair_no_partial_commit(self):
        with self.assertRaises(Refused):self.bridge.save('kraken_read',{'kraken_read_key':'x'},'a'*64,100)
        with self.v.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM secrets').fetchone()[0],0)
    def test_bad_value_no_mutation(self):
        with self.assertRaises(Refused):self.bridge.save('grok',{'grok':'secret\npassword'},'a'*64,100)
    def test_invalid_policy_no_mutation(self):
        with self.assertRaises(Refused):self.bridge.save('grok',{'grok':'secret'},'*',100)
    def test_replacement_in_one_tx(self):
        vals={'kraken_read_key':'synthetic','kraken_read_secret':'secret'}
        self.bridge.save('kraken_read',vals,'a'*64,100)
        self.bridge.save('kraken_read',vals,'a'*64,101)
        with self.v.transaction() as db:self.assertEqual(db.execute('SELECT generation FROM metadata').fetchone()[0],2)
    def test_remove_atomically(self):
        self.bridge.save('grok',{'grok':'secret'},'a'*64,100);self.bridge.remove('grok',101)
        with self.v.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM secrets').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM metadata').fetchone()[0],0)
    def peer(self):
        left,right=socket.socketpair(socket.AF_UNIX)
        self.addCleanup(left.close);self.addCleanup(right.close)
        return left
    def test_review_required(self):
        self.bridge.save('grok',{'grok':'secret'},'a'*64,100)
        with self.assertRaises(Refused):self.bridge.reserve_from_operator_peer('grok','a'*64,101,None,True)
    def test_wrong_uid_refused(self):
        self.bridge.save('grok',{'grok':'secret'},'a'*64,100)
        with self.assertRaises(Refused):self.bridge.reserve_from_operator_peer('grok','a'*64,101,self.peer(),True)
    def test_operator_peer_consent(self):
        self.bridge.save('grok',{'grok':'secret'},'a'*64,100)
        with patch('vault_bridge.pwd.getpwnam',return_value=types.SimpleNamespace(pw_uid=1001)), patch('vault_bridge.socket.socket.getsockopt',return_value=struct.pack('3i',42,1001,1001)):
            with self.assertRaises(Refused):self.bridge.reserve_from_operator_peer('grok','a'*64,101,self.peer(),False)
            a=self.bridge.reserve_from_operator_peer('grok','a'*64,101,self.peer(),True)
        self.bridge.complete_from_protected_adapter(a,'metadata_verified',102)
        self.assertEqual(self.bridge.status_local('grok','a'*64,102),'metadata_verified')
    def test_interrupted_invalidates(self):
        self.bridge.save('grok',{'grok':'secret'},'a'*64,100)
        with patch('vault_bridge.pwd.getpwnam',return_value=types.SimpleNamespace(pw_uid=1001)), patch('vault_bridge.socket.socket.getsockopt',return_value=struct.pack('3i',42,1001,1001)):
            self.bridge.reserve_from_operator_peer('grok','a'*64,101,self.peer(),True)
        self.bridge.startup_invalidate(102)
        self.assertEqual(self.bridge.status_local('grok','a'*64,102),'stored_unverified')
    def test_closed_source_only(self):
        self.assertFalse(hasattr(self.bridge,'read_credentials'))
        self.assertFalse(hasattr(self.bridge,'send_order'))
    def test_migration_over_saved_data_refused(self):
        with self.v.transaction() as db:db.execute('INSERT INTO secrets VALUES (?,?)',('grok',self.v.cipher.encrypt(b'maj-vaj:PAPER:credential-vault:v1\0grok\0secret')))
        with self.assertRaises(Refused):self.bridge.migrate_empty()
    def test_existing_state_cannot_reinitialize(self):
        with self.assertRaises(sqlite3.OperationalError):self.bridge.migrate_empty()
    def test_atomicity_when_hook_fails(self):
        with patch('vault_bridge.OwnerHooks',FakeOwnerHooks):
            with patch.object(FakeOwnerHooks,'replace',side_effect=Refused('staged failure')):
                with self.assertRaises(Refused):self.bridge.save('grok',{'grok':'secret'},'a'*64,100)
        with self.v.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM secrets').fetchone()[0],0)

if __name__=='__main__':unittest.main()