"""R5-R3A: host-only, read-only signed package and publisher policy verifier.

Authoritative policy and last-installed state are supplied by a trusted caller,
NOT by the candidate package. Production root-owned custody, transactional
state updates, installer execution and approval are *separate R5-R3B gates*.
This module never installs, starts services, rotates keys or accesses venues.
"""
from __future__ import annotations

import base64
import binascii
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

DOMAIN = b"MAJ-VAJ-R5-R3A-SIGNED-PAPER-PACKAGE-V1\x00"
FORMAT = "maj-vaj-r5-r3a-package-v1"
TRUST_FORMAT = "maj-vaj-r5-r3a-trust-v1"
STATE_FORMAT = "maj-vaj-r5-r3a-release-state-v1"
MAX_JSON = 65536
MAX_FILES = 128
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[a-z][a-z0-9-]{1,63}\Z")
_UNIT = re.compile(r"maj-vaj-[a-z0-9-]+\.service\Z")


class Refused(ValueError):
    """Stable non-sensitive refusal code for host regression receipts."""


def deny(code: str):
    raise Refused(code)


def _exact(obj, keys, reason):
    if type(obj) is not dict or set(obj) != set(keys):
        deny(reason)


def _integer(value, *, minimum=0, maximum=2**63-1, reason="invalid_integer"):
    if type(value) is not int or not minimum <= value <= maximum:
        deny(reason)
    return value


def _sha(s, reason):
    if type(s) is not str or _HEX.fullmatch(s) is None:
        deny(reason)
    return s


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _no_duplicate_pairs(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            deny("duplicate_json_field")
        result[k] = v
    return result


def _parse(raw: bytes, *, kind: str):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_JSON:
        deny(kind + "_size")
    try:
        obj = json.loads(raw.decode("ascii"), object_pairs_hook=_no_duplicate_pairs,
                         parse_constant=lambda _: deny(kind + "_nonfinite"))
    except (UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, Refused):
            raise
        deny(kind + "_json")
    try:
        expected = canonical(obj)
    except (ValueError, TypeError, OverflowError):
        deny(kind + "_json")
    if expected != raw:
        deny(kind + "_noncanonical")
    return obj


def _relative_path(path):
    if (type(path) is not str or not 1 <= len(path) <= 200
            or path.startswith("/") or "\\" in path or "\x00" in path):
        deny("unsafe_payload_path")
    parts = path.split("/")
    if (len(parts) < 2 or parts[0] != "payload" or
            any(p in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", p)
                for p in parts)):
        deny("unsafe_payload_path")
    return parts


def _destination(path):
    if (type(path) is not str or len(path) > 240 or not path.startswith("/")):
        deny("unsafe_install_path")
    parts = path.split("/")[1:]
    if (not parts or any(p in ("", ".", "..") or
             not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", p) for p in parts)):
        deny("unsafe_install_path")
    if not (path.startswith("/opt/maj-vaj/") or path.startswith("/etc/systemd/system/")):
        deny("unsafe_install_path")
    return path


def _read_regular(root: Path, relative: str, limit: int):
    """Bounded O_NOFOLLOW/O_NONBLOCK read, traversing relative to root fd."""
    parts = relative.split("/")
    fds = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fds.append(fd)
        for component in parts[:-1]:
            if component in ("", ".", ".."):
                deny("unsafe_package_component")
            fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                         os.O_CLOEXEC, dir_fd=fd)
            fds.append(fd)
        if parts[-1] in ("", ".", ".."):
            deny("unsafe_package_component")
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                     os.O_CLOEXEC, dir_fd=fd)
        fds.append(fd)
        initial = os.fstat(fd)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1 or
                not 0 <= initial.st_size <= limit):
            deny("unsafe_package_file")
        chunks = []
        length = 0
        while True:
            b = os.read(fd, min(131072, limit + 1 - length))
            if not b:
                break
            chunks.append(b)
            length += len(b)
            if length > limit:
                deny("oversize_package_file")
        final = os.fstat(fd)
        if (length != initial.st_size or final.st_size != initial.st_size or
                (final.st_ino, final.st_dev, final.st_mtime_ns, final.st_ctime_ns) !=
                (initial.st_ino, initial.st_dev, initial.st_mtime_ns, initial.st_ctime_ns)):
            deny("concurrent_package_mutation")
        return b"".join(chunks)
    except (OSError, OverflowError) as exc:
        deny("unreadable_package_file")
    finally:
        for descriptor in reversed(fds):
            os.close(descriptor)


def _all_package_files(package: Path):
    seen = set()
    dirs = [package]
    while dirs:
        directory = dirs.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        deny("package_symlink_refused")
                    rel = Path(entry.path).relative_to(package).as_posix()
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        if entry.stat(follow_symlinks=False).st_nlink != 1:
                            deny("package_hardlink_refused")
                        seen.add(rel)
                    else:
                        deny("unexpected_package_object")
                    if len(seen) > MAX_FILES + 2:
                        deny("excess_package_inventory")
        except OSError:
            deny("package_listing_failed")
    return seen


def _validate_trust(raw):
    policy = _parse(raw, kind="trust")
    _exact(policy, ("format", "publisher", "generation", "minimum_epoch",
                    "keys", "targets", "services"), "trust_policy_fields")
    if policy["format"] != TRUST_FORMAT or not _NAME.fullmatch(policy["publisher"] if type(policy["publisher"]) is str else ""):
        deny("trust_policy_format")
    _integer(policy["generation"], minimum=1, reason="trust_generation_invalid")
    _integer(policy["minimum_epoch"], minimum=1, reason="trust_epoch_invalid")
    if type(policy["keys"]) is not list or not 1 <= len(policy["keys"]) <= 32:
        deny("trust_keys_invalid")
    key_map = {}
    for key in policy["keys"]:
        _exact(key, ("key_id", "public_key", "status", "from_epoch", "through_epoch"), "trust_key_fields")
        kid = _sha(key["key_id"], "trust_key_id")
        if type(key["public_key"]) is not str or len(key["public_key"]) != 44:
            deny("trust_public_key")
        try:
            pub = base64.b64decode(key["public_key"], validate=True)
        except (binascii.Error, ValueError):
            deny("trust_public_key")
        if len(pub) != 32 or hashlib.sha256(pub).hexdigest() != kid:
            deny("trust_key_identity")
        if key["status"] not in ("active", "revoked"):
            deny("trust_key_status")
        first = _integer(key["from_epoch"], minimum=1, reason="trust_key_epoch")
        last = key["through_epoch"]
        if last is not None:
            _integer(last, minimum=first, reason="trust_key_epoch")
        if kid in key_map:
            deny("trust_duplicate_key")
        key_map[kid] = (key, pub)
    if type(policy["targets"]) is not list or not 1 <= len(policy["targets"]) <= MAX_FILES:
        deny("trust_targets_invalid")
    targets = {}
    for target in policy["targets"]:
        _exact(target, ("install", "mode"), "trust_target_fields")
        dst = _destination(target["install"])
        if target["mode"] not in ("0644", "0755") or dst in targets:
            deny("trust_target_invalid")
        if dst.startswith("/etc/systemd/system/") and target["mode"] != "0644":
            deny("trust_target_invalid")
        targets[dst] = target["mode"]
    if type(policy["services"]) is not list or len(policy["services"]) > 16:
        deny("trust_services_invalid")
    services = {}
    for s in policy["services"]:
        _exact(s, ("unit", "user", "group"), "trust_service_fields")
        u = s["unit"]
        if type(u) is not str or _UNIT.fullmatch(u) is None or u in services:
            deny("trust_service_invalid")
        if (type(s["user"]) is not str or type(s["group"]) is not str or
                not _NAME.fullmatch(s["user"]) or not _NAME.fullmatch(s["group"]) or
                s["user"] == "root" or s["group"] == "root"):
            deny("trust_service_identity")
        if "/etc/systemd/system/" + u not in targets:
            deny("trust_service_target_missing")
        services[u] = (s["user"], s["group"])
    if {p.rsplit("/", 1)[-1] for p in targets if p.startswith("/etc/systemd/system/")} != set(services):
        deny("trust_service_inventory")
    return policy, key_map, targets, services


def _validate_state(raw):
    state = _parse(raw, kind="state")
    _exact(state, ("format", "publisher", "release_sequence", "security_epoch",
                   "trust_generation", "schema_version", "manifest_sha256"), "state_fields")
    if state["format"] != STATE_FORMAT or type(state["publisher"]) is not str:
        deny("state_format")
    for key in ("release_sequence", "security_epoch", "trust_generation", "schema_version"):
        _integer(state[key], minimum=0, reason="state_counter_invalid")
    _sha(state["manifest_sha256"], "state_manifest_hash")
    if state["release_sequence"] == 0 and state["manifest_sha256"] != "0" * 64:
        deny("state_initial_digest")
    return state


def _validate_manifest(raw):
    manifest = _parse(raw, kind="manifest")
    _exact(manifest, ("format", "publisher", "signing_key_id", "release_sequence", "security_epoch",
                      "platform", "scope", "schema", "files", "services"), "manifest_fields")
    if manifest["format"] != FORMAT:
        deny("manifest_format")
    if type(manifest["publisher"]) is not str or not _NAME.fullmatch(manifest["publisher"]):
        deny("manifest_publisher")
    _sha(manifest["signing_key_id"], "manifest_key_id")
    _integer(manifest["release_sequence"], minimum=1, reason="manifest_release_sequence")
    _integer(manifest["security_epoch"], minimum=1, reason="manifest_epoch")
    _exact(manifest["platform"], ("os", "arch"), "manifest_platform")
    if manifest["platform"] != {"os": "linux", "arch": "x86_64"}:
        deny("unsupported_platform")
    if manifest["scope"] != "paper-staging-only":
        deny("release_scope_refused")
    _exact(manifest["schema"], ("from", "to", "migration"), "manifest_schema")
    schema = manifest["schema"]
    if (type(schema["from"]) is not int or type(schema["to"]) is not int or
            schema["from"] < 1 or schema["to"] != schema["from"] or schema["migration"] != "none"):
        deny("schema_migration_not_authorized")
    if type(manifest["files"]) is not list or not 1 <= len(manifest["files"]) <= MAX_FILES:
        deny("manifest_file_inventory")
    files, source_set = {}, set()
    total = 0
    for f in manifest["files"]:
        _exact(f, ("source", "install", "sha256", "size", "mode"), "manifest_file_fields")
        _relative_path(f["source"])
        dst = _destination(f["install"])
        _sha(f["sha256"], "manifest_file_hash")
        size = _integer(f["size"], minimum=0, maximum=MAX_FILE, reason="manifest_file_size")
        if f["mode"] not in ("0644", "0755") or dst in files or f["source"] in source_set:
            deny("manifest_duplicate_or_mode")
        total += size
        if total > MAX_TOTAL:
            deny("manifest_total_size")
        files[dst] = f
        source_set.add(f["source"])
    if type(manifest["services"]) is not list or len(manifest["services"]) > 16:
        deny("manifest_services")
    services = {}
    for s in manifest["services"]:
        _exact(s, ("unit", "user", "group"), "manifest_service_fields")
        unit = s["unit"]
        if type(unit) is not str or _UNIT.fullmatch(unit) is None or unit in services:
            deny("manifest_service_invalid")
        if type(s["user"]) is not str or type(s["group"]) is not str:
            deny("manifest_service_identity")
        services[unit] = (s["user"], s["group"])
    return manifest, files, source_set, services


def _verify_unit(data: bytes, user: str, group: str):
    try:
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read_string(data.decode("utf-8"))
        if not parser.has_section("Service") or parser.defaults():
            deny("unit_identity_mismatch")
        if parser.get("Service", "User", fallback="") != user or parser.get("Service", "Group", fallback="") != group:
            deny("unit_identity_mismatch")
        if "User" not in parser.options("Service") or "Group" not in parser.options("Service"):
            deny("unit_identity_mismatch")
    except (UnicodeError, configparser.Error):
        deny("unit_identity_mismatch")


def verify_release(package: Path, *, trust_policy: bytes, installed_state: bytes):
    """Return safe release metadata if a *new* signed PAPER package is admissible.

    All three inputs are required, and trust_policy/installed_state MUST originate
    from independently protected custody, not package files or caller-substituted
    install inputs. This preflight is not permission to install or trade.
    """
    package = Path(package)
    policy, keys, targets, permitted_services = _validate_trust(trust_policy)
    state = _validate_state(installed_state)
    manifest_raw = _read_regular(package, "manifest.json", MAX_JSON)
    manifest, files, source_set, services = _validate_manifest(manifest_raw)
    if manifest["publisher"] != policy["publisher"] or state["publisher"] != policy["publisher"]:
        deny("unauthorized_publisher")
    if state["trust_generation"] > policy["generation"]:
        deny("trust_policy_rollback")
    if manifest["release_sequence"] <= state["release_sequence"]:
        deny("release_downgrade_or_replay")
    if (manifest["security_epoch"] < state["security_epoch"] or
            manifest["security_epoch"] < policy["minimum_epoch"]):
        deny("release_epoch_rollback")
    if manifest["schema"]["from"] != state["schema_version"]:
        deny("schema_state_mismatch")
    signing = keys.get(manifest["signing_key_id"])
    if signing is None:
        deny("unauthorized_signing_key")
    key, public = signing
    if (key["status"] != "active" or manifest["security_epoch"] < key["from_epoch"] or
            (key["through_epoch"] is not None and manifest["security_epoch"] > key["through_epoch"])):
        deny("revoked_or_inactive_signing_key")
    signature = _read_regular(package, "signature.ed25519", 64)
    if len(signature) != 64:
        deny("signature_size")
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, DOMAIN + manifest_raw)
    except (InvalidSignature, ValueError):
        deny("invalid_package_signature")
    if set(files) != set(targets):
        deny("unauthorized_install_inventory")
    if services != permitted_services:
        deny("service_identity_mismatch")
    if {p.rsplit("/", 1)[-1] for p in files if p.startswith("/etc/systemd/system/")} != set(services):
        deny("manifest_service_inventory")
    if {p: f["mode"] for p, f in files.items()} != targets:
        deny("install_mode_mismatch")
    if _all_package_files(package) != source_set | {"manifest.json", "signature.ed25519"}:
        deny("unexpected_package_inventory")
    for dst, item in files.items():
        data = _read_regular(package, item["source"], MAX_FILE)
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            deny("payload_digest_mismatch")
        if dst.startswith("/etc/systemd/system/"):
            user, group = services[dst.rsplit("/", 1)[-1]]
            _verify_unit(data, user, group)
    return {"publisher": manifest["publisher"], "release_sequence": manifest["release_sequence"],
            "security_epoch": manifest["security_epoch"], "trust_generation": policy["generation"],
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(), "files": len(files),
            "scope": "paper-staging-only"}
