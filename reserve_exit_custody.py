"""Native-owned protective exit custody. No entry, activation or live interface.

A fresh signed desk snapshot supplies the exact existing intent; the native
worker may only protect exposure actually observed in the bound PAPER database.
A durable pre-action intent is never retried if its tagged outcome is missing.
"""
import os
import re
import stat
from pathlib import Path
from contextlib import closing
from reserve_checkpoint import require
from reserve_ipc import canonical, strict
from financial_ledger import amount, ident, sha
from paper_execution import event_detail
from paper_supervisor import checked_view, custody, released
from paper_custody import AuthenticatedNativeDriver, authenticate, packet, workspace, workspace_key

MAX_ATTEMPTS=256
MAX_RECORD=4096


def authorized_row(db, instance, intent, binding):
    ident(intent)
    row=db.execute('SELECT id,instance_id,state,entry,stop,target,expires FROM gate_intents WHERE id=?',(intent,)).fetchone()
    require(row is not None and row[1]==instance and row[2] in ('CLAIMED','UNKNOWN','FILLED_HELD') and not released(db,intent),'exit_intent_scope')
    pair=db.execute('SELECT pair,mode FROM instances WHERE id=?',(instance,)).fetchone()
    require(pair is not None and pair[1]=='PAPER' and pair[0] in ('BTC/USD','ETH/USD','SOL/USD'),'exit_paper_scope')
    bound=event_detail(db,intent,'native_bound');attempt=event_detail(db,intent,'native_attempt')
    require(bound is not None and bound.get('binding')==binding and attempt is not None and attempt.get('binding')==binding,'exit_claim_binding')
    out=dict(zip(('id','instance_id','state','entry','stop','target','expires'),row))|{'pair':pair[0]}
    require(amount(out['stop'],True)<amount(out['entry'],True)<amount(out['target'],True),'exit_levels')
    amount(out['expires'],True)
    return out


def check_requests(row, requests, view, *, now=None, complete=False):
    """Exact sequential tagged reductions; an unfinished/missing attempt holds."""
    checked_view(view,row)
    require(type(requests) is list and len(requests)<=MAX_ATTEMPTS,'exit_attempt_limit')
    sells=[o for o in view['orders'] if o['side']=='sell']
    by_tag={o['tag']:o for o in sells}
    require(len(by_tag)==len(sells),'duplicate_exit_tag')
    buy=[o for o in view['orders'] if o['side']=='buy']
    require(len(buy)==1 and buy[0]['terminal'] and buy[0]['qty']>0,'exit_entry_unresolved')
    left=buy[0]['qty'];last_at=0
    for i,p in enumerate(requests,1):
        require(type(p) is dict and set(p)=={'tag','reason','qty','price','at'},'exit_attempt_schema')
        require(p['tag']=='mv-exit:'+row['id']+':'+str(i),'exit_attempt_tag')
        require(p['reason'] in ('stop','target') and (i==1 or p['reason']==requests[0]['reason']),'exit_reason_changed')
        require(amount(p['qty'],True)==left and amount(p['at'],True)>=last_at and amount(p['price'],True)>0,'exit_attempt_scope')
        if now is not None:require(p['at']<=now,'exit_future_attempt')
        if i==1:require(p['price']<=row['stop'] if p['reason']=='stop' else p['price']>=row['target'],'exit_trigger_missing')
        last_at=p['at'];o=by_tag.get(p['tag'])
        require(o is not None and o['terminal'] and o['qty']<=left,'ambiguous_exit_attempt')
        left-=o['qty']
    require(set(by_tag)=={p['tag'] for p in requests} and left==view['position'],'unattributed_native_exit')
    if complete:require(view['closed'] and left==0 and requests,'exit_not_closed')
    return left


class ExitJournal:
    """Native-owned append-only fsynced records, each authenticated with local key.

    The parent is the already leased and independently rebound native workspace.
    Partial files are intentionally not removed after failure: corrupt or
    incomplete records block execution; only an explicit recovery review may
    resolve them. No automatic truncation or retry.
    """
    def __init__(self,work,key,binding,intent):
        self.work=Path(work);self.key=key;self.binding=binding;self.intent=ident(intent)
        require(re.fullmatch('[0-9a-f]{64}',binding) is not None,'exit_binding')
        self.name='exit-'+sha([binding,intent]);s=self.work.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,'exit_workspace_custody')
    def read(self):
        from reserve_ipc import protected_dir
        # Workspace ancestors are native-owned; custody lease validates parent.
        d=os.open(self.work,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            candidates=sorted(n for n in os.listdir(d) if n.startswith(self.name+'.'))
            require(len(candidates)<=MAX_ATTEMPTS,'exit_attempt_limit');result=[]
            for i,name in enumerate(candidates,1):
                require(name==self.name+f'.{i:03d}','exit_journal_gap')
                f=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=d)
                with os.fdopen(f,'rb') as stream:
                    s=os.fstat(stream.fileno());require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=MAX_RECORD,'exit_record_custody')
                    raw=stream.read(MAX_RECORD+1);require(len(raw)==s.st_size,'exit_record_size')
                b=authenticate(self.key,'protected-exit-attempt',strict(raw))
                require(set(b)=={'binding','intent','seq','previous','request'} and b['binding']==self.binding and b['intent']==self.intent and b['seq']==i and b['previous']==sha(result),'exit_record_chain')
                result.append(b['request'])
            return result
        finally:os.close(d)
    def append(self,previous,request):
        require(len(previous)<MAX_ATTEMPTS and self.read()==previous,'exit_journal_changed')
        body=dict(binding=self.binding,intent=self.intent,seq=len(previous)+1,previous=sha(previous),request=request)
        raw=canonical(packet(self.key,'protected-exit-attempt',body));require(len(raw)<=MAX_RECORD,'exit_record_size')
        d=os.open(self.work,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            f=os.open(self.name+f'.{len(previous)+1:03d}',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=d)
            with os.fdopen(f,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.fsync(d)
        finally:os.close(d)


def protect(driver,row,journal,*,clock):
    """Only terminal buys and terminal prior exits; never poll/re-fill open buys."""
    view=checked_view(driver.view(row),row)
    if any(not o['terminal'] for o in view['orders']):return 'open_order_held'
    previous=journal.read()
    if view['closed']:
        check_requests(row,previous,view,now=int(clock()),complete=True)
        return 'closed'
    left=check_requests(row,previous,view,now=int(clock()))
    require(left>0,'exit_position_missing')
    price,at=driver.bid(row);now=int(clock());amount(price,True);amount(at,True)
    require(0<=now-at<=30,'stale_protection_quote')
    reason=previous[0]['reason'] if previous else 'stop' if price<=row['stop'] else 'target' if price>=row['target'] else None
    if not reason:return 'monitoring'
    request=dict(tag='mv-exit:'+row['id']+':'+str(len(previous)+1),reason=reason,qty=left,price=price,at=at)
    # No exception handler permits a new attempt after ambiguity. Even a crash
    # before the exchange call leaves a durable hold for manual reconciliation.
    journal.append(previous,request)
    driver.exit(row,request)
    return 'exit_requested'


def step(producer,inbox,store,relay_pin,native_gid,instance,intent,factory):
    """Verified fresh snapshot, independent migration binding and native lease."""
    from reserve_native_handoff import open_reviewed
    from reserve_native_rebind import verify
    from reserve_ipc import pinned_key
    import hmac
    require(producer.rebind_dir is not None,'exit_rebind_required')
    require(len({pinned_key(p) for p in (relay_pin,producer.native_pin,producer.human_pin,producer.rebind_pin)})==4,'exit_independent_pins')
    with open_reviewed(inbox,producer.workspace,store,relay_pin,native_uid=producer.uid,native_gid=native_gid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid,clock=producer.clock) as (db,_):
        work=workspace(producer.data,instance)
        with custody(work):
            bound=verify(db,producer.data,producer.rebind_dir,producer.rebind_pin,instance,native_uid=producer.uid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid)
            key,binding=workspace_key(producer.data,instance)
            row=authorized_row(db,instance,intent,binding)
            require(bound['binding']==binding,'exit_binding_changed')
            with factory(work) as driver:
                require(isinstance(driver,AuthenticatedNativeDriver) and driver.instance==instance and driver.path==work/'paper.sqlite' and driver.binding==binding and hmac.compare_digest(driver.key,key),'exit_driver_custody')
                from reserve_cancel_custody import NativeCancellationDriver
                if isinstance(driver,NativeCancellationDriver):
                    from reserve_zero_transport import produce as zero_produce
                    from paper_execution import units
                    frozen=driver.recover_cancel(row)
                    if frozen is not None and not units(frozen['filled']):
                        return zero_produce(producer,db,row,driver)
                    paused=any(db.execute('SELECT paused,human_pause FROM desk WHERE id=1').fetchone())
                    if frozen is None and (paused or int(producer.clock())>=row['expires']):
                        if driver.cancel_before_poll(row):
                            frozen=driver.frozen(row)
                            if frozen is not None and not units(frozen['filled']):
                                return zero_produce(producer,db,row,driver)
                            return 'cancel_requested'
                journal=ExitJournal(work,key,binding,intent)
                return protect(driver,row,journal,clock=producer.clock)
