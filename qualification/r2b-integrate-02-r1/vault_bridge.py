"""R2B-INTEGRATE-02-R1: owner-only integration seam for existing PaperVault.

Call from isolated vault service, never from dashboard or generic RPC. This
module neither reads secret values out nor grants orders, provider access or
LIVE state.  No database writes occur outside the existing vault transaction.
"""
from __future__ import annotations
import os
import re
import pwd
import socket
import struct
from vault_owner_hooks import OwnerHooks, ROLES, Refused

DOMAIN = b'maj-vaj:PAPER:credential-vault:v1\0'
POLICY_FORMAT = re.compile(r'[0-9a-f]{64}\Z')


def _values(values, role):
    if type(values) is not dict or role not in ROLES or set(values) != set(ROLES[role]):
        raise Refused('credential_selection_refused')
    if any(type(v) is not str or not 0 < len(v) <= 8192 or
           any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in v)
           for v in values.values()):
        raise Refused('credential_value_refused')
    return values


def _policy(p):
    if type(p) is not str or not POLICY_FORMAT.fullmatch(p):
        raise Refused('policy_unavailable')
    return p


def _require_owner():
    """Enforce the protected service principal, never an operator or root shell."""
    try:
        owner_uid = pwd.getpwnam('maj-vaj-vault').pw_uid
    except KeyError:
        raise Refused('vault_identity_unavailable') from None
    if owner_uid <= 0 or os.getuid() != owner_uid or os.geteuid() != owner_uid:
        raise Refused('vault_owner_required')


class VaultBridge:
    """Bridge to an existing PaperVault with transaction(), cipher, _verify().

    A reference to this class must never cross a service boundary. A read-only
    credential status API is a separate, existing contract. Metadata migration
    is explicit and refuses unknown pre-existing credential state by default.
    """
    def __init__(self, vault, tag_key: bytes):
        _require_owner()
        if type(tag_key) is not bytes or len(tag_key) < 32:
            raise Refused('separate_tag_key_required')
        self.vault = vault
        self.tag_key = tag_key

    def _owner(self, db):
        self.vault._verify(db)  # also refuses missing or incomplete Kraken pair
        return OwnerHooks(db, self.vault.cipher, self.tag_key)

    def migrate_empty(self):
        """Explicit fresh installation; refuse migration over saved credentials.
        Existing credentials require separately reviewed disabled-state migration.
        """
        with self.vault.transaction() as db:
            if self.vault._verify(db):
                raise Refused('existing_credentials_require_review')
            self._owner(db).initialize()

    @staticmethod
    def _sealed(db, role):
        rows = {}
        for slot in ROLES[role]:
            entry = db.execute('SELECT ciphertext FROM secrets WHERE slot=?', (slot,)).fetchone()
            if entry is None:
                raise Refused('incomplete_credential_pair')
            rows[slot] = entry[0]
        return rows

    def save(self, role, values, policy, now):
        """Atomic paired insert AND verification invalidation, no provider call."""
        vals = _values(values, role)
        _policy(policy)
        sealed = [(slot, self.vault.cipher.encrypt(DOMAIN + slot.encode() + b'\0' + value.encode('utf-8')))
                  for slot, value in vals.items()]
        with self.vault.transaction() as db:
            owner = self._owner(db)
            db.executemany('INSERT INTO secrets(slot,ciphertext) VALUES(?,?) '
                           'ON CONFLICT(slot) DO UPDATE SET ciphertext=excluded.ciphertext', sealed)
            self.vault._verify(db)
            owner.replace(role, self._sealed(db, role), policy, now)

    def remove(self, role, now):
        if role not in ROLES:
            raise Refused('unsupported_role')
        with self.vault.transaction() as db:
            owner = self._owner(db)
            db.executemany('DELETE FROM secrets WHERE slot=?', [(slot,) for slot in ROLES[role]])
            self.vault._verify(db)
            owner.remove(role, now)

    def disable(self, role, now):
        with self.vault.transaction() as db:
            self._owner(db).disable(role, now)

    @staticmethod
    def _operator_peer(peer_socket):
        """Kernel-supplied local UID only; not client-provided JSON or boolean.

        Session/PAM and actual UI intent are separate mandatory native gates.
        """
        if not isinstance(peer_socket, socket.socket) or peer_socket.family != socket.AF_UNIX:
            raise Refused('local_operator_peer_required')
        try:
            data = peer_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            pid, uid, gid = struct.unpack('3i', data)
            expected = pwd.getpwnam('maj-vaj-credential-operator').pw_uid
        except (OSError, KeyError, struct.error):
            raise Refused('operator_identity_unavailable') from None
        if expected <= 0 or pid <= 0 or uid != expected:
            raise Refused('operator_identity_refused')

    def reserve_from_operator_peer(self, role, policy, now, peer_socket, reviewed):
        """No public RPC: verify SO_PEERCRED plus separately reviewed intent.

        reviewed must originate from the native operator-only UI, never browser
        or request payload. Full local PAM/session/confirmation proof remains
        for installed acceptance.
        """
        self._operator_peer(peer_socket)
        if reviewed is not True:
            raise Refused('operator_review_required')
        _policy(policy)
        with self.vault.transaction() as db:
            return self._owner(db).begin(role, self._sealed(db, role), policy, now)

    def complete_from_protected_adapter(self, attempt, result, now):
        """Only a trusted in-process adapter result may be passed.

        Public clients MUST NOT be allowed to forge result or attempt; no IPC
        operation is introduced here. Receipt provenance requires OS isolation.
        """
        with self.vault.transaction() as db:
            self._owner(db).finish(attempt, self._sealed(db, attempt.role), result, now)

    def startup_invalidate(self, now):
        with self.vault.transaction() as db:
            self._owner(db).recover_interrupted(now)

    def status_local(self, role, policy, now):
        """Owner-only observation; normal dashboard still uses existing status."""
        with self.vault.transaction() as db:
            owner = self._owner(db)
            try:
                sealed = self._sealed(db, role)
            except Refused:
                sealed = {}
            return owner.status(role, sealed, policy, now)