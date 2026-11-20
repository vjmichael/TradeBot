import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from cryptography.fernet import Fernet, InvalidToken
from vault_owner_hooks import OwnerHooks, Refused, ROLES

DOMAIN=b'maj-vaj:PAPER:credential-vault:v1\0'
KEY=b'k'*32
P='a'*64
Q='b'*64
R='c'*64

class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.dbpath=Path(self.tmp.name)/'vault.sqlite3'
        self.cipher=Fernet(Fernet.generate_key())
        with self.tx() as s:
            s.db.execute('CREATE TABLE secrets (slot TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)')
            s.initialize()
    def tearDown(self):self.tmp.cleanup()
    @contextlib.contextmanager
    def tx(self):
        db=sqlite3.connect(self.dbpath,isolation_level=None)
        db.execute('BEGIN IMMEDIATE')
        try:
            yield OwnerHooks(db,self.cipher,KEY)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:db.close()
    def save(self, role='kraken_read', text='one', now=100, policy=P):
        with self.tx() as s:
            slots=ROLES[role]
            for slot in slots:
                s.db.execute('INSERT OR REPLACE INTO secrets VALUES (?,?)',(slot,self.cipher.encrypt(DOMAIN+slot.encode()+b'\0'+(text+slot).encode())))
            s.replace(role,self.items(s,role),policy,now)
    def items(self,s,role='kraken_read'):
        return {name:s.db.execute('SELECT ciphertext FROM secrets WHERE slot=?',(name,)).fetchone()[0] for name in ROLES[role]}
    def start(self,role='kraken_read',now=101,policy=P):
        with self.tx() as s:return s.begin(role,self.items(s,role),policy,now)
    def finish(self,a,result,now=102):
        with self.tx() as s:s.finish(a,self.items(s,a.role),result,now)
    def status(self,role='kraken_read',now=102,policy=P):
        with self.tx() as s:
            try:items=self.items(s,role)
            except (TypeError,KeyError):items={}
            return s.status(role,items,policy,now)
    def test_initial(self):self.assertEqual(self.status(),'not_configured')
    def test_success(self):self.save();a=self.start();self.finish(a,'permissions_verified');self.assertEqual(self.status(),'permissions_verified')
    def test_expiry(self):self.save();a=self.start();self.finish(a,'permissions_verified');self.assertEqual(self.status(now=401),'stored_unverified');self.assertEqual(self.status(now=401),'stored_unverified')
    def test_no_resurrection(self):self.save();a=self.start();self.finish(a,'permissions_verified');self.status(now=402);self.assertEqual(self.status(now=401),'verification_unavailable')
    def test_replace_invalidates(self):self.save();a=self.start();self.finish(a,'permissions_verified');self.save(text='two',now=104);self.assertEqual(self.status(now=104),'stored_unverified')
    def test_late_result(self):self.save();a=self.start();self.save(text='two',now=102);self.assertRaises(Refused,self.finish,a,'permissions_verified',103)
    def test_remove(self):
        self.save();a=self.start();self.finish(a,'permissions_verified');
        with self.tx() as s:
            s.db.execute('DELETE FROM secrets');s.remove('kraken_read',103)
        self.assertEqual(self.status(now=104),'not_configured')
    def test_nonce_retained_after_remove(self):
        self.save();a=self.start()
        with self.tx() as s:s.remove('kraken_read',101)
        self.save(now=101);b=self.start(now=101);self.assertGreater(b.nonce,a.nonce)
    def test_pending(self):self.save();self.start();self.assertRaises(Refused,self.start)
    def test_pending_restart_kraken(self):
        self.save();a=self.start()
        with self.tx() as s:s.recover_interrupted(102)
        self.assertEqual(self.status(),'stored_unverified');self.assertRaises(Refused,self.finish,a,'permissions_verified',103)
    def test_pending_restart_telegram_unknown(self):
        self.save('telegram_desk');self.start('telegram_desk');
        with self.tx() as s:s.recover_interrupted(102)
        self.assertEqual(self.status('telegram_desk'),'delivery_unknown')
    def test_role_collision(self):
        self.save('telegram_desk',text='same')
        with self.assertRaises(Refused):
            with self.tx() as s:
                slot='telegram_recovery';s.db.execute('INSERT INTO secrets VALUES (?,?)',(slot,self.cipher.encrypt(DOMAIN+slot.encode()+b'\0'+('sametelegram_desk').encode())));s.replace('telegram_recovery',self.items(s,'telegram_recovery'),Q,101)
    def test_roles_distinct(self):self.save('telegram_desk');self.save('telegram_recovery',text='two');self.assertEqual(self.status('telegram_recovery',policy=P),'stored_unverified')
    def test_disable(self):
        self.save();a=self.start();self.finish(a,'permissions_verified')
        with self.tx() as s:s.disable('kraken_read',103)
        self.assertEqual(self.status(now=103),'disabled');self.assertRaises(Refused,self.start,now=104)
    def test_policy_change(self):self.save();a=self.start();self.finish(a,'permissions_verified');self.assertEqual(self.status(policy=Q),'stored_unverified')
    def test_invalid_result(self):self.save();a=self.start();self.assertRaises(Refused,self.finish,a,'untrusted_unlimited',102)
    def test_wrong_provider_result(self):self.save();a=self.start();self.assertRaises(Refused,self.finish,a,'metadata_verified',102)
    def test_failure(self):self.save();a=self.start();self.finish(a,'missing_permissions');self.assertEqual(self.status(),'missing_permissions')
    def test_clock_rollback(self):self.save();self.assertRaises(Refused,self.start,now=99)
    def test_future_nonce(self):self.save();a=self.start();self.assertGreaterEqual(a.nonce,a.started*1000)
    def test_atomic_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.tx() as s:
                for slot in ROLES['kraken_read']:s.db.execute('INSERT INTO secrets VALUES(?,?)',(slot,self.cipher.encrypt(DOMAIN+slot.encode()+b'\0x')))
                s.replace('kraken_read',self.items(s),P,100);raise RuntimeError()
        self.assertEqual(self.status(),'not_configured')
    def test_ciphertext_no_plaintext(self):self.save(text='SENSITIVE_TEST_MARKER');self.assertNotIn(b'SENSITIVE_TEST_MARKER',self.dbpath.read_bytes())
    def test_tamper(self):
        self.save()
        with self.tx() as s:s.db.execute("UPDATE provider_metadata SET sealed=X'0102'")
        with self.assertRaises(InvalidToken):self.status()
    def test_no_implicit_recreate(self):
        with self.tx() as s:s.db.execute('DELETE FROM provider_metadata')
        self.assertRaises(Refused,self.status)
    def test_db_transaction_required(self):
        with sqlite3.connect(self.dbpath) as db:self.assertRaises(Refused,OwnerHooks,db,self.cipher,KEY)
    def test_tag_key_required(self):
        with self.tx() as s:self.assertRaises(Refused,OwnerHooks,s.db,self.cipher,b'too-short')
    def test_invalid_sealed_source(self):
        self.save()
        with self.tx() as s:
            items=self.items(s);items['kraken_read_key']=b'junk'
            with self.assertRaises(InvalidToken):s.replace('kraken_read',items,P,101)
    def test_pending_manual_invalidation_via_replace(self):self.save();a=self.start();self.save(text='new',now=102);self.assertRaises(Refused,self.finish,a,'permissions_verified',103)
    def test_expired_attempt_refused(self):self.save();a=self.start();self.finish(a,'permissions_verified',now=402);self.assertEqual(self.status(now=402),'stored_unverified')
    def test_same_key_rotation_new_nonces(self):self.save();a=self.start();self.save(now=102);b=self.start(now=102);self.assertGreater(b.nonce,a.nonce)
    def test_telegram_ack(self):self.save('telegram_desk');a=self.start('telegram_desk');self.finish(a,'test_accepted_by_telegram');self.assertEqual(self.status('telegram_desk'),'test_accepted_by_telegram')
    def test_grok(self):self.save('grok');a=self.start('grok');self.finish(a,'metadata_verified');self.assertEqual(self.status('grok'),'metadata_verified')

if __name__=='__main__':unittest.main()