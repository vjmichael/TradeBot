"""R5-R3B-R1 independent host-only durable staging/custody negative tests."""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_publisher_verifier as fixtures
import publisher_verifier as v
import protected_staging as s


class ProtectedStagingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublisherContract("test_accepts_provisioned_active_publisher_with_exact_inventory")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tmp = self.fixture.root
        self.store = self.tmp / "protected"
        self.store.mkdir(mode=0o700)
        self.policy_path = self.store / s.STORE_POLICY
        self.state_path = self.store / s.STORE_STATE
        self._write_protected(self.policy_path, v.canonical(self.fixture.policy))
        self._write_protected(self.state_path, v.canonical(self.fixture.state))
        self.owner = s.ProtectedStaging(self.store, expected_uid=os.geteuid())

    def _write_protected(self, path, raw):
        path.write_bytes(raw)
        path.chmod(0o600)
        with path.open('rb') as f:
            os.fsync(f.fileno())
        d = os.open(self.store, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(d)
        finally:
            os.close(d)

    def _refused(self, code, operation=None):
        with self.assertRaises(s.Refused) as e:
            (operation or (lambda: self.owner.stage(self.fixture.package)))()
        self.assertEqual(str(e.exception), code)

    def test_stage_and_revalidate_without_advancing_installed_state(self):
        prior = self.state_path.read_bytes()
        receipt = self.owner.stage(self.fixture.package)
        self.assertEqual(receipt['publisher'], 'maj-vaj')
        self.assertEqual(receipt['release_sequence'], 2)
        self.assertEqual(receipt, self.owner.verify_ready())
        self.assertEqual(self.state_path.read_bytes(), prior)
        self.assertFalse((self.store / s.HOLD).exists())
        self.assertTrue((self.store / s.READY / s.BUNDLE / 'payload' / 'managed_rpc.py').is_file())
        self.assertEqual((self.store / s.READY / s.BUNDLE / 'payload' / 'managed_rpc.py').read_bytes(), fixtures.CODE)

    def test_mode_0644_policy_refused(self):
        self.policy_path.chmod(0o644)
        self._refused('protected_file_custody')

    def test_mode_0644_state_refused(self):
        self.state_path.chmod(0o644)
        self._refused('protected_file_custody')

    def test_root_mode_insecure_refused(self):
        self.store.chmod(0o755)
        self._refused('protected_root_custody')

    def test_missing_trust_refused(self):
        self.policy_path.unlink()
        self._refused('protected_store_inventory')

    def test_trust_symlink_refused(self):
        old = self.policy_path.read_bytes()
        self.policy_path.unlink()
        outside = self.tmp / 'outside.json'
        outside.write_bytes(old)
        self.policy_path.symlink_to(outside)
        self._refused('protected_file_missing_or_unsafe')

    def test_trust_hardlink_refused(self):
        os.link(self.policy_path, self.tmp / 'other-trust.json')
        self._refused('protected_file_custody')

    def test_state_hardlink_refused(self):
        os.link(self.state_path, self.tmp / 'other-state.json')
        self._refused('protected_file_custody')

    def test_store_extra_object_refused(self):
        (self.store / 'publisher-package.pub').write_text('not allowed')
        self._refused('protected_store_inventory')

    def test_source_tamper_before_stage_refused(self):
        (self.fixture.package / 'payload' / 'managed_rpc.py').write_bytes(b'forged')
        self._refused('payload_digest_mismatch')
        self.assertFalse((self.store / s.HOLD).exists())

    def test_source_symlink_refused(self):
        package = self.fixture.package
        alias = self.tmp / 'alias'
        alias.symlink_to(package, target_is_directory=True)
        self._refused('unreadable_package_file', lambda: self.owner.stage(alias))

    def test_double_stage_refused(self):
        self.owner.stage(self.fixture.package)
        self._refused('unfinished_or_existing_stage')

    def test_recover_with_no_hold_no_mutation(self):
        self.assertEqual(self.owner.recover(), {'action': 'nothing_pending'})

    def test_recover_interrupted_mkdir_without_journal(self):
        (self.store / s.HOLD).mkdir(mode=0o700)
        self.assertEqual(self.owner.recover()['action'], 'abandoned_incomplete_stage')
        self.assertFalse((self.store / s.HOLD).exists())

    def test_recover_incomplete_private_bundle(self):
        pending = self.store / s.HOLD
        pending.mkdir(mode=0o700)
        unfinished = pending / s.BUNDLE
        unfinished.mkdir(mode=0o700)
        (unfinished / 'payload').mkdir(mode=0o700)
        raw = b'partial-copy'
        p = unfinished / 'payload' / 'managed_rpc.py'
        p.write_bytes(raw); p.chmod(0o600)
        # First durable phase journal uses the same validated admission metadata.
        admission = self.fixture.verify()
        journal = s._canonical_journal('PREPARING', admission,
                                       self.policy_path.read_bytes(), self.state_path.read_bytes())
        p = pending / s.JOURNAL
        p.write_bytes(journal); p.chmod(0o600)
        self.assertEqual(self.owner.recover()['action'], 'abandoned_incomplete_stage')
        self.assertFalse(pending.exists())
        self.assertEqual(self.state_path.read_bytes(), v.canonical(self.fixture.state))

    def test_recover_prepared_before_final_rename(self):
        ready = self.owner.stage(self.fixture.package)
        (self.store / s.READY).rename(self.store / s.HOLD)
        found = self.owner.recover()
        self.assertEqual(found['action'], 'published_verified_stage')
        self.assertEqual(found['receipt'], ready)
        self.assertEqual(self.owner.verify_ready(), ready)

    def test_injected_failure_before_copy_leaves_recoverable_hold(self):
        with patch.object(self.owner, "_copy_bundle", side_effect=OSError("fixture interruption")):
            with self.assertRaises(OSError):
                self.owner.stage(self.fixture.package)
        self.assertTrue((self.store / s.HOLD / s.JOURNAL).is_file())
        self._refused('unfinished_or_existing_stage')
        self.assertEqual(self.owner.recover()['action'], 'abandoned_incomplete_stage')
        self.assertFalse((self.store / s.HOLD).exists())
        self.assertEqual(self.owner.stage(self.fixture.package)['release_sequence'], 2)

    def test_injected_interruption_before_publish_is_recoverable(self):
        with patch.object(s.os, "rename", side_effect=OSError("fixture publish interruption")):
            self._refused('staging_publish_failed')
        self.assertTrue((self.store / s.HOLD / s.JOURNAL).is_file())
        self.assertFalse((self.store / s.READY).exists())
        evidence = self.owner.recover()
        self.assertEqual(evidence['action'], 'published_verified_stage')
        self.assertEqual(evidence['receipt']['release_sequence'], 2)

    def test_incomplete_journal_is_not_promoted_to_ready(self):
        (self.store / s.HOLD).mkdir(mode=0o700)
        journal = self.store / s.HOLD / s.JOURNAL
        journal.write_bytes(b'{"phase":"PREPARED"}')
        journal.chmod(0o600)
        self._refused('staging_journal_fields', self.owner.recover)
        self.assertFalse((self.store / s.READY).exists())

    def test_staged_manifest_tamper_and_recovery_refused(self):
        self.owner.stage(self.fixture.package)
        (self.store / s.READY).rename(self.store / s.HOLD)
        p = self.store / s.HOLD / s.BUNDLE / "manifest.json"
        p.write_bytes(b'{}')
        p.chmod(0o600)
        self._refused('manifest_fields', self.owner.recover)
        self.assertFalse((self.store / s.READY).exists())

    def test_recover_ready_is_idempotent(self):
        receipt = self.owner.stage(self.fixture.package)
        self.assertEqual(self.owner.recover(), {'action': 'already_ready', 'receipt': receipt})

    def test_recover_conflicting_hold_ready_refused(self):
        self.owner.stage(self.fixture.package)
        (self.store / s.HOLD).mkdir(mode=0o700)
        self._refused('conflicting_staging_state', self.owner.recover)

    def test_ready_payload_tamper_refused(self):
        self.owner.stage(self.fixture.package)
        p = self.store / s.READY / s.BUNDLE / 'payload' / 'managed_rpc.py'
        p.write_bytes(b'tampered'); p.chmod(0o600)
        self._refused('payload_digest_mismatch', self.owner.verify_ready)

    def test_ready_receipt_tamper_refused(self):
        self.owner.stage(self.fixture.package)
        journal = self.store / s.READY / s.JOURNAL
        record = v._parse(journal.read_bytes(), kind='j')
        record['release_sequence'] = 88
        self._write_protected(journal, v.canonical(record))
        self._refused('staged_receipt_mismatch', self.owner.verify_ready)

    def test_ready_journal_file_mode_refused(self):
        self.owner.stage(self.fixture.package)
        (self.store / s.READY / s.JOURNAL).chmod(0o644)
        self._refused('protected_file_custody', self.owner.verify_ready)

    def test_stale_trust_after_staging_refused(self):
        self.owner.stage(self.fixture.package)
        self.fixture.policy['keys'][0]['status'] = 'revoked'
        self._write_protected(self.policy_path, v.canonical(self.fixture.policy))
        self._refused('staged_authority_changed', self.owner.verify_ready)

    def test_state_advanced_after_staging_refused(self):
        self.owner.stage(self.fixture.package)
        self.fixture.state['release_sequence'] = 2
        self._write_protected(self.state_path, v.canonical(self.fixture.state))
        self._refused('staged_authority_changed', self.owner.verify_ready)

    def test_stage_is_not_an_authorized_install(self):
        prior_state = self.state_path.read_bytes()
        self.owner.stage(self.fixture.package)
        self.assertEqual(prior_state, self.state_path.read_bytes())
        self.assertFalse((self.store / 'installed-approval.json').exists())

    def test_discard_ready_and_restage(self):
        self.owner.stage(self.fixture.package)
        self.assertTrue(self.owner.discard_ready())
        self.assertFalse((self.store / s.READY).exists())
        self.assertEqual(self.owner.stage(self.fixture.package)['release_sequence'], 2)

    def test_discard_ready_with_symlink_refuses(self):
        self.owner.stage(self.fixture.package)
        (self.store / s.READY / s.BUNDLE / 'payload' / 'link.py').symlink_to(self.policy_path)
        self._refused('staging_cleanup_custody', self.owner.discard_ready)

    def test_lock_contention_refused(self):
        fd = os.open(self.store, os.O_DIRECTORY | os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._refused('staging_busy')
        finally:
            os.close(fd)

    def test_read_only_old_vault_data_unaffected(self):
        # Deliberately outside staging: never inspect or mutate a vault.
        vault = self.tmp / 'vault.sqlite3'
        payload = b'encrypted-ciphertext-fixture-not-provider-credential'
        vault.write_bytes(payload)
        old_digest = hashlib.sha256(vault.read_bytes()).hexdigest()
        self.owner.stage(self.fixture.package)
        self.assertEqual(hashlib.sha256(vault.read_bytes()).hexdigest(), old_digest)

    def test_ready_stage_missing_file_refused(self):
        self.owner.stage(self.fixture.package)
        (self.store / s.READY / s.BUNDLE / 'payload' / 'managed_rpc.py').unlink()
        self._refused('unexpected_package_inventory', self.owner.verify_ready)

    def test_false_public_authority_policy_not_accepted(self):
        self.fixture.policy['publisher'] = 'other-publisher'
        self._write_protected(self.policy_path, v.canonical(self.fixture.policy))
        self._refused('unauthorized_publisher')


if __name__ == '__main__':
    unittest.main()
