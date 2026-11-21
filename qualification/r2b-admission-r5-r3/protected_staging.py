"""R5-R3B-R1: host-side protected custody + durable *staging*, not installation.

This component is an in-process API for a trusted host administrator. The
protected root/trust/state files MUST be independently provisioned by the OS
owner (root in production); package input cannot provide either. No CLI,
network, provider, key migration, service activation or installation mutation.

A staged package is NOT an installed release: installed-state.json is read-only
in this task. Its authoritative advancement needs an authenticated, installed
receipt in the separate R5-R3B-R2 integration gate.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

import publisher_verifier as verify

STORE_POLICY = "trust-policy.json"
STORE_STATE = "installed-state.json"
HOLD = ".pending"
READY = "ready"
JOURNAL = "journal.json"
BUNDLE = "bundle"
JOURNAL_FORMAT = "maj-vaj-r5-r3b-staging-journal-v1"
MAX_OBJECTS = verify.MAX_FILES + 16


Refused = verify.Refused  # One canonical refusal type from the R5-R3A verifier.


def _refuse(reason: str):
    raise Refused(reason)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _root_dir(path: Path, owner_uid: int):
    if not path.is_absolute() or path == Path("/"):
        _refuse("protected_root_not_absolute")
    # O_NOFOLLOW prevents a final-component redirect; preceding ancestors
    # are the OS/administrator's separately trusted filesystem boundary.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        st = os.fstat(fd)
        if st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != 0o700:
            os.close(fd)
            _refuse("protected_root_custody")
        return fd
    except OSError:
        _refuse("protected_root_unavailable")


def _directory(fd: int, name: str, owner_uid: int, *, mode=0o700):
    try:
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                        os.O_CLOEXEC, dir_fd=fd)
        st = os.fstat(child)
        if st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != mode:
            os.close(child)
            _refuse("staging_directory_custody")
        return child
    except OSError:
        _refuse("staging_directory_refused")


def _read(fd: int, name: str, owner_uid: int, *, max_size=verify.MAX_JSON):
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                         os.O_CLOEXEC, dir_fd=fd)
    except OSError:
        _refuse("protected_file_missing_or_unsafe")
    try:
        before = os.fstat(handle)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != owner_uid or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                not 0 < before.st_size <= max_size):
            _refuse("protected_file_custody")
        chunks = []
        length = 0
        while True:
            b = os.read(handle, min(131072, max_size + 1 - length))
            if not b:
                break
            length += len(b)
            if length > max_size:
                _refuse("protected_file_oversize")
            chunks.append(b)
        after = os.fstat(handle)
        if (length != before.st_size or
                (before.st_dev, before.st_ino, before.st_ctime_ns, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_ctime_ns, after.st_mtime_ns)):
            _refuse("protected_file_changed")
        return b"".join(chunks)
    except OSError:
        _refuse("protected_file_read_failed")
    finally:
        os.close(handle)


def _atomic(fd: int, name: str, content: bytes, owner_uid: int):
    """Crash-safe private file creation/replacement in an already guarded dir."""
    if not name or "/" in name or name in (".", "..") or len(content) > verify.MAX_JSON:
        _refuse("invalid_staging_record")
    temp = "." + name + "." + secrets.token_hex(8)
    try:
        h = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                    os.O_CLOEXEC, 0o600, dir_fd=fd)
    except OSError:
        _refuse("durable_write_failed")
    try:
        os.fchmod(h, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(h, view)
            if written <= 0:
                _refuse("durable_write_failed")
            view = view[written:]
        os.fsync(h)
    except OSError:
        _refuse("durable_write_failed")
    finally:
        os.close(h)
    try:
        # Existing files must have intact root/private custody before replace.
        try:
            _read(fd, name, owner_uid)
        except Refused as err:
            if str(err) != "protected_file_missing_or_unsafe":
                raise
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    except OSError:
        _refuse("durable_write_failed")


def _make_dir(fd: int, name: str, owner_uid: int):
    try:
        os.mkdir(name, 0o700, dir_fd=fd)
        os.fsync(fd)
        return _directory(fd, name, owner_uid)
    except FileExistsError:
        _refuse("unfinished_or_existing_stage")
    except OSError:
        _refuse("staging_directory_create_failed")


def _copy_private(fd: int, name: str, data: bytes):
    if "/" in name or name in ("", ".", ".."):
        _refuse("unsafe_stage_filename")
    try:
        h = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                    os.O_CLOEXEC, 0o600, dir_fd=fd)
        try:
            os.fchmod(h, 0o600)
            view = memoryview(data)
            while view:
                n = os.write(h, view)
                if n <= 0:
                    _refuse("staging_copy_failed")
                view = view[n:]
            os.fsync(h)
        finally:
            os.close(h)
        os.fsync(fd)
    except OSError:
        _refuse("staging_copy_failed")


def _canonical_journal(phase: str, admission: dict, policy: bytes, state: bytes) -> bytes:
    return verify.canonical({
        "format": JOURNAL_FORMAT, "phase": phase,
        "publisher": admission["publisher"],
        "release_sequence": admission["release_sequence"],
        "manifest_sha256": admission["manifest_sha256"],
        "policy_sha256": _sha(policy), "state_sha256": _sha(state),
        "trust_generation": admission["trust_generation"],
        "scope": "paper-staging-only"})


def _journal_bytes(fd: int, owner_uid: int):
    return _read(fd, JOURNAL, owner_uid)


def _load_journal(fd: int, owner_uid: int):
    raw = _journal_bytes(fd, owner_uid)
    data = verify._parse(raw, kind="staging_journal")
    verify._exact(data, ("format", "phase", "publisher", "release_sequence",
                         "manifest_sha256", "policy_sha256", "state_sha256",
                         "trust_generation", "scope"), "staging_journal_fields")
    if data["format"] != JOURNAL_FORMAT or data["scope"] != "paper-staging-only" or data["phase"] not in ("PREPARING", "PREPARED"):
        _refuse("staging_journal_invalid")
    for field in ("manifest_sha256", "policy_sha256", "state_sha256"):
        verify._sha(data[field], "staging_journal_digest_invalid")
    verify._integer(data["release_sequence"], minimum=1, reason="staging_journal_version_invalid")
    verify._integer(data["trust_generation"], minimum=1, reason="staging_journal_version_invalid")
    return data


def _safe_remove_tree(fd: int, owner_uid: int, depth=0):
    """Delete only a bounded private staging tree; never traverse links."""
    if depth > 8:
        _refuse("staging_cleanup_depth")
    try:
        names = os.listdir(fd)
        if len(names) > MAX_OBJECTS:
            _refuse("staging_cleanup_inventory")
        for name in names:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if st.st_uid != owner_uid or st.st_mode & 0o077:
                _refuse("staging_cleanup_custody")
            if stat.S_ISDIR(st.st_mode):
                child = _directory(fd, name, owner_uid)
                try:
                    _safe_remove_tree(child, owner_uid, depth + 1)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=fd)
            elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600:
                os.unlink(name, dir_fd=fd)
            else:
                _refuse("staging_cleanup_unsafe_object")
            os.fsync(fd)
    except OSError:
        _refuse("staging_cleanup_failed")


class ProtectedStaging:
    """One stage at a time, serialized by flock on the protected root fd.

    expected_uid MUST remain 0 for production use. Test callers may inject the
    current synthetic OS owner; this is NOT a process elevation mechanism.
    root must be a pre-existing OS-administrator provisioned 0700 directory.
    """

    def __init__(self, root: Path, *, expected_uid: int = 0):
        self.root = Path(root)
        self.uid = expected_uid
        if type(expected_uid) is not int or expected_uid < 0:
            _refuse("invalid_trusted_owner")

    def _open_locked(self):
        fd = _root_dir(self.root, self.uid)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            _refuse("staging_busy")
        return fd

    def _inputs(self, fd: int):
        return (_read(fd, STORE_POLICY, self.uid), _read(fd, STORE_STATE, self.uid))

    def _layout(self, fd: int):
        try:
            names = set(os.listdir(fd))
        except OSError:
            _refuse("protected_root_unreadable")
        if not {STORE_POLICY, STORE_STATE} <= names or not names <= {STORE_POLICY, STORE_STATE, HOLD, READY}:
            _refuse("protected_store_inventory")
        if HOLD in names and READY in names:
            _refuse("conflicting_staging_state")
        return names

    def _copy_bundle(self, original: Path, stage_fd: int, manifest: dict):
        bundle_fd = _make_dir(stage_fd, BUNDLE, self.uid)
        try:
            entries = ["manifest.json", "signature.ed25519"] + [f["source"] for f in manifest["files"]]
            for relative in entries:
                if relative not in ("manifest.json", "signature.ed25519"):
                    verify._relative_path(relative)
                parts = relative.split("/")
                current = bundle_fd
                opened = []
                try:
                    for component in parts[:-1]:
                        try:
                            nextfd = _directory(current, component, self.uid)
                        except Refused as exc:
                            if str(exc) != "staging_directory_refused":
                                raise
                            nextfd = _make_dir(current, component, self.uid)
                        opened.append(nextfd)
                        current = nextfd
                    data = verify._read_regular(original, relative, verify.MAX_FILE)
                    _copy_private(current, parts[-1], data)
                finally:
                    for d in reversed(opened):
                        os.close(d)
            os.fsync(bundle_fd)
        finally:
            os.close(bundle_fd)

    def _verify_snapshot(self, root_fd: int, location: str, *, need_phase: str):
        policy, state = self._inputs(root_fd)
        stagefd = _directory(root_fd, location, self.uid)
        try:
            journal = _load_journal(stagefd, self.uid)
            if journal["phase"] != need_phase:
                _refuse("staging_phase_refused")
            if journal["policy_sha256"] != _sha(policy) or journal["state_sha256"] != _sha(state):
                _refuse("staged_authority_changed")
            bundle_path = self.root / location / BUNDLE
            result = verify.verify_release(bundle_path, trust_policy=policy, installed_state=state)
            checks = {"publisher": result["publisher"], "release_sequence": result["release_sequence"],
                      "manifest_sha256": result["manifest_sha256"], "trust_generation": result["trust_generation"]}
            if any(journal[name] != value for name, value in checks.items()):
                _refuse("staged_receipt_mismatch")
            return result
        finally:
            os.close(stagefd)

    def stage(self, package: Path):
        """Return a validated staged receipt; DOES NOT advance release state."""
        fd = self._open_locked()
        try:
            names = self._layout(fd)
            if HOLD in names or READY in names:
                _refuse("unfinished_or_existing_stage")
            policy, state = self._inputs(fd)
            admission = verify.verify_release(package, trust_policy=policy, installed_state=state)
            raw_manifest = verify._read_regular(Path(package), "manifest.json", verify.MAX_JSON)
            manifest = verify._validate_manifest(raw_manifest)[0]
            pending_fd = _make_dir(fd, HOLD, self.uid)
            try:
                # Durable fail-closed hold before any untrusted bytes are staged.
                _atomic(pending_fd, JOURNAL, _canonical_journal("PREPARING", admission, policy, state), self.uid)
                self._copy_bundle(Path(package), pending_fd, manifest)
                # Verify the *copied* bytes, against *independent* policy/state.
                staged = verify.verify_release(self.root / HOLD / BUNDLE,
                                               trust_policy=policy, installed_state=state)
                if staged != admission:
                    _refuse("staged_input_changed")
                _atomic(pending_fd, JOURNAL, _canonical_journal("PREPARED", admission, policy, state), self.uid)
            finally:
                os.close(pending_fd)
            # Reverify while hold remains; then publish by a single durable rename.
            self._verify_snapshot(fd, HOLD, need_phase="PREPARED")
            try:
                os.rename(HOLD, READY, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            except OSError:
                _refuse("staging_publish_failed")
            return self._verify_snapshot(fd, READY, need_phase="PREPARED")
        finally:
            os.close(fd)

    def verify_ready(self):
        fd = self._open_locked()
        try:
            names = self._layout(fd)
            if READY not in names:
                _refuse("no_ready_stage")
            return self._verify_snapshot(fd, READY, need_phase="PREPARED")
        finally:
            os.close(fd)

    def recover(self):
        """Reconcile only the staged bytes; never change installed state."""
        fd = self._open_locked()
        try:
            names = self._layout(fd)
            if READY in names:
                return {"action": "already_ready", "receipt": self._verify_snapshot(fd, READY, need_phase="PREPARED")}
            if HOLD not in names:
                return {"action": "nothing_pending"}
            pending_fd = _directory(fd, HOLD, self.uid)
            try:
                try:
                    j = _load_journal(pending_fd, self.uid)
                except Refused as exc:
                    if str(exc) == "protected_file_missing_or_unsafe":
                        # A crash after durable mkdir but before journal write.
                        j = {"phase": "PREPARING"}
                    else:
                        raise
                phase = j["phase"]
                if phase == "PREPARING":
                    _safe_remove_tree(pending_fd, self.uid)
                elif phase != "PREPARED":
                    _refuse("staging_phase_refused")
            finally:
                os.close(pending_fd)
            if phase == "PREPARING":
                os.rmdir(HOLD, dir_fd=fd)
                os.fsync(fd)
                return {"action": "abandoned_incomplete_stage"}
            result = self._verify_snapshot(fd, HOLD, need_phase="PREPARED")
            try:
                os.rename(HOLD, READY, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            except OSError:
                _refuse("staging_publish_failed")
            return {"action": "published_verified_stage", "receipt": result}
        finally:
            os.close(fd)

    def discard_ready(self):
        """Explicit trusted-caller discard of a never-installed staging copy."""
        fd = self._open_locked()
        try:
            names = self._layout(fd)
            if READY not in names:
                _refuse("no_ready_stage")
            stagefd = _directory(fd, READY, self.uid)
            try:
                _safe_remove_tree(stagefd, self.uid)
            finally:
                os.close(stagefd)
            os.rmdir(READY, dir_fd=fd)
            os.fsync(fd)
            return True
        finally:
            os.close(fd)
