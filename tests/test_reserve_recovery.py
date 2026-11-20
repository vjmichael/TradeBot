"""Separate-UID recovery and native Tk qualification with fixture financial data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from reserve_checkpoint import GENESIS
from reserve_ipc import canonical
from reserve_recovery import event_checkpoint

AUTH=65533;DESK=65534;NATIVE=65532
ROOT=Path(__file__).resolve().parents[1]
ZERO='0'*64


def event(kind='reserve_adopt'):
    p={'version':'a'}
    if kind=='reserve_adopt':p['policy']=dict(rate_bps=500,floor_mode='fixed',floor_fixed=100_000_000,floor_bps=0,ceiling=20_000_000_000,cycle_cap=100_000_000,lifetime_cap=500_000_000,anchor=0,start=100,end=1000,expiry=1000)
    e=dict(seq=1,id='fixture-approval',instance='fixture-a',kind=kind,body={'payload':{'payload':p,'at':100},'recorded_at':100},previous=ZERO)
    e['digest']=hashlib.sha256(canonical([e['seq'],e['id'],e['instance'],e['kind'],e['body'],e['previous']])).hexdigest()
    return e


def db(path=':memory:'):
    c=sqlite3.connect(path,isolation_level=None)
    c.execute('CREATE TABLE IF NOT EXISTS fin_events(seq INTEGER PRIMARY KEY,id TEXT UNIQUE,instance TEXT,kind TEXT,body TEXT,previous TEXT,digest TEXT UNIQUE)')
    c.execute('CREATE TABLE IF NOT EXISTS fin_head(id INTEGER PRIMARY KEY,count INTEGER,digest TEXT,boot INTEGER)')
    c.execute('INSERT OR IGNORE INTO fin_head VALUES(1,0,?,0)',(ZERO,));c.execute('BEGIN IMMEDIATE');return c


def replay(c):
    # Reconstruct the exact journal hash contract; not the native financial engine.
    head=ZERO;n=0
    for seq,eid,i,kind,raw,prev,digest in c.execute('SELECT * FROM fin_events ORDER BY seq'):
        n+=1;assert seq==n and prev==head
        body=json.loads(raw);assert canonical(body).decode()==raw
        assert hashlib.sha256(canonical([seq,eid,i,kind,body,prev])).hexdigest()==digest;head=digest
    assert c.execute('SELECT count,digest FROM fin_head').fetchone()==(n,head)
    return {},{},n,head


def insert(c,e):
    c.execute('INSERT INTO fin_events VALUES(?,?,?,?,?,?,?)',(e['seq'],e['id'],e['instance'],e['kind'],canonical(e['body']).decode(),e['previous'],e['digest']))
    c.execute('UPDATE fin_head SET count=?,digest=? WHERE id=1',(e['seq'],e['digest']))


@unittest.skipUnless(os.geteuid()==0 and shutil.which('setpriv'),'root/setpriv required')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='reserve-recover-',dir='/run'));self.root.chmod(0o755);self.addCleanup(shutil.rmtree,self.root)
        for name,uid,mode in [('witness',AUTH,0o755),('socket',AUTH,0o755),('credential',AUTH,0o700),('native',NATIVE,0o700),('desk',DESK,0o700)]:
            p=self.root/name;p.mkdir();p.chmod(mode);os.chown(p,uid,uid)
        for n in ['reserve_checkpoint.py','reserve_ipc.py','reserve_enrollment.py','reserve_recovery.py','reserve_exact_review.py']:
            shutil.copyfile(ROOT/n,self.root/n)
        shutil.copyfile(__file__,self.root/'fixture.py')
        self.prefix=(f'import sys;sys.path.insert(0,{str(self.root)!r});from fixture import *;'
                     'from reserve_recovery import *;from reserve_exact_review import *;import reserve_enrollment as en;'
                     f'root=Path({str(self.root)!r});ids=dict(authority_uid={AUTH},desk_uid={DESK});'
                     'w=root/"witness";sock=root/"socket";creds=root/"credential";pin=root/"permit.pub";native=root/"native.pub";'
                     'password="fixture-only-long-password";store=AuthorityStore(w,**ids);'
                     'e=event();c=db();')
        pub=self.run_as(AUTH,'store.initialize(c);print(en.enroll(creds,password,**ids).hex())')
        (self.root/'permit.pub').write_bytes(bytes.fromhex(pub));(self.root/'permit.pub').chmod(0o644)
        pub=self.run_as(NATIVE,'seed=secrets.token_bytes(32);p=root/"native"/"key";p.write_bytes(seed);p.chmod(0o600);print(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex())')
        (self.root/'native.pub').write_bytes(bytes.fromhex(pub));(self.root/'native.pub').chmod(0o644)
        self.process=None;self.addCleanup(self.stop)

    def cmd(self,uid,code):return ['setpriv',f'--reuid={uid}',f'--regid={uid}','--clear-groups',sys.executable,'-B','-c',self.prefix+code]
    def run_as(self,uid,code):
        r=subprocess.run(self.cmd(uid,code),capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr);return r.stdout.strip()
    def start(self):
        self.process=subprocess.Popen(self.cmd(AUTH,'RecoverableAuthorityService(store,sock,**ids,workspace="fixture",pin=pin).serve()'),stderr=subprocess.PIPE)
        for _ in range(100):
            if self.process.poll() is not None:self.fail(self.process.stderr.read().decode())
            try:
                import socket
                with socket.socket(socket.AF_UNIX) as conn:conn.connect(str(self.root/'socket'/'authority.sock'))
                return
            except OSError:time.sleep(.05)
        self.fail('service unavailable')
    def stop(self):
        if self.process is not None:self.process.terminate();self.process.wait(timeout=5);self.process.stderr.close();self.process=None
    def evidence(self):
        return self.run_as(NATIVE,'b=evidence_body("fixture",e,int(time.time()),"a"*64);key=Ed25519PrivateKey.from_private_bytes((root/"native"/"key").read_bytes());print(canonical({"body":b,"signature":key.sign(DOMAIN+canonical(b)).hex()}).decode())')
    def review_prefix(self,proof):
        return f'proof=json.loads({proof!r});r=ExactReview("fixture",creds,**ids,permit_pin=pin,native_pin=native,revalidate=lambda prev,ev,pr:prev==GENESIS and ev==event() and pr==proof);'
    def reviewed(self):
        proof=self.evidence();return self.run_as(AUTH,self.review_prefix(proof)+'v=r.begin(GENESIS,e,proof);print(canonical(r.confirm(v["challenge"],password)).decode())')
    def client(self,code):return self.run_as(DESK,'a=RecoveryClient(sock,**ids,workspace="fixture");pub=pinned_key(pin);'+code)
    def test_exact_recovery_after_real_service_restart(self):
        packet=self.reviewed();self.start()
        self.assertEqual(self.client(f'p=json.loads({packet!r});c=db(root/"desk"/"journal.sqlite");insert(c,e);print(a.advance_event(c,GENESIS,p,pub,replay)==event_checkpoint(e));c.rollback()'),'True')
        self.stop();self.start()
        code='c=db(root/"desk"/"journal.sqlite");assert replay(c)[2]==0;'
        self.assertEqual(self.client(code+'print(a.recover_event(c,pub,replay,lambda db,p: True));c.commit()'),'recovered')
        self.assertEqual(self.client('c=db(root/"desk"/"journal.sqlite");print(a.recover_event(c,pub,replay,lambda db,p: False));c.commit()'),'already_committed')
    def test_recovery_refuses_changed_context_and_legacy_advance(self):
        packet=self.reviewed();self.start()
        self.assertEqual(self.client(f'p=json.loads({packet!r});exec("try:\\n a.request(\\\"advance\\\",p[\\\"permit\\\"])\\nexcept ValueError:\\n print(\\\"refused\\\")\\nelse:\\n raise AssertionError()")'),'refused')
        self.client(f'p=json.loads({packet!r});insert(c,e);a.advance_event(c,GENESIS,p,pub,replay);c.rollback()')
        code='try:\n a.recover_event(c,pub,replay,lambda db,p: False)\nexcept ValueError:\n assert replay(c)[2]==0;print("refused")\nelse:\n raise AssertionError()'
        self.assertEqual(self.client('exec('+repr(code)+')'),'refused')
    def test_record_before_checkpoint_failure_and_retry(self):
        packet=self.reviewed();code=f'p=json.loads({packet!r});j=RecoveryStore(store,"fixture",pin);'
        action='try:\n j.advance(p,int(time.time()),before_publish=lambda: (_ for _ in ()).throw(RuntimeError("fixture crash")))\nexcept RuntimeError:\n assert store.read()==GENESIS;print("unadvanced")\nelse:\n raise AssertionError()'
        self.assertEqual(self.run_as(AUTH,code+'exec('+repr(action)+')'),'unadvanced')
        self.assertEqual(self.run_as(AUTH,code+'print(j.advance(p,int(time.time()))==event_checkpoint(e));assert j.current_packet()[1]==p'),'True')
    def test_wrong_evidence_and_review_consumption(self):
        proof=self.evidence();base=self.review_prefix(proof)
        script='try:\n r.begin(GENESIS,e,dict(proof,signature="0"*128))\nexcept ValueError:\n assert r.pending is None\nelse:\n raise AssertionError()\nv=r.begin(GENESIS,e,proof)\ntry:\n r.confirm("wrong",password)\nexcept ValueError:\n assert r.pending is None;print("refused")\nelse:\n raise AssertionError()'
        self.assertEqual(self.run_as(AUTH,base+'exec('+repr(script)+')'),'refused')
        script='v=r.begin(GENESIS,e,proof);r.revalidate=lambda *a:False\ntry:\n r.confirm(v["challenge"],password)\nexcept ValueError:\n assert r.pending is None;print("refused")\nelse:\n raise AssertionError()'
        self.assertEqual(self.run_as(AUTH,base+'exec('+repr(script)+')'),'refused')
    def test_stale_wrong_scope_and_revocation_without_native_pin(self):
        proof=self.evidence();base=self.review_prefix(proof)
        script='now=proof["body"]["observed"]+31;r.clock=lambda:now\ntry:\n r.begin(GENESIS,e,proof)\nexcept ValueError:\n assert r.pending is None\nelse:\n raise AssertionError()\nr.clock=time.time;r.workspace="other"\ntry:\n r.begin(GENESIS,e,proof)\nexcept ValueError:\n print("refused")\nelse:\n raise AssertionError()'
        self.assertEqual(self.run_as(AUTH,base+'exec('+repr(script)+')'),'refused')
        code='ev=event("reserve_revoke");r=ExactReview("fixture",creds,**ids,permit_pin=pin,native_pin=root/"missing",revalidate=lambda prev,evv,pr:prev==GENESIS and evv==ev and pr is None);v=r.begin(GENESIS,ev,None);p=r.confirm(v["challenge"],password);assert p["event"]==ev;print("revoked permit only")'
        self.assertEqual(self.run_as(AUTH,code),'revoked permit only')
    @unittest.skipUnless(shutil.which('xvfb-run') and shutil.which('xauth'),'Xvfb required')
    def test_native_tk_exact_review(self):
        proof=self.evidence();code=self.review_prefix(proof)+'v=r.begin(GENESIS,e,proof);import tkinter as tk;ui=tk.Tk();out=[];win=ExactReviewWindow(ui,r,v,out.append);ui.update();win.challenge.set(v["challenge"]);win.password.set(password);win.confirm_button.invoke();ui.update();assert len(out)==1 and out[0]["event"]==e and not win.password.get();assert "not yet confirmed" in win.status.get();ui.destroy();print("reviewed")'
        result=subprocess.run(self.cmd(AUTH,code)[:4]+['xvfb-run','-a']+self.cmd(AUTH,code)[4:],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'reviewed')

if __name__=='__main__':unittest.main()
