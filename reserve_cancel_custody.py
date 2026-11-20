"""Native-owned cancellation evidence; no desk DB access or entry authority."""
import os
import sqlite3
import stat
from contextlib import closing, contextmanager
from pathlib import Path
from paper_recovery import RecoveryDriver
from reserve_checkpoint import require


@contextmanager
def local_transaction(work):
    """Only inside exclusive native workspace custody. No caller SQL/RPC."""
    work=Path(work);s=work.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,'cancel_workspace_custody')
    path=work/'cancel-receipts.sqlite'
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600)
    try:
        s=os.fstat(fd);require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600,'cancel_receipt_custody')
        with closing(sqlite3.connect(path,isolation_level=None)) as db:
            require(path.stat().st_ino==s.st_ino and path.stat().st_dev==s.st_dev,'cancel_receipt_replaced')
            db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('CREATE TABLE IF NOT EXISTS gate_events(seq INTEGER PRIMARY KEY,intent_id TEXT NOT NULL,action TEXT NOT NULL CHECK(action=\'native_cancel_frozen\'),at INTEGER NOT NULL,detail TEXT NOT NULL,UNIQUE(intent_id,action))')
                yield db;db.commit()
                d=os.open(work,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
                try:os.fsync(d)
                finally:os.close(d)
            except BaseException:db.rollback();raise
    finally:os.close(fd)


class NativeCancellationDriver(RecoveryDriver):
    """Reuse the proven frozen PAPER cancellation with native-private storage.

    No mutable desk journal or remote signing interface. A missing raw open
    cache is not a zero fill. Restart only reapplies an actual frozen receipt.
    """
    def __init__(self,bot,path,instance,key,*,clock):
        super().__init__(bot,path,instance,key,lambda:local_transaction(Path(path).parent),clock=clock)
