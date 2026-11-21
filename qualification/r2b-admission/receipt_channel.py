"""Protected receive-only AF_UNIX worker receipt transport (R2B candidate).

The owner supplies expected worker UID, authoritative pending Attempt,
and a separate systemd-provisioned signing key. The sender has no way to
provide its own identity, attempt, destination or policy to the caller.
This module has no sign operation, no network, no provider dispatch,
no credential retrieval and no mutation by itself.

A production vault must prove an approval was already durably consumed,
reserve OwnerHooks.begin() in the existing transaction BEFORE launching a
fixed-destination worker, and finish in a second transaction only after
the authenticated receipt. These steps are not yet installed/wired.
"""
from __future__ import annotations

import json
import socket
import stat

from approval_ledger import Refused
from operator_session import peer_from_socket
from worker_receipt_verifier import verify_receipt

MAX_WIRE=2048


def _unique(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:
            raise Refused('worker_receipt_duplicate_field')
        obj[k]=v
    return obj


def read_authenticated_receipt(conn, *, worker_uid:int, attempt, signing_key:bytes, now:int):
    """Receives exact one line + peer EOF, validates kernel uid and HMAC.

    The result is a sanitized enum, not a provider response or user string.
    Do not use worker_uid from the request, browser, IPC frame or receipt.
    """
    if type(worker_uid) is not int or worker_uid<=0:
        raise Refused('worker_identity_required')
    peer=peer_from_socket(conn)
    if peer.uid!=worker_uid:
        raise Refused('worker_peer_refused')
    conn.settimeout(2.0)
    raw=bytearray()
    try:
        while len(raw)<=MAX_WIRE:
            chunk=conn.recv(min(256,MAX_WIRE+1-len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
    except (OSError,TimeoutError):
        raise Refused('worker_frame_unavailable') from None
    if (not raw or len(raw)>MAX_WIRE or raw.count(b'\n')!=1
            or raw[-1]!=10 or b'\x00' in raw):
        raise Refused('worker_frame_refused')
    try:
        text=bytes(raw[:-1]).decode('ascii','strict')
        data=json.loads(text,object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(Refused('worker_json_refused')))
    except (UnicodeError,ValueError,TypeError,OverflowError):
        raise Refused('worker_json_refused') from None
    return verify_receipt(data,attempt,signing_key,now)
