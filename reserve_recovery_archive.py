"""Independent authenticated archive for reviewed non-invocation mailboxes.

Retirement never writes an economic event. Native owner attests exact committed
resolution from a current signed snapshot, or an expired proof with a held
intent. Both owners retain immutable copies before removing pending files.
"""
import fcntl
import os
import secrets
import stat
import time
from contextlib import contextmanager
from financial_ledger import sha
from paper_execution import event_detail
from reserve_checkpoint import require
from reserve_ipc import canonical, strict, pinned_key
from reserve_no_effect import AGE, HUMAN_DOMAIN, _verify, _signed, verify_native
from reserve_native_handoff import TTL

# The signed snapshot may lag a still-committing approval. After this
# grace, even its oldest admissible extraction began after proof expiry.
ARCHIVE_AGE=AGE+TTL+1

DOMAIN=b'maj-vaj:PAPER:review-archive:v1\0'
LIMIT=32768


@contextmanager
def locked(proofbox,approvalbox):
    # Both writers use the same lock order. No GUI/network call inside locks.
    require(proofbox.owner!=approvalbox.owner and proofbox.group==approvalbox.group,'archive_roles')
    with proofbox._directory() as p,approvalbox._directory() as a:
        for fd,box in ((p,proofbox),(a,approvalbox)):
            require(os.fstat(fd).st_gid==box.group,'archive_group')
            fcntl.flock(fd,fcntl.LOCK_EX)
        yield p,a


def _name(proof):return 'review-'+sha(proof)+'.json'


def _read(box,fd,name):
    try:f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
    except FileNotFoundError:return None
    with os.fdopen(f,'rb') as stream:
        s=os.fstat(stream.fileno());require(stat.S_ISREG(s.st_mode) and s.st_uid==box.owner and s.st_gid==box.group and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o640 and 0<s.st_size<=LIMIT,'archive_custody')
        raw=stream.read(LIMIT+1);require(len(raw)==s.st_size,'archive_size')
    return strict(raw)


def _save(box,fd,name,packet):
    require(os.geteuid()==box.owner,'archive_owner')
    old=_read(box,fd,name)
    if old is not None:
        require(old==packet,'archive_conflict');return
    raw=canonical(packet);require(0<len(raw)<=LIMIT,'archive_size')
    temp='.archive-'+secrets.token_hex(16)
    f=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
    try:
        with os.fdopen(f,'wb') as out:
            out.write(raw);out.flush();os.fchown(out.fileno(),-1,box.group);os.fchmod(out.fileno(),0o640);os.fsync(out.fileno())
        # Serialized owner operations, refuse replacement even after restart.
        require(_read(box,fd,name) is None,'archive_conflict')
        os.replace(temp,name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
    finally:
        try:os.unlink(temp,dir_fd=fd)
        except FileNotFoundError:pass


def _review(native,human,workspace,proof,review):
    require(native!=human,'archive_independent_keys')
    b=verify_native(native,workspace,proof)
    if review is not None:
        require(type(review) is dict and set(review)=={'proof','approval'} and review['proof']==proof,'archive_review_scope')
        a=_verify(human,HUMAN_DOMAIN,review['approval'])
        require(set(a)=={'workspace','proof','expires'} and a['workspace']==workspace and a['proof']==sha(proof) and type(a['expires']) is int and b['at']<a['expires']<=b['at']+AGE,'archive_approval_scope')
    return b


def verify_archive(native,human,workspace,packet):
    b=_verify(native,DOMAIN,packet)
    require(set(b)=={'v','workspace','proof','review','status','at'} and b['v']==1 and b['workspace']==workspace and b['status'] in ('accounted','expired') and type(b['at']) is int,'archive_schema')
    p=_review(native,human,workspace,b['proof'],b['review'])
    require(b['at']>=p['at'],'archive_time')
    if b['status']=='accounted':require(b['review'] is not None,'archive_review_required')
    else:require(b['at']>=p['at']+ARCHIVE_AGE,'archive_not_expired')
    return b


def disposition(db,proof,review,native,human,workspace,now):
    b=_review(native,human,workspace,proof,review);intent=b['row']['id']
    row=db.execute('SELECT instance_id,state FROM gate_intents WHERE id=?',(intent,)).fetchone()
    require(row is not None and row[0]==b['row']['instance_id'],'archive_intent_scope')
    if row[1]=='NO_FILL':
        require(review is not None and event_detail(db,intent,'native_reviewed_no_effect')=={'receipt':sha(review),'proof':sha(proof)},'archive_resolution_mismatch')
        return 'accounted'
    require(row[1] in ('CLAIMED','UNKNOWN'),'archive_intent_changed')
    return 'expired' if now>=b['at']+ARCHIVE_AGE else None


def native_step(producer,inbox,store,relay_pin,native_gid,proofbox,approvalbox):
    from reserve_native_handoff import open_reviewed
    require(os.geteuid()==proofbox.owner==producer.uid and approvalbox.owner==producer.authority_uid,'archive_native_identity')
    require(len({pinned_key(x) for x in (relay_pin,producer.native_pin,producer.human_pin,producer.rebind_pin)})==4,'archive_independent_pins')
    n,h=pinned_key(producer.native_pin),pinned_key(producer.human_pin)
    with locked(proofbox,approvalbox) as (p,a):
        proof,review=proofbox._read(p),approvalbox._read(a)
        if proof is None:
            require(review is None,'archive_orphan_approval');return 'idle'
        name=_name(proof);packet=_read(proofbox,p,name)
        if packet is None:
            with open_reviewed(inbox,producer.workspace,store,relay_pin,native_uid=producer.uid,native_gid=native_gid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid,clock=producer.clock) as (db,_):
                now=int(producer.clock());status=disposition(db,proof,review,n,h,producer.workspace,now)
                if status is None:return 'review_pending'
                packet=_signed(producer._key(),DOMAIN,dict(v=1,workspace=producer.workspace,proof=proof,review=review,status=status,at=now))
            _save(proofbox,p,name,packet)
        b=verify_archive(n,h,producer.workspace,packet);require(b['proof']==proof,'archive_proof_changed')
        if review is not None:
            require(b['review']==review,'archive_review_changed');return 'authority_archive_pending'
        if b['review'] is not None:require(_read(approvalbox,a,name)==packet,'archive_authority_copy_missing')
        os.unlink('pending.json',dir_fd=p);os.fsync(p)
        return 'archived_'+b['status']


def authority_step(proofbox,approvalbox,native,human,workspace):
    require(os.geteuid()==approvalbox.owner,'archive_authority_identity')
    with locked(proofbox,approvalbox) as (p,a):
        proof,review=proofbox._read(p),approvalbox._read(a)
        if review is None:
            if proof is not None and _read(proofbox,p,_name(proof)) is not None:return 'awaiting_native_retirement'
            return 'idle'
        require(proof is not None and review.get('proof')==proof,'archive_pending_scope')
        name=_name(proof);packet=_read(proofbox,p,name)
        if packet is None:return 'awaiting_reconciliation'
        b=verify_archive(native,human,workspace,packet)
        require(b['proof']==proof and b['review']==review,'archive_review_changed')
        _save(approvalbox,a,name,packet)
        os.unlink('pending.json',dir_fd=a);os.fsync(a)
        return 'archived_'+b['status']


def publish_review(proofbox,approvalbox,proof,review,*,clock=time.time):
    # Hold proof lock through approvalbox.publish (same order as archival).
    with proofbox._directory() as p:
        fcntl.flock(p,fcntl.LOCK_EX)
        require(proofbox._read(p)==proof and review.get('proof')==proof and _read(proofbox,p,_name(proof)) is None,'archive_proof_changed')
        require(int(clock())<review['approval']['body']['expires'],'archive_approval_expired')
        approvalbox.publish(review)
