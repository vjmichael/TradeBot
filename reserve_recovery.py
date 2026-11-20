"""Recover an exact, already-authorized PAPER reserve event; never re-evaluate it.

Candidate protocol. The caller must supply complete financial replay and hold
BEGIN IMMEDIATE for the entire operation. No automatic authority rewind.
"""
import argparse
import hashlib
import json
import os
import pwd
import re
import secrets
import socket
import stat
import time
from dataclasses import asdict
from pathlib import Path

from reserve_checkpoint import AuthorityStore, Checkpoint, require, verify_prefix, reserve_events
from reserve_ipc import (AuthorityService, AuthorityClient, canonical, strict, checkpoint,
                         pinned_key, protected_dir, peer, receive, send, verify_permit, TIMEOUT, MAX)


def event_checkpoint(event):
    require(type(event) is dict and set(event) == {'seq','id','instance','kind','body','previous','digest'}, 'event_schema')
    require(event['kind'] in ('reserve_adopt','reserve_revoke','reserve_tick'), 'event_kind')
    for field in ('id','instance'):
        require(type(event[field]) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',event[field]), 'event_identity')
    require(type(event['previous']) is str and re.fullmatch('[0-9a-f]{64}',event['previous']), 'event_previous')
    require(type(event['body']) is dict, 'event_body')
    cp = Checkpoint(event['seq'],event['digest'])
    expected = hashlib.sha256(canonical([cp.seq,event['id'],event['instance'],event['kind'],event['body'],event['previous']])).hexdigest()
    require(cp.seq > 0 and cp.digest == expected, 'event_digest')
    return cp


def verify_packet(public,workspace,packet,now,*,historical=False):
    require(type(packet) is dict and set(packet)=={'event','permit'}, 'recovery_packet_schema')
    cp=event_checkpoint(packet['event'])
    # Historical validation is only for a packet retrieved for the CURRENT
    # already-advanced witness. Never use historical validation to advance.
    permit=packet['permit']
    at=permit['body']['expires'] if historical else now
    prev,nxt=verify_permit(public,workspace,permit,at)
    require(nxt==cp,'permit_event_mismatch')
    require(len(canonical(packet))<=MAX-512,'recovery_packet_too_large')
    return prev,nxt


def name_for(cp):
    return f'recovery-{cp.seq}-{cp.digest}.json'


class RecoveryStore:
    def __init__(self,store,workspace,pin):
        self.store,self.workspace,self.pin=store,workspace,Path(pin)

    def _read(self,fd,cp):
        f=os.open(name_for(cp),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
        with os.fdopen(f,'rb') as stream:
            s=os.fstat(stream.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_uid==self.store.uid and s.st_nlink==1 and
                    stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=MAX,'recovery_record_custody')
            packet=strict(stream.read(MAX+1))
        require(verify_packet(pinned_key(self.pin),self.workspace,packet,0,historical=True)[1]==cp,'recovery_record_identity')
        return packet

    def _persist(self,fd,cp,packet):
        # The record is durable before the independent witness can advance.
        # Immutable records are retained; never garbage-collect recovery data
        # merely on the desk's untrusted claim of successful commit.
        try:
            old=self._read(fd,cp)
        except FileNotFoundError:
            temp='.recovery-'+secrets.token_hex(16)
            f=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
            try:
                with os.fdopen(f,'wb') as stream:
                    stream.write(canonical(packet));stream.flush();os.fsync(stream.fileno())
                os.link(temp,name_for(cp),src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
                os.unlink(temp,dir_fd=fd);os.fsync(fd)
            finally:
                try:os.unlink(temp,dir_fd=fd)
                except FileNotFoundError:pass
        else:
            require(old==packet,'recovery_record_conflict')

    def advance(self,packet,now,*,before_publish=lambda:None):
        self.store._writer()
        prev,nxt=verify_packet(pinned_key(self.pin),self.workspace,packet,now)
        with self.store._directory() as fd,self.store._lock(fd):
            require(self.store._read(fd)==prev,'checkpoint_compare_and_swap')
            self._persist(fd,nxt,packet)
            before_publish()  # qualification crash-boundary hook, not exposed by IPC
            self.store._publish(fd,nxt)
        return nxt

    def current_packet(self):
        self.store._writer()
        with self.store._directory() as fd,self.store._lock(fd):
            cp=self.store._read(fd)
            require(cp.seq>0,'no_recovery_record')
            return cp,self._read(fd,cp)


class RecoverableAuthorityService(AuthorityService):
    """Strict new lane: legacy digest-only advance is NOT accepted."""
    def dispatch(self,request):
        require(set(request)=={'v','nonce','op','workspace','permit'} and type(request['v']) is int and request['v']==1 and
                type(request['nonce']) is str and re.fullmatch('[0-9a-f]{32}',request['nonce']) and
                request['workspace']==self.workspace,'request_identity')
        op=request['op']; payload=request['permit']; journal=RecoveryStore(self.store,self.workspace,self.pin)
        if op=='checkpoint':return super().dispatch(request)
        response={'v':1,'nonce':request['nonce'],'workspace':self.workspace}
        if op=='advance_recoverable':
            cp=journal.advance(payload,int(time.time()));return response|{'checkpoint':asdict(cp)}
        if op=='recovery':
            require(payload is None,'unexpected_permit');cp,packet=journal.current_packet()
            return response|{'checkpoint':asdict(cp),'packet':packet}
        raise ValueError('unknown_operation')


class RecoveryClient(AuthorityClient):
    def recovery_packet(self):
        nonce=secrets.token_hex(16)
        with protected_dir(self.directory,self.uid,0o755) as fd:
            s=os.stat('authority.sock',dir_fd=fd,follow_symlinks=False)
            require(stat.S_ISSOCK(s.st_mode) and s.st_uid==self.uid,'socket_custody')
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
                conn.settimeout(TIMEOUT);conn.connect(str(self.directory/'authority.sock'))
                require(peer(conn)==self.uid,'server_identity')
                send(conn,{'v':1,'nonce':nonce,'op':'recovery','workspace':self.workspace,'permit':None});reply=receive(conn)
        require(set(reply)=={'v','nonce','workspace','checkpoint','packet'} and type(reply['v']) is int and reply['v']==1 and
                reply['nonce']==nonce and reply['workspace']==self.workspace,'recovery_reply')
        return checkpoint(reply['checkpoint']),reply['packet']

    def advance_event(self,db,expected,packet,public,replay,*,clock=time.time):
        prev,nxt=verify_packet(public,self.workspace,packet,int(clock()))
        require(prev==expected,'permit_predecessor')
        verify_prefix(db,expected)
        require(reserve_events(db,expected.seq)==[nxt],'exact_next_event_required')
        require(replay(db)[2:4]==(nxt.seq,nxt.digest),'event_not_exact_head')
        require(self.request('advance_recoverable',packet)==nxt,'checkpoint_response_mismatch')
        return nxt

    def recover_event(self,db,public,replay,reconcile):
        """Explicit recovery only. Restore precisely the authorized event, never
        recompute a release against new financial evidence. Commit is caller-owned.
        """
        require(db.in_transaction,'serialized_desk_transaction_required')
        cp,packet=self.recovery_packet();prev,nxt=verify_packet(public,self.workspace,packet,0,historical=True)
        require(cp==nxt,'recovery_checkpoint_mismatch')
        # Full replay of the local journal precedes any mutation.
        _,_,count,head=replay(db);event=packet['event']
        if count>=nxt.seq:
            verify_prefix(db,nxt)
            require(not reserve_events(db,nxt.seq),'unwitnessed_reserve_event')
            require(self.request('checkpoint')==nxt,'checkpoint_changed')
            return 'already_committed'
        require((count,head)==(nxt.seq-1,event['previous']),'recovery_predecessor_changed')
        verify_prefix(db,prev);require(not reserve_events(db,prev.seq),'unwitnessed_reserve_event')
        # A matching journal alone does not prove current Gate/native state.
        # An independently trusted recovery validator must reconcile it.
        require(reconcile(db,packet) is True,'recovery_reconciliation_required')
        require(replay(db)[2:4]==(count,head),'recovery_context_changed')
        db.execute('SAVEPOINT reserve_recovery')
        try:
            db.execute('INSERT INTO fin_events VALUES(?,?,?,?,?,?,?)',(event['seq'],event['id'],event['instance'],event['kind'],canonical(event['body']).decode(),event['previous'],event['digest']))
            db.execute('UPDATE fin_head SET count=?,digest=? WHERE id=1',(nxt.seq,nxt.digest))
            require(replay(db)[2:4]==(nxt.seq,nxt.digest),'recovery_replay_mismatch')
            require(self.request('checkpoint')==nxt,'checkpoint_changed')
        except BaseException:
            db.execute('ROLLBACK TO reserve_recovery');db.execute('RELEASE reserve_recovery');raise
        db.execute('RELEASE reserve_recovery')
        return 'recovered'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('socket-dir','witness','authority-user','desk-user','workspace','pin'):p.add_argument('--'+name,required=True)
    a=p.parse_args();authority=pwd.getpwnam(a.authority_user).pw_uid;desk=pwd.getpwnam(a.desk_user).pw_uid
    store=AuthorityStore(a.witness,authority_uid=authority,desk_uid=desk)
    RecoverableAuthorityService(store,a.socket_dir,authority_uid=authority,desk_uid=desk,workspace=a.workspace,pin=a.pin).serve()

if __name__=='__main__':main()
