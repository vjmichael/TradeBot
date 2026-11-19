"""TB-FIN-01A: reporting-only PAPER financial plans. No order or pause authority."""
import calendar
from datetime import datetime, time as midnight, timedelta
from decimal import Decimal, localcontext
import json
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from financial_ledger import SCALE, CENT, ZERO, LIMIT, AccountingError, apply, canon, ident, observed, qty, replay, require, sha
from paper_custody import authenticate
import goal_evidence

FIELDS = {'expenses_cents','income_cents','savings_cents','extra_costs_cents','tax_bps','zone','target_kind','target_value'}
DEFAULT = dict(expenses_cents=0,income_cents=0,savings_cents=0,extra_costs_cents=0,tax_bps=0,zone='America/Phoenix',target_kind='none',target_value=0,pause_on_goal=False,conservative_bps=0)


def validate(p):
    require(type(p) is dict and FIELDS<=set(p)<=FIELDS|{'pause_on_goal','conservative_bps'},'invalid_goal_settings')
    for k in ('expenses_cents','income_cents','savings_cents','extra_costs_cents','target_value'):
        require(type(p[k]) is int and 0<=p[k]<=100_000_000_000,'invalid_goal_amount')
    require(type(p['tax_bps']) is int and 0<=p['tax_bps']<10000,'invalid_estimated_tax')
    require(p['target_kind'] in ('none','usd','points') and (p['target_kind']!='none' or p['target_value']==0),'invalid_primary_goal')
    require(type(p.get('pause_on_goal',False)) is bool and (not p.get('pause_on_goal') or (p['target_kind']!='none' and p['target_value']>0)),'invalid_goal_pause')
    bps=p.get('conservative_bps',0)
    require(type(bps) is int and (bps==0 or 100<=bps<=9900 and bps%100==0 and not p.get('pause_on_goal',False) and p['target_kind']!='none' and p['target_value']>0),'invalid_conservative_limit')
    require(p['target_kind']!='points' or p['target_value']<=1_000_000,'invalid_point_target')
    require(type(p['zone']) is str and 1<=len(p['zone'])<=100 and not p['zone'].startswith(('/', 'posix/', 'right/')) and '..' not in p['zone'],'invalid_reporting_zone')
    try:ZoneInfo(p['zone'])
    except (ZoneInfoNotFoundError,ValueError) as exc:raise AccountingError('invalid_reporting_zone') from exc
    return dict(p)


def initialize(db):
    goal_evidence.initialize(db)
    # Additive reporting tables; not read by Gate, order sizing or reserve policy.
    db.execute('CREATE TABLE IF NOT EXISTS goal_daily(instance TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(instance,day,kind))')
    db.execute('CREATE TABLE IF NOT EXISTS goal_versions(instance TEXT NOT NULL, version INTEGER NOT NULL, at INTEGER NOT NULL, effective_day TEXT NOT NULL, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(instance,version))')


def day_record(db,instance,day,kind):
    row=db.execute('SELECT body,digest FROM goal_daily WHERE instance=? AND day=? AND kind=?',(instance,day,kind)).fetchone()
    if row is None:return None
    body=json.loads(row[0]);require(row[0]==canon(body) and row[1]==sha([instance,day,kind,body]),'goal_daily_integrity')
    require(db.execute('SELECT 1 FROM fin_events WHERE digest=?',(body['journal_head'],)).fetchone(),'goal_daily_source_missing')
    return body


def versions(db,instance):
    prev=ZERO; result=[]
    for version,at,day,body,old,digest in db.execute('SELECT version,at,effective_day,body,previous,digest FROM goal_versions WHERE instance=? ORDER BY version',(instance,)):
        p=validate(json.loads(body));require(version==len(result)+1 and old==prev and body==canon(p) and digest==sha([instance,version,at,day,p,old]),'goal_history_integrity')
        require(not result or (at>=result[-1]['at'] and day>=result[-1]['effective_day'] and p['zone']==result[-1]['settings']['zone']),'goal_history_order')
        result.append(dict(version=version,at=at,effective_day=day,settings=p,digest=digest));prev=digest
    return result


def scope(db,i):
    ident(i);r=db.execute('SELECT pair,virtual_cents,mode FROM instances WHERE id=?',(i,)).fetchone()
    require(r is not None and r[2]=='PAPER','paper_instance_required');return r


def save(transaction,instance,p,expected_version,*,clock=time.time):
    p=validate(p);require(type(expected_version) is int and expected_version>=0,'invalid_goal_version')
    with transaction() as db:
        scope(db,instance);hist=versions(db,instance);now=int(clock())
        require(len(hist)==expected_version and len(hist)<LIMIT,'goal_settings_changed')
        if hist:
            require(now>=hist[-1]['at'],'goal_clock_rollback')
            # A zone change could redefine an already started day; defer such
            # migrations to a separately reviewed reporting-calendar transition.
            require(p['zone']==hist[0]['settings']['zone'],'reporting_zone_locked')
        d=datetime.fromtimestamp(now,ZoneInfo(p['zone'])).date()
        effective=(d+timedelta(days=1) if hist else d).isoformat()
        prev=hist[-1]['digest'] if hist else ZERO;v=len(hist)+1
        digest=sha([instance,v,now,effective,p,prev])
        db.execute('INSERT INTO goal_versions VALUES(?,?,?,?,?,?,?)',(instance,v,now,effective,canon(p),prev,digest))
        return dict(version=v,effective_day=effective,settings=p)


def bounds(now,zone):
    z=ZoneInfo(zone);d=datetime.fromtimestamp(now,z).date();month=d.replace(day=1)
    def ts(day):return int(datetime.combine(day,midnight.min,z).timestamp())
    return d.isoformat(),ts(d),ts(d+timedelta(days=1)),ts(month),calendar.monthrange(d.year,d.month)[1]


def fmt(d):
    if d is None:return None
    # Decimal output strings; no binary floating-point decisions. Display rounds only.
    return format(d,'.8f')


def usd(n):return None if n is None else fmt(Decimal(n)/SCALE)


def plan(p,days):
    with localcontext() as ctx:
        ctx.prec=48
        gap=max(0,p['expenses_cents']+p['savings_cents']+p['extra_costs_cents']-p['income_cents'])
        monthly=Decimal(gap)*100/(10000-p['tax_bps'])
        return dict(shortfall_usd=fmt(Decimal(gap)/100),monthly_pre_tax_usd=fmt(monthly),daily_plan_usd=fmt(monthly/days),days=days)


def boundary_base(db,instance,s,receipt,cutoff,key_for):
    """Only a signed flat-wallet observation immediately preceding midnight.

    Never use today's balance to backfill a missed opening snapshot. Historical
    receipt prefix stays immutable; baseline is derived from that durable receipt.
    """
    if not s or not receipt or receipt['kind']!='desk_reconcile' or key_for is None:return None,None
    p=receipt['payload']
    try:
        require(0<=cutoff-p['observed']<=30 and p['open_orders']==[] and not s['lots'] and not s['sales'] and not s['correction_locked'],'baseline_unavailable')
        # No execution action since the independent snapshot and before cutoff.
        require(not db.execute('SELECT 1 FROM gate_events WHERE at>=? AND at<? LIMIT 1',(p['observed'],cutoff)).fetchone(),'baseline_execution_changed')
        packets=p['worker_evidence'];require(sha(packets)==p['evidence_digest'],'baseline_evidence_changed')
        key,binding=key_for(instance);body=authenticate(key,'flat-wallet',packets[instance])
        require(body['instance']==instance and body['pair']==s['pair'].replace('-','/') and body['binding']==binding and body['head']==receipt['previous'] and body['boot']==p['boot'] and body['anchor']==p['execution_anchor'] and 0<=cutoff-body['at']<=30,'baseline_scope')
        require(body['qty']==0 and body['quote']==s['active']+s['protected'] and p['accounts'][instance]==observed(s)|{'covered_qty':0},'baseline_wallet')
        return s['active'],receipt['digest']
    except (AccountingError,KeyError,TypeError,ValueError,OSError):return None,None


def report(transaction,instance,*,clock=time.time,key_for=None):
    now=int(clock())
    with transaction() as db:
        row=scope(db,instance);hist=versions(db,instance);p=hist[-1]['settings'] if hist else DEFAULT
        day,cutoff,end,m_start,days=bounds(now,p['zone'])
        effective=next((v for v in reversed(hist) if v['effective_day']<=day),None)
        settings=effective['settings'] if effective else DEFAULT|{'zone':p['zone']}
        states,_,_,head=replay(db);s=states.get(instance)
        # Replay verified history into immutable cutoff snapshots. Ledger events
        # are attributed to journal posting time; prior-day late fills are not
        # silently assigned an invented exchange execution timestamp.
        running,last={},{};pre_day=None;old_at=0;base=receipt=evidence=None;cut_seen=False
        execution_day=execution_month=unknown_day=unknown_month=adjust_day=adjust_month=0
        def opening_snapshot():
            original,source=boundary_base(db,instance,running.get(instance),pre_day,cutoff,key_for)
            if source:return original,source,None
            if pre_day:
                value,proof=goal_evidence.opening(db,instance,running.get(instance),pre_day['digest'],cutoff,key_for)
                if proof:return value,pre_day['digest'],proof
            return None,None,None
        day_pl=month_pl=day_fees=month_fees=day_settled=0;valid=True
        for seq,e,i,k,raw,previous,digest in db.execute('SELECT * FROM fin_events ORDER BY seq'):
            body=json.loads(raw);at=body['recorded_at'];require(type(at) is int,'invalid_posting_time')
            if not old_at<=at<=now:valid=False
            old_at=at
            if at>=cutoff and not cut_seen:
                base,receipt,evidence=opening_snapshot()
                cut_seen=True
            before=running.get(instance);pl=-before['pl'] if before else 0;fees=before['fees'] if before else 0;settled=before['settled_pl'] if before else 0
            apply(running,last,i,k,e,body['payload'])
            cur=running.get(instance);dp=(-cur['pl'] if cur else 0)-pl;df=(cur['fees'] if cur else 0)-fees;ds=(cur['settled_pl'] if cur else 0)-settled
            if i==instance and k=='sell':
                interval=goal_evidence.attributed(db,instance,e,body,key_for)
                if interval is None:
                    unknown_day+=int(at>=cutoff);unknown_month+=int(at>=m_start)
                else:
                    lo,hi=interval
                    if lo>=cutoff and hi<end:execution_day+=dp
                    elif lo<cutoff<=hi:unknown_day+=1
                    if lo>=m_start:execution_month+=dp
                    elif lo<m_start<=hi:unknown_month+=1
            elif i==instance and k in ('correct','late_correct','execution_rounding'):
                if at>=cutoff:adjust_day+=dp
                if at>=m_start:adjust_month+=dp
            if at<cutoff:
                pre_day=dict(kind=k,payload=body['payload'],previous=previous,digest=digest)
            else:day_pl+=dp;day_fees+=df;day_settled+=ds
            if at>=m_start:month_pl+=dp;month_fees+=df
        if not cut_seen:base,receipt,evidence=opening_snapshot()
        if not valid:base=receipt=evidence=None
        if db.execute('SELECT 1 FROM fin_conflicts LIMIT 1').fetchone() or (s and s['correction_locked']):base=receipt=evidence=None
        if not valid or not s:day_pl=month_pl=day_fees=month_fees=day_settled=None
        with localcontext() as ctx:
            ctx.prec=48
            points=None if not base or day_pl is None else Decimal(day_pl)*100/base
            kind=settings['target_kind'];value=settings['target_value']
            target_usd=Decimal(value)/100 if kind=='usd' else Decimal(value)*base/(10000*SCALE) if kind=='points' and base else None
            target_points=Decimal(value)/100 if kind=='points' else target_usd*SCALE*100/base if kind=='usd' and base else None
            pnl=None if day_pl is None else Decimal(day_pl)/SCALE
            reached=None if target_usd is None or pnl is None else pnl>=target_usd
            shortfall=None if target_usd is None or pnl is None else max(Decimal(0),target_usd-pnl)
        stored=day_record(db,instance,day,'baseline')
        if stored and base is not None:
            require(stored['base']==base and stored['receipt']==receipt and stored.get('evidence')==evidence,'goal_baseline_changed')
        notice=day_record(db,instance,day,'reached')
        mark=s['mark'] if s else None
        mark_value=0 if s and not qty(s) else qty(s)*mark[0]//SCALE if s and mark and 0<=now-mark[1]<=60 else None
        unrealized=None if mark_value is None else mark_value-s['inventory']
        equity=None if mark_value is None else s['active']+mark_value+sum(min(net,basis) for net,basis in s['sales'].values())
        return dict(mode='PAPER',currency='USD',instance=instance,day=day,zone=p['zone'],day_start=cutoff,day_end=end,as_of=now,
                    settings=hist[-1] if hist else dict(version=0,settings=DEFAULT,effective_day=day),effective_version=effective['version'] if effective else 0,
                    effective_settings=settings,plan=plan(settings,days),authorized_principal_usd=usd(row[1]*CENT),
                    day_start_active_usd=usd(base),baseline_receipt=receipt,baseline_state=('verified_open_bid_snapshot' if evidence else 'verified_flat_snapshot') if receipt else 'unavailable',baseline_evidence=evidence,
                    daily_realized_usd=usd(day_pl),month_realized_usd=usd(month_pl),daily_settled_usd=usd(day_settled),daily_fees_usd=usd(day_fees),month_fees_usd=usd(month_fees),
                    execution_day_original_pl_usd=usd(execution_day if valid and s and not unknown_day else None),execution_month_original_pl_usd=usd(execution_month if valid and s and not unknown_month else None),execution_day_unknown=unknown_day,execution_month_unknown=unknown_month,posting_day_adjustments_usd=usd(adjust_day if valid and s else None),posting_month_adjustments_usd=usd(adjust_month if valid and s else None),
                    daily_points=fmt(points),target_usd=fmt(target_usd),target_points=fmt(target_points),target_reached=reached,target_shortfall_usd=fmt(shortfall),
                    active_cash_usd=usd(s['active']) if s else None,protected_usd=usd(s['protected']) if s else None,
                    unrealized_usd=usd(unrealized),active_equity_usd=usd(equity),active_equity_change_usd=usd(equity-base if equity is not None and base is not None else None),reserve_earmarks_usd={k:usd(v) for k,v in s['reserve']['earmarks'].items()} if s else None,
                    actual_service_costs_usd=None,withdrawal_status='not_available',journal_head=head,posting_time_valid=valid,
                    progress_basis='journal_posting_time',goal_pause_enabled=settings.get('pause_on_goal',False),
                    goal_pause_latched=bool(notice and notice['pause']),conservative_bps=settings.get('conservative_bps',0),
                    conservative_active_bps=notice.get('conservative_bps',0) if notice else 0,goal_notification=notice,baseline_persisted=bool(stored and receipt),real_income=False)
