"""PAPER-only publicly verifiable flat-wallet evidence after native rebinding.

No order, generic signing endpoint or access to native secrets by the desk.
Publication checks the current serialized ledger, root-signed native binding and
one fresh independently observed wallet per instance. A valid observation grants
no entry or reserve authority by itself.
"""
import json
import re
import time
from contextlib import nullcontext
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from reserve_checkpoint import require
from reserve_ipc import canonical, scope, pinned_key

DOMAIN=b'maj-vaj:PAPER:flat-wallet-public:v1\0'
FIELDS={'instance','pair','binding','head','anchor','boot','at','quote','qty'}


def _body(workspace, wallet):
    require(type(wallet) is dict and set(wallet)==FIELDS,'public_wallet_fields')
    require(all(type(wallet[k]) is str and re.fullmatch('[0-9a-f]{64}',wallet[k]) for k in ('binding','head','anchor')),'public_wallet_digest')
    require(type(wallet['instance']) is str and re.fullmatch('[0-9a-f]{32}',wallet['instance']) and wallet['pair'] in ('BTC/USD','ETH/USD','SOL/USD'),'public_wallet_scope')
    require(all(type(wallet[k]) is int and wallet[k]>=0 for k in ('boot','at','quote','qty')),'public_wallet_amount')
    return {'v':1,'workspace':scope(workspace),'wallet':wallet}


def _sign_observed(seed, workspace, wallet):
    # Private local composition only: caller obtains a real native observation.
    body=_body(workspace,wallet)
    return {'body':body,'signature':Ed25519PrivateKey.from_private_bytes(seed).sign(DOMAIN+canonical(body)).hex()}


def verify_observation(public, workspace, packet):
    require(type(public) is bytes and len(public)==32,'public_wallet_key')
    require(type(packet) is dict and set(packet)=={'body','signature'} and type(packet['signature']) is str and re.fullmatch('[0-9a-f]{128}',packet['signature']),'public_wallet_envelope')
    b=packet['body'];require(type(b) is dict and set(b)=={'v','workspace','wallet'} and b==_body(workspace,b['wallet']),'public_wallet_scope')
    try:Ed25519PublicKey.from_public_bytes(public).verify(bytes.fromhex(packet['signature']),DOMAIN+canonical(b))
    except (InvalidSignature,ValueError) as exc:raise ValueError('public_wallet_signature') from exc
    return b['wallet']


def accounts(db, packets, binding_for, public, workspace, now, head, anchor, boot):
    from financial_ledger import replay, observed, amount
    require(db.in_transaction and type(now) is int,'public_wallet_transaction')
    states,_,_,_=replay(db)
    instances={r[0]:r[1:] for r in db.execute('SELECT id,pair,mode FROM instances')}
    require(type(packets) is dict and set(packets)==set(instances)==set(states) and 0<len(instances)<=128,'public_wallet_complete_scope')
    result={}
    for i in sorted(instances):
        b=verify_observation(public,workspace,packets[i]);s=states[i]
        require((b['instance'],b['pair'],instances[i][1],b['binding'])==(i,instances[i][0],'PAPER',binding_for(db,i)),'public_wallet_binding')
        require((b['head'],b['anchor'],b['boot'])==(head,anchor,boot) and 0<=now-b['at']<=30,'public_wallet_stale')
        require(not s['lots'] and not s['sales'] and amount(b['qty'])==0 and amount(b['quote'])==s['active']+s['protected'],'public_wallet_mismatch')
        result[i]=observed(s)|{'covered_qty':0}
    return result


def rebound_bindings(directory, pin, *, native_uid, desk_uid, authority_uid):
    from reserve_native_rebind import verify_record
    def binding_for(db,i):
        return verify_record(db,directory,pin,i,native_uid=native_uid,desk_uid=desk_uid,authority_uid=authority_uid)['new']['binding']
    return binding_for


def publish(transaction, packets, binding_for, public, workspace, *, clock=time.time):
    from financial_ledger import replay, execution_anchor, UNRELEASED, FinancialLedger, sha
    with transaction() as db:
        now=int(clock());_,_,_,head=replay(db);anchor=execution_anchor(db);boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
        require(not db.execute("SELECT 1 FROM gate_intents WHERE state IN ('CLAIMED','UNKNOWN','FILLED_HELD') AND "+UNRELEASED+" LIMIT 1").fetchone(),'unresolved_execution')
        require(not db.execute("SELECT 1 FROM gate_intents WHERE state='AUTHORIZED' LIMIT 1").fetchone(),'authorization_in_flight')
        verified=accounts(db,packets,binding_for,public,workspace,now,head,anchor,boot)
        body={'observed':min(p['body']['wallet']['at'] for p in packets.values()),'evidence_digest':sha(packets),'accounts':verified,'open_orders':[],'worker_evidence':packets}
        ledger=FinancialLedger(lambda:nullcontext(db),clock=clock)
        return ledger._append('workers:'+sha(packets),'desk','desk_reconcile',body)


def projection(binding_for, public, workspace):
    """Suitable for native or desk checks; native may additionally check disk binding."""
    from dataclasses import asdict
    from financial_ledger import projection_values, sha, execution_anchor
    from risk_gate import RiskState, digest, GateRefusal
    import sqlite3
    def project(db,now):
        try:
            values=projection_values(db,now)
            row=db.execute('SELECT previous,body FROM fin_events ORDER BY seq DESC LIMIT 1').fetchone()
            p=json.loads(row[1])['payload'];packets=p.get('worker_evidence')
            require(type(packets) is dict and sha(packets)==p['evidence_digest'],'public_wallet_evidence_required')
            require(p['execution_anchor']==execution_anchor(db),'public_wallet_execution_changed')
            verified=accounts(db,packets,binding_for,public,workspace,now,row[0],p['execution_anchor'],p['boot'])
            require(verified==p['accounts'] and p['observed']==min(q['body']['wallet']['at'] for q in packets.values()),'public_wallet_evidence_mismatch')
            risk=RiskState(**values);risk.check(now);return risk,digest(asdict(risk))
        except (ValueError,OSError,KeyError,TypeError,sqlite3.Error) as exc:raise GateRefusal('financial:'+str(exc)) from exc
    return project


def installed_projection(native_pin, rebind_pin, rebind_directory, human_pin, workspace, **ids):
    native=pinned_key(native_pin);rebind=pinned_key(rebind_pin);human=pinned_key(human_pin)
    require(len({native,rebind,human})==3,'public_wallet_independent_pins')
    return projection(rebound_bindings(rebind_directory,rebind_pin,**ids),native,workspace)
