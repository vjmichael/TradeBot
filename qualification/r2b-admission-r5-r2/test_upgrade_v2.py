"""R5-R2 pure offline cryptographic and transaction regression tests."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

import upgrade_v2 as upgrade
from r5_transaction import Transaction,Refused,IDENTITY,ORIGINAL

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        top=Path(self.temp.name)
        self.root=top/"root";self.etc=top/"etc";self.systemd=top/"systemd"
        for p in (self.root,self.etc,self.systemd):p.mkdir(mode=0o700)
        self.originals=[]
        for name in IDENTITY:
            path=self.systemd/name[9:] if name.startswith("@systemd/") else (
                self.etc/name[5:] if name.startswith("@etc/") else self.root/name)
            path.parent.mkdir(parents=True,exist_ok=True)
            data=b"def run(): return 'offline'\n" if name=="managed_rpc.py" else (name+"\n").encode()
            path.write_bytes(data)
            path.chmod(0o600 if "signing-key" in name else 0o644)
        self.tx=Transaction(self.root,self.etc,self.systemd)
        self.tx.prepare();self.tx.commit();self.tx.verify()
        self.original=self.tx.path("managed_rpc.py").read_bytes()
        self.ident=self.tx.identity.read_bytes()
        self.secret=Ed25519PrivateKey.generate()
        self.pub=self.secret.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        package=self.root/"r5-r2-package";package.mkdir()
        payload=self.original+upgrade.SUFFIX
        m={"format":"maj-vaj-r5-r2-ed25519-v1","from_version":1,
           "to_version":2,"file":"managed_rpc.py","before":upgrade.digest(self.original),
           "after":upgrade.digest(payload),"schema":"unchanged","scope":"offline-synthetic-only"}
        raw=upgrade.encoded(m)
        (package/"managed_rpc-v2.py").write_bytes(payload)
        (package/"manifest.json").write_bytes(raw)
        (package/"signature.ed25519").write_bytes(self.secret.sign(upgrade.DOMAIN+raw))
        (self.etc/"r5-r2-trust-root.pub").write_bytes(self.pub)
        (self.etc/"r5-r2-trust-root.sha256").write_bytes((upgrade.digest(self.pub)+"\n").encode())
        for p in (self.etc/"r5-r2-trust-root.pub",self.etc/"r5-r2-trust-root.sha256"):p.chmod(0o600)
        self.patchers=[
            patch.object(upgrade,"ROOT",self.root),patch.object(upgrade,"ETC",self.etc),
            patch.object(upgrade,"SYSTEMD",self.systemd),patch.object(upgrade,"PACKAGE",package),
            patch.object(upgrade,"PUB",self.etc/"r5-r2-trust-root.pub"),
            patch.object(upgrade,"COMMITMENT",self.etc/"r5-r2-trust-root.sha256"),
            patch.object(upgrade,"PENDING",self.etc/"r5-r2-upgrade-pending"),
            patch.object(upgrade,"BACKUP",self.etc/"r5-r2-upgrade-backup"),
            patch.object(upgrade,"RECORD",self.etc/"r5-r2-release-receipt"),
            patch.object(upgrade,"PAYLOAD",package/"managed_rpc-v2.py"),
            patch.object(upgrade,"MANIFEST",package/"manifest.json"),
            patch.object(upgrade,"SIGNATURE",package/"signature.ed25519"),
            patch.object(upgrade,"TARGET",self.root/"managed_rpc.py"),
            patch.object(upgrade,"stable_services",lambda:None),
            patch.object(upgrade,"inventory_ok",lambda:None)]
        for p in self.patchers:p.start()
    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        self.temp.cleanup()
    def test_signature_and_compatible_payload(self):
        m,data,md=upgrade.release()
        self.assertEqual(m["from_version"],1)
        self.assertEqual(data,self.original+upgrade.SUFFIX)
    def test_modified_manifest_is_refused_even_if_parseable(self):
        path=upgrade.MANIFEST
        o=json.loads(path.read_bytes())
        o["scope"]="offline-synthetic-only "
        path.write_bytes(upgrade.encoded(o))
        with self.assertRaises(Refused):upgrade.release()
    def test_foreign_trust_root_refuses_existing_signature(self):
        other=Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        upgrade.PUB.write_bytes(other)
        upgrade.COMMITMENT.write_bytes((upgrade.digest(other)+"\n").encode())
        with self.assertRaises(Refused):upgrade.release()
    def test_modified_payload_refused(self):
        upgrade.PAYLOAD.write_bytes(self.original+upgrade.SUFFIX+b"#not signed\n")
        with self.assertRaises(Refused):upgrade.release()
    def test_valid_upgrade_updates_code_and_identity(self):
        upgrade.apply()
        self.assertTrue(upgrade.verify())
        self.assertEqual(upgrade.TARGET.read_bytes(),self.original+upgrade.SUFFIX)
        self.assertNotEqual(self.tx.identity.read_bytes(),self.ident)
        self.assertFalse(upgrade.PENDING.exists())
        with self.assertRaises(Refused):upgrade.apply()
    def test_crash_after_source_copy_can_rollback(self):
        with patch.object(upgrade,"fixture_kill",side_effect=lambda x: (_ for _ in ()).throw(Refused("simulated-kill")) if x=="upgrade-after-code" else None):
            with self.assertRaises(Refused):upgrade.apply()
        self.assertTrue(upgrade.PENDING.exists())
        with self.assertRaises(Refused):upgrade.verify()
        upgrade.recover()
        self.assertEqual(upgrade.TARGET.read_bytes(),self.original)
        self.assertEqual(self.tx.identity.read_bytes(),self.ident)
        self.assertTrue(self.tx.verify())
        self.assertFalse(upgrade.PENDING.exists())
        upgrade.apply()
        self.assertTrue(upgrade.verify())
    def test_crash_after_identity_can_rollback(self):
        with patch.object(upgrade,"fixture_kill",side_effect=lambda x: (_ for _ in ()).throw(Refused("simulated-kill")) if x=="upgrade-after-identity" else None):
            with self.assertRaises(Refused):upgrade.apply()
        upgrade.recover()
        self.assertEqual(self.tx.identity.read_bytes(),self.ident)
        self.assertTrue(self.tx.verify())
    def test_partial_snapshot_preparing_abandons_without_mutation(self):
        with patch.object(upgrade,"fixture_kill",side_effect=lambda x: (_ for _ in ()).throw(Refused("simulated-kill")) if x=="prepare-first" else None):
            with self.assertRaises(Refused):upgrade.apply()
        upgrade.recover()
        self.assertEqual(upgrade.TARGET.read_bytes(),self.original)
        self.assertEqual(self.tx.identity.read_bytes(),self.ident)
        self.assertFalse(upgrade.PENDING.exists())
    def test_cleanup_resume_after_repeated_crash(self):
        with patch.object(upgrade,"fixture_kill",side_effect=lambda x: (_ for _ in ()).throw(Refused("simulated-kill")) if x=="upgrade-after-code" else None):
            with self.assertRaises(Refused):upgrade.apply()
        with patch.object(upgrade,"fixture_kill",side_effect=lambda x: (_ for _ in ()).throw(Refused("simulated-kill")) if x=="recover-cleanup-first" else None):
            with self.assertRaises(Refused):upgrade.recover()
        self.assertTrue(upgrade.PENDING.exists())
        upgrade.recover()
        self.assertEqual(upgrade.TARGET.read_bytes(),self.original)
        self.assertFalse(upgrade.BACKUP.exists())
        self.assertTrue(self.tx.verify())

if __name__=="__main__":unittest.main()
