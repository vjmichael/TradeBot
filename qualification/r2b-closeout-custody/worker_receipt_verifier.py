"""R2B receive-only protected provider worker receipt verification candidate.

No provider API calls, signing endpoint, request dispatch, local shell,
browser authority, vault export, financial operation or one-use operator grant.
Positive use is forbidden until independent native session admission and
a genuine fixed-destination supervised worker are installed/qualified.

Receipt key must be a distinct, root-provisioned worker->owner signing
credential, never the encrypted vault key or provider-tag-key. The trusted
server gets worker uid/pid from kernel SO_PEERCRED; the caller cannot nominate
it. This verifier intentionally cannot certify an OS principal by itself.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import re

from vault_owner_hooks import Attempt, ALLOWED, PROVIDER, Refused

DOMAIN=b'maj-vaj:r2b:fixed-provider-worker-receipt:v1\x00'
WORKER={
    'kraken_read': 'kraken-read-adapter-v1',
    'telegram_desk': 'telegram-desk-adapter-v1',
    'telegram_recovery': 'telegram-recovery-adapter-v1',
    'grok': 'grok-metadata-adapter-v1',
}
FIELDS=frozenset({'version','worker','role','generation','attempt_id','policy','started','nonce','result','observed','mac'})
MAX_AGE=300

def _signed_bytes(receipt):
    return DOMAIN + json.dumps({k:receipt[k] for k in FIELDS if k!='mac'},
        sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def verify_receipt(receipt, attempt, signing_key:bytes, now:int):
    """Return sanitized provider result only on a complete authenticated match.

    The owner still must verify kernel peer credentials and call OwnerHooks
    finish under its existing BEGIN IMMEDIATE transaction. Caller-supplied
    JSON without a separate authorized worker and independent key is useless.
    """
    if type(receipt) is not dict or set(receipt)!=FIELDS:
        raise Refused('worker_receipt_shape_refused')
    if type(attempt) is not Attempt or type(signing_key) is not bytes or len(signing_key)<32:
        raise Refused('trusted_worker_credential_required')
    if type(now) is not int or not 0<now<2**63:
        raise Refused('trusted_clock_unavailable')
    for k in ('worker','role','generation','attempt_id','policy','result','mac'):
        if type(receipt[k]) is not str:
            raise Refused('worker_receipt_field_refused')
    for k in ('version','started','nonce','observed'):
        if type(receipt[k]) is not int:
            raise Refused('worker_receipt_field_refused')
    if receipt['version']!=1 or receipt['role']!=attempt.role or receipt['worker']!=WORKER.get(attempt.role):
        raise Refused('worker_receipt_origin_refused')
    if (receipt['generation']!=attempt.generation or receipt['attempt_id']!=attempt.attempt_id
            or receipt['policy']!=attempt.policy or receipt['started']!=attempt.started
            or receipt['nonce']!=attempt.nonce):
        raise Refused('worker_receipt_attempt_mismatch')
    if receipt['result'] not in ALLOWED[attempt.role]:
        raise Refused('worker_receipt_result_refused')
    if (not 0 < receipt['started']<=receipt['observed']<=now
            or now>=receipt['started']+MAX_AGE):
        raise Refused('worker_receipt_stale')
    mac=receipt['mac']
    if not re.fullmatch('[0-9a-f]{64}',mac):
        raise Refused('worker_receipt_mac_refused')
    expected=hmac.new(signing_key,_signed_bytes(receipt),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,mac):
        raise Refused('worker_receipt_mac_refused')
    return receipt['result']
