"""Root-reviewed signed cold SQLite handoff; no desk-accessible signing service.

A live broker must acquire a consistent current snapshot and validate native
execution custody before invoking this code. Root signature is transport
integrity, not proof of funds or authority to execute an order.
"""
import hashlib
import os
import re
import secrets
import stat
import time
from contextlib import closing, contextmanager
from dataclasses import asdict
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from reserve_checkpoint import require, verify_prefix, reserve_events
from reserve_ipc import canonical, strict, scope, checkpoint, pinned_key, protected_dir
from reserve_native_migration import check_sqlite, read_export, MAX_DB

DOMAIN=b'maj-vaj:PAPER:reviewed-snapshot:v1\0'
TTL=30
MAX_PACKET=MAX_DB+8192


def state_identity(db):
    from financial_ledger import replay, execution_anchor
    _,_,count,head=replay(db)
    boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
    return {'count':count,'head':head,'boot':boot,'anchor':execution_anchor(db)}


def _identity(db, previous):
    require(db.in_transaction,'snapshot_transaction_required')
    verify_prefix(db,previous)
    require(not reserve_events(db,previous.seq),'snapshot_unwitnessed_reserve')
    return state_identity(db)


def _body(workspace, previous, identity, raw, issued, native_uid, native_gid):
    return {'v':1,'workspace':scope(workspace),'native_uid':native_uid,'native_gid':native_gid,'previous':asdict(previous),'identity':identity,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'issued':issued,'expires':issued+TTL}


def issue_reviewed(export_dir, reviewed_sha, directory, workspace, store, seed, *, native_uid, native_gid, clock=time.time):
    """Root-only bounded, atomic publication from an independently reviewed cold
    export. The caller owns snapshot freshness and source provenance; this is
    not a general-purpose signing service, and does not grant reserve authority.
    """
    require(os.geteuid()==0 and type(seed) is bytes and len(seed)==32 and type(native_uid) is int and native_uid>0 and type(native_gid) is int and native_gid>0,'snapshot_relay_identity')
    raw=read_export(export_dir,'desk.sqlite3')
    require(hashlib.sha256(raw).hexdigest()==reviewed_sha,'snapshot_review_changed')
    with closing(check_sqlite(raw)) as db:
        db.execute('BEGIN');previous=store.read();identity=_identity(db,previous)
    issued=int(clock());body=_body(workspace,previous,identity,raw,issued,native_uid,native_gid)
    envelope=canonical({'body':body,'signature':Ed25519PrivateKey.from_private_bytes(seed).sign(DOMAIN+canonical(body)).hex()})
    require(len(envelope)<=8192,'snapshot_header_size')
    packet=len(envelope).to_bytes(4,'big')+envelope+raw
    with protected_dir(directory,0,0o750) as fd:
        require(os.fstat(fd).st_gid==native_gid,'snapshot_directory_group')
        name='.snapshot-'+secrets.token_hex(16)
        f=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
        try:
            with os.fdopen(f,'wb') as out:out.write(packet);out.flush();os.fchown(out.fileno(),0,native_gid);os.fchmod(out.fileno(),0o640);os.fsync(out.fileno())
            os.replace(name,'snapshot.bin',src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
        finally:
            try:os.unlink(name,dir_fd=fd)
            except FileNotFoundError:pass
    return body


@contextmanager
def open_reviewed(directory, workspace, store, pin, *, native_uid, native_gid, desk_uid, authority_uid, clock=time.time):
    require(os.geteuid()==native_uid and len({native_uid,desk_uid,authority_uid})==3 and all(type(i) is int and i>0 for i in (native_uid,desk_uid,authority_uid)),'snapshot_reader_identity')
    require(type(native_gid) is int and native_gid>0 and native_gid in {os.getegid(),*os.getgroups()},'snapshot_reader_group')
    with protected_dir(directory,0,0o750) as fd:
        require(os.fstat(fd).st_gid==native_gid,'snapshot_directory_group')
        f=os.open('snapshot.bin',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
        with os.fdopen(f,'rb') as stream:
            st=os.fstat(stream.fileno())
            require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_gid==native_gid and st.st_nlink==1 and stat.S_IMODE(st.st_mode)==0o640 and 4<st.st_size<=MAX_PACKET+4,'snapshot_file_custody')
            packet=stream.read(MAX_PACKET+5)
            require(len(packet)==st.st_size,'snapshot_file_size')
    size=int.from_bytes(packet[:4],'big');require(0<size<=8192 and len(packet)>size+4,'snapshot_frame')
    env=strict(packet[4:4+size]);raw=packet[4+size:]
    require(len(raw)<=MAX_DB and set(env)=={'body','signature'} and type(env['signature']) is str and re.fullmatch('[0-9a-f]{128}',env['signature']),'snapshot_envelope')
    body=env['body'];require(type(body) is dict and set(body)=={'v','workspace','native_uid','native_gid','previous','identity','size','sha256','issued','expires'},'snapshot_body')
    try:Ed25519PublicKey.from_public_bytes(pinned_key(pin)).verify(bytes.fromhex(env['signature']),DOMAIN+canonical(body))
    except (InvalidSignature,ValueError) as exc:raise ValueError('snapshot_signature') from exc
    now=int(clock());issued=body['issued'];require(type(issued) is int and 0<=now-issued<=TTL,'snapshot_stale')
    require(body['workspace']==workspace and body['native_uid']==native_uid and body['native_gid']==native_gid and body['size']==len(raw) and body['sha256']==hashlib.sha256(raw).hexdigest(),'snapshot_scope_or_content_changed')
    previous=checkpoint(body['previous']);require(store.read()==previous,'snapshot_checkpoint_changed')
    with closing(check_sqlite(raw)) as db:
        db.execute('BEGIN');identity=_identity(db,previous)
        require(body==_body(workspace,previous,identity,raw,issued,native_uid,native_gid),'snapshot_scope_or_content_changed')
        yield db,previous
        require(store.read()==previous and 0<=int(clock())-issued<=TTL,'snapshot_changed_during_review')
