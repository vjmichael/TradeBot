"""Authoritative vault-owner execution bridge — no provider dispatcher.

One-use approval is consumed OUTSIDE the encrypted-vault BEGIN IMMEDIATE
transaction before any provider attempt can be reserved. The authoritative
generation, policy, ciphertext binding and pending attempt are checked both
before and after consumption; any refusal consumes no worker resource.
A failed post-consume check loses the approval, never restores it.
Only a separate verified worker SO_PEERCRED/HMAC result can reach finish().

No network, account permissions, order, withdrawal, external endpoints,
worker spawn or credential export capability exists here. Production must
use a kernel-authenticated broker client and fixed installed worker.
"""
from __future__ import annotations

from managed_paper_vault import ManagedPaperVault,Refused,_require_owner
from vault_owner_hooks import Attempt,ROLES
from approval_ledger import Context
from receipt_channel import read_authenticated_receipt


def _authoritative(managed:ManagedPaperVault,context:Context):
    if type(context) is not Context or context.operation!='provider_test' or context.role!='kraken_read':
        raise Refused('provider_role_not_admitted')
    context.validated()
    if not isinstance(managed,ManagedPaperVault):
        raise Refused('managed_vault_required')
    with managed._vault.transaction() as db:
        owner=managed._owner(db)
        state=managed._consistent(db,owner)
        role=state['roles'].get(context.role)
        if (role is None or role['generation']!=context.generation
                or role['policy']!=context.policy or role['disabled']
                or role['pending'] is not None
                or managed._policies[context.role]!=context.policy):
            raise Refused('authoritative_generation_changed')
        # Consistency already rechecked actual authenticated ciphertext.
    return context


def begin_once(managed,context:Context,*,consume,now:int):
    """Only from protected vault owner. Never inject consume from RPC.

    consume(context) must perform the broker's server-side durable
    one-use transaction and return *literal True* only after success.
    """
    _require_owner()
    if not callable(consume):
        raise Refused('trusted_broker_consume_required')
    if type(now) is not int or now<=0:
        raise Refused('owner_clock_invalid')
    _authoritative(managed,context)
    if consume(context) is not True:
        raise Refused('trusted_broker_consume_required')
    # A concurrent credential removal/re-enroll between consumption and
    # reservation is checked again; review is lost, never replayed.
    _authoritative(managed,context)
    with managed._vault.transaction() as db:
        owner=managed._owner(db)
        state=managed._consistent(db,owner)
        record=state['roles'].get(context.role)
        if (record is None or record['generation']!=context.generation
                or record['policy']!=context.policy or record['pending'] is not None
                or record['disabled']):
            raise Refused('authoritative_generation_changed')
        attempt=owner.begin(context.role,managed._sealed(db,context.role),
                            context.policy,now)
        if attempt.generation!=context.generation:
            raise Refused('authoritative_generation_changed')
        return attempt


def finish_verified(managed,attempt:Attempt,*,worker_conn,worker_uid:int,
                    signing_key:bytes,now:int):
    """Receive only a fixed authenticated worker report; no untrusted result."""
    _require_owner()
    if type(attempt) is not Attempt or attempt.role!='kraken_read':
        raise Refused('provider_attempt_not_admitted')
    if type(now) is not int or now<=0:
        raise Refused('owner_clock_invalid')
    # The kernel peer UID and full separate-key HMAC are verified BEFORE
    # entering the authoritative owner transaction. No dynamic endpoint.
    result=read_authenticated_receipt(worker_conn,worker_uid=worker_uid,
        attempt=attempt,signing_key=signing_key,now=now)
    with managed._vault.transaction() as db:
        owner=managed._owner(db)
        managed._consistent(db,owner)
        owner.finish(attempt,managed._sealed(db,attempt.role),result,now)
    return result
