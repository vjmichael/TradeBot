"""Local reduction grant negatives. Actual native recovery is tested separately."""
import os
import tempfile
import unittest
from pathlib import Path
from reserve_outage_protection import save_grant, read_grant
from financial_ledger import sha


class OutageGrantTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.work=Path(tmp.name);self.key=b'x'*32;self.binding='a'*64
        self.row=dict(id='intent',instance_id='a',pair='BTC/USD',entry=100,stop=90,target=120,notional=500,reserved=510,expires=200)
    def write(self):save_grant(self.work,self.key,self.row,'b'*32,self.binding,100,102)
    def read(self,**changes):
        args=dict(work=self.work,key=self.key,instance='a',intent='intent',binding=self.binding,pair='BTC/USD',now=400)|changes
        return read_grant(**args)
    def test_expired_entry_permit_retains_reduce_only_scope(self):
        self.write();self.assertEqual(self.read(),self.row)
        with self.assertRaises(FileExistsError):self.write()
    def test_scope_and_clock_mismatch(self):
        self.write()
        for kw in (dict(pair='ETH/USD'),dict(instance='b'),dict(now=99),dict(key=b'y'*32)):
            with self.assertRaises(ValueError):self.read(**kw)
    def test_missing_and_changed_grant_never_authorize(self):
        with self.assertRaises(FileNotFoundError):self.read()
        self.write();path=self.work/('grant-'+sha([self.binding,'intent'])+'.json')
        path.write_bytes(path.read_bytes().replace(b'BTC/USD',b'ETH/USD'))
        with self.assertRaises(ValueError):self.read()
    def test_custody_and_expiration_fail_closed(self):
        for at,until in [(100,103),(101,101),(199,201)]:
            with self.assertRaises(ValueError):save_grant(self.work,self.key,self.row,'b'*32,self.binding,at,until)
        self.write();p=next(self.work.iterdir());p.chmod(0o644)
        with self.assertRaises(ValueError):self.read()
        p.unlink();p.symlink_to('/dev/null')
        with self.assertRaises(OSError):self.read()

if __name__=='__main__':unittest.main()
