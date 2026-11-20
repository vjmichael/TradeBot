"""Protected PAPER reserve event derivation and transaction admission.

No orders, arbitrary ledger mutations or real funds. Installed callers must
use root-pinned configuration and migrated, independently owned native records.
"""
import copy
import json
import time
from contextlib import nullcontext
from dataclasses import asdict

from reserve_checkpoint import require, verify_prefix, reserve_events
from reserve_recovery import event_checkpoint, verify_packet


def derive_event(db, previous, instance, action, receipt, recorded_at, now, verify):
    """Reproduce the only admissible next event from the current transaction.

    'verify' is trusted local code, not an RPC argument. Adoption and tick
    require a verified whole-desk projection, with actual native evidence on
    the producer. The desk independently replays the financial constraints.
    """
    from financial_ledger import replay, canon, ident, sha, apply, LIMIT
    from reserve_policy import ReserveManager, DAY, evaluate
    from reserve_preview import read
    require(db.in_transaction, 'serialized_desk_transaction_required')
    require(type(now) is int and type(recorded_at) is int and 0 <= now-recorded_at <= 30,
            'reserve_event_stale')
    ident(instance); ident(receipt)
    require(action in ('adopt', 'revoke', 'tick'), 'unsupported_protected_action')
    verify_prefix(db, previous)
    require(not reserve_events(db, previous.seq), 'unwitnessed_reserve_event')
    states, last, count, head = replay(db)
    require(count < LIMIT and instance in states, 'reserve_scope_or_capacity')
    row=db.execute('SELECT mode FROM instances WHERE id=?',(instance,)).fetchone()
    require(row is not None and row[0]=='PAPER', 'paper_instance_required')
    require(db.execute('SELECT 1 FROM fin_events WHERE id=?',(receipt,)).fetchone() is None,
            'reserve_receipt_already_recorded')
    s=states[instance]; r=s['reserve']; active=r['active']
    manager=ReserveManager(lambda:nullcontext(db),clock=lambda:recorded_at)
    if action=='adopt':
        drafts,_,_,draft_head,disputed,_=read(db,instance,now)
        require(drafts and not disputed and draft_head==head, 'review_unavailable')
        draft=drafts[-1]; policy=draft['policy']; version=draft['id']
        require(now<min(policy['end'],policy['expiry']) and version not in r['activated'],
                'expired_or_previously_activated')
        require(not db.execute("SELECT 1 FROM fin_events WHERE instance=? AND kind IN ('reserve_save','reserve_adopt') AND json_extract(body,'$.payload.payload.version')=?",(instance,version)).fetchone(), 'policy_version_reused')
        require(verify is not None, 'native_evidence_required'); verify(db,now)
        manager._limits(db,states,now)
        request={'version':version,'policy':policy}; payload=request
    elif action=='revoke':
        require(active is not None,'reinvestment_off')
        request={'version':active['version']}; payload=request
    else:
        require(active is not None, 'reinvestment_off')
        p=active['policy']; cycle=(recorded_at-p['anchor'])//DAY
        require(cycle==(now-p['anchor'])//DAY and cycle>r['last_cycle'], 'stale_cycle')
        require(receipt=='cycle:'+sha([instance,active['version'],cycle]), 'cycle_receipt_identity')
        require(verify is not None,'native_evidence_required'); verify(db,now)
        room=manager._limits(db,states,now)
        request={'version':active['version'],'cycle':cycle}
        result=evaluate(s,p,now=recorded_at,room=room,allowed=True)
        # Do not advance a positive result if the grace window/time eligibility
        # has elapsed during delivery. Any change requires a fresh proposal.
        require(result==evaluate(s,p,now=now,room=room,allowed=True),'cycle_context_changed')
        payload={'version':active['version'],'room':room,'allowed':True,'result':result}
    kind='reserve_'+action; body={'at':recorded_at,'payload':copy.deepcopy(payload)}
    apply(states,last,instance,kind,receipt,body)
    event={'request_hash':sha([instance,action,request,None]),'payload':body,'recorded_at':recorded_at}
    seq=count+1; digest=sha([seq,receipt,instance,kind,event,head])
    result={'seq':seq,'id':receipt,'instance':instance,'kind':kind,'body':event,'previous':head,'digest':digest}
    event_checkpoint(result)
    return result


def commit_packet(transaction, client, packet, human_public, native_public, *, clock=time.time, verify_projection=None):
    """Desk-only transaction entry shared by human delivery and scheduled ticks.

    The pinned native/human permit authenticates the independent authorization;
    the desk also reproduces the proposed event and replays the real ledger.
    Any interrupted authority advance requires explicit protected recovery.
    """
    from financial_ledger import replay, canon, projection_values
    with transaction() as db:
        now=int(clock())
        prev,nxt=verify_packet(human_public,client.workspace,packet,now,native_public=native_public)
        require(client.verify_transaction(db)==prev,'protected_checkpoint_changed')
        e=packet['event']; recorded=e['body']['recorded_at']
        if e['kind']=='reserve_adopt':
            require(type(native_public) is bytes and len(native_public)==32 and native_public!=human_public,'independent_native_key_required')
        # Installed callers must supply the independent public-wallet verifier.
        if client.workspace=='installed-paper' and e['kind'] in ('reserve_adopt','reserve_tick'):
            require(verify_projection is not None,'installed_public_wallet_verification_required')
        require(e==derive_event(db,prev,e['instance'],e['kind'].removeprefix('reserve_'),e['id'],recorded,now,verify_projection or projection_values),
                'protected_event_context_changed')
        db.execute('INSERT INTO fin_events VALUES(?,?,?,?,?,?,?)',(e['seq'],e['id'],e['instance'],e['kind'],canon(e['body']),e['previous'],e['digest']))
        db.execute('UPDATE fin_head SET count=?,digest=? WHERE id=1',(nxt.seq,nxt.digest))
        require(replay(db)[2:4]==(nxt.seq,nxt.digest),'protected_replay_mismatch')
        client.advance_event(db,prev,packet,human_public,replay,clock=clock,native_public=native_public)
        result={'checkpoint':asdict(nxt),'receipt':e['id'],'kind':e['kind'],'release':e['body']['payload']['payload'].get('result',{}).get('release',0)}
    return result


# This repository's installed Linux desk has a fixed location. Tests and
# portable fixture APIs do not configure an operator installation.
def installed_desk(directory):
    from pathlib import Path
    return Path(directory).resolve()==Path('/var/lib/maj-vaj/data').resolve()


def refuse_legacy_installed(directory):
    require(not installed_desk(directory),'protected_reserve_installation_required')


def guard_installed_projection(directory, projection):
    """Paper entry stays fail-closed for installed reserve history. The
    existing protective exit path is not dependent on this entry projection.
    """
    if not installed_desk(directory):return projection
    def project(db,now):
        import pwd
        import os
        from reserve_recovery import RecoveryClient
        from risk_gate import GateRefusal
        try:
            if reserve_events(db) or os.path.lexists('/etc/maj-vaj/reserve-protected-enabled'):
                authority=pwd.getpwnam('maj-vaj-approval').pw_uid;desk=pwd.getpwnam('maj-vaj').pw_uid
                RecoveryClient('/run/maj-vaj-reserve-authority',authority_uid=authority,desk_uid=desk,workspace='installed-paper').verify_transaction(db)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise GateRefusal('protected_reserve_unavailable') from exc
        return projection(db,now)
    return project
