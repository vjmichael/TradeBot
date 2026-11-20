"""R2B-INTEGRATE-02-R2: strict transaction adapter for the authoritative PaperVault.

Staged for review. Use only in the existing maj-vaj-vault service process. No
RPC, provider network, model, order or credential export capability is offered.
"""
from __future__ import annotations
import os
import pwd
import re
import sqlite3
import sys
from pathlib import Path
from types import MappingProxyType

# During qualification, import the authoritative earlier candidate's hooks;
# after convergence these modules live together in the owner package.
_shared = Path(__file__).resolve().parent.parent / 'r2b-integrate-02'
if _shared.is_dir():
    sys.path.insert(0, str(_shared))
from vault_owner_hooks import OwnerHooks, ROLES, Refused

DOMAIN = b'maj-vaj:PAPER:credential-vault:v1\0'
POLICY = re.compile('[0-9a-f]{64}\\Z')


def _require_owner():
    try:
        uid = pwd.getpwnam('maj-vaj-vault').pw_uid
    except KeyError:
        raise Refused('vault_identity_unavailable') from None
    if uid <= 0 or os.getuid() != uid or os.geteuid() != uid:
        raise Refused('vault_owner_required')


def _strict_policy(policies):
    if type(policies) is not dict or set(policies) != set(ROLES):
        raise Refused('policy_set_unavailable')
    if any(type(v) is not str or not POLICY.fullmatch(v) for v in policies.values()):
        raise Refused('policy_set_unavailable')
    return MappingProxyType(policies.copy())


class ManagedPaperVault:
    """Wrap exactly one existing PaperVault, do not create a second vault.

    The service must call this instead of the legacy enroll/remove routes after
    the *reviewed* migration; calling old mutation routes in parallel is forbidden.
    No API to reserve a provider test is exposed until native authorization and
    adapter-result provenance are implemented and installed-accepted.
    """
    def __init__(self, vault, tag_key: bytes, policy_by_role: dict[str, str]):
        _require_owner()
        if type(tag_key) is not bytes or len(tag_key) < 32:
            raise Refused('separate_tag_key_required')
        for attr in ('transaction', '_verify', '_status', 'cipher'):
            if not hasattr(vault, attr):
                raise Refused('paper_vault_contract_missing')
        self._vault = vault
        self._tag_key = tag_key
        self._policies = _strict_policy(policy_by_role)

    def _owner(self, db):
        self._vault._verify(db)
        return OwnerHooks(db, self._vault.cipher, self._tag_key)

    @staticmethod
    def _established(owner):
        try:
            return owner._load()
        except sqlite3.Error:
            raise Refused('verification_state_missing') from None

    def _consistent(self, db, owner):
        """Refuse any legacy/bypass write or orphaned metadata, cross-role."""
        slots=self._vault._verify(db)
        state=self._established(owner)
        if set(state['roles'])-set(ROLES):
            raise Refused('unexpected_role_metadata')
        for role,names in ROLES.items():
            have=set(names)&slots
            record=state['roles'].get(role)
            if not have:
                if record is not None:
                    raise Refused('orphaned_role_metadata')
                continue
            if have!=set(names) or record is None:
                raise Refused('untracked_credential')
            binding,tag=owner._bindings(role,self._sealed(db,role))
            if record['binding']!=binding or record['tag']!=tag:
                raise Refused('binding_changed')
        return state

    @staticmethod
    def _sealed(db, role):
        if role not in ROLES:
            raise Refused('unsupported_role')
        out = {}
        for slot in ROLES[role]:
            row = db.execute('SELECT ciphertext FROM secrets WHERE slot=?', (slot,)).fetchone()
            if row is None or type(row[0]) is not bytes:
                raise Refused('incomplete_credential_pair')
            out[slot] = row[0]
        return out

    def migrate_empty(self, now):
        """Explicit migration. Existing ciphertext is preserved, never trusted.

        Existing saved credentials are never silently adopted or overwritten.
        A separately reviewed migration will be required before deployment.
        """
        _require_owner()
        with self._vault.transaction() as db:
            slots = self._vault._verify(db)
            owner = self._owner(db)
            if slots:
                raise Refused('legacy_upgrade_requires_native_review')
            for role, names in ROLES.items():
                part = set(names) & slots
                if part and part != set(names):
                    raise Refused('incomplete_credential_pair')
            if slots - {s for group in ROLES.values() for s in group}:
                raise Refused('unexpected_legacy_slot')
            owner.initialize()  # table-exists or failure -> rollback, no silent reset
            state=owner._load()
            owner._clock(state,now)
            owner._put(state)
            for role, names in ROLES.items():
                if set(names) <= slots:
                    owner.replace(role, self._sealed(db, role), self._policies[role], now)
            # No successful provider verification can survive migration.

    def _input(self, role, values):
        if role not in ROLES or type(values) is not dict or set(values) != set(ROLES[role]):
            raise Refused('credential_selection_refused')
        if any(type(v) is not str or not 0 < len(v) <= 8192 or
               any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in v)
               for v in values.values()):
            raise Refused('credential_value_refused')
        return values

    def enroll(self, role, values, now):
        """Authoritative transaction: credential seal + generation invalidation."""
        _require_owner()
        values = self._input(role, values)
        sealed = [(slot, self._vault.cipher.encrypt(DOMAIN + slot.encode() + b'\0' + v.encode('utf-8')))
                  for slot, v in values.items()]
        with self._vault.transaction() as db:
            owner = self._owner(db)  # requires explicitly migrated metadata
            self._consistent(db, owner)
            db.executemany('INSERT INTO secrets(slot,ciphertext) VALUES (?,?) '
                           'ON CONFLICT(slot) DO UPDATE SET ciphertext=excluded.ciphertext', sealed)
            self._vault._verify(db)
            owner.replace(role, self._sealed(db, role), self._policies[role], now)
            return self._vault._status(db)  # original status-only contract

    def remove(self, role, now):
        _require_owner()
        if role not in ROLES:
            raise Refused('unsupported_role')
        with self._vault.transaction() as db:
            owner = self._owner(db)
            self._consistent(db, owner)
            self._sealed(db, role)  # reject missing role before deletion
            db.executemany('DELETE FROM secrets WHERE slot=?', ((slot,) for slot in ROLES[role]))
            self._vault._verify(db)
            owner.remove(role, now)
            return self._vault._status(db)

    def disable(self, role, now):
        _require_owner()
        with self._vault.transaction() as db:
            owner=self._owner(db)
            self._consistent(db,owner)
            owner.disable(role,now)

    def startup_invalidate(self, now):
        _require_owner()
        with self._vault.transaction() as db:
            owner=self._owner(db)
            self._consistent(db,owner)
            owner.recover_interrupted(now)
            # Prior successful metadata checks are not considered valid after
            # a service restart without an independent fresh provider review.
            state=owner._load()
            for rec in state['roles'].values():
                v=rec['receipt']
                if v is not None and v['expires']:
                    rec['receipt']=None
            owner._put(state)

    def status(self):
        _require_owner()
        with self._vault.transaction() as db:
            owner=self._owner(db)
            self._consistent(db,owner)
            return self._vault._status(db)

    # No begin/complete on purpose: positive verification is NOT admitted here.