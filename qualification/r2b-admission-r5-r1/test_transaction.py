"""R5-R1 source-only transaction tests (not native service acceptance).
All temporary files have disposable no-secret contents. Native QEMU handles
actual systemd/PAM, power interruption and encrypted credential assertions.
"""
import os
from pathlib import Path
import tempfile
import unittest

from r5_transaction import Transaction,Refused,ORIGINAL,IDENTITY

class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        base=Path(self.tmp.name)
        self.root,self.etc,self.systemd=(base/"root",base/"etc",base/"systemd")
        for x in (self.root,self.etc,self.systemd):
            x.mkdir()
        self.tx=Transaction(self.root,self.etc,self.systemd)
        for entry in IDENTITY:
            p=self.tx.path(entry)
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(("original:"+entry).encode())
            os.chmod(p,0o600 if "signing-key" in entry else 0o644)
        self.baseline={entry:self.tx.path(entry).read_bytes() for entry in ORIGINAL}
    def tearDown(self):
        self.tmp.cleanup()
    def mutate_installed(self):
        for entry in ORIGINAL[:3]:
            self.tx.path(entry).write_bytes(("patched:"+entry).encode())
    def test_prepared_transaction_is_fail_closed_until_restoration(self):
        self.tx.prepare()
        self.mutate_installed()
        with self.assertRaises(Refused):
            self.tx.verify()
        self.tx.restore()
        self.tx.cleanup()
        for entry,value in self.baseline.items():
            self.assertEqual(self.tx.path(entry).read_bytes(),value)
        self.assertFalse(self.tx.pending.exists())
        self.assertFalse(self.tx.backup.exists())
    def test_committed_identity_accepts_only_exact_installed_content(self):
        self.tx.prepare()
        self.mutate_installed()
        self.tx.commit()
        self.assertTrue(self.tx.verify())
        self.assertTrue(self.tx.identity.exists())
        self.assertFalse(self.tx.pending.exists())
    def test_worker_key_same_length_rotation_refused(self):
        self.tx.prepare()
        self.tx.commit()
        p=self.tx.path("@etc/credential-keys/r2b-worker-signing-key")
        p.write_bytes(b"R"*32)
        with self.assertRaises(Refused):self.tx.verify()
    def test_source_and_installed_unit_mutation_refused(self):
        for name in ("managed_rpc.py","@systemd/maj-vaj-r2b-approval.service"):
            with self.subTest(name=name):
                t=Transaction(self.root,self.etc,self.systemd)
                if not t.backup.exists():
                    t.prepare()
                    t.commit()
                p=t.path(name)
                baseline=p.read_bytes()
                p.write_bytes(baseline+b"\n#modified\n")
                with self.assertRaises(Refused):t.verify()
                p.write_bytes(baseline)
                self.assertTrue(t.verify())
    def test_backup_byte_tamper_fails_closed_before_restore(self):
        self.tx.prepare()
        backup=self.tx.backup/self.tx._backup_name(ORIGINAL[0])
        backup.write_bytes(b"untrusted")
        with self.assertRaises(Refused):self.tx.restore()
    def test_symlink_to_external_source_refused(self):
        target=self.tx.path("managed_rpc.py")
        target.unlink()
        target.symlink_to(self.root/"credential_graphical.py")
        with self.assertRaises(Refused):self.tx.prepare()
    def test_world_writable_source_refused(self):
        p=self.tx.path("managed_rpc.py")
        p.chmod(0o666)
        with self.assertRaises(Refused):self.tx.prepare()
    def test_duplicate_prepared_install_refused(self):
        self.tx.prepare()
        with self.assertRaises(Refused):self.tx.prepare()
    def test_backup_bad_manifest_refused(self):
        self.tx.prepare()
        (self.tx.backup/"manifest.json").write_bytes(b"{}")
        with self.assertRaises(Refused):self.tx.restore()
    def test_restored_baseline_cannot_be_cleaned_up_if_modified(self):
        self.tx.prepare()
        self.tx.restore()
        self.tx.path(ORIGINAL[0]).write_bytes(b"not-restored")
        with self.assertRaises(Refused):self.tx.cleanup()

if __name__=="__main__":
    unittest.main()
