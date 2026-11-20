"""Bounded native-owned, replaceable signed flat-wallet observation.

Unlike the immutable settlement mailbox, this is a short-lived observation,
not execution evidence, a reservation release, or an authorization. Only the
current exact ledger prefix and account observation can be imported.
"""
import os
import stat
import secrets
import time
from contextlib import nullcontext
from reserve_checkpoint import require
from reserve_ipc import canonical, strict
from reserve_native_wallet import publish, verify_observation
from financial_ledger import replay

NAME='wallet.json'
LIMIT=8192


def _read(mailbox,fd):
    require(os.fstat(fd).st_gid==mailbox.group,'wallet_mailbox_group')
    try:f=os.open(NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
    except FileNotFoundError:return None
    with os.fdopen(f,'rb') as stream:
        s=os.fstat(stream.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid==mailbox.owner and s.st_gid==mailbox.group and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o640 and 0<s.st_size<=LIMIT,'wallet_mailbox_custody')
        raw=stream.read(LIMIT+1);require(len(raw)==s.st_size,'wallet_mailbox_size')
    return strict(raw)


def write(mailbox,packet):
    require(os.geteuid()==mailbox.owner,'wallet_writer_identity')
    raw=canonical(packet);require(0<len(raw)<=LIMIT,'wallet_mailbox_size')
    with mailbox._directory() as fd:
        _read(mailbox,fd)  # reject unsafe existing file, never follow a link
        name='.wallet-'+secrets.token_hex(16)
        f=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
        try:
            with os.fdopen(f,'wb') as stream:
                stream.write(raw);stream.flush();os.fchown(stream.fileno(),-1,mailbox.group);os.fchmod(stream.fileno(),0o640);os.fsync(stream.fileno())
            os.replace(name,NAME,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
        finally:
            try:os.unlink(name,dir_fd=fd)
            except FileNotFoundError:pass


def consume(transaction,mailbox,bindings,public,workspace,store,*,clock=time.time):
    require(os.geteuid()!=mailbox.owner,'wallet_reader_identity')
    with mailbox._directory() as fd:packet=_read(mailbox,fd)
    if packet is None:return 'idle'
    b=verify_observation(public,workspace,packet);now=int(clock())
    with transaction() as db:
        witness=store.verify(db);_,_,_,head=replay(db)
        # Refuse stale/duplicate observations without changing the ledger.
        if b['head']!=head or not 0<=now-b['at']<=30:return 'wallet_held'
        if db.execute("SELECT 1 FROM gate_intents WHERE state='AUTHORIZED' LIMIT 1").fetchone():return 'wallet_held'
        from financial_ledger import UNRELEASED
        if db.execute("SELECT 1 FROM gate_intents WHERE state IN ('CLAIMED','UNKNOWN','FILLED_HELD') AND "+UNRELEASED+" LIMIT 1").fetchone():return 'wallet_held'
        publish(lambda:nullcontext(db),{b['instance']:packet},bindings,public,workspace,clock=lambda:now)
        require(store.verify(db)==witness,'wallet_witness_changed')
    return 'wallet_accounted'
