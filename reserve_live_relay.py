"""Automated PAPER snapshot relay; root never opens the desk database.

The fixed, unprivileged child constructs the minimized image in a serialized
transaction. Root reads only bounded projected bytes, independently replays
and signs them. No root or signer RPC, caller-supplied SQL or credential export.
"""
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from reserve_checkpoint import require
from reserve_ipc import protected_dir, pinned_key
from reserve_native_migration import MAX_DB
from reserve_native_handoff import issue_projected, TTL
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _source(root):
    root=Path(root)
    # Root launches installed immutable source only. No site/PYTHONPATH injection.
    with protected_dir(root,0,0o755):pass
    for name in ('reserve_live_relay.py','reserve_runtime.py','reserve_snapshot.py'):
        s=(root/name).lstat()
        require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'relay_source_custody')
    return root


def extract(data):
    from reserve_runtime import desk_transaction
    from reserve_snapshot import project
    require(os.geteuid()>0,'relay_extractor_identity')
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_DB,MAX_DB))
    with desk_transaction(Path(data))() as db:
        raw=project(db)
    sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()


def relay(data,directory,workspace,store,seed,*,desk_uid,desk_gid,native_uid,native_gid,root=None,clock=time.time):
    require(os.geteuid()==0 and desk_uid>0 and desk_gid>0 and native_uid>0 and desk_uid!=native_uid,'relay_identity')
    root=_source(root or Path(__file__).resolve().parent)
    issued=int(clock());before=store.read()
    groups=[]
    from reserve_entry_fence import installed_enabled,open_fence,LOCK
    if installed_enabled(data):
        import grp
        group=grp.getgrnam('maj-vaj-transfer').gr_gid
        fd=open_fence(LOCK)
        try:require(os.fstat(fd).st_gid==group,'relay_entry_fence_group')
        finally:os.close(fd)
        groups=[group]  # only the root-pinned fence transfer group
    # Anonymous bounded-on-read spool. Child may only receive its own copy of
    # the output descriptor; no inherited secrets, journal or signing key fd.
    with tempfile.TemporaryFile() as stream:
        p=subprocess.run([sys.executable,'-I','-B','-c',
                          'import sys;sys.path.insert(0,sys.argv[1]);from reserve_live_relay import extract;extract(sys.argv[2])',str(root),str(data)],
                         cwd=root,user=desk_uid,group=desk_gid,extra_groups=groups,umask=0o077,
                         env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent'},stdin=subprocess.DEVNULL,
                         stdout=stream,stderr=subprocess.DEVNULL,timeout=15,check=False)
        require(p.returncode==0 and 0<stream.tell()<=MAX_DB,'relay_extraction_failed')
        stream.seek(0);raw=stream.read(MAX_DB+1)
    require(store.read()==before and 0<=int(clock())-issued<=TTL,'relay_source_changed')
    from contextlib import closing
    from reserve_snapshot import open_projected
    from reserve_native_handoff import _identity
    with closing(open_projected(raw)) as db:
        db.execute('BEGIN');identity=_identity(db,before)
    return issue_projected(raw,directory,workspace,store,seed,native_uid=native_uid,native_gid=native_gid,expected=(before,identity),issued=issued,clock=clock)


def root_seed(directory,pin):
    with protected_dir(directory,0,0o700) as fd:
        f=os.open('relay.key',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
        with os.fdopen(f,'rb') as stream:
            s=os.fstat(stream.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and s.st_size==32,'relay_key_custody')
            seed=stream.read(33)
    require(len(seed)==32 and Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)==pinned_key(pin),'relay_pin_mismatch')
    return seed


def main():
    import pwd
    from reserve_checkpoint import AuthorityStore
    desk=pwd.getpwnam('maj-vaj');native=pwd.getpwnam('maj-vaj-native');auth=pwd.getpwnam('maj-vaj-approval')
    require(len({desk.pw_uid,native.pw_uid,auth.pw_uid})==3,'relay_independent_principals')
    pin='/etc/maj-vaj/reserve-relay.pub'
    require(len({pinned_key(p) for p in (pin,'/etc/maj-vaj/reserve-native.pub','/etc/maj-vaj/reserve-checkpoint.pub','/etc/maj-vaj/reserve-rebind.pub')})==4,'relay_independent_pins')
    seed=root_seed('/var/lib/maj-vaj-reserve-relay',pin)
    store=AuthorityStore('/var/lib/maj-vaj-reserve-authority',authority_uid=auth.pw_uid,desk_uid=desk.pw_uid)
    relay('/var/lib/maj-vaj/data','/var/lib/maj-vaj-native-inbox','installed-paper',store,seed,desk_uid=desk.pw_uid,desk_gid=desk.pw_gid,native_uid=native.pw_uid,native_gid=native.pw_gid)

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('PAPER snapshot relay unavailable') from None
