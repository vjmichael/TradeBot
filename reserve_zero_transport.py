"""Signed zero-fill PAPER cancellation; absence of an order is never proof."""
import re
import time
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from financial_ledger import amount, ident, observed, replay, require, sha, CENT, SCALE
from paper_execution import append_event, event_detail, units
from paper_supervisor import events
from reserve_ipc import canonical, scope

DOMAIN=b'maj-vaj:PAPER:zero-cancel:v1\0'


def body(workspace,instance,intent,binding,count,head,zero):
    ident(instance);ident(intent)
    require(type(count) is int and count>0 and all(type(x) is str and re.fullmatch('[0-9a-f]{64}',x) for x in (head,binding)),'zero_prefix')
    require(type(zero) is dict and set(zero)=={'order','qty','price','quote','at'},'zero_schema')
    require(type(zero['order']) is str and re.fullmatch('[0-9a-f]{64}',zero['order']),'zero_order')
    for k in ('qty','price','at'):amount(zero[k],True)
    amount(zero['quote'])
    return dict(v=3,workspace=scope(workspace),instance=instance,intent=intent,binding=binding,count=count,head=head,zero=zero)


def verify(public,workspace,packet):
    require(type(packet) is dict and set(packet)=={'body','signature'} and type(public) is bytes and len(public)==32,'zero_packet')
    b=packet['body'];require(type(b) is dict and set(b)=={'v','workspace','instance','intent','binding','count','head','zero'},'zero_packet_body')
    require(b==body(workspace,b['instance'],b['intent'],b['binding'],b['count'],b['head'],b['zero']),'zero_scope')
    sig=packet['signature'];require(type(sig) is str and re.fullmatch('[0-9a-f]{128}',sig),'zero_signature')
    try:Ed25519PublicKey.from_public_bytes(public).verify(bytes.fromhex(sig),DOMAIN+canonical(b))
    except (InvalidSignature,ValueError) as exc:raise ValueError('zero_signature') from exc
    return b


def produce(producer,db,row,driver):
    """Caller holds independently rebound native workspace lease and snapshot."""
    from reserve_cancel_custody import NativeCancellationDriver
    require(isinstance(driver,NativeCancellationDriver) and driver.instance==row['instance_id'],'zero_native_driver')
    raw=driver.frozen(row)
    require(raw is not None and raw['status']=='canceled' and raw['side']=='buy' and raw['type']=='limit' and raw['symbol']==row['pair'] and str(raw['id']).startswith('dry_run_buy_'),'zero_cancel_proof')
    require(units(raw['filled'])==units(raw['cost'])==0 and raw['fee'] is None and units(raw['amount'])==units(raw['remaining']),'zero_cancel_fill')
    wallet=driver.observation(row);require(wallet['qty']==0,'zero_native_wallet')
    _,_,count,head=replay(db)
    proof=dict(order=sha([row['pair'],raw['id']]),qty=units(raw['amount']),price=units(raw['price']),quote=wallet['quote'],at=int(producer.clock()))
    b=body(producer.workspace,row['instance_id'],row['id'],driver.binding,count,head,proof)
    return {'body':b,'signature':Ed25519PrivateKey.from_private_bytes(producer._key()).sign(DOMAIN+canonical(b)).hex()}


def _scope(db,b,binding_for):
    _,_,count,_=replay(db);row=db.execute('SELECT digest FROM fin_events WHERE seq=?',(b['count'],)).fetchone()
    require(0<b['count']<=count and row is not None and row[0]==b['head'],'zero_prefix_changed')
    row=db.execute('SELECT instance_id,state,entry FROM gate_intents WHERE id=?',(b['intent'],)).fetchone()
    require(row is not None and row[0]==b['instance'] and row[1] in ('CLAIMED','UNKNOWN','NO_FILL') and row[2]==b['zero']['price'],'zero_intent_scope')
    require(binding_for(db,b['instance'])==b['binding'],'zero_binding_changed')
    for action in ('native_bound','native_attempt'):
        detail=event_detail(db,b['intent'],action)
        require(detail is not None and detail.get('binding')==b['binding'],'zero_attempt_binding')
    return row[1]


def acknowledged(db,packet,binding_for,public,workspace):
    b=verify(public,workspace,packet);state=_scope(db,b,binding_for)
    old=event_detail(db,b['intent'],'native_zero_released')
    if state!='NO_FILL':
        require(old is None,'zero_ack_conflict');return False
    require(old=={'binding':b['binding'],'receipt':sha(packet)},'zero_ack_conflict')
    return True


def consume(transaction,packet,binding_for,public,workspace,*,store,clock=time.time):
    b=verify(public,workspace,packet);z=b['zero'];intent=b['intent']
    with transaction() as db:
        witness=store.verify(db)
        if acknowledged(db,packet,binding_for,public,workspace):return {'state':'NO_FILL','duplicate':True}
        require(z['at']<=int(clock()),'zero_future_observation')
        current=db.execute('SELECT claim_token,notional,resolution_receipt FROM gate_intents WHERE id=?',(intent,)).fetchone()
        require(current[0] and current[2] is None and z['qty']*z['price']//SCALE<=current[1]*CENT,'zero_claim_bound')
        require(not any(events(db,intent,x) for x in ('native_partial_accounted','native_accounted','native_exit_attempt','native_released','resolution')),'zero_prior_execution')
        states,_,_,_=replay(db);s=states[b['instance']];balance=observed(s)
        require(not s['correction_locked'] and not db.execute('SELECT 1 FROM fin_conflicts LIMIT 1').fetchone(),'zero_financial_conflict')
        require(balance['qty']==balance['pending']==0 and balance['active']+balance['protected']==z['quote'],'zero_wallet_mismatch')
        receipt='zero:'+sha(packet)
        require(not db.execute('SELECT 1 FROM gate_intents WHERE resolution_receipt=?',(receipt,)).fetchone(),'zero_receipt_reused')
        db.execute("UPDATE gate_intents SET state='NO_FILL',resolution_receipt=? WHERE id=?",(receipt,intent))
        append_event(db,intent,'resolution',{'receipt':receipt,'outcome':'NO_FILL','proof':sha(packet),'wallet':{'quote':z['quote'],'qty':0}},int(clock()))
        append_event(db,intent,'native_zero_released',{'binding':b['binding'],'receipt':sha(packet)},int(clock()))
        require(store.verify(db)==witness,'zero_witness_changed')
        return {'state':'NO_FILL','duplicate':False}
