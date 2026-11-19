"""Dashboard draft and review only. No approval key, ledger mutation or scheduler."""
import copy
import json
import time
from contextlib import nullcontext
from uuid import uuid4

from financial_ledger import CENT, LIMIT, ZERO, amount, canon, replay, require, sha, AccountingError
from financial_goals import scope, usd
from reserve_policy import DAY, ReserveManager, evaluate, surplus, validate

DOLLARS=('floor_fixed','ceiling','cycle_cap','lifetime_cap')


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS reserve_drafts(instance TEXT NOT NULL, version INTEGER NOT NULL, id TEXT NOT NULL UNIQUE, at INTEGER NOT NULL, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(instance,version))')


def history(db,instance):
    prev=ZERO; rows=[]
    for v,ident,at,raw,old,digest in db.execute('SELECT version,id,at,body,previous,digest FROM reserve_drafts WHERE instance=? ORDER BY version',(instance,)):
        p=validate(json.loads(raw))
        require(v==len(rows)+1 and old==prev and raw==canon(p) and digest==sha([instance,v,ident,at,p,old]),'draft_integrity')
        require(not rows or at>=rows[-1]['at'],'draft_clock')
        rows.append(dict(version=v,id=ident,at=at,policy=p,digest=digest));prev=digest
    return rows


def policy_view(p):
    return {k:(p[k]//CENT if k in DOLLARS else p[k]) for k in p}


def record_view(r):
    return dict(version=r['version'],id=r['id'],at=r['at'],policy=policy_view(r['policy'])) if r else None


def read(db,instance,now):
    scope(db,instance);rows=history(db,instance);states,_,_,head=replay(db)
    s=states.get(instance);r=s['reserve'] if s else None
    if s:
        pair,principal,_=scope(db,instance)
        require(s['pair']==pair.replace('/','-') and -s['capital']==principal*CENT,'draft_scope_changed')
    disputed=bool(db.execute('SELECT 1 FROM fin_conflicts LIMIT 1').fetchone() or (s and s['correction_locked']))
    active=r['active'] if r else None; p=active['policy'] if active else None
    current='off' if not active else 'expired' if now>=min(p['end'],p['expiry']) else 'scheduled' if now<p['start'] else 'recorded_authorization'
    return rows,states,s,head,disputed,dict(mode='PAPER',instance=instance,as_of=now,version=len(rows),draft=record_view(rows[-1]) if rows else None,
        current_state='review_required' if disputed else current,recorded_policy=({'id':active['version'],'policy':policy_view(p)} if active else None),
        active_cash_usd=usd(s['active']) if s and not disputed else None,protected_cash_usd=usd(s['protected']) if s and not disputed else None,
        released_usd=usd(r['released']) if r and not disputed else None,approval_available=False,default_rate_bps=0,journal_head=head)


def status(transaction,instance,*,clock=time.time):
    with transaction() as db:return read(db,instance,int(clock()))[-1]


def save(transaction,instance,policy,expected_version,*,clock=time.time):
    p=validate(policy);require(type(expected_version) is int and expected_version>=0,'invalid_draft_version')
    with transaction() as db:
        now=int(clock());rows,_,_,_,_,_=read(db,instance,now)
        require(len(rows)==expected_version and len(rows)<LIMIT,'draft_changed')
        require(not rows or now>=rows[-1]['at'],'draft_clock')
        require(now<p['end'] and now<p['expiry'],'expired_draft')
        v=len(rows)+1;ident=uuid4().hex;prev=rows[-1]['digest'] if rows else ZERO
        digest=sha([instance,v,ident,now,p,prev]);db.execute('INSERT INTO reserve_drafts VALUES(?,?,?,?,?,?,?)',(instance,v,ident,now,canon(p),prev,digest))
        return record_view(dict(version=v,id=ident,at=now,policy=p))


def preview(transaction,instance,policy,hypothetical_gain,*,clock=time.time,verify=None):
    p=validate(policy);amount(hypothetical_gain);require(hypothetical_gain%CENT==0,'cent_precision_required')
    with transaction() as db:
        now=int(clock());_,states,s,head,disputed,_=read(db,instance,now)
        out=dict(mode='PAPER',journal_head=head,as_of=now,actual=None,example=None,
                 approval_available=False,reason='financial_records_unavailable')
        if not s or disputed:return out
        manager=ReserveManager(lambda:nullcontext(db),clock=lambda:now)
        # Authenticated whole-desk projection is mandatory for an actual preview.
        if verify is not None:
            try:
                verify(db,now)
                a=manager.preview(instance,p)
                out['actual']=dict(**{k+'_usd':usd(a[k]) for k in ('active_cash','protected_cash','release','floor','eligible_surplus','free_surplus','desk_room')},next_cycle=a['next_cycle'],reason=a['reason'])
                out['reason']='verified_booked_preview'
            except (ValueError,OSError,KeyError,TypeError):pass
        # Hypothetical next cycle after a NEW approval. Current surplus is
        # excluded exactly as reserve_activate does. No risk authority: assume
        # future reconciliation and risk eligibility only for the illustration.
        sim=copy.deepcopy(s);r=sim['reserve'];now_cycle=(now-p['anchor'])//DAY
        ref,_,eligible,_=surplus(sim,p)
        r.update(last_cycle=now_cycle,cursor=max(r['cursor'],eligible),reference=ref,policy_released=0)
        next_cycle=(now_cycle+1)*DAY+p['anchor']
        sim['gains']=amount(sim['gains']+hypothetical_gain);sim['protected']=amount(sim['protected']+hypothetical_gain)
        cap=db.execute('SELECT virtual_cap_cents FROM desk WHERE id=1').fetchone()[0]
        allocated=db.execute('SELECT COALESCE(SUM(virtual_cents),0) FROM instances').fetchone()[0]*CENT
        room=max(0,cap*CENT-allocated-sum(x['reserve']['released'] for x in states.values()))
        result=evaluate(sim,p,now=next_cycle,room=room,allowed=not any(x['losses'] or x['correction_locked'] or x['sales'] for x in states.values()))
        _,floor,eligible,free=surplus(sim,p);n=result['release']
        out['example']=dict(conditional=True,next_cycle=next_cycle,reason=result['reason'],hypothetical_gain_usd=usd(hypothetical_gain),
            active_before_usd=usd(sim['active']),protected_before_usd=usd(sim['protected']),active_after_usd=usd(sim['active']+n),protected_after_usd=usd(sim['protected']-n),
            release_usd=usd(n),floor_usd=usd(floor),incremental_eligible_usd=usd(max(0,eligible-r['cursor'])),free_surplus_usd=usd(free),desk_room_usd=usd(room))
        return out


def review(transaction,instance,expected_version,*,clock=time.time):
    with transaction() as db:
        now=int(clock());rows,_,_,head,disputed,_=read(db,instance,now)
        require(type(expected_version) is int and rows and len(rows)==expected_version,'draft_changed')
        r=rows[-1];require(not disputed and now<r['policy']['end'] and now<r['policy']['expiry'],'review_unavailable')
        # Informational exact review only. A separate broker must independently
        # validate, persist its own reserve_save, review current evidence and
        # authenticate a human before issuing any reserve activation signature.
        return dict(instance=instance,mode='PAPER',draft_version=r['version'],draft_id=r['id'],journal_head=head,
            policy=policy_view(r['policy']),broker_payload=canon({'version':r['id'],'policy':r['policy']}),
            approval_available=False,authorized=False,expires=now+300)
