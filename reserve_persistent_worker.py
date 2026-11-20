"""Single continuously leased PAPER runtime. Readiness is not order authority.

No RPC, new trading permission, or cached financial evidence. The warm stopped
bot is only loaned to existing independently checked operations on this thread.
"""
import grp
import hmac
import os
import pwd
import re
import signal
import stat
import socket
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
from reserve_checkpoint import AuthorityStore, require
from reserve_ipc import pinned_key, protected_dir
from reserve_native_rebind import verified_identity, workspace as rebound_workspace, binding as rebound_binding
from paper_custody import workspace, workspace_key, AuthenticatedNativeDriver
from reserve_entry_worker import native_once
from reserve_terminal_worker import native_step, native_driver_context
from reserve_terminal_transport import TerminalMailbox

MARKER=Path('/etc/maj-vaj/persistent-native-enabled')


def notify(message):
    """One-way systemd readiness/watchdog, not an application command socket."""
    address=os.environ.get('NOTIFY_SOCKET')
    if not address:return
    if address.startswith('@'):address='\0'+address[1:]
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
        sock.settimeout(1);sock.connect(address);sock.sendall(message.encode('ascii'))


class WarmRuntime:
    def __init__(self,producer,relay_pin,factory):
        self.producer=producer;self.relay_pin=relay_pin;self.factory=factory;self.active=False;self.borrowed=False

    def _identity(self):
        p=self.producer
        require(os.geteuid()==p.uid and p.rebind_dir is not None,'persistent_native_identity')
        require(len({pinned_key(x) for x in (self.relay_pin,p.native_pin,p.human_pin,p.rebind_pin)})==4,'persistent_independent_pins')
        with protected_dir(p.rebind_dir,0,0o755) as fd:
            names=os.listdir(fd);require(len(names)==1 and re.fullmatch('[0-9a-f]{32}\\.json',names[0]) is not None,'persistent_single_workspace')
        i=names[0][:-5]
        b=verified_identity(p.rebind_dir,p.rebind_pin,i,native_uid=p.uid,desk_uid=p.desk_uid,authority_uid=p.authority_uid)
        return i,b

    def _assert_current(self):
        require(self.active and self.owner==(os.getpid(),threading.get_ident()),'persistent_lease_owner')
        i,b=self._identity();require(i==self.instance and b==self.record,'persistent_identity_changed')
        p=self.producer
        with rebound_workspace(p.data,i,p.uid) as (_,fd):
            require(rebound_binding(p.data,fd,b['scope'],p.uid)==b['new'],'persistent_binding_changed')
            lock=os.stat('paper-worker.lock',dir_fd=fd,follow_symlinks=False)
            require((lock.st_dev,lock.st_ino)==self.lock_identity and stat.S_ISREG(lock.st_mode) and lock.st_uid==p.uid and lock.st_nlink==1 and stat.S_IMODE(lock.st_mode)==0o600,'persistent_lock_changed')
        key,binding=workspace_key(p.data,i)
        require(binding==self.driver.binding and hmac.compare_digest(key,self.driver.key),'persistent_driver_binding_changed')
        self.driver.verify({'instance_id':i,'pair':b['scope']['pair']})

    @contextmanager
    def start(self):
        require(not self.active,'persistent_already_active')
        p=self.producer;i,b=self._identity();scope=b['scope'];work=workspace(p.data,i)
        # Lifetime lease acquired before any database open or expensive import.
        with rebound_workspace(p.data,i,p.uid,lease=True) as (_,fd):
            require(rebound_binding(p.data,fd,scope,p.uid)==b['new'],'persistent_binding_changed')
            lock=os.stat('paper-worker.lock',dir_fd=fd,follow_symlinks=False)
            self.lock_identity=(lock.st_dev,lock.st_ino)
            with self.factory(i,scope['pair'],scope['principal_cents'],work) as driver:
                require(isinstance(driver,AuthenticatedNativeDriver) and driver.instance==i and driver.path==work/'paper.sqlite','persistent_driver_scope')
                self.instance,self.record,self.work,self.driver=i,b,work,driver
                self.owner=(os.getpid(),threading.get_ident());self.active=True
                try:
                    self._assert_current();yield self
                finally:self.active=False;self.driver=None

    @contextmanager
    def lease(self,work):
        self._assert_current();require(Path(work)==self.work and not self.borrowed,'persistent_lease_scope')
        self.borrowed=True
        try:yield
        finally:self.borrowed=False

    @contextmanager
    def borrow(self,instance,pair,cents,work):
        self._assert_current();s=self.record['scope']
        require(self.borrowed and (instance,pair,cents,Path(work))==(self.instance,s['pair'],s['principal_cents'],self.work),'persistent_borrow_scope')
        yield self.driver

    def cycle(self,inbox,store,native_gid,mailbox,socket_dir,lock):
        self._assert_current()
        # Existing exposure always gets first opportunity for protection.
        try:
            outcome=native_step(self.producer,inbox,store,self.relay_pin,native_gid,mailbox,factory=self.borrow,lease=self.lease,protected=True)
            if outcome!='idle':return outcome
            # The signed snapshot may still show AUTHORIZED after the native
            # buy persisted. Actual local exposure outranks an idle projection:
            # never start a second entry or restart solely because it exists.
            with closing(sqlite3.connect(self.driver.path.as_uri()+'?mode=ro',uri=True)) as db:
                exposed=db.execute('SELECT 1 FROM trades WHERE is_open=1 LIMIT 1').fetchone() or db.execute('SELECT 1 FROM orders WHERE ft_is_open=1 LIMIT 1').fetchone()
            if exposed:
                from reserve_outage_protection import step as outage_step
                return outage_step(self.producer,self.relay_pin,factory=self.borrow,lease=self.lease)
            outcome=native_once(self.producer,inbox,store,self.relay_pin,native_gid,socket_dir,lock,factory=self.borrow,lease=self.lease)
            if outcome=='idle':self.refresh_wallet(inbox,store,native_gid,mailbox)
            return outcome
        except (FileNotFoundError, ValueError) as exc:
            # A refresh race is not a reason to restart and lose the simulator's
            # pending-order cache. No stale accounting or entry authorization.
            unavailable=(isinstance(exc,FileNotFoundError) and exc.filename=='snapshot.bin') or str(exc) in ('snapshot_stale','snapshot_checkpoint_changed','snapshot_changed_during_review')
            if not unavailable:raise
            if mailbox.read() is not None:return 'snapshot_held'
            from reserve_outage_protection import step as outage_step
            return outage_step(self.producer,self.relay_pin,factory=self.borrow,lease=self.lease)

    def refresh_wallet(self,inbox,store,native_gid,mailbox):
        from reserve_native_handoff import open_reviewed
        from reserve_wallet_transport import write
        from financial_ledger import UNRELEASED
        import json
        p=self.producer
        with open_reviewed(inbox,p.workspace,store,self.relay_pin,native_uid=p.uid,native_gid=native_gid,desk_uid=p.desk_uid,authority_uid=p.authority_uid,clock=p.clock) as (db,_):
            if db.execute("SELECT 1 FROM gate_intents WHERE state='AUTHORIZED' OR (state IN ('CLAIMED','UNKNOWN','FILLED_HELD') AND "+UNRELEASED+") LIMIT 1").fetchone():return
            last=db.execute('SELECT kind,body FROM fin_events ORDER BY seq DESC LIMIT 1').fetchone()
            if last and last[0]=='desk_reconcile':
                at=json.loads(last[1])['payload']['observed']
                if 0<=int(p.clock())-at<15:return
        scope=self.record['scope']
        proof=p.observe_from_handoff(inbox,store,self.relay_pin,native_gid,self.instance,lambda work:self.borrow(self.instance,scope['pair'],scope['principal_cents'],work),lease=self.lease)
        write(mailbox,proof)


def marker(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid==0 and not path.stat().st_mode&0o022,'persistent_not_enabled')


def run(*,factory=native_driver_context):
    from reserve_native_evidence import NativeProducer
    from reserve_entry_fence import LOCK,open_fence
    for name in ('persistent-native-enabled','protected-entry-enabled','protected-exit-enabled','reserve-transport-enabled'):
        marker(Path('/etc/maj-vaj')/name)
    os.close(open_fence(LOCK))
    d,n,a=(pwd.getpwnam(x) for x in ('maj-vaj','maj-vaj-native','maj-vaj-approval'))
    g=grp.getgrnam('maj-vaj-transfer').gr_gid
    require(os.geteuid()==n.pw_uid and len({d.pw_uid,n.pw_uid,a.pw_uid})==3 and min(d.pw_uid,n.pw_uid,a.pw_uid,g)>0 and g in {os.getegid(),*os.getgroups()} and g not in {d.pw_gid,n.pw_gid,a.pw_gid},'persistent_service_identity')
    pins=Path('/etc/maj-vaj');relay=pins/'reserve-relay.pub';base=Path('/var/lib')
    store=AuthorityStore(base/'maj-vaj-reserve-authority',authority_uid=a.pw_uid,desk_uid=d.pw_uid)
    producer=NativeProducer('installed-paper',base/'maj-vaj-native',base/'maj-vaj-native-signing',native_uid=n.pw_uid,desk_uid=d.pw_uid,authority_uid=a.pw_uid,native_pin=pins/'reserve-native.pub',human_pin=pins/'reserve-checkpoint.pub',rebind_dir=pins/'reserve-rebind',rebind_pin=pins/'reserve-rebind.pub')
    mailbox=TerminalMailbox(base/'maj-vaj-terminal-outbox',n.pw_uid,g)
    stopped=threading.Event()
    def stop(*_):stopped.set()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,stop)
    with WarmRuntime(producer,relay,factory).start() as warm:
        if stopped.is_set():return
        notify('READY=1\nSTATUS=Native PAPER runtime ready; current authorization still required')
        while not stopped.is_set():
            # Refusal is intentionally not retried in the same cached runtime:
            # restart reconstructs durable native state and cannot repeat claims.
            warm.cycle(base/'maj-vaj-native-inbox',store,n.pw_gid,mailbox,'/run/maj-vaj-entry',LOCK)
            notify('WATCHDOG=1')
            stopped.wait(2)
        notify('STOPPING=1')


def main():
    try:run()
    except Exception:raise SystemExit('PAPER runtime unavailable; unresolved execution held') from None

if __name__=='__main__':main()
