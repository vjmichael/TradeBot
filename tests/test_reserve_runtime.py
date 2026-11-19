"""Real SQLite plus encrypted local credential store. No venue or installed service."""
import fcntl
import json
import subprocess
import sys
import os
from pathlib import Path
import tempfile
import unittest

from financial_ledger import AccountingError, SCALE, start
from reserve_policy import DAY, ReserveManager
from reserve_runtime import ApprovalBroker, desk_transaction, provision, status, tick_all, unlock
import test_reserve_policy as fixture

PASSWORD='a-test-passphrase-only-2026'


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.ReserveTests('test_default_saved_inactive_and_nonmutating_preview')
        self.h.setUp(); self.addCleanup(self.h.doCleanups)
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.creds=Path(tmp.name); provision(self.creds,PASSWORD)
        self.tx=self.h.h.tx; self.clock=lambda:self.h.h.now
        self.b=ApprovalBroker(self.tx,self.creds,clock=self.clock)
        self.p={'version':'v1','policy':self.h.p}

    def confirm(self,action,payload):
        r=self.b.review('a',action,payload)
        return self.b.confirm(r['receipt'],r['confirmation'],PASSWORD)

    def activate(self):
        self.confirm('save',self.p); self.h.observe('ready'); self.confirm('activate',self.p)

    def test_credentials_encrypted_exclusive_and_private(self):
        data=json.loads((self.creds/'reserve-approval.json').read_text())
        self.assertNotIn('key',data); self.assertEqual(len(unlock(self.creds,PASSWORD)),32)
        with self.assertRaisesRegex(AccountingError,'authentication_failed'):
            unlock(self.creds,'incorrect-password-000')
        with self.assertRaises(FileExistsError):provision(self.creds,PASSWORD)
        os.chmod(self.creds/'reserve-approval.json',0o644)
        with self.assertRaisesRegex(AccountingError,'private_file_required'):unlock(self.creds,PASSWORD)

    def test_explicit_review_and_consumed_challenge(self):
        r=self.b.review('a','save',self.p)
        with self.assertRaisesRegex(AccountingError,'confirmation_mismatch'):
            self.b.confirm(r['receipt'],'YES',PASSWORD)
        with self.assertRaisesRegex(AccountingError,'review_expired_or_consumed'):
            self.b.confirm(r['receipt'],r['confirmation'],PASSWORD)
        self.assertIsNone(self.h.h.snap()['reserve_policy']['saved'])
        self.confirm('save',self.p); self.h.observe('fresh')
        r=self.b.review('a','activate',self.p); self.h.observe('changed')
        with self.assertRaisesRegex(AccountingError,'review_changed'):
            self.b.confirm(r['receipt'],r['confirmation'],PASSWORD)
        self.assertIsNone(self.h.h.snap()['reserve_policy']['active'])

    def test_approval_expiry_and_restart(self):
        r=self.b.review('a','save',self.p); self.h.h.now+=301
        with self.assertRaisesRegex(AccountingError,'review_expired_or_consumed'):
            self.b.confirm(r['receipt'],r['confirmation'],PASSWORD)
        r=self.b.review('a','save',self.p)
        with self.tx() as db:start(db)
        with self.assertRaisesRegex(AccountingError,'review_changed'):
            self.b.confirm(r['receipt'],r['confirmation'],PASSWORD)

    def test_keyless_worker_only_and_durable_cycle(self):
        self.activate(); self.h.profitable('gain')
        self.h.h.now+=DAY; self.h.observe('fresh')
        self.assertEqual(tick_all(self.tx,clock=self.clock)['a']['release'],5*SCALE)
        self.assertEqual(tick_all(self.tx,clock=self.clock),{})
        with self.tx() as db:start(db)
        self.assertEqual(tick_all(self.tx,clock=self.clock),{})
        with self.assertRaisesRegex(AccountingError,'approval_broker_required'):
            ReserveManager(self.tx,clock=self.clock).submit('keyless','a','save',self.p)
        self.assertEqual(self.h.h.snap()['protected'],95*SCALE)

    def test_no_budget_reset_same_version_and_revocation(self):
        self.activate(); self.h.profitable('gain'); self.h.h.now+=DAY; self.h.observe('fresh')
        tick_all(self.tx,clock=self.clock)
        self.h.observe('fresh2')
        with self.assertRaisesRegex(AccountingError,'policy_already_activated'):
            self.confirm('activate',self.p)
        r=self.b.review('a','revoke',{'version':'v1'}); self.h.observe('fresh3')
        self.b.confirm(r['receipt'],r['confirmation'],PASSWORD)
        self.assertFalse(status(self.tx,clock=self.clock)['instances']['a']['authorized'])
        self.assertEqual(self.h.h.snap()['reserve_policy']['released'],5*SCALE)
        self.h.observe('fresh4')
        with self.assertRaisesRegex(AccountingError,'policy_already_activated'):
            self.confirm('activate',self.p)

    def test_effective_off_after_expiry(self):
        self.p['policy']=self.h.p|{'end':self.clock()+DAY,'expiry':self.clock()+DAY}
        self.activate(); self.h.h.now+=DAY
        s=status(self.tx,clock=self.clock)['instances']['a']
        self.assertFalse(s['authorized']); self.assertEqual(s['effective_rate_bps'],0)
        self.assertEqual(tick_all(self.tx,clock=self.clock)['a']['release'],0)

    def test_worker_no_database_creation_or_boot(self):
        directory=self.h.h.path.parent; os.chmod(self.h.h.path,0o600)
        with self.tx() as db:db.execute('PRAGMA user_version=4')
        tx=desk_transaction(directory)
        before=status(tx,clock=self.clock)
        self.assertEqual(tick_all(tx,clock=self.clock),{})
        self.assertEqual(before,status(tx,clock=self.clock))
        with tempfile.TemporaryDirectory() as d:
            missing=desk_transaction(Path(d))
            with self.assertRaises(FileNotFoundError):status(missing,clock=self.clock)
            self.assertFalse((Path(d)/'desk.sqlite3').exists())


    def test_actual_worker_process_and_exclusive_lock(self):
        directory=self.h.h.path.parent; os.chmod(self.h.h.path,0o600)
        with self.tx() as db:db.execute('PRAGMA user_version=4')
        command=[sys.executable,'-m','reserve_runtime',str(directory),'tick']
        lock=directory/'reserve-scheduler.lock'
        with lock.open('w') as handle:
            os.chmod(lock,0o600); fcntl.flock(handle,fcntl.LOCK_EX)
            r=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertNotEqual(r.returncode,0)
        before=status(desk_transaction(directory),clock=self.clock)
        r=subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout),{})
        self.assertEqual(status(desk_transaction(directory),clock=self.clock),before)


if __name__=='__main__':unittest.main()
