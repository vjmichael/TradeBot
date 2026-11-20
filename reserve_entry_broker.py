"""Staged synchronous independent PAPER entry; no external order or RUN API."""
import array
import fcntl
import os
import re
import secrets
import select
import socket
import stat
import time
from contextlib import closing,contextmanager
from pathlib import Path
from financial_ledger import ident,sha,amount
from paper_execution import append_event,event_detail
from reserve_checkpoint import require
from reserve_entry_fence import fence,verify_received
from reserve_ipc import canonical,peer,protected_dir,receive,send,strict

FIELDS=('id','instance_id','pair','entry','stop','target','notional','reserved','expires')


def offer(row):
    r={k:row[k] for k in FIELDS}
    for k in ('id','instance_id'):ident(r[k])
    require(r['pair'] in ('BTC/USD','ETH/USD','SOL/USD'),'entry_scope')
    for k in FIELDS[3:]:amount(r[k],True)
    require(r['stop']<r['entry']<r['target'] and r['reserved']>=r['notional'],'entry_amounts')
    return r


def transfer(conn,fd):
    require(conn.sendmsg([b'F'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))])==1,'entry_fence_send')


def accept_fence(conn,path):
    data,anc,flags,_=conn.recvmsg(1,socket.CMSG_SPACE(array.array('i').itemsize),socket.MSG_CMSG_CLOEXEC)
    fds=[]
    try:
        for level,kind,raw in anc:
            require(level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS,'entry_fence_message')
            a=array.array('i');a.frombytes(raw);fds.extend(a)
        require(data==b'F' and not flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC) and len(fds)==1,'entry_fence_message')
        verify_received(fds[0],path)
        return fds.pop()
    finally:
        for fd in fds:os.close(fd)


def attempt(work,row,nonce,binding):
    """Exclusive native lease is held by caller. Never replace a prior attempt."""
    body=canonical(dict(row=row,nonce=nonce,binding=binding));name='entry-'+sha([binding,row['id']])+'.json'
    d=os.open(work,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(d);require(s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,'entry_workspace')
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=d)
        with os.fdopen(fd,'wb') as out:out.write(body);out.flush();os.fsync(out.fileno())
        os.fsync(d)
    finally:os.close(d)
    return sha([row,nonce,binding])


def serve_request(conn,transaction,gate,bindings,store,lock,*,native_uid,desk_uid,clock=time.time):
    """Desk UID only. transaction is the internal unfenced desk transaction;
    the outer shared-description fence covers the entire protocol including ORM.
    """
    require(os.geteuid()==desk_uid and peer(conn)==native_uid and desk_uid!=native_uid,'entry_peer')
    q=receive(conn)
    require(set(q)=={'v','intent','instance','binding','nonce'} and q['v']==1 and re.fullmatch('[0-9a-f]{32}',q['nonce']) and re.fullmatch('[0-9a-f]{64}',q['binding']),'entry_request')
    intent=ident(q['intent']);instance=ident(q['instance']);binding=q['binding'];nonce=q['nonce']
    claimed=False
    with fence(lock) as fd:
        try:
            with transaction() as db:
                store.verify(db)
                r=db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone()
                require(r is not None and r['state']=='AUTHORIZED' and r['instance_id']==instance and bindings(db,instance)==binding,'entry_authorization')
                owner,epoch=r['owner'],r['epoch']
            require(gate.claim(intent,owner,epoch).get('claimed') is True,'entry_claim_refused');claimed=True
            with transaction() as db:
                r=dict(db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone());row=offer(r)
                require(r['state']=='CLAIMED' and r['claim_token'] and event_detail(db,intent,'native_bound') is None and bindings(db,instance)==binding,'entry_claim_changed')
                append_event(db,intent,'native_bound',{'binding':binding,'instance':instance,'pair':row['pair']},int(clock()))
            send(conn,{'nonce':nonce,'row':row})
            receipt=sha([row,nonce,binding])
            require(receive(conn)=={'nonce':nonce,'ready':receipt},'entry_ready_changed')
            def validate(db):
                store.verify(db);now=gate._now(db)
                current=db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone()
                require(dict(current)==r and bindings(db,instance)==binding,'entry_claim_changed')
                gate.check_entry_boundary(db,current,now)
                return now
            with transaction() as db:
                now=validate(db);require(event_detail(db,intent,'native_attempt') is None,'entry_attempt_repeated')
                append_event(db,intent,'native_attempt',{'binding':binding},now)
                append_event(db,intent,'native_entry_ready',{'receipt':receipt},now)
            with transaction() as db:
                now=validate(db)
                require(event_detail(db,intent,'native_attempt')=={'binding':binding},'entry_attempt_changed')
                send(conn,{'nonce':nonce,'permit':receipt,'until':min(now+2,row['expires'])})
                transfer(conn,fd)
                require(receive(conn)=={'nonce':nonce,'observed':True},'entry_outcome_unverified')
                append_event(db,intent,'native_return',{'binding':binding,'result':{'observed':True}},int(clock()))
            send(conn,{'nonce':nonce,'committed':True});return 'submitted'
        except Exception:
            if claimed:
                with transaction() as db:
                    db.execute("UPDATE gate_intents SET state='UNKNOWN' WHERE id=? AND state='CLAIMED'",(intent,))
                    append_event(db,intent,'native_uncertain',{'reason':'independent_entry_unresolved'},int(clock()))
            raise


def execute(conn,driver,row_id,work,lock,*,desk_uid,native_uid,pair,instance,clock=time.time):
    """Actual NativeDriver.entry guards the one exact order call. No token/raw DB."""
    require(os.geteuid()==native_uid and peer(conn)==desk_uid and native_uid!=desk_uid,'entry_peer')
    binding=driver.preflight(instance,pair);nonce=secrets.token_hex(16)
    send(conn,dict(v=1,intent=ident(row_id),instance=ident(instance),binding=binding,nonce=nonce))
    reply=receive(conn);require(set(reply)=={'nonce','row'} and reply['nonce']==nonce,'entry_offer')
    row=offer(reply['row']);require(row==reply['row'] and (row['id'],row['instance_id'],row['pair'])==(row_id,instance,pair) and int(clock())<row['expires'],'entry_offer_scope')
    fd=None;called=False
    try:
        def guard(operation):
            nonlocal fd,called
            require(not called,'entry_repeat');called=True
            receipt=attempt(work,row,nonce,binding)
            send(conn,{'nonce':nonce,'ready':receipt})
            p=receive(conn);require(set(p)=={'nonce','permit','until'} and p['nonce']==nonce and p['permit']==receipt and type(p['until']) is int,'entry_permit')
            fd=accept_fence(conn,lock)
            require(int(clock())<p['until']<=row['expires'] and p['until']<=int(clock())+2,'entry_permit_expired')
            require(not select.select([conn],[],[],0)[0],'entry_guardian_disconnected')
            from reserve_outage_protection import save_grant
            save_grant(work,driver.key,row,nonce,binding,int(clock()),p['until'])
            require(int(clock())<p['until'] and not select.select([conn],[],[],0)[0],'entry_permit_lost')
            return operation()
        result=driver.entry(row,guard)
        require(called and fd is not None and result=={'observed':True},'entry_outcome_unverified')
        send(conn,{'nonce':nonce,'observed':True})
        require(receive(conn)=={'nonce':nonce,'committed':True},'entry_commit_unknown')
        return 'submitted'
    finally:
        if fd is not None:os.close(fd)


@contextmanager
def listener(directory,desk_uid):
    require(os.geteuid()==desk_uid,'entry_listen_identity')
    directory=Path(directory)
    with protected_dir(directory,desk_uid,0o755) as d:
        fd=os.open('entry.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=d)
        try:
            s=os.fstat(fd);require(stat.S_ISREG(s.st_mode) and s.st_uid==desk_uid and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600,'entry_listener_lock')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);name='entry.sock'
            try:
                s=os.stat(name,dir_fd=d,follow_symlinks=False);require(stat.S_ISSOCK(s.st_mode) and s.st_uid==desk_uid,'entry_socket');os.unlink(name,dir_fd=d)
            except FileNotFoundError:pass
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                sock.bind(str(directory/name));os.chmod(name,0o666,dir_fd=d);sock.listen(1)
                try:yield sock
                finally:os.unlink(name,dir_fd=d)
        finally:os.close(fd)


@contextmanager
def connection(directory,desk_uid):
    directory=Path(directory)
    with protected_dir(directory,desk_uid,0o755) as d:
        s=os.stat('entry.sock',dir_fd=d,follow_symlinks=False);require(stat.S_ISSOCK(s.st_mode) and s.st_uid==desk_uid,'entry_socket')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.settimeout(2);conn.connect(str(directory/'entry.sock'));require(peer(conn)==desk_uid,'entry_peer');yield conn
