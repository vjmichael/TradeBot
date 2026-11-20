import sqlite3, tempfile, unittest
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass, replace
from cryptography.fernet import Fernet, InvalidToken
from verification_state import State

@dataclass(frozen=True)
class Receipt:
    status: str
    role: str
    generation: str
    checked_at: int
    expires_at: int

A='a'*64; B='b'*64; T='1'*64; U='2'*64
class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'fixture.sqlite3'; self.cipher=Fernet(Fernet.generate_key())
        with self.tx() as s: s.initialize()
    def tearDown(self): self.tmp.cleanup()
    @contextmanager
    def tx(self):
        db=sqlite3.connect(self.path,isolation_level=None); db.execute('BEGIN IMMEDIATE')
        try: yield State(db,self.cipher); db.commit()
        except BaseException: db.rollback(); raise
        finally: db.close()
    def enroll(self,role='read',binding=A,tag=T,now=100):
        with self.tx() as s: s.replace(role,binding,tag,now)
    def reserve(self,binding=A,now=101):
        with self.tx() as s: return s.reserve('read',binding,now)
    def status(self,binding=A,now=101):
        with self.tx() as s: return s.status('read',binding,now)
    def receipt(self,a): return Receipt('permissions_verified',a.role,a.generation,a.started,a.started+300)
    def finish(self,a,binding=A,receipt=None,now=102):
        with self.tx() as s: s.finish(a,binding,receipt or self.receipt(a),now)
    def test_initial(self): self.assertEqual(self.status(None),'not_configured'); self.assertEqual(self.status(),'verification_unavailable')
    def test_receipt_expiry(self):
        self.enroll(); a=self.reserve(); self.finish(a); self.assertEqual(self.status(now=400),'permissions_verified'); self.assertEqual(self.status(now=401),'stored_unverified')
    def test_expired_cannot_resurrect(self):
        self.enroll(); a=self.reserve(); self.finish(a); self.assertEqual(self.status(now=401),'stored_unverified'); self.assertEqual(self.status(now=400),'verification_unavailable'); self.assertEqual(self.status(now=402),'stored_unverified')
    def test_atomic_with_vault_fixture(self):
        with self.tx() as s: s.db.execute('CREATE TABLE fixture_pair (sealed BLOB NOT NULL)')
        with self.assertRaises(RuntimeError):
            with self.tx() as s:
                s.db.execute('INSERT INTO fixture_pair VALUES (?)',(self.cipher.encrypt(b"synthetic-pair"),)); s.replace('read',A,T,100); raise RuntimeError()
        with self.tx() as s:
            self.assertEqual(s.db.execute('SELECT count(*) FROM fixture_pair').fetchone()[0],0); self.assertEqual(s.status('read',None,101),'not_configured')
        with self.tx() as s:
            s.db.execute('INSERT INTO fixture_pair VALUES (?)',(self.cipher.encrypt(b"synthetic-pair"),)); s.replace('read',A,T,100)
        self.assertEqual(self.status(),'stored_unverified')
    def test_replace_invalidates(self):
        self.enroll(); a=self.reserve(); self.finish(a); self.enroll(binding=B,now=103); self.assertEqual(self.status(B,104),'stored_unverified'); self.assertEqual(self.status(A,104),'verification_unavailable')
    def test_late_completion(self):
        self.enroll(); a=self.reserve(); self.enroll(binding=B,now=102)
        with self.assertRaises(ValueError): self.finish(a,B,now=103)
    def test_remove_nonce_retained(self):
        self.enroll(); a=self.reserve()
        with self.tx() as s: s.remove('read',101)
        self.enroll(now=101); b=self.reserve(); self.assertGreater(b.nonce,a.nonce); self.assertNotEqual(b.generation,a.generation)
    def test_role_collision(self):
        self.enroll()
        with self.assertRaises(ValueError): self.enroll('trade',B,T)
    def test_distinct_role(self):
        self.enroll(); self.enroll('trade',B,U)
        with self.tx() as s: self.assertEqual(s.status('trade',B,101),'stored_unverified')
    def test_pending_duplicate(self):
        self.enroll(); self.reserve()
        with self.assertRaises(ValueError): self.reserve()
        self.assertEqual(self.status(),'verification_pending')
    def test_restart_invalidates_pending(self):
        self.enroll(); a=self.reserve()
        with self.tx() as s: s.invalidate('read',A,101)
        b=self.reserve(now=101); self.assertGreater(b.nonce,a.nonce)
        with self.assertRaises(ValueError): self.finish(a)
    def test_receipt_role_mismatch(self):
        self.enroll(); a=self.reserve()
        with self.assertRaises(ValueError): self.finish(a,receipt=replace(self.receipt(a),role='trade'))
    def test_receipt_generation_mismatch(self):
        self.enroll(); a=self.reserve()
        with self.assertRaises(ValueError): self.finish(a,receipt=replace(self.receipt(a),generation='0'*32))
    def test_receipt_long_expiry(self):
        self.enroll(); a=self.reserve()
        with self.assertRaises(ValueError): self.finish(a,receipt=replace(self.receipt(a),expires_at=10000))
    def test_failure_clears_prior(self):
        self.enroll(); a=self.reserve(); self.finish(a); b=self.reserve(now=103); self.finish(b,receipt=replace(self.receipt(b),status='provider_refused',expires_at=0),now=104); self.assertEqual(self.status(now=104),'provider_refused')
    def test_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.tx() as s: s.replace('read',A,T,100); raise RuntimeError()
        self.assertEqual(self.status(None),'not_configured')
    def test_clock_rollback(self):
        self.enroll(); self.reserve(); self.assertEqual(self.status(now=99),'verification_unavailable')
        with self.assertRaises(ValueError): self.reserve(now=99)
    def test_tamper(self):
        with self.tx() as s: s.db.execute("UPDATE kraken_verification SET sealed=X'0102'")
        with self.assertRaises(InvalidToken): self.status()
    def test_no_implicit_recreate(self):
        with self.tx() as s: s.db.execute('DELETE FROM kraken_verification')
        with self.assertRaises(ValueError): self.status()
    def test_ciphertext_no_opaque_markers(self):
        self.enroll(); self.reserve(); data=self.path.read_bytes(); self.assertNotIn(A.encode(),data); self.assertNotIn(T.encode(),data)
    def test_require_transaction(self):
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(ValueError): State(db,self.cipher)

if __name__=='__main__': unittest.main()
