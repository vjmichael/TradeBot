"""R2B no-network synthetic worker receipts, NEVER provider verification.

Distinct domain and worker label make synthetic evidence impossible to
misrepresent as a real Kraken adapter receipt. Result is ALWAYS
'provider_refused'. Signer code is used only inside the worker UID;
owner verification requires real SO_PEERCRED and a separately
root-provisioned receipt key. Never enable any positive permissions.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import re
import socket
from operator_session import peer_from_socket
from vault_owner_hooks import Attempt,Refused

DOMAIN=b'maj-vaj:r2b:offline-synthetic-worker:v1\x00'
WORKER='maj-vaj-synthetic-no-network-refusal-v1'
FIELDS=frozenset({'version','worker','role','generation','attempt_id',
    'policy','started','nonce','observed','result','mac'})
MAX=1600

def canonical(record):
    return DOMAIN+json.dumps({k:record[k] for k in FIELDS if k!='mac'},
        sort_keys=True,separators=(',',':'),ensure_ascii=True,
        allow_nan=False).encode('ascii')


def attempt_guard(attempt):
    if (type(attempt) is not Attempt or attempt.role!='kraken_read'
            or type(attempt.generation) is not str
            or re.fullmatch(r'[0-9a-f]{32}',attempt.generation) is None
            or type(attempt.attempt_id) is not str
            or re.fullmatch(r'[0-9a-f]{32}',attempt.attempt_id) is None
            or type(attempt.policy) is not str
            or re.fullmatch(r'[0-9a-f]{64}',attempt.policy) is None
            or type(attempt.started) is not int or not 0<attempt.started<2**63
            or type(attempt.nonce) is not int or not 0<attempt.nonce<2**63):
        raise Refused('synthetic_attempt_refused')


def sign_denial(attempt,signing_key,observed):
    attempt_guard(attempt)
    if (type(signing_key) is not bytes or len(signing_key)<32
            or type(observed) is not int or observed<attempt.started
            or observed>=attempt.started+300):
        raise Refused('synthetic_signing_unavailable')
    record={'version':1,'worker':WORKER,'role':attempt.role,
        'generation':attempt.generation,'attempt_id':attempt.attempt_id,
        'policy':attempt.policy,'started':attempt.started,
        'nonce':attempt.nonce,'observed':observed,
        'result':'provider_refused','mac':'0'*64}
    record['mac']=hmac.new(signing_key,canonical(record),hashlib.sha256).hexdigest()
    return record


def _unique(pairs):
    data={}
    for key,value in pairs:
        if key in data:raise Refused('synthetic_duplicate_field')
        data[key]=value
    return data


def one_frame(conn):
    data=bytearray()
    conn.settimeout(3)
    while len(data)<=MAX:
        chunk=conn.recv(min(256,MAX+1-len(data)))
        if not chunk:break
        data.extend(chunk)
    if (not data or len(data)>MAX or data[-1:]!=b'\n'
            or data.count(b'\n')!=1 or b'\x00' in data):
        raise Refused('synthetic_frame_refused')
    try:
        obj=json.loads(bytes(data[:-1]).decode('ascii','strict'),
                       object_pairs_hook=_unique)
    except (ValueError,UnicodeError,TypeError):
        raise Refused('synthetic_frame_refused') from None
    if type(obj) is not dict:
        raise Refused('synthetic_frame_refused')
    return obj


def verify_denial(receipt,attempt,signing_key,now):
    attempt_guard(attempt)
    if type(receipt) is not dict or set(receipt)!=FIELDS:
        raise Refused('synthetic_receipt_shape_refused')
    if (type(signing_key) is not bytes or len(signing_key)<32
            or type(now) is not int or not 0<now<2**63):
        raise Refused('synthetic_receipt_key_refused')
    for field in ('version','started','nonce','observed'):
        if type(receipt[field]) is not int:raise Refused('synthetic_receipt_field_refused')
    for field in ('worker','role','generation','attempt_id','policy','result','mac'):
        if type(receipt[field]) is not str:raise Refused('synthetic_receipt_field_refused')
    if (receipt['version']!=1 or receipt['worker']!=WORKER
            or receipt['role']!=attempt.role or receipt['generation']!=attempt.generation
            or receipt['attempt_id']!=attempt.attempt_id or receipt['policy']!=attempt.policy
            or receipt['started']!=attempt.started or receipt['nonce']!=attempt.nonce
            or receipt['result']!='provider_refused' or
            not attempt.started<=receipt['observed']<=now<
                attempt.started+300):
        raise Refused('synthetic_receipt_binding_refused')
    if re.fullmatch('[0-9a-f]{64}',receipt['mac']) is None:
        raise Refused('synthetic_receipt_mac_refused')
    expected=hmac.new(signing_key,canonical(receipt),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(receipt['mac'],expected):
        raise Refused('synthetic_receipt_mac_refused')
    return 'provider_refused'


def receive_verified(conn,attempt,signing_key,now,*,worker_uid):
    if type(worker_uid) is not int or worker_uid<=0:
        raise Refused('synthetic_worker_uid_refused')
    peer=peer_from_socket(conn)
    if peer.uid!=worker_uid:
        raise Refused('synthetic_worker_peer_refused')
    return verify_denial(one_frame(conn),attempt,signing_key,now)
