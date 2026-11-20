"""Local protected PAPER-only delivery; no credential, SQL or signing RPC."""
import os
import stat
import secrets
import time
from pathlib import Path
from reserve_checkpoint import require
from reserve_ipc import protected_dir, canonical, strict, MAX, pinned_key
from reserve_recovery import verify_packet, RecoveryClient, event_checkpoint
from reserve_protected import commit_packet, derive_event


class Mailbox:
    """Single pending signed operation per producer. Desk can only read it.

    Removal/replacement is authority-owned. An unresolved human packet blocks
    unattended cycles until reviewed; there is no automatic stale approval.
    """
    def __init__(self,directory,owner,kind):
        require(type(owner) is int and owner>0 and kind in ('human','native'),'mailbox_scope')
        self.directory,self.owner,self.kind=Path(directory),owner,kind

    def _kind(self,packet):
        event_checkpoint(packet['event'])
        require(packet['event']['kind'] in (('reserve_adopt','reserve_revoke') if self.kind=='human' else ('reserve_tick',)), 'mailbox_action_refused')

    def read(self):
        with protected_dir(self.directory,self.owner,0o755) as fd:
            try:f=os.open('pending.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
            except FileNotFoundError:return None
            with os.fdopen(f,'rb') as stream:
                s=os.fstat(stream.fileno())
                require(stat.S_ISREG(s.st_mode) and s.st_uid==self.owner and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o644 and 0<s.st_size<=MAX,'mailbox_custody')
                packet=strict(stream.read(MAX+1))
        self._kind(packet);return packet

    def publish(self,packet):
        require(os.geteuid()==self.owner,'mailbox_writer_refused');self._kind(packet)
        raw=canonical(packet);require(0<len(raw)<=MAX,'mailbox_size')
        with protected_dir(self.directory,self.owner,0o755) as fd:
            name='.pending-'+secrets.token_hex(16)
            out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
            try:
                with os.fdopen(out,'wb') as stream:
                    stream.write(raw);stream.flush();os.fchmod(stream.fileno(),0o644);os.fsync(stream.fileno())
                # Link without replacement: unresolved packets need explicit
                # owner review and removal rather than last-writer-wins.
                os.link(name,'pending.json',src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
                os.unlink(name,dir_fd=fd);os.fsync(fd)
            finally:
                try:os.unlink(name,dir_fd=fd)
                except FileNotFoundError:pass


def open_human_review(root, review, previous, event, evidence, mailbox):
    """Bind native Tk to the authority-owned delivery callback, not desk writes."""
    from reserve_exact_review import ExactReviewWindow
    require(mailbox.kind=='human' and mailbox.owner==os.geteuid(),'approval_delivery_identity')
    require(mailbox.read() is None,'unresolved_human_delivery')
    view=review.begin(previous,event,evidence)
    return ExactReviewWindow(root,review,view,mailbox.publish)


def human_revalidator(transaction, store, *, clock=time.time):
    """Authority-local snapshot verification. Transaction must refer to an
    independently transferred current consistent snapshot; no desk RPC input.
    """
    from financial_ledger import projection_values
    def check(previous,event,evidence):
        with transaction() as db:
            if store.verify(db)!=previous:return False
            return event==derive_event(db,previous,event['instance'],event['kind'].removeprefix('reserve_'),event['id'],event['body']['recorded_at'],int(clock()),projection_values)
    return check


def schedule_once(transaction,client,human,native,human_public,native_public,*,clock=time.time):
    """Process at most one operation, prioritizing a pending human choice.
    A matching already committed receipt is nonmutating; malformed, stale, or
    conflicting human packets stop the cycle rather than falling through.
    """
    from financial_ledger import replay
    for mailbox in (human,native):
        packet=mailbox.read()
        if packet is None:continue
        # Historical validation is permitted only to classify an already
        # witnessed exact event, not to advance an expired permit.
        _,cp=verify_packet(human_public,client.workspace,packet,0,historical=True,native_public=native_public)
        with transaction() as db:
            witness=client.verify_transaction(db);replay(db)
            if cp.seq<=witness.seq:
                row=db.execute('SELECT digest,kind FROM fin_events WHERE seq=?',(cp.seq,)).fetchone()
                require(row is not None and row[0]==cp.digest and row[1]==packet['event']['kind'],'old_delivery_conflict')
                continue
        return commit_packet(transaction,client,packet,human_public,native_public,clock=clock)
    # Even no work requires witness verification (no optimistic OFF).
    with transaction() as db:client.verify_transaction(db);replay(db)
    return {'status':'no_pending_delivery'}
