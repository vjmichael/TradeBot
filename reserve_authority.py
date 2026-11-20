"""Public verification of PAPER reserve approvals without the signing seed."""
import os
import stat
import json
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from financial_ledger import canon, require, AccountingError

TRUST_ANCHOR = Path('/etc/maj-vaj/reserve-approval.pub')
DOMAIN = b'maj-vaj:PAPER:reserve:public:v1\0'


def public_key(seed):
    require(type(seed) is bytes and len(seed)==32,'invalid_approval_seed')
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)


def sign(seed,message):
    return Ed25519PrivateKey.from_private_bytes(seed).sign(DOMAIN+message).hex()


def check(public,message,signature):
    require(type(public) is bytes and len(public)==32 and type(signature) is str and len(signature)==128,'approval_attestation_missing')
    try:Ed25519PublicKey.from_public_bytes(public).verify(bytes.fromhex(signature),DOMAIN+message)
    except (ValueError,InvalidSignature) as exc:raise AccountingError('approval_attestation_invalid') from exc


def pinned_public(path=TRUST_ANCHOR):
    # Installation trust anchor, never generated from worker-writable state.
    path=Path(path);require(path.is_absolute(),'approval_anchor_path')
    for directory in path.parents:
        st=directory.lstat();require(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022,'approval_anchor_directory')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno());require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022 and st.st_size==32,'approval_anchor_file')
        return f.read(32)


def verify_active(db,instance,s,now,public):
    active=s['reserve']['active'];require(active is not None,'reinvestment_off')
    rows=db.execute("SELECT id,kind,body,previous FROM fin_events WHERE instance=? AND kind IN ('reserve_activate','reserve_adopt') AND json_extract(body,'$.payload.payload.version')=?",(instance,active['version'])).fetchall()
    require(len(rows)==1,'approval_receipt_missing')
    eid,kind,raw,head=rows[0];event=json.loads(raw);grant=event.get('approval') or {};payload=event['payload']['payload']
    require(payload==active and grant.get('head')==head and type(grant.get('expiry')) is int and type(event['recorded_at']) is int and event['recorded_at']<=min(grant['expiry'],now),'approval_receipt_scope')
    msg=canon(['maj-vaj:PAPER:reserve:v1',eid,instance,kind.removeprefix('reserve_'),payload,head,grant['expiry']]).encode()
    check(public,msg,grant.get('attestation'))
