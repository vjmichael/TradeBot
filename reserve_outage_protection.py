"""Native-only reduction of previously authorized PAPER exposure during relay loss.

No snapshot is treated as entry or accounting authority, and the local grant is
never a reusable order permit. Terminal accounting waits for a fresh snapshot.
"""
import os
import re
import stat
from contextlib import contextmanager
from reserve_checkpoint import require
from reserve_ipc import canonical, strict, protected_dir, pinned_key
from financial_ledger import sha
from paper_custody import authenticate, packet, workspace, workspace_key, AuthenticatedNativeDriver
from paper_supervisor import custody
from reserve_native_rebind import read_owned, verified_identity, workspace as rebound_workspace, binding as rebound_binding

DOMAIN='accepted-entry-reduce-only'


@contextmanager
def _work_fd(work):
    fd=os.open(work,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd);require(st.st_uid==os.geteuid() and stat.S_IMODE(st.st_mode)==0o700,'outage_workspace')
        yield fd
    finally:os.close(fd)


def save_grant(work,key,row,nonce,binding,at,until):
    """Called only at final synchronous permit boundary, before native effect."""
    from reserve_entry_broker import offer
    require(row==offer(row) and re.fullmatch('[0-9a-f]{32}',nonce) is not None and re.fullmatch('[0-9a-f]{64}',binding) is not None,'outage_grant_scope')
    require(type(at) is int and type(until) is int and 0<until-at<=2 and until<=row['expires'],'outage_grant_expiry')
    body=dict(row=row,nonce=nonce,binding=binding,at=at,until=until)
    raw=canonical(packet(key,DOMAIN,body));name='grant-'+sha([binding,row['id']])+'.json'
    with _work_fd(work) as fd:
        f=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
        with os.fdopen(f,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
        os.fsync(fd)


def read_grant(work,key,instance,intent,binding,pair,now):
    from reserve_entry_broker import offer
    name='grant-'+sha([binding,intent])+'.json'
    with _work_fd(work) as fd:
        b=authenticate(key,DOMAIN,strict(read_owned(fd,name,os.geteuid(),4096)))
    require(set(b)=={'row','nonce','binding','at','until'} and b['binding']==binding and re.fullmatch('[0-9a-f]{32}',b['nonce']) is not None,'outage_grant_scope')
    row=b['row'];require(row==offer(row) and (row['id'],row['instance_id'],row['pair'])==(intent,instance,pair),'outage_grant_scope')
    require(type(b['at']) is int and type(b['until']) is int and 0<b['until']-b['at']<=2 and b['until']<=row['expires'] and b['at']<=now,'outage_grant_time')
    return row


def reduce(driver,work,key,instance,pair,binding,clock):
    """Discover actual exposure, not a requested intent or guessed fill."""
    from freqtrade.persistence import Trade
    from reserve_exit_custody import ExitJournal, protect
    from reserve_cancel_custody import NativeCancellationDriver
    from paper_execution import units
    trades=Trade.get_trades_proxy(is_open=True)
    if not trades:return 'outage_no_open_exposure'  # NOT a zero-fill receipt
    require(len(trades)==1,'outage_multiple_exposures')
    t=trades[0];tag=t.enter_tag
    require(type(tag) is str and tag.startswith('mv:') and t.pair==pair,'outage_unattributed_trade')
    row=read_grant(work,key,instance,tag[3:],binding,pair,int(clock()))
    driver.verify(row)
    if isinstance(driver,NativeCancellationDriver):
        frozen=driver.recover_cancel(row)
        if frozen is not None and not units(frozen['filled']):return 'outage_zero_awaiting_snapshot'
        # A pending buy is cancelled, never filled by polling, if its actual
        # simulator state is available. Missing raw state remains unresolved.
        if any(o.ft_order_side=='buy' for o in t.open_orders):
            if driver.cancel_before_poll(row):return 'outage_cancel_requested'
    journal=ExitJournal(work,key,binding,row['id'])
    return 'outage_'+protect(driver,row,journal,clock=clock)


def step(producer,relay_pin,*,factory,lease=None):
    """No desk connection or stale snapshot required to reduce held exposure."""
    import hmac
    lease=lease or custody
    require(os.geteuid()==producer.uid and producer.rebind_dir is not None,'outage_native_identity')
    require(len({pinned_key(p) for p in (relay_pin,producer.native_pin,producer.human_pin,producer.rebind_pin)})==4,'outage_independent_pins')
    # The installed migration currently supports exactly one immutable workspace.
    with protected_dir(producer.rebind_dir,0,0o755) as fd:
        names=os.listdir(fd);require(len(names)==1 and re.fullmatch('[0-9a-f]{32}\\.json',names[0]) is not None,'outage_single_workspace')
    instance=names[0][:-5]
    b=verified_identity(producer.rebind_dir,producer.rebind_pin,instance,native_uid=producer.uid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid)
    scope=b['scope'];work=workspace(producer.data,instance)
    with lease(work):
        with rebound_workspace(producer.data,instance,producer.uid) as (_,fd):
            require(rebound_binding(producer.data,fd,scope,producer.uid)==b['new'],'outage_rebind_changed')
        key,binding=workspace_key(producer.data,instance)
        with factory(instance,scope['pair'],scope['principal_cents'],work) as driver:
            require(isinstance(driver,AuthenticatedNativeDriver) and driver.instance==instance and driver.path==work/'paper.sqlite' and driver.binding==binding==b['new']['binding'] and hmac.compare_digest(driver.key,key),'outage_native_driver')
            return reduce(driver,work,key,instance,scope['pair'],binding,producer.clock)
