"""Archive is not authority to release; exact signed proof/approval required."""
import tempfile
import unittest
import sqlite3
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from financial_ledger import sha
from reserve_no_effect import _body,_signed,NATIVE_DOMAIN,HUMAN_DOMAIN
from reserve_recovery_archive import DOMAIN,disposition,verify_archive,_save,_read

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.ns=b'n'*32;self.hs=b'h'*32
        self.n,self.h=[Ed25519PrivateKey.from_private_bytes(s).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw) for s in (self.ns,self.hs)]
        row=dict(id='i',instance_id='a',pair='BTC/USD',entry=100,stop=90,target=120,notional=500,reserved=510,expires=200)
        self.proof=_signed(self.ns,NATIVE_DOMAIN,_body('paper',row,'a'*64,'b'*64,1,'c'*64,1,'d'*64,1000,100))
        self.review=dict(proof=self.proof,approval=_signed(self.hs,HUMAN_DOMAIN,dict(workspace='paper',proof=sha(self.proof),expires=400)))
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript("CREATE TABLE gate_intents(id TEXT, instance_id TEXT, state TEXT); INSERT INTO gate_intents VALUES('i','a','UNKNOWN'); CREATE TABLE gate_events(seq INTEGER PRIMARY KEY,intent_id TEXT,action TEXT,at INTEGER, detail TEXT);")
    def check(self,review=None,now=100):return disposition(self.db,self.proof,review,self.n,self.h,'paper',now)
    def archive(self,**kw):return _signed(self.ns,DOMAIN,dict(v=1,workspace='paper',proof=self.proof,review=self.review,status='accounted',at=120)|kw)
    def test_expiry_does_not_mutate_hold(self):
        self.assertIsNone(self.check(now=399));self.assertIsNone(self.check(now=430));self.assertEqual(self.check(now=431),'expired')
        self.assertEqual(self.check(self.review,431),'expired')
        self.assertEqual(self.db.execute('SELECT state FROM gate_intents').fetchone()[0],'UNKNOWN')
    def test_resolution_must_match_exact_committed_pair(self):
        from paper_execution import append_event
        self.db.execute("UPDATE gate_intents SET state='NO_FILL'")
        with self.assertRaises(ValueError):self.check(self.review)
        append_event(self.db,'i','native_reviewed_no_effect',{'receipt':sha(self.review),'proof':sha(self.proof)},120)
        self.assertEqual(self.check(self.review),'accounted')
        with self.assertRaises(ValueError):self.check(None)
        wrong=dict(proof=self.proof,approval=_signed(self.hs,HUMAN_DOMAIN,dict(workspace='paper',proof=sha(self.proof),expires=399)))
        with self.assertRaises(ValueError):self.check(wrong)
    def test_unsigned_expiry_tampering_and_domain_refusal(self):
        self.assertEqual(verify_archive(self.n,self.h,'paper',self.archive())['status'],'accounted')
        for kw in (dict(status='expired',at=430),dict(status='accounted',review=None),dict(workspace='other')):
            with self.assertRaises(ValueError):verify_archive(self.n,self.h,'paper',self.archive(**kw))
        p=self.archive();p['body']['at']=121
        with self.assertRaises(ValueError):verify_archive(self.n,self.h,'paper',p)
    def test_archive_immutable_and_custody(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            fd=os.open(d,os.O_RDONLY|os.O_DIRECTORY)
            try:
                box=SimpleNamespace(owner=os.geteuid(),group=os.getegid());p=self.archive()
                _save(box,fd,'review.json',p);_save(box,fd,'review.json',p)
                self.assertEqual(_read(box,fd,'review.json'),p)
                with self.assertRaises(ValueError):_save(box,fd,'review.json',self.archive(at=130))
                os.unlink(Path(d)/'review.json');(Path(d)/'review.json').symlink_to('/dev/null')
                with self.assertRaises(OSError):_read(box,fd,'review.json')
            finally:os.close(fd)
