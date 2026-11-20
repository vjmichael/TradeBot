"""PAPER non-invocation recovery. A human cannot override ambiguous execution.

The native owner commits PREPARED before invocation is possible, then atomically
commits POSSIBLE before the single side effect. Only positive PREPARED/SEALED
state can establish non-invocation; missing or legacy state is never evidence.
All transitions occur under the exclusive workspace lease.
"""
import copy
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from financial_ledger import amount, execution_anchor, observed, replay, sha
from paper_execution import append_event, event_detail
from paper_custody import authenticate, packet, workspace, workspace_key, AuthenticatedNativeDriver
from paper_supervisor import custody, events
from reserve_checkpoint import require
from reserve_ipc import canonical, pinned_key, scope, strict
from reserve_native_rebind import read_owned, verify as verify_rebind
from reserve_outage_protection import _work_fd, read_grant

NATIVE_DOMAIN=b'maj-vaj:PAPER:non-invocation:v1\0'
HUMAN_DOMAIN=b'maj-vaj:PAPER:reviewed-non-invocation:v1\0'
AGE=300


def _name(binding,intent):return 'entry-phase-'+sha([binding,intent])+'.json'


def _phase(work,key,row,binding,ready,old,new):
    name=_name(binding,row['id']);body=dict(row=row,binding=binding,ready=ready,phase=new)
    raw=canonical(packet(key,'entry-effect-phase',body))
    with _work_fd(work) as fd:
        if old is not None:
            found=authenticate(key,'entry-effect-phase',strict(read_owned(fd,name,os.geteuid(),4096)))
            require(found==dict(body,phase=old),'entry_phase_changed')
        tmp='.phase-'+secrets.token_hex(16)
        f=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
        try:
            with os.fdopen(f,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
            if old is None:
                os.link(tmp,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False);os.unlink(tmp,dir_fd=fd)
            else:os.replace(tmp,name,src_dir_fd=fd,dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:os.unlink(tmp,dir_fd=fd)
            except FileNotFoundError:pass


def prepared(work,key,row,binding,ready):_phase(work,key,row,binding,ready,None,'PREPARED')
def possible(work,key,row,binding,ready):_phase(work,key,row,binding,ready,'PREPARED','POSSIBLE')


def seal(work,key,row,binding,now):
    from reserve_entry_broker import offer
    with _work_fd(work) as fd:
        b=authenticate(key,'entry-effect-phase',strict(read_owned(fd,_name(binding,row['id']),os.geteuid(),4096)))
    require(set(b)=={'row','binding','ready','phase'} and b['binding']==binding and b['phase'] in ('PREPARED','SEALED'),'effect_possible_or_unproven')
    full=b['row'];require(full==offer(full) and all(full[k]==v for k,v in row.items() if k!='state'),'no_effect_scope')
    require(read_grant(work,key,full['instance_id'],full['id'],binding,full['pair'],now)==full,'no_effect_grant')
    with _work_fd(work) as fd:
        grant=authenticate(key,'accepted-entry-reduce-only',strict(read_owned(fd,'grant-'+sha([binding,full['id']])+'.json',os.geteuid(),4096)))
    require(b['ready']==sha([full,grant['nonce'],binding]),'no_effect_ready')
    if b['phase']=='PREPARED':_phase(work,key,full,binding,b['ready'],'PREPARED','SEALED')
    return full,b['ready']


def _hex(s):return type(s) is str and re.fullmatch('[0-9a-f]{64}',s) is not None

def _body(workspace,row,binding,ready,count,head,boot,anchor,quote,at):
    from reserve_entry_broker import offer
    require(type(row) is dict and row==offer(row),'no_effect_offer')
    require(all(_hex(x) for x in (binding,ready,head,anchor)),'no_effect_digest')
    for n in (count,boot,quote,at):amount(n)
    require(count>0 and at>0,'no_effect_time')
    return dict(v=1,workspace=scope(workspace),row=row,binding=binding,ready=ready,count=count,head=head,boot=boot,anchor=anchor,quote=quote,at=at)


def _signed(seed,domain,b):return {'body':b,'signature':Ed25519PrivateKey.from_private_bytes(seed).sign(domain+canonical(b)).hex()}

def _verify(public,domain,p):
    require(type(p) is dict and set(p)=={'body','signature'} and type(p['body']) is dict and type(p['signature']) is str and re.fullmatch('[0-9a-f]{128}',p['signature']) is not None,'recovery_signature_schema')
    try:Ed25519PublicKey.from_public_bytes(public).verify(bytes.fromhex(p['signature']),domain+canonical(p['body']))
    except (InvalidSignature,ValueError) as exc:raise ValueError('recovery_signature') from exc
    return p['body']


def verify_native(public,workspace,p):
    b=_verify(public,NATIVE_DOMAIN,p)
    require(set(b)=={'v','workspace','row','binding','ready','count','head','boot','anchor','quote','at'},'no_effect_body')
    require(b==_body(workspace,**{k:v for k,v in b.items() if k not in ('v','workspace')}),'no_effect_body')
    return b


def _paused(db):require(tuple(db.execute('SELECT paused,human_pause FROM desk WHERE id=1').fetchone())==(1,1),'recovery_requires_human_pause')


def _balance(db,instance,quote):
    states,_,count,head=replay(db);s=states[instance];v=observed(s)
    require(not s['correction_locked'] and not db.execute('SELECT 1 FROM fin_conflicts LIMIT 1').fetchone(),'recovery_financial_conflict')
    require(v['qty']==v['pending']==0 and v['active']+v['protected']==quote,'recovery_wallet_mismatch')
    return count,head,db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0],execution_anchor(db)


def produce(producer,inbox,store,relay_pin,native_gid,*,factory):
    from reserve_native_handoff import open_reviewed
    from reserve_exit_custody import authorized_row
    require(os.geteuid()==producer.uid and producer.rebind_dir is not None,'recovery_native_identity')
    require(len({pinned_key(p) for p in (relay_pin,producer.native_pin,producer.human_pin,producer.rebind_pin)})==4,'recovery_independent_pins')
    with open_reviewed(inbox,producer.workspace,store,relay_pin,native_uid=producer.uid,native_gid=native_gid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid,clock=producer.clock) as (db,_):
        _paused(db)
        rows=db.execute("SELECT id,instance_id FROM gate_intents WHERE state IN ('CLAIMED','UNKNOWN')").fetchall()
        require(len(rows)==1,'recovery_single_unresolved_intent');intent,instance=rows[0]
        work=workspace(producer.data,instance)
        with custody(work):
            bound=verify_rebind(db,producer.data,producer.rebind_dir,producer.rebind_pin,instance,native_uid=producer.uid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid)
            key,binding=workspace_key(producer.data,instance);require(bound['binding']==binding,'recovery_binding_changed')
            row=authorized_row(db,instance,intent,binding)
            full,ready=seal(work,key,row,binding,int(producer.clock()))
            cents=db.execute('SELECT virtual_cents FROM instances WHERE id=?',(instance,)).fetchone()[0]
            with factory(instance,row['pair'],cents,work) as driver:
                require(isinstance(driver,AuthenticatedNativeDriver) and driver.instance==instance and driver.binding==binding and driver.path==work/'paper.sqlite' and hmac.compare_digest(driver.key,key),'recovery_driver_custody')
                driver.preflight(instance,row['pair'])
                with closing(sqlite3.connect(driver.path.as_uri()+'?mode=ro',uri=True)) as ndb:
                    require(not ndb.execute('SELECT 1 FROM trades WHERE enter_tag=?',('mv:'+intent,)).fetchone(),'recovery_native_trade_exists')
                wallet=driver.observation(row);require(wallet['qty']==0,'recovery_native_not_flat')
                count,head,boot,anchor=_balance(db,instance,wallet['quote'])
                b=_body(producer.workspace,full,binding,ready,count,head,boot,anchor,wallet['quote'],int(producer.clock()))
                result=_signed(producer._key(),NATIVE_DOMAIN,b)
    return result


class Review:
    """Independent local human authority; no desk signing endpoint."""
    def __init__(self,workspace,credentials,native_pin,human_pin,*,authority_uid,desk_uid,clock=time.time,monotonic=time.monotonic):
        require(os.geteuid()==authority_uid and authority_uid!=desk_uid,'recovery_review_identity')
        self.workspace,self.credentials,self.native_pin,self.human_pin=workspace,credentials,native_pin,human_pin
        self.ids=dict(authority_uid=authority_uid,desk_uid=desk_uid);self.clock,self.monotonic=clock,monotonic;self.pending=None
    def _check(self,p):
        native,human=pinned_key(self.native_pin),pinned_key(self.human_pin);require(native!=human,'recovery_independent_keys')
        b=verify_native(native,self.workspace,p);require(0<=int(self.clock())-b['at']<AGE,'recovery_evidence_stale');return b,human
    def cancel(self):self.pending=None
    def begin(self,p):
        self.cancel();b,_=self._check(p);challenge='RELEASE '+secrets.token_hex(4)
        self.pending=(copy.deepcopy(p),challenge,self.monotonic()+AGE)
        from decimal import Decimal
        row=b['row'];reserved=format(Decimal(row['reserved'])/100,',.2f')
        return dict(challenge=challenge,display='PAPER only. Independent evidence proves the order call could not have started.\nPortfolio: '+row['instance_id']+'\nOrder: '+row['id']+'\nMarket: '+row['pair']+'\nUnused reservation: $'+reserved+'\nRelease this reservation and keep trading paused. No new order, profit or loss is booked.')
    def confirm(self,challenge,password):
        p=self.pending;self.cancel();require(p is not None,'recovery_review_missing');proof,expected,deadline=p
        require(type(challenge) is str and hmac.compare_digest(challenge,expected) and self.monotonic()<=deadline,'recovery_review_expired_or_mismatch')
        from reserve_enrollment import unlock
        seed=unlock(self.credentials,password,**self.ids);b,human=self._check(proof)
        require(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)==human,'recovery_human_pin')
        now=int(self.clock());require(self.monotonic()<=deadline and now<b['at']+AGE,'recovery_review_expired')
        return dict(proof=proof,approval=_signed(seed,HUMAN_DOMAIN,dict(workspace=self.workspace,proof=sha(proof),expires=min(now+AGE,b['at']+AGE))))


def consume(transaction,review,bindings,native_public,human_public,workspace,*,store,clock=time.time):
    require(native_public!=human_public and set(review)=={'proof','approval'},'recovery_approval_required')
    proof=review['proof'];b=verify_native(native_public,workspace,proof);a=_verify(human_public,HUMAN_DOMAIN,review['approval'])
    require(set(a)=={'workspace','proof','expires'} and a['workspace']==workspace and a['proof']==sha(proof) and type(a['expires']) is int and b['at']<a['expires']<=b['at']+AGE,'recovery_approval_scope')
    row=b['row'];intent=row['id'];instance=row['instance_id'];receipt='reviewed-no-effect:'+sha(review)
    with transaction() as db:
        witness=store.verify(db);current=db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone()
        require(current is not None and current['instance_id']==instance,'recovery_intent_missing')
        if current['state']=='NO_FILL':
            require(current['resolution_receipt']==receipt and event_detail(db,intent,'native_reviewed_no_effect')=={'receipt':sha(review),'proof':sha(proof)},'recovery_duplicate_conflict')
            return 'already_accounted'
        _paused(db);now=int(clock());require(b['at']<=now<a['expires'],'recovery_approval_expired')
        from reserve_entry_broker import offer
        require(current['state'] in ('UNKNOWN','CLAIMED') and current['claim_token'] and current['resolution_receipt'] is None and offer(current)==row and bindings(db,instance)==b['binding'],'recovery_claim_changed')
        require(event_detail(db,intent,'native_entry_ready')=={'receipt':b['ready']} and all((event_detail(db,intent,x) or {}).get('binding')==b['binding'] for x in ('native_bound','native_attempt')),'recovery_attempt_changed')
        require(not any(events(db,intent,x) for x in ('native_return','native_partial_accounted','native_accounted','native_exit_attempt','native_released','native_zero_released','resolution','native_reviewed_no_effect')),'recovery_prior_execution')
        require(_balance(db,instance,b['quote'])==(b['count'],b['head'],b['boot'],b['anchor']),'recovery_context_changed')
        require(not db.execute('SELECT 1 FROM gate_intents WHERE resolution_receipt=?',(receipt,)).fetchone(),'recovery_reused')
        db.execute("UPDATE gate_intents SET state='NO_FILL',resolution_receipt=? WHERE id=?",(receipt,intent))
        append_event(db,intent,'resolution',{'receipt':receipt,'outcome':'NO_FILL','proof':sha(proof),'wallet':{'quote':b['quote'],'qty':0}},now)
        append_event(db,intent,'native_reviewed_no_effect',{'receipt':sha(review),'proof':sha(proof)},now)
        require(store.verify(db)==witness,'recovery_witness_changed')
        return 'accounted'
