"""Deterministic R5-R3A source-only refusal and legitimate key-rotation tests."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import publisher_verifier as v

CODE_PATH = "/opt/maj-vaj/managed_rpc.py"
UNIT = "maj-vaj-r2b-approval.service"
UNIT_PATH = "/etc/systemd/system/" + UNIT
CODE = b"# offline synthetic PAPER code only\nR5R3_VERSION = 3\n"
SERVICE = ("[Unit]\nDescription=Maj Vaj test approval\n"
           "[Service]\nType=simple\nUser=maj-vaj-approval\n"
           "Group=maj-vaj-approval\nPrivateNetwork=yes\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pub_bytes(key):
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)


def add_key(key, *, status="active", first=1, through=None):
    pub = pub_bytes(key)
    return {"key_id": digest(pub), "public_key": base64.b64encode(pub).decode("ascii"),
            "status": status, "from_epoch": first, "through_epoch": through}


class PublisherContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / "candidate"
        self.package.mkdir()
        (self.package / "payload").mkdir()
        self.key = Ed25519PrivateKey.generate()  # ephemeral in-memory, NEVER exported
        self.other = Ed25519PrivateKey.generate()
        self.policy = {
            "format": v.TRUST_FORMAT, "publisher": "maj-vaj", "generation": 2,
            "minimum_epoch": 1, "keys": [add_key(self.key)],
            "targets": [{"install": CODE_PATH, "mode": "0644"},
                        {"install": UNIT_PATH, "mode": "0644"}],
            "services": [{"unit": UNIT, "user": "maj-vaj-approval", "group": "maj-vaj-approval"}]
        }
        self.state = {"format": v.STATE_FORMAT, "publisher": "maj-vaj",
                      "release_sequence": 1, "security_epoch": 1, "trust_generation": 1,
                      "schema_version": 1, "manifest_sha256": "1" * 64}
        self.manifest = {
            "format": v.FORMAT, "publisher": "maj-vaj", "signing_key_id": add_key(self.key)["key_id"],
            "release_sequence": 2, "security_epoch": 2,
            "platform": {"os": "linux", "arch": "x86_64"},
            "scope": "paper-staging-only",
            "schema": {"from": 1, "to": 1, "migration": "none"},
            "files": [
                {"source": "payload/managed_rpc.py", "install": CODE_PATH,
                 "sha256": digest(CODE), "size": len(CODE), "mode": "0644"},
                {"source": "payload/" + UNIT, "install": UNIT_PATH,
                 "sha256": digest(SERVICE), "size": len(SERVICE), "mode": "0644"}],
            "services": [{"unit": UNIT, "user": "maj-vaj-approval", "group": "maj-vaj-approval"}]
        }
        (self.package / "payload" / "managed_rpc.py").write_bytes(CODE)
        (self.package / "payload" / UNIT).write_bytes(SERVICE)
        self.sign()

    def sign(self, key=None):
        raw = v.canonical(self.manifest)
        (self.package / "manifest.json").write_bytes(raw)
        (self.package / "signature.ed25519").write_bytes((key or self.key).sign(v.DOMAIN + raw))

    def verify(self):
        return v.verify_release(self.package, trust_policy=v.canonical(self.policy),
                                installed_state=v.canonical(self.state))

    def refused(self, expected):
        with self.assertRaises(v.Refused) as caught:
            self.verify()
        self.assertEqual(str(caught.exception), expected)

    def test_accepts_provisioned_active_publisher_with_exact_inventory(self):
        accepted = self.verify()
        self.assertEqual(accepted["release_sequence"], 2)
        self.assertEqual(accepted["files"], 2)
        self.assertEqual(accepted["scope"], "paper-staging-only")
        self.assertEqual(accepted["manifest_sha256"], digest((self.package / "manifest.json").read_bytes()))

    def test_tampered_signature_refused(self):
        p = self.package / "signature.ed25519"
        b = bytearray(p.read_bytes()); b[0] ^= 1; p.write_bytes(b)
        self.refused("invalid_package_signature")

    def test_signature_wrong_private_key_refused(self):
        self.sign(self.other)
        self.refused("invalid_package_signature")

    def test_tampered_manifest_refused(self):
        self.manifest["release_sequence"] = 3
        (self.package / "manifest.json").write_bytes(v.canonical(self.manifest))
        self.refused("invalid_package_signature")

    def test_manifest_noncanonical_refused(self):
        (self.package / "manifest.json").write_bytes(json.dumps(self.manifest).encode())
        self.refused("manifest_noncanonical")

    def test_manifest_duplicate_json_key_refused(self):
        p = self.package / "manifest.json"
        p.write_bytes(p.read_bytes().replace(b'"scope":', b'"scope":"paper-staging-only","scope":', 1))
        self.refused("duplicate_json_field")

    def test_tampered_payload_refused(self):
        (self.package / "payload" / "managed_rpc.py").write_bytes(CODE + b"# changed\n")
        self.refused("payload_digest_mismatch")

    def test_modified_service_unit_refused(self):
        (self.package / "payload" / UNIT).write_bytes(SERVICE + b"ExecStart=/bin/sh\n")
        self.refused("payload_digest_mismatch")

    def test_signed_unit_identity_swap_refused_by_policy(self):
        self.manifest["services"][0]["user"] = "maj-vaj-r2b-worker"
        self.sign()
        self.refused("service_identity_mismatch")

    def test_signed_unit_user_bytes_mismatch_refused(self):
        changed = SERVICE.replace(b"User=maj-vaj-approval", b"User=maj-vaj-r2b-worker")
        p = self.package / "payload" / UNIT
        p.write_bytes(changed)
        self.manifest["files"][1].update(sha256=digest(changed), size=len(changed))
        self.sign()
        self.refused("unit_identity_mismatch")

    def test_unauthorized_publisher_refused_even_if_self_signed(self):
        self.manifest["publisher"] = "unauthorized-issuer"
        self.sign()
        self.refused("unauthorized_publisher")

    def test_unauthorized_signing_key_refused(self):
        self.manifest["signing_key_id"] = add_key(self.other)["key_id"]
        self.sign(self.other)
        self.refused("unauthorized_signing_key")

    def test_revoked_key_refused(self):
        self.policy["keys"][0]["status"] = "revoked"
        self.refused("revoked_or_inactive_signing_key")

    def test_expired_key_refused(self):
        self.policy["keys"][0]["through_epoch"] = 1
        self.refused("revoked_or_inactive_signing_key")

    def test_future_key_refused(self):
        self.policy["keys"][0]["from_epoch"] = 3
        self.refused("revoked_or_inactive_signing_key")

    def test_provisioned_key_rotation_admits_new_key(self):
        self.policy["generation"] = 3
        self.policy["keys"][0]["status"] = "revoked"
        self.policy["keys"].append(add_key(self.other, first=2))
        self.manifest["signing_key_id"] = add_key(self.other)["key_id"]
        self.sign(self.other)
        self.assertEqual(self.verify()["trust_generation"], 3)
        self.manifest["signing_key_id"] = add_key(self.key)["key_id"]
        self.sign(self.key)
        self.refused("revoked_or_inactive_signing_key")

    def test_replay_same_release_refused(self):
        self.state["release_sequence"] = 2
        self.refused("release_downgrade_or_replay")

    def test_version_downgrade_refused(self):
        self.state["release_sequence"] = 3
        self.refused("release_downgrade_or_replay")

    def test_security_epoch_rollback_refused(self):
        self.state["security_epoch"] = 3
        self.refused("release_epoch_rollback")

    def test_policy_epoch_floor_refused(self):
        self.policy["minimum_epoch"] = 3
        self.refused("release_epoch_rollback")

    def test_stale_trust_policy_refused(self):
        self.state["trust_generation"] = 3
        self.refused("trust_policy_rollback")

    def test_unauthorized_install_destination_refused(self):
        self.manifest["files"][0]["install"] = "/opt/maj-vaj/unknown.py"
        self.sign()
        self.refused("unauthorized_install_inventory")

    def test_install_path_traversal_refused(self):
        self.manifest["files"][0]["install"] = "/opt/maj-vaj/../etc/shadow"
        self.sign()
        self.refused("unsafe_install_path")

    def test_payload_path_traversal_refused(self):
        self.manifest["files"][0]["source"] = "payload/../signature.ed25519"
        self.sign()
        self.refused("unsafe_payload_path")

    def test_extra_unlisted_file_refused(self):
        (self.package / "payload" / "r5-r3-trust-root.json").write_text("{}")
        self.refused("unexpected_package_inventory")

    def test_payload_symlink_refused(self):
        p = self.package / "payload" / "managed_rpc.py"
        p.unlink()
        p.symlink_to(self.package / "manifest.json")
        self.refused("package_symlink_refused")

    def test_package_root_symlink_refused(self):
        alias = self.root / "alias"
        alias.symlink_to(self.package, target_is_directory=True)
        self.package = alias
        self.refused("unreadable_package_file")

    def test_payload_hardlink_refused(self):
        os.link(self.package / "payload" / "managed_rpc.py", self.package / "payload" / "linked.py")
        self.refused("package_hardlink_refused")

    def test_wrong_install_mode_refused(self):
        self.manifest["files"][0]["mode"] = "0755"
        self.sign()
        self.refused("install_mode_mismatch")

    def test_schema_migration_refused(self):
        self.manifest["schema"]["to"] = 2
        self.sign()
        self.refused("schema_migration_not_authorized")

    def test_schema_state_mismatch_refused(self):
        self.state["schema_version"] = 2
        self.refused("schema_state_mismatch")

    def test_live_scope_refused(self):
        self.manifest["scope"] = "live"
        self.sign()
        self.refused("release_scope_refused")

    def test_non_linux_platform_refused(self):
        self.manifest["platform"]["os"] = "windows"
        self.sign()
        self.refused("unsupported_platform")

    def test_untrusted_policy_key_identity_refused(self):
        self.policy["keys"][0]["key_id"] = "1" * 64
        self.refused("trust_key_identity")

    def test_duplicate_provisioned_key_refused(self):
        self.policy["keys"].append(copy.deepcopy(self.policy["keys"][0]))
        self.refused("trust_duplicate_key")

    def test_trust_target_unsafe_path_refused(self):
        self.policy["targets"][0]["install"] = "/etc/maj-vaj/root.pub"
        self.refused("unsafe_install_path")

    def test_bool_version_refused(self):
        self.manifest["release_sequence"] = True
        self.sign()
        self.refused("manifest_release_sequence")

    def test_signature_missing_refused(self):
        (self.package / "signature.ed25519").unlink()
        self.refused("unreadable_package_file")

    def test_non_json_policy_refused(self):
        with self.assertRaises(v.Refused):
            v.verify_release(self.package, trust_policy=b"not json",
                             installed_state=v.canonical(self.state))

    def test_non_json_state_refused(self):
        with self.assertRaises(v.Refused):
            v.verify_release(self.package, trust_policy=v.canonical(self.policy),
                             installed_state=b"not json")

    def test_verified_manifest_does_not_modify_candidate(self):
        before = sorted((p.relative_to(self.package).as_posix(), digest(p.read_bytes()))
                        for p in self.package.rglob("*") if p.is_file())
        self.verify()
        after = sorted((p.relative_to(self.package).as_posix(), digest(p.read_bytes()))
                       for p in self.package.rglob("*") if p.is_file())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
