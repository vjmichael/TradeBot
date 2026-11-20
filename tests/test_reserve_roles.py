"""Genuine UID, key separation and recoverable service fixture checks."""
import os
import shutil
import unittest
import test_reserve_recovery as fixture
AUTH,DESK,NATIVE=fixture.AUTH,fixture.DESK,fixture.NATIVE

@unittest.skipUnless(os.geteuid()==0 and shutil.which('setpriv'),'root/setpriv required')
class RolesTests(unittest.TestCase):
    def setUp(self):
        h=fixture.RecoveryTests('test_exact_recovery_after_real_service_restart');h.setUp();self.addCleanup(h.doCleanups);self.h=h
        for name in ('reserve_native_evidence.py','reserve_protected.py','reserve_delivery.py'):
            shutil.copyfile(fixture.ROOT/name,h.root/name)
        for name,uid in [('human-out',AUTH),('native-out',NATIVE)]:
            p=h.root/name;p.mkdir();p.chmod(0o755);os.chown(p,uid,uid)
        # Copy fixture seed as a distinct independently owned test credential.
        shutil.copyfile(h.root/'native'/'key',h.root/'native'/'native-ed25519.key')
        os.chown(h.root/'native'/'native-ed25519.key',NATIVE,NATIVE);(h.root/'native'/'native-ed25519.key').chmod(0o600)
        h.prefix+='from reserve_native_evidence import NativeProducer;from reserve_delivery import Mailbox;'
    def refuse(self,uid,code):
        script='try:\n '+code+'\nexcept (OSError,ValueError):\n print("refused")\nelse:\n raise AssertionError("unexpected success")'
        self.assertEqual(self.h.run_as(uid,'exec('+repr(script)+')'),'refused')
    def start(self):
        import subprocess,time,socket
        h=self.h;h.process=subprocess.Popen(h.cmd(AUTH,'RecoverableAuthorityService(store,sock,**ids,workspace="fixture",pin=pin,native_pin=native).serve()'),stderr=subprocess.PIPE)
        for _ in range(100):
            if h.process.poll() is not None:self.fail(h.process.stderr.read().decode())
            try:
                with socket.socket(socket.AF_UNIX) as conn:conn.connect(str(h.root/'socket'/'authority.sock'))
                return
            except OSError:time.sleep(.05)
        self.fail('service unavailable')
    def test_native_only_tick_and_human_only_adopt(self):
        h=self.h;self.start()
        native_tick=h.run_as(NATIVE,'ev=event("reserve_tick");seed=(root/"native"/"key").read_bytes();print(canonical({"event":ev,"permit":sign_permit(seed,"fixture",GENESIS,event_checkpoint(ev),int(time.time())+120)}).decode())')
        human_tick=h.run_as(AUTH,'ev=event("reserve_tick");seed=en.unlock(creds,password,**ids);print(canonical({"event":ev,"permit":sign_permit(seed,"fixture",GENESIS,event_checkpoint(ev),int(time.time())+120)}).decode())')
        native_adopt=h.run_as(NATIVE,'seed=(root/"native"/"key").read_bytes();print(canonical({"event":e,"permit":sign_permit(seed,"fixture",GENESIS,event_checkpoint(e),int(time.time())+120)}).decode())')
        for packet in [human_tick,native_adopt]:
            self.refuse(DESK,f'RecoveryClient(sock,**ids,workspace="fixture").request("advance_recoverable",json.loads({packet!r}))')
        self.assertEqual(h.client(f'p=json.loads({native_tick!r});insert(c,p["event"]);print(a.advance_event(c,GENESIS,p,pub,replay,native_public=pinned_key(native))==event_checkpoint(p["event"]));c.rollback()'),'True')
        h.stop();self.start()
        self.assertEqual(h.client('print(a.recover_event(c,pub,replay,lambda db,p: True,native_public=pinned_key(native)))'),'recovered')
    def test_native_key_custody_and_legacy_data_refusal(self):
        make=f'P=NativeProducer("fixture",root/"native",root/"native",native_uid={NATIVE},authority_uid={AUTH},desk_uid={DESK},native_pin=native,human_pin=pin);'
        self.assertEqual(self.h.run_as(NATIVE,make+'print(len(P._key()))'),'32')
        self.refuse(DESK,make+'P._key()');self.refuse(AUTH,make+'P._key()')
        self.refuse(NATIVE,make+'P.data=root/"desk";P.propose(c,GENESIS,"fixture-a","adopt","receipt")')
        self.refuse(NATIVE,make+'P.human_pin=native;P._key()')
    def test_mailbox_owner_and_no_overwrite(self):
        h=self.h;packet=h.reviewed(); code=f'p=json.loads({packet!r});m=Mailbox(root/"human-out",{AUTH},"human");'
        h.run_as(AUTH,code+'m.publish(p)');self.assertEqual(h.run_as(DESK,code+'print(m.read()==p)'),'True')
        self.refuse(DESK,code+'m.publish(p)');self.refuse(AUTH,code+'m.publish(p)')
        self.refuse(NATIVE,f'p=json.loads({packet!r});Mailbox(root/"native-out",{NATIVE},"native").publish(p)')
        self.refuse(DESK,'os.unlink(root/"human-out"/"pending.json")')

if __name__=='__main__':unittest.main()
