import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from contextlib import closing
from market_memory import MarketMemory,MemoryUnavailable
from economic_memory import EconomicMemory
from market_quality import QualityMemory
from data_preparation import Preparation,_db
from data_lifecycle import backup,restore
from data_retention import retire_books,MIN_AGE
from sample_archive import archived_sample,status,consolidate
from recovery_rotation import preview,apply
class CustodyTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.data=self.root/'data';self.data.mkdir(mode=0o700)
        self.m=MarketMemory(self.data);EconomicMemory(self.data);QualityMemory(self.m)
        self.p=Preparation(self.data);self.addCleanup(self.p.close)
        self.copies=self.root/'data-recovery-copies';self.copies.mkdir(mode=0o700)
        self.now=int(time.time());self.slot=(self.now-MIN_AGE-1200)//300*300
    def copy(self,n):
        key=f'{n:032x}';receipt=backup(self.data,self.copies/key)
        with closing(_db(self.p.root)) as db,db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(key,'backup','complete','done',self.now,self.now))
            db.execute('INSERT INTO maintenance_results VALUES(?,?)',(key,json.dumps(receipt)))
        return key,receipt
    def test_archive_transaction_and_restore(self):
        with self.m.tx() as db:
            for t in (self.slot,self.slot+300):db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',('BTC/USD',t,t,t,'unavailable'))
        key,receipt=self.copy(1)
        out=retire_books(self.data,self.copies/key,receipt['manifest_sha256'],apply=True,now=self.now)
        self.assertEqual(out['attempts'],1)
        archived=archived_sample(self.m,'BTC/USD',self.slot)
        self.assertEqual(archived['attempt']['state'],'unavailable')
        self.assertEqual(status(self.m)['archived_records'],1)
        with self.m.tx() as db:db.execute('DELETE FROM quality_archive')
        self.assertEqual(consolidate(self.data,self.copies/key,receipt['manifest_sha256'])['archived'],1)
        self.assertEqual(archived_sample(self.m,'BTC/USD',self.slot),archived)
        key2,r2=self.copy(2)
        restore(self.copies/key2,self.root/'restored',r2['manifest_sha256'])
        self.assertEqual(archived_sample(MarketMemory(self.root/'restored'),'BTC/USD',self.slot),archived)
        with self.m.tx() as db:db.execute("UPDATE quality_archive SET compressed=X'31'")
        with self.assertRaises(MemoryUnavailable):archived_sample(self.m,'BTC/USD',self.slot)
        with self.assertRaises(MemoryUnavailable):backup(self.data,self.root/'corrupt-copy')
    def test_rotation_preserves_references_and_two_newer_copies(self):
        a,ra=self.copy(1)
        with self.m.tx() as db:
            db.execute('CREATE TABLE quality_retired(pair TEXT NOT NULL,slot INTEGER NOT NULL,had_book INTEGER NOT NULL,retired_at INTEGER NOT NULL,manifest_sha256 TEXT NOT NULL,PRIMARY KEY(pair,slot))')
            db.execute('INSERT INTO quality_retired VALUES(?,?,?,?,?)',('BTC/USD',self.slot,0,self.now,ra['manifest_sha256']))
        b,rb=self.copy(2);c,rc=self.copy(3);d,rd=self.copy(4)
        plan=preview(self.data,self.copies);self.assertTrue(plan['eligible']);self.assertEqual(plan['victim']['id'],b)
        result=apply(self.data,self.copies,plan)
        self.assertEqual(result['state'],'complete');self.assertFalse((self.copies/b).exists())
        self.assertTrue(all((self.copies/k).exists() for k in (a,c,d)))
        self.assertEqual(apply(self.data,self.copies,plan)['state'],'complete')
        self.assertFalse(preview(self.data,self.copies)['eligible'])
    def test_rotation_resumes_after_quarantine_interrupt(self):
        a,_=self.copy(1);b,_=self.copy(2);c,_=self.copy(3)
        plan=preview(self.data,self.copies)
        self.assertTrue(plan['eligible'])
        from recovery_rotation import shutil
        original=shutil.rmtree
        def interrupt(path,*args,**kwargs):
            if Path(path)==self.copies/('.rotation-'+a):raise OSError('simulated interruption')
            return original(path,*args,**kwargs)
        with patch('recovery_rotation.shutil.rmtree',side_effect=interrupt):
            with self.assertRaises(OSError):apply(self.data,self.copies,plan)
        self.assertTrue((self.copies/('.rotation-'+a)).is_dir())
        self.assertEqual(apply(self.data,self.copies,plan)['state'],'complete')
        self.assertTrue((self.copies/b).exists() and (self.copies/c).exists())
    def test_multi_batch_archive_and_legacy_resume(self):
        # Time-shifted sample receipts, not genuinely elapsed month-long run.
        from market_directory import MarketDirectory,parse_directory
        from test_market_directory import payload
        registry=MarketDirectory(self.data)
        snap=registry.refresh(now=self.now,fetch=lambda:parse_directory(payload()))
        registry.select('ADA/USD',True,snap['version'],now=self.now)
        pairs=registry.selected_symbols()
        self.assertEqual(len(pairs),4)
        old=self.slot-305*300
        with self.m.tx() as db:
            for pair in pairs:
                for i in range(301):
                    slot=old+i*300
                    db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',(pair,slot,slot,slot,'unavailable'))
                db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',(pair,self.slot,self.slot,self.slot,'unavailable'))
        key,r=self.copy(1);digest=r['manifest_sha256']
        first=retire_books(self.data,self.copies/key,digest,apply=True,now=self.now,limit=1000)
        second=retire_books(self.data,self.copies/key,digest,apply=True,now=self.now,limit=1000)
        self.assertEqual((first['attempts'],second['attempts']),(1000,204))
        self.assertEqual(status(self.m)['archived_records'],1204)
        with self.m.tx() as db:db.execute('DELETE FROM quality_archive')
        self.assertEqual(consolidate(self.data,self.copies/key,digest)['archived'],1000)
        self.assertEqual(consolidate(self.data,self.copies/key,digest)['archived'],204)
        self.assertEqual(consolidate(self.data,self.copies/key,digest)['archived'],0)
        key2,r2=self.copy(2);restore(self.copies/key2,self.root/'multi-restored',r2['manifest_sha256'])
        reopened=MarketMemory(self.root/'multi-restored')
        self.assertEqual(status(reopened)['archived_records'],1204)
        self.assertEqual(archived_sample(reopened,'ADA/USD',old)['attempt']['state'],'unavailable')
    def test_rotation_resume_after_removal_before_terminal_commit(self):
        a,_=self.copy(1);b,_=self.copy(2);c,_=self.copy(3)
        plan=preview(self.data,self.copies)
        with patch('recovery_rotation.syncdir',side_effect=OSError('simulated crash after removal')):
            with self.assertRaises(OSError):apply(self.data,self.copies,plan)
        self.assertFalse((self.copies/a).exists())
        self.assertEqual(apply(self.data,self.copies,plan)['state'],'complete')
        self.assertTrue((self.copies/b).exists() and (self.copies/c).exists())
    def test_rotation_rejects_unknown_copy(self):
        a,_=self.copy(1);b,_=self.copy(2);c,_=self.copy(3)
        plan=preview(self.data,self.copies);self.assertTrue(plan['eligible'])
        (self.copies/'unexpected').mkdir()
        with self.assertRaises(MemoryUnavailable):apply(self.data,self.copies,plan)
        self.assertTrue((self.copies/a).exists())
