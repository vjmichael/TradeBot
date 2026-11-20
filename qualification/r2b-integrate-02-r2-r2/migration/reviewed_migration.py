"""Reviewed, non-destructive legacy vault migration candidate.

Owner-internal only. Caller must supply a one-use, independently authenticated
operator approval via `consume` (not provided here). No IPC or browser surface.
No network or model, no plaintext credential retrieval, no trade authority.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import hmac

from managed_paper_vault import ManagedPaperVault, Refused, _require_owner
from vault_owner_hooks import OwnerHooks, ROLES

DOMAIN = b'maj-vaj:credential-migration-review:v1\0'

@dataclass(frozen=True)
class Inventory:
    roles: tuple[str, ...]
    fingerprint: str


def _inventory(vault: ManagedPaperVault, db) -> Inventory:
    names = vault._vault._verify(db)
    allowed = {s for roles in ROLES.values() for s in roles}
    if names - allowed:
        raise Refused('unexpected_legacy_slot')
    roles = []
    for role, slots in ROLES.items():
        present = names & set(slots)
        if present and present != set(slots):
            raise Refused('incomplete_credential_pair')
        if present:
            roles.append(role)
    digest = hmac.new(vault._tag_key, DOMAIN, hashlib.sha256)
    for slot in sorted(names):
        (ciphertext,) = db.execute('SELECT ciphertext FROM secrets WHERE slot=?',(slot,)).fetchone()
        if type(ciphertext) is not bytes or not ciphertext:
            raise Refused('invalid_sealed_source')
        for data in (slot.encode('ascii'), ciphertext):
            digest.update(len(data).to_bytes(4,'big'))
            digest.update(data)
    return Inventory(tuple(sorted(roles)), digest.hexdigest())


def inspect_legacy(vault: ManagedPaperVault) -> Inventory:
    _require_owner()
    if type(vault) is not ManagedPaperVault:
        raise Refused('managed_vault_required')
    with vault._vault.transaction() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_metadata'").fetchone():
            raise Refused('already_migrated')
        inventory=_inventory(vault,db)
        if not inventory.roles:
            raise Refused('empty_vault_uses_fresh_migration')
        return inventory


def migrate_reviewed(vault: ManagedPaperVault, expected: Inventory, consume, now: int) -> Inventory:
    """Called only by vault owner after an authenticated native review.

    `consume` is a private, one-use trusted review authority, NOT a boolean,
    browser callback or user-controlled IPC field. It consumes review even on
    later transaction failure (conservative, review required again).
    """
    _require_owner()
    if type(vault) is not ManagedPaperVault or not isinstance(expected,Inventory):
        raise Refused('review_context_refused')
    if type(expected.fingerprint) is not str or len(expected.fingerprint)!=64:
        raise Refused('review_context_refused')
    if not callable(consume):
        raise Refused('trusted_review_required')
    with vault._vault.transaction() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_metadata'").fetchone():
            raise Refused('already_migrated')
        actual=_inventory(vault,db)
        if not actual.roles:
            raise Refused('empty_vault_uses_fresh_migration')
        if actual.roles!=expected.roles or not hmac.compare_digest(actual.fingerprint, expected.fingerprint):
            raise Refused('inventory_changed')
        # One-use authority must bind role list, exact encrypted fingerprint,
        # operator's native reviewed action and single executable operation.
        # This module never creates or accepts that authority from HTTP/IPC.
        if consume(actual,now) is not True:
            raise Refused('trusted_review_required')
        owner=OwnerHooks(db,vault._vault.cipher,vault._tag_key)
        owner.initialize()
        state=owner._load()
        owner._clock(state,now)
        owner._put(state)
        for role in actual.roles:
            owner.replace(role,vault._sealed(db,role),vault._policies[role],now)
        vault._consistent(db,owner)
        # No credential rewrite, and no success receipt can be imported.
        return actual