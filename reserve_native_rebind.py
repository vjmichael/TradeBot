"""Offline PAPER workspace rebinding, preserving the original provision receipt.

Root signs an immutable reviewed binding only for a stopped, pristine workspace.
This is not a live execution broker or authority to start a worker. The native
principal checks the new binding and original journal prefix on every use.
"""
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from contextlib import closing, contextmanager
from dataclasses import asdict
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from reserve_checkpoint import require, verify_prefix, reserve_events
from reserve_ipc import canonical, strict, protected_dir, pinned_key, checkpoint
from reserve_native_migration import read_export, digest, check_sqlite, pristine_native, pristine_ledger, MAX_DB

DOMAIN=b'maj-vaj:PAPER:native-rebind:v1\0'


def read_owned(fd, name, uid, limit):
    raw=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
    with os.fdopen(raw,'rb') as f:
        st=os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid==uid and st.st_nlink==1 and stat.S_IMODE(st.st_mode)==0o600 and 0<st.st_size<=limit,'native_file_custody')
        data=f.read(limit+1);require(len(data)==st.st_size,'native_file_changed')
        return data


def _child(fd,name,uid):
    new=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
    s=os.fstat(new)
    if s.st_uid!=uid or stat.S_IMODE(s.st_mode)!=0o700:
        os.close(new);raise ValueError('native_directory_custody')
    return new


@contextmanager
def workspace(data,instance,uid,*,lease=False):
    require(type(instance) is str and re.fullmatch('[0-9a-f]{32}',instance),'native_instance_id')
    with protected_dir(data,uid,0o700) as root:
        paper=_child(root,'paper',uid)
        try:work=_child(paper,instance,uid)
        finally:os.close(paper)
        try:
            if not lease:yield root,work;return
            flags=os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK
            try:lock=os.open('paper-worker.lock',flags|os.O_CREAT|os.O_EXCL,0o600,dir_fd=work);os.fchown(lock,uid,-1)
            except FileExistsError:lock=os.open('paper-worker.lock',flags,dir_fd=work)
            try:
                s=os.fstat(lock);require(stat.S_ISREG(s.st_mode) and s.st_uid==uid and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600,'native_lease_custody')
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);yield root,work
            finally:os.close(lock)
        finally:os.close(work)


def binding(data,work,expected,uid):
    st=os.stat('paper.sqlite',dir_fd=work,follow_symlinks=False)
    require(stat.S_ISREG(st.st_mode) and st.st_uid==uid and st.st_nlink==1 and stat.S_IMODE(st.st_mode)==0o600,'native_database_custody')
    key=read_owned(work,'receipt.key',uid,32);require(len(key)==32,'native_receipt_key_size')
    bind=digest(canonical([expected['instance'],str(Path(data)/'paper'/expected['instance']/'paper.sqlite'),st.st_dev,st.st_ino]))
    body=expected|{'binding':bind,'version':1}
    mac=hmac.new(key,canonical(['maj-vaj:PAPER:R2','workspace-provision-v1',body]),hashlib.sha256).hexdigest()
    proof=strict(read_owned(work,'provision.json',uid,8192))
    require(set(proof)=={'body','mac'} and proof['body']==body and type(proof['mac']) is str and hmac.compare_digest(proof['mac'],mac),'native_provision_manifest')
    return {'binding':bind,'key_sha':digest(canonical(key.hex()))}


def _record(directory,instance):
    with protected_dir(directory,0,0o755) as fd:
        f=os.open(instance+'.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
        with os.fdopen(f,'rb') as stream:
            s=os.fstat(stream.fileno());require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o644 and 0<s.st_size<=8192,'rebind_record_custody')
            return strict(stream.read(8193))


def issue(export_dir,data,directory,review,store,seed,*,native_uid,desk_uid,authority_uid):
    require(os.geteuid()==0 and len({native_uid,desk_uid,authority_uid})==3 and all(type(i) is int and i>0 for i in (native_uid,desk_uid,authority_uid)),'rebind_principals')
    require(type(seed) is bytes and len(seed)==32,'rebind_signing_key')
    require(type(review) is dict and set(review)=={'scope','native_sha256','desk_sha256'},'rebind_review')
    expected=review['scope'];require(type(expected) is dict and set(expected)=={'instance','pair','principal_cents','mode'} and expected['mode']=='PAPER','rebind_scope')
    instance=expected['instance'];native=read_export(export_dir,'paper.sqlite');desk=read_export(export_dir,'desk.sqlite3')
    require(digest(native)==review['native_sha256'] and digest(desk)==review['desk_sha256'],'rebind_export_changed')
    pristine_native(native)
    with closing(check_sqlite(desk)) as db:
        db.execute('BEGIN');prior=pristine_ledger(db,expected);cp=store.read();verify_prefix(db,cp)
        require(not reserve_events(db,cp.seq),'rebind_unwitnessed_reserve')
    with workspace(data,instance,native_uid,lease=True) as (root,work):
        require(not any(os.path.lexists(Path(data)/'paper'/instance/('paper.sqlite'+suffix)) for suffix in ('-wal','-shm','-journal')),'rebind_cold_workspace_required')
        require(digest(read_owned(work,'paper.sqlite',native_uid,MAX_DB))==review['native_sha256'],'rebind_native_changed')
        new=binding(data,work,expected,native_uid)
        staged=strict(read_owned(root,'migration-candidate.json',native_uid,8192))
        require(staged=={'state':'staged_not_activated','scope':expected,**new,'review':review,'prior':prior},'rebind_stage_changed')
        require(new['key_sha']!=prior['old_provision']['key_sha'] and new['binding']!=prior['old_provision']['binding'],'rebind_new_identity_required')
        body={'v':1,'scope':expected,'native_uid':native_uid,'desk_uid':desk_uid,'authority_uid':authority_uid,'prior':prior,'new':new,'review':review,'checkpoint':asdict(cp)}
        packet={'body':body,'signature':Ed25519PrivateKey.from_private_bytes(seed).sign(DOMAIN+canonical(body)).hex()}
        # Immutable per-instance receipt, no silent overwrite or re-enrollment.
        with protected_dir(directory,0,0o755) as fd:
            temp='.rebind-'+secrets.token_hex(16)
            f=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
            try:
                with os.fdopen(f,'wb') as out:out.write(canonical(packet));out.flush();os.fchmod(out.fileno(),0o644);os.fsync(out.fileno())
                os.link(temp,instance+'.json',src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False);os.unlink(temp,dir_fd=fd);os.fsync(fd)
            finally:
                try:os.unlink(temp,dir_fd=fd)
                except FileNotFoundError:pass
    return body


def verified_identity(directory,pin,instance,*,native_uid,desk_uid,authority_uid):
    """Verify immutable root-issued identity, without authorizing any action."""
    require(os.geteuid() in (native_uid,desk_uid,authority_uid) and len({native_uid,desk_uid,authority_uid})==3 and all(type(i) is int and i>0 for i in (native_uid,desk_uid,authority_uid)),'rebind_reader_identity')
    require(type(instance) is str and re.fullmatch('[0-9a-f]{32}',instance),'rebind_instance')
    packet=_record(directory,instance);require(set(packet)=={'body','signature'} and type(packet['signature']) is str and re.fullmatch('[0-9a-f]{128}',packet['signature']),'rebind_envelope')
    b=packet['body'];require(type(b) is dict and set(b)=={'v','scope','native_uid','desk_uid','authority_uid','prior','new','review','checkpoint'},'rebind_receipt')
    try:Ed25519PublicKey.from_public_bytes(pinned_key(pin)).verify(bytes.fromhex(packet['signature']),DOMAIN+canonical(b))
    except (InvalidSignature,ValueError) as exc:raise ValueError('rebind_signature') from exc
    require(b['v']==1 and (b['native_uid'],b['desk_uid'],b['authority_uid'])==(native_uid,desk_uid,authority_uid),'rebind_identity_changed')
    expected=b['scope']
    require(type(expected) is dict and set(expected)=={'instance','pair','principal_cents','mode'} and expected['instance']==instance and expected['mode']=='PAPER','rebind_scope_changed')
    return b


def verify_record(db,directory,pin,instance,*,native_uid,desk_uid,authority_uid):
    """Verify public root-issued rebinding and original desk journal prefix."""
    require(db.in_transaction,'rebind_transaction_required')
    b=verified_identity(directory,pin,instance,native_uid=native_uid,desk_uid=desk_uid,authority_uid=authority_uid)
    expected=b['scope'];row=db.execute('SELECT pair,virtual_cents,mode FROM instances WHERE id=?',(instance,)).fetchone()
    require(expected['instance']==instance and expected['mode']=='PAPER' and row is not None and tuple(row)==(expected['pair'],expected['principal_cents'],'PAPER'),'rebind_scope_changed')
    old=b['prior']['old_provision'];rows=db.execute("SELECT detail FROM gate_events WHERE intent_id=? AND action='workspace_provisioned'",(instance,)).fetchall()
    require(len(rows)==1 and json.loads(rows[0][0])==old,'rebind_original_receipt_changed')
    from financial_ledger import replay
    _,_,count,_=replay(db);n=b['prior']['ledger_count'];h=b['prior']['ledger_head']
    row=db.execute('SELECT digest FROM fin_events WHERE seq=?',(n,)).fetchone()
    require(type(n) is int and 0<n<=count and row is not None and tuple(row)==(h,),'rebind_original_prefix_missing')
    verify_prefix(db,checkpoint(b['checkpoint']))
    return b


def verify(db,data,directory,pin,instance,*,native_uid,desk_uid,authority_uid):
    """Native-owned caller holding the native lease and consistent ledger view."""
    require(os.geteuid()==native_uid,'rebind_reader_identity')
    b=verify_record(db,directory,pin,instance,native_uid=native_uid,desk_uid=desk_uid,authority_uid=authority_uid)
    with workspace(data,instance,native_uid) as (_,work):require(binding(data,work,b['scope'],native_uid)==b['new'],'rebind_native_identity_changed')
    return b['new']
