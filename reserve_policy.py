"""TB-FIN-01C: internal PAPER reserve policy; no external money movement."""
import copy
import hashlib
import hmac
import json
import time
from financial_ledger import (CENT, MAX, LIMIT, amount, canon, ident, require,
                              sha, replay, apply, projection_values, AccountingError, UNRELEASED)

DAY = 86400
GRACE = 300
BUCKETS = ('tax', 'household', 'emergency', 'savings')
FIELDS = {'rate_bps','floor_mode','floor_fixed','floor_bps','ceiling','cycle_cap',
          'lifetime_cap','anchor','start','end','expiry'}


def fresh():
    return dict(earmarks=dict.fromkeys(BUCKETS,0), released=0, saved=None, active=None,
                cursor=0, reference=0, last_cycle=None, last_time=0, policy_released=0, activated=[])


def validate(policy):
    require(type(policy) is dict and set(policy)==FIELDS,'invalid_policy_fields')
    for k in FIELDS-{'floor_mode'}:
        amount(policy[k])
    p=policy
    require(0<p['rate_bps']<=10000 and p['floor_bps']<=10000,'invalid_rate')
    require(p['floor_mode'] in ('fixed','percent','combined'),'invalid_floor')
    require(p['floor_mode']!='fixed' or p['floor_bps']==0,'unused_floor')
    require(p['floor_mode']!='percent' or p['floor_fixed']==0,'unused_floor')
    require(0<p['cycle_cap']<=p['lifetime_cap']<=p['ceiling']<=MAX,'invalid_caps')
    require(all(p[k]%CENT==0 for k in ('floor_fixed','ceiling','cycle_cap','lifetime_cap')),'cent_precision_required')
    require(p['anchor']<DAY and 0<p['start']<p['end']<=p['expiry'] and p['expiry']-p['start']<=90*DAY,'invalid_window')
    return copy.deepcopy(p)


def surplus(s,p):
    r=s['reserve']
    ref=max(r['reference'],s['gains'])
    floor=max(p['floor_fixed'],(ref*p['floor_bps']+9999)//10000)
    earmarked=sum(r['earmarks'].values())
    return ref,floor,max(0,s['gains']-earmarked-floor),max(0,s['protected']-earmarked-floor)


def evaluate(s,p, *, now, room, allowed):
    amount(room); require(type(allowed) is bool,'invalid_risk_disposition')
    r=s['reserve']; ref,floor,eligible,free=surplus(s,p)
    cycle=(now-p['anchor'])//DAY
    increment=max(0,eligible-r['cursor'])
    reason='eligible'
    if not p['start']<=now<p['end'] or now>=p['expiry']:
        reason='outside_approval'
    elif r['last_cycle'] is None or cycle!=r['last_cycle']+1 or (now-p['anchor'])%DAY>GRACE:
        reason='missed_cycle'
    elif not allowed or s['losses'] or s['correction_locked'] or s['sales']:
        reason='financial_lock'
    n=0 if reason!='eligible' else min(increment*p['rate_bps']//10000,free,room,
          p['cycle_cap'],p['lifetime_cap']-r['policy_released'],p['ceiling']-(-s['capital']+r['released']))
    n=max(0,n)//CENT*CENT
    return dict(cycle=cycle,reference=ref,cursor=max(r['cursor'],eligible),release=n,reason=reason)


def move(s,n):
    r=s['reserve']; amount(n); require(n%CENT==0,'cent_precision_required')
    require(not s['losses'] and not s['correction_locked'] and not s['sales'],'financial_lock')
    require(n<=s['protected']-sum(r['earmarks'].values()),'reserve_insufficient')
    s['protected']-=n; s['active']+=n; r['released']+=n


def apply_event(s,kind,body):
    """Replay the immutable policy receipt; never call clock or external services."""
    r=s['reserve']; now=amount(body['at'],True)
    require(now>=r['last_time'],'reserve_clock_rollback')
    action=kind.removeprefix('reserve_'); p=body['payload']
    if action=='save':
        r['saved']={'version':ident(p['version']),'policy':validate(p['policy'])}
    elif action=='adopt':
        # One signed event makes the reviewed saved version active atomically.
        apply_event(s,'reserve_save',body)
        apply_event(s,'reserve_activate',body)
    elif action=='activate':
        require(r['saved'] is not None and r['saved']['version']==p['version'] and r['saved']['policy']==p['policy'],'unsaved_policy')
        policy=validate(p['policy'])
        require(p['version'] not in r['activated'],'policy_already_activated')
        r['activated'].append(p['version'])
        require(now<policy['end'] and now<policy['expiry'] and not s['losses'] and not s['correction_locked'],'activation_blocked')
        ref,_,eligible,_=surplus(s,policy)
        r.update(active=copy.deepcopy(r['saved']),reference=ref,cursor=max(r['cursor'],eligible),
                 last_cycle=(now-policy['anchor'])//DAY,policy_released=0)
    elif action=='revoke':
        require(r['active'] is not None and r['active']['version']==p['version'],'no_matching_active_policy')
        r['active']=None
    elif action=='earmark':
        require(type(p) is dict and set(p)==set(BUCKETS),'invalid_earmarks')
        require(sum(amount(v) for v in p.values())<=s['protected'],'earmark_exceeds_reserve')
        r['earmarks']=dict(p)
        if r['active']:
            ref,_,eligible,_=surplus(s,r['active']['policy'])
            r['reference']=ref; r['cursor']=max(r['cursor'],eligible)
    elif action=='tick':
        require(r['active'] is not None and r['active']['version']==p['version'],'no_matching_active_policy')
        policy=r['active']['policy']; result=evaluate(s,policy,now=now,room=p['room'],allowed=p['allowed'])
        require(type(p['allowed']) is bool and result==p['result'] and result['cycle']>r['last_cycle'],'cycle_mismatch')
        if result['release']:
            move(s,result['release']); r['policy_released']+=result['release']
        r.update(cursor=result['cursor'],reference=result['reference'],last_cycle=result['cycle'])
    elif action=='once':
        require(r['active'] is None,'standing_policy_active')
        n=amount(p['amount'],True); floor=amount(p['floor']); ceiling=amount(p['ceiling'],True)
        require(n<=p['room'] and n<=s['protected']-sum(r['earmarks'].values())-floor and -s['capital']+r['released']+n<=ceiling,'one_time_limit')
        move(s,n)
    else:
        raise AccountingError('unknown_reserve_event')
    r['last_time']=now
    s['reconciled']=False


class ReserveManager:
    """Internal service; injected key belongs solely to a future human approval broker.

    No default key, no public HTTP route, no model access. Test keys are fixtures.
    """
    def __init__(self, transaction, approval_key=None, *, clock=time.time, verify=None):
        self.verify=verify
        require(approval_key is None or type(approval_key) is bytes and len(approval_key)>=32,'invalid_approval_broker_key')
        self.tx=transaction; self.key=approval_key; self.clock=clock

    def _grant(self, db, receipt, instance, action, payload, grant, now, head):
        require(self.key is not None,'approval_broker_required')
        require(type(grant) is dict and set(grant)=={'expiry','head','signature'},'human_approval_required')
        expiry=amount(grant['expiry'],True)
        require(now<=expiry<=now+900 and (action=='revoke' or grant['head']==head),'stale_human_approval')
        # Restrictive revocation can race with a new observation; its exact policy
        # version still binds the signed request, without broadening authority.
        msg=canon(['maj-vaj:PAPER:reserve:v1',receipt,instance,action,payload,grant['head'],expiry]).encode()
        expected=hmac.new(self.key,msg,hashlib.sha256).hexdigest()
        require(type(grant['signature']) is str and hmac.compare_digest(expected,grant['signature']),'invalid_human_approval')

    def _limits(self,db,states,now):
        # No release while an order reservation or hard pause exists. Check the
        # independent Gate's actual current policy instead of trusting a caller flag.
        account=projection_values(db,now)
        paused,human,cap=db.execute('SELECT paused,human_pause,virtual_cap_cents FROM desk WHERE id=1').fetchone()
        require(not paused and not human,'desk_paused')
        require(not db.execute("SELECT 1 FROM gate_intents WHERE state IN ('AUTHORIZED','CLAIMED','UNKNOWN','FILLED_HELD') AND "+UNRELEASED+" LIMIT 1").fetchone(),'held_execution')
        row=db.execute("SELECT i.body,i.hash FROM gate_current c JOIN gate_inputs i ON i.kind=c.kind AND i.scope=c.scope AND i.receipt=c.receipt WHERE c.kind='policy' AND c.scope='desk'").fetchone()
        require(row is not None,'missing_risk_policy')
        p=json.loads(row[0]); require(sha(p)==row[1] and now<p['expires'],'risk_policy_expired_or_invalid')
        for field,limit in (('daily_loss_cents','daily_loss_limit_cents'),('weekly_loss_cents','weekly_loss_limit_cents'),('drawdown_cents','drawdown_limit_cents')):
            require(account[field]+account['open_risk_cents']<p[limit],'hard_risk_lock')
        require(sum(s['losses'] for s in states.values())==0,'realized_loss_lock')
        return max(0,cap*CENT-sum(-s['capital']+s['reserve']['released'] for s in states.values()))

    def preview(self,instance,policy):
        p=validate(policy)
        with self.tx() as db:
            states,_,_,head=replay(db); s=states[instance]; now=int(self.clock())
            try: room=self._limits(db,states,now); allowed=True
            except (AccountingError, KeyError, ValueError): room=0; allowed=False
            ref,floor,eligible,free=surplus(s,p)
            active=s['reserve']['active']
            sim=copy.deepcopy(s)
            if active is None or active['policy']!=p:
                # Saving/activating a new policy excludes existing surplus.
                sim['reserve'].update(last_cycle=(now-p['anchor'])//DAY,
                    cursor=max(sim['reserve']['cursor'],eligible),policy_released=0)
            return evaluate(sim,p,now=now,room=room,allowed=allowed)|{
                'journal_head':head,'simulation':True,'active_cash':s['active'],
                'protected_cash':s['protected'],'floor':floor,'eligible_surplus':eligible,
                'free_surplus':free,'desk_room':room,'next_cycle':((now-p['anchor'])//DAY+1)*DAY+p['anchor']}

    def submit(self,receipt,instance,action,payload,grant=None):
        ident(receipt); ident(instance); require(action in ('save','activate','adopt','revoke','earmark','tick','once'),'invalid_reserve_action')
        request=sha([instance,action,payload,grant])
        with self.tx() as db:
            now=amount(int(self.clock()),True)
            states,last,count,head=replay(db); require(instance in states,'unfunded_instance')
            old=db.execute('SELECT body FROM fin_events WHERE id=?',(receipt,)).fetchone()
            if old:
                require(json.loads(old[0])['request_hash']==request,'reserve_receipt_conflict')
                return {'duplicate':True,'receipt':receipt}
            s=states[instance]; r=s['reserve']; p=copy.deepcopy(payload)
            if self.key is None:
                require(action=='tick','approval_broker_required')
            if action not in ('save','tick') or (action=='save' and grant is not None):
                self._grant(db,receipt,instance,action,payload,grant,now,head)
            if action=='save':
                require(type(p) is dict and set(p)=={'version','policy'},'invalid_saved_policy')
                ident(p['version']); validate(p['policy'])
                require(not db.execute("SELECT 1 FROM fin_events WHERE instance=? AND kind IN ('reserve_save','reserve_adopt') AND json_extract(body,'$.payload.payload.version')=?",(instance,p['version'])).fetchone(),'policy_version_reused')
            if action in ('activate','adopt'):
                require(type(p) is dict and set(p)=={'version','policy'},'invalid_activation')
                if action=='adopt':
                    require(self.verify is not None,'independent_evidence_required')
                    self.verify(db,now)
                    require(not db.execute("SELECT 1 FROM fin_events WHERE instance=? AND kind IN ('reserve_save','reserve_adopt') AND json_extract(body,'$.payload.payload.version')=?",(instance,p['version'])).fetchone(),'policy_version_reused')
                self._limits(db,states,now)
            if action=='tick':
                require(r['active'] is not None,'reinvestment_off')
                policy=r['active']['policy']; cycle=(now-policy['anchor'])//DAY
                require(p=={} or p=={'version':r['active']['version'],'cycle':cycle},'cycle_authority_changed')
                require(now>=r['last_time'],'reserve_clock_rollback')
                if cycle<=r['last_cycle']:
                    return {'duplicate':True,'receipt':receipt,'release':0}
                try:
                    if self.verify is not None:self.verify(db,now)
                    room=self._limits(db,states,now); allowed=True
                except (AccountingError,OSError,KeyError,ValueError): room=0; allowed=False
                p={'version':r['active']['version'],'room':room,'allowed':allowed,
                   'result':evaluate(s,policy,now=now,room=room,allowed=allowed)}
            if action=='revoke':
                require(type(p) is dict and set(p)=={'version'},'invalid_revocation')
            if action=='once':
                require(type(p) is dict and set(p)=={'amount','floor','ceiling'},'invalid_exact_release')
                p['room']=self._limits(db,states,now)
            require(count<LIMIT,'journal_capacity')
            kind='reserve_'+action; body={'at':now,'payload':p}
            apply(states,last,instance,kind,receipt,body)
            event={'request_hash':request,'payload':body,'recorded_at':now}
            if grant is not None:
                event['approval']=copy.deepcopy(grant)
            n=count+1; digest=sha([n,receipt,instance,kind,event,head])
            db.execute('INSERT INTO fin_events VALUES(?,?,?,?,?,?,?)',(n,receipt,instance,kind,canon(event),head,digest))
            db.execute('UPDATE fin_head SET count=?,digest=? WHERE id=1',(n,digest))
            return {'duplicate':False,'receipt':receipt,'release':p.get('result',{}).get('release',p.get('amount',0) if action=='once' else 0)}
