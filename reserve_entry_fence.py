"""Shared kernel fence for staged independent PAPER execution.

Only close descriptors: LOCK_UN would drop the shared open-description lock
while the native effect is in flight. Every cooperating desk writer must use
this fence. Root installs/removes it only while all services are stopped.
"""
import fcntl
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from reserve_checkpoint import require
from reserve_ipc import protected_dir

LOCK=Path('/etc/maj-vaj/entry-fence.lock')
MARKER=Path('/etc/maj-vaj/protected-entry-enabled')
DATA=Path('/var/lib/maj-vaj/data')


def open_fence(path):
    path=Path(path)
    with protected_dir(path.parent,0,0o755) as d:
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=d)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_gid>0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o640,'entry_fence_custody')
        return fd
    except BaseException:os.close(fd);raise


@contextmanager
def fence(path,*,timeout=10):
    fd=open_fence(path)
    try:
        end=time.monotonic()+timeout
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                require(time.monotonic()<end,'entry_fence_busy');time.sleep(.02)
        yield fd
    finally:os.close(fd)


def verify_received(fd,path):
    other=open_fence(path)
    try:
        a,b=os.fstat(fd),os.fstat(other)
        require((a.st_dev,a.st_ino)==(b.st_dev,b.st_ino),'entry_fence_identity')
        # Independent description must be blocked. The received description
        # must already hold the same lock, not a different open description.
        try:fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:pass
        else:raise ValueError('entry_fence_not_held')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:os.close(other)


def installed_enabled(data):
    return Path(data).resolve()==DATA and (os.path.lexists(LOCK) or os.path.lexists(MARKER))


@contextmanager
def installed_fence(data):
    if not installed_enabled(data):
        yield;return
    with fence(LOCK):yield
