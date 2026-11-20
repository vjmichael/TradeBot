"""Genuine local service and SO_PEERCRED tests, no real financial desk."""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from reserve_checkpoint import Checkpoint, GENESIS

AUTHORITY=65533
DESK=65534
OTHER=65532
ROOT=Path(__file__).resolve().parents[1]


def db():
    c=sqlite3.connect(':memory:')
    c.execute('CREATE TABLE fin_events(seq INTEGER PRIMARY KEY,kind TEXT,digest TEXT)')
    c.execute('BEGIN IMMEDIATE')
    return c


def event(c, seq, kind='reserve_adopt'):
    cp=Checkpoint(seq,hashlib.sha256(f'{seq}:{kind}'.encode()).hexdigest())
    c.execute('INSERT INTO fin_events VALUES(?,?,?)',(seq,kind,cp.digest))
    return cp


@unittest.skipUnless(os.geteuid()==0 and shutil.which('setpriv'),'root/setpriv required for real UID tests')
class IPCTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='reserve-ipc-',dir='/run')); self.root.chmod(0o755)
        self.addCleanup(shutil.rmtree,self.root)
        for name, mode in [('witness',0o755),('socket',0o755),('credential',0o700)]:
            p=self.root/name;p.mkdir();p.chmod(mode);os.chown(p,AUTHORITY,AUTHORITY)
        for name in ['reserve_checkpoint.py','reserve_ipc.py','reserve_enrollment.py']:
            shutil.copyfile(ROOT/name,self.root/name)
        shutil.copyfile(__file__,self.root/'fixture.py')
        self.prefix=(f'import sys;sys.path.insert(0,{str(self.root)!r});'
                     'from fixture import *;from reserve_ipc import *;import reserve_enrollment as en;'
                     f'w={str(self.root/"witness")!r};d={str(self.root/"socket")!r};k={str(self.root/"credential")!r};p={str(self.root/"public")!r};'
                     f'ids=dict(authority_uid={AUTHORITY},desk_uid={DESK});'
                     'store=AuthorityStore(w,**ids);c=db();'
                     'password="fixture-passphrase-not-real";')
        pub=self.run_as(AUTHORITY,'store.initialize(c);print(en.enroll(k,password,**ids).hex())')
        (self.root/'public').write_bytes(bytes.fromhex(pub));(self.root/'public').chmod(0o644)
        self.process=None;self.addCleanup(self.stop);self.start()

    def cmd(self,uid,code):
        return ['setpriv',f'--reuid={uid}',f'--regid={uid}','--clear-groups',sys.executable,'-B','-c',self.prefix+code]

    def run_as(self,uid,code):
        r=subprocess.run(self.cmd(uid,code),capture_output=True,text=True,timeout=12)
        self.assertEqual(r.returncode,0,r.stderr)
        return r.stdout.strip()

    def start(self):
        self.process=subprocess.Popen(self.cmd(AUTHORITY,'AuthorityService(store,d,**ids,workspace="fixture",pin=p).serve()'),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(80):
            if self.process.poll() is not None:self.fail(self.process.stderr.read().decode())
            if (self.root/'socket'/'authority.sock').exists():
                # A SIGTERM may leave a stale socket; wait for a successful connection.
                import socket
                try:
                    with socket.socket(socket.AF_UNIX) as s:s.connect(str(self.root/'socket'/'authority.sock'))
                    return
                except OSError:pass
            time.sleep(.05)
        self.fail('service failed to listen')

    def stop(self):
        if self.process is not None:
            self.process.terminate();self.process.wait(timeout=5);self.process.stderr.close();self.process=None

    def client(self, code):
        return self.run_as(DESK,'a=AuthorityClient(d,**ids,workspace="fixture");'+code)

    def refusal(self, uid, code):
        script='try:\n '+code+'\nexcept (OSError,ValueError):\n print("refused")\nelse:\n raise AssertionError("unexpected success")'
        self.assertEqual(self.run_as(uid,'exec('+repr(script)+')'),'refused')

    def test_enrollment_isolation_and_no_overwrite(self):
        self.refusal(AUTHORITY,'en.enroll(k,password,**ids)')
        self.refusal(AUTHORITY,'en.unlock(k,"incorrect-passphrase",**ids)')
        self.refusal(DESK,'Path(k,en.NAME).read_bytes()')
        self.refusal(DESK,'en.unlock(k,password,**ids)')
        self.assertEqual(self.run_as(AUTHORITY,'print(len(en.unlock(k,password,**ids)))'),'32')

    def test_exact_permit_advance_restart_and_rollback(self):
        self.assertEqual(self.client('print(a.verify_transaction(c)==GENESIS)'),'True')
        signed=self.run_as(AUTHORITY,'n=event(c,1);print(canonical(sign_permit(en.unlock(k,password,**ids),"fixture",GENESIS,n,int(time.time())+120)).decode())')
        self.assertEqual(self.client(f'n=event(c,1);print(a.advance_transaction(c,GENESIS,json.loads({signed!r}))==n);c.commit()'),'True')
        self.stop();self.start()
        self.assertEqual(self.client('n=event(c,1);print(a.verify_transaction(c)==n)'),'True')
        self.refusal(DESK,'AuthorityClient(d,**ids,workspace="fixture").verify_transaction(c)')
        self.refusal(DESK,f'AuthorityClient(d,**ids,workspace="fixture").request("advance",json.loads({signed!r}))')

    def test_forged_expired_wrong_scope_and_no_sign_api(self):
        for code in [
            'a.request("sign")',
            'a.request("advance",{})',
            'n=event(c,1);a.request("advance",sign_permit(b"x"*32,"fixture",GENESIS,n,int(time.time())+60))',
            'AuthorityClient(d,**ids,workspace="wrong").request("checkpoint")']:
            self.refusal(DESK,'a=AuthorityClient(d,**ids,workspace="fixture");'+code)
        signed=self.run_as(AUTHORITY,'n=event(c,1);print(canonical(sign_permit(en.unlock(k,password,**ids),"fixture",GENESIS,n,int(time.time())-1)).decode())')
        self.refusal(DESK,f'AuthorityClient(d,**ids,workspace="fixture").request("advance",json.loads({signed!r}))')
        self.assertEqual(self.client('print(a.verify_transaction(c)==GENESIS)'),'True')

    def test_peer_refusal_and_unavailable_service(self):
        # Send directly to bypass client-side identity admission.
        self.assertEqual(self.run_as(OTHER,'s=socket.socket(socket.AF_UNIX);s.connect(d+"/authority.sock");print(receive(s)=={"error":"authority_refused"});s.close()'),'True')
        self.stop()
        self.refusal(DESK,'AuthorityClient(d,**ids,workspace="fixture").request("checkpoint")')

    def test_exact_event_mismatch_and_interrupted_commit_refusal(self):
        signed=self.run_as(AUTHORITY,'n=event(c,1);print(canonical(sign_permit(en.unlock(k,password,**ids),"fixture",GENESIS,n,int(time.time())+120)).decode())')
        self.refusal(DESK,f'event(c,1,"reserve_revoke");AuthorityClient(d,**ids,workspace="fixture").advance_transaction(c,GENESIS,json.loads({signed!r}))')
        self.assertEqual(self.client('print(a.verify_transaction(c)==GENESIS)'),'True')
        self.assertEqual(self.client(f'n=event(c,1);print(a.advance_transaction(c,GENESIS,json.loads({signed!r}))==n);c.rollback()'),'True')
        self.refusal(DESK,'AuthorityClient(d,**ids,workspace="fixture").verify_transaction(c)')


if __name__=='__main__':unittest.main()
