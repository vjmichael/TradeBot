"""Kernel fence and minimized entry request negatives; no exchange or secrets."""
import os
import socket
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from reserve_entry_broker import attempt,offer,transfer,accept_fence,serve_request
from reserve_entry_fence import fence,open_fence,installed_fence


@unittest.skipUnless(os.geteuid()==0,'disposable root-owned fence required')
class EntryFenceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(dir='/run');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.root.chmod(0o755)
        self.lock=self.root/'fence.lock';self.lock.touch();os.chown(self.lock,0,65531);self.lock.chmod(0o640)
        self.row=dict(id='intent',instance_id='a',pair='BTC/USD',entry=100,stop=90,target=120,notional=500,reserved=510,expires=1000)
    def test_transferred_description_retains_lock_after_origin_close(self):
        a,b=socket.socketpair()
        with closing(a),closing(b):
            with fence(self.lock) as original:
                transfer(a,original);fd=accept_fence(b,self.lock)
            try:
                with self.assertRaisesRegex(ValueError,'entry_fence_busy'):
                    with fence(self.lock,timeout=.05):pass
            finally:os.close(fd)
            with fence(self.lock,timeout=.1):pass
    def test_unlocked_or_wrong_inode_refused(self):
        a,b=socket.socketpair()
        with closing(a),closing(b):
            fd=open_fence(self.lock)
            try:
                transfer(a,fd)
                with self.assertRaisesRegex(ValueError,'entry_fence_not_held'):accept_fence(b,self.lock)
            finally:os.close(fd)
            other=self.root/'other.lock';other.touch();os.chown(other,0,65531);other.chmod(0o640)
            with fence(other) as fd:
                transfer(a,fd)
                with self.assertRaisesRegex(ValueError,'entry_fence_identity'):accept_fence(b,self.lock)
    def test_lock_persists_when_enable_marker_removed(self):
        with patch('reserve_entry_fence.DATA',self.root),patch('reserve_entry_fence.LOCK',self.lock),patch('reserve_entry_fence.MARKER',self.root/'absent'):
            with installed_fence(self.root):
                with self.assertRaisesRegex(ValueError,'entry_fence_busy'):
                    with fence(self.lock,timeout=.05):pass
    def test_attempt_is_immutable_and_omits_claim_token(self):
        work=self.root/'native';work.mkdir(mode=0o700)
        row=offer(self.row|{'claim_token':'synthetic-private-token'})
        self.assertEqual(row,self.row);attempt(work,row,'a'*32,'b'*64)
        raw=next(work.iterdir()).read_bytes();self.assertNotIn(b'synthetic-private-token',raw)
        with self.assertRaises(FileExistsError):attempt(work,row,'c'*32,'b'*64)
        self.assertEqual(next(work.iterdir()).read_bytes(),raw)
        for bad in ({'pair':'BTC/EUR'},{'reserved':499},{'stop':100}):
            with self.assertRaises(ValueError):offer(self.row|bad)
    def test_wrong_peer_rejected_before_transaction(self):
        a,b=socket.socketpair()
        with closing(a),closing(b):
            with self.assertRaisesRegex(ValueError,'entry_peer'):
                serve_request(a,None,None,None,None,self.lock,native_uid=65532,desk_uid=os.geteuid())

if __name__=='__main__':unittest.main()
