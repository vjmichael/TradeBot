"""R2B private vault owner -> installed broker and synthetic worker client.

All entrypoints are PRIVATE to maj-vaj-paper-vault.service. This module
must NEVER be exposed in dashboard, operator JSON, arbitrary URLs,
task execution or normal credential-status APIs. Owner must hold a
current generation/policy committed by ManagedPaperVault.
No real provider request or positive permissions result is possible.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import stat
import time

from approval_ledger import Context,Refused
from synthetic_worker_protocol import receive_verified
from vault_owner_hooks import Attempt
from owner_execution_bridge import begin_once

BROKER='/run/maj-vaj-r2b-approval/approval.sock'
WORKER='/run/maj-vaj-r2b-synthetic-worker/result.sock'
NAME='r2b-worker-signing-key'


def _private_socket(path,principal):
    peer=pwd.getpwnam(principal)
    if peer.pw_uid<=0:raise Refused('expected_kernel_peer_unavailable')
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        s.settimeout(5)
        s.connect(path)
        raw=s.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
        pid,uid,gid=struct.unpack('3i',raw)
        if pid<=1 or uid!=peer.pw_uid or gid!=peer.pw_gid:
            raise Refused('kernel_peer_mismatch')
        return s
    except BaseException:
        s.close()
        raise


def _send(s,record):
    data=json.dumps(record,sort_keys=True,separators=(',',':')).encode('ascii')+b'\n'
    if len(data)>1024:raise Refused('owner_ipc_frame_refused')
    s.sendall(data)
    s.shutdown(socket.SHUT_WR)


def _reply(s):
    data=bytearray()
    while len(data)<=1024:
        chunk=s.recv(min(128,1025-len(data)))
        if not chunk:break
        data.extend(chunk)
    if not data or len(data)>1024 or data[-1:]!=b'\n' or data.count(b'\n')!=1:
        raise Refused('owner_ipc_frame_refused')
    try:return json.loads(data[:-1].decode('ascii','strict'))
    except (ValueError,UnicodeError,TypeError):
        raise Refused('owner_ipc_frame_refused') from None


def broker_request(operation,request_id,context):
    if operation not in ('prepare','consume','cancel'):
        raise Refused('owner_operation_refused')
    if type(context) is not Context:
        raise Refused('owner_context_refused')
    fields=json.loads(context.canonical())
    if operation=='prepare':
        request={'op':'prepare','context':fields}
    elif operation=='consume':
        if type(request_id) is not str or len(request_id)!=32:
            raise Refused('owner_request_id_refused')
        request={'op':'consume','request_id':request_id,'context':fields}
    else:
        if type(request_id) is not str or len(request_id)!=32:
            raise Refused('owner_request_id_refused')
        request={'op':'cancel','request_id':request_id}
    with _private_socket(BROKER,'maj-vaj-approval') as s:
        _send(s,request)
        outcome=_reply(s)
    if type(outcome) is not dict or outcome.get('ok') is not True:
        raise Refused('broker_refused')
    if operation=='prepare':
        if set(outcome)!={'ok','request_id'}:
            raise Refused('broker_response_refused')
        return outcome['request_id']
    if outcome!={'ok':True}:
        raise Refused('broker_response_refused')
    return True


def read_owner_receipt_key():
    """Fixed systemd credential mount only, never caller-supplied key."""
    base=Path('/run/credentials/maj-vaj-paper-vault.service')
    if os.environ.get('CREDENTIALS_DIRECTORY')!=str(base):
        raise Refused('owner_worker_key_custody_refused')
    st=base.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0,os.geteuid())
            or stat.S_IMODE(st.st_mode) not in (0o500,0o550)):
        raise Refused('owner_worker_key_custody_refused')
    fd=os.open(base/NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid not in (0,os.geteuid())
                or stat.S_IMODE(st.st_mode) not in (0o400,0o440)
                or st.st_nlink!=1 or st.st_size!=32):
            raise Refused('owner_worker_key_custody_refused')
        key=f.read(33)
    if len(key)!=32:raise Refused('owner_worker_key_custody_refused')
    return key


def reserve_after_approval(managed,request_id,context,now):
    """Only the authoritative vault process may invoke this function."""
    return begin_once(managed,context,now=now,
         consume=lambda ctx:broker_request('consume',request_id,ctx))


def finish_fixed_synthetic(managed,attempt:Attempt,*,now):
    """One authenticated negative receipt; no provider network or green status."""
    from managed_paper_vault import ManagedPaperVault,_require_owner
    _require_owner()
    if type(managed) is not ManagedPaperVault or type(attempt) is not Attempt:
        raise Refused('owner_attempt_refused')
    if type(now) is not int or now<=0:
        raise Refused('owner_clock_refused')
    worker_uid=pwd.getpwnam('maj-vaj-r2b-worker').pw_uid
    key=read_owner_receipt_key()
    request={'op':'fixed_synthetic_denial','attempt':dict(
        role=attempt.role,generation=attempt.generation,attempt_id=attempt.attempt_id,
        policy=attempt.policy,started=attempt.started,nonce=attempt.nonce)}
    with _private_socket(WORKER,'maj-vaj-r2b-worker') as s:
        _send(s,request)
        result=receive_verified(s,attempt,key,now,worker_uid=worker_uid)
    if result!='provider_refused':
        raise Refused('synthetic_result_not_negative')
    with managed._vault.transaction() as db:
        owner=managed._owner(db)
        managed._consistent(db,owner)
        owner.finish(attempt,managed._sealed(db,attempt.role),result,now)
    return 'provider_refused'
