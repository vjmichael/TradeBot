"""Restrictive-only daily goal decisions; never creates trading authority."""
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
import sqlite3
from zoneinfo import ZoneInfo

from financial_ledger import SCALE, CENT, canon, require, sha
from financial_goals import bounds, day_record, report, versions


def record(db,instance,day,kind,body):
    old=day_record(db,instance,day,kind)
    if old is not None:return old
    db.execute('INSERT INTO goal_daily VALUES(?,?,?,?,?)',(instance,day,kind,canon(body),sha([instance,day,kind,body])))
    return body


def evaluate(db,instance,now,key_for,*,verify=None):
    """Called in the caller's desk transaction. Returns restriction, not authority.

    Notification is recorded only with current authenticated whole-desk evidence.
    A latched restriction survives corrections and restarts until calendar rollover.
    Final submission can check the restriction without re-publishing wallet evidence.
    """
    hist=versions(db,instance)
    if not hist:return None
    require(now>=hist[-1]['at'],'goal_clock_rollback')
    day,_,_,_,_=bounds(now,hist[-1]['settings']['zone'])
    effective=next((h for h in reversed(hist) if h['effective_day']<=day),None)
    if effective is None:return None
    p=effective['settings'];pause=p.get('pause_on_goal',False)
    prior=day_record(db,instance,day,'reached')
    if prior:
        require(prior['version']==effective['version'],'goal_notice_version_changed')
        return 'daily_goal_reached' if prior['pause'] else None
    r=report(lambda:nullcontext(db),instance,clock=lambda:now,key_for=key_for)
    if r['baseline_receipt']:
        record(db,instance,day,'baseline',dict(base=int(Decimal(r['day_start_active_usd'])*SCALE),receipt=r['baseline_receipt'],journal_head=r['baseline_receipt'],evidence=r['baseline_evidence']))
    if p['target_kind']=='none':return None
    if not r['posting_time_valid'] or r['daily_realized_usd'] is None:
        return 'goal_progress_unavailable' if pause else None
    pnl=int(Decimal(r['daily_realized_usd'])*SCALE)
    if p['target_kind']=='points':
        if not r['day_start_active_usd'] or Decimal(r['day_start_active_usd'])<=0:
            return 'goal_baseline_unavailable' if pause else None
        base=int(Decimal(r['day_start_active_usd'])*SCALE)
        reached=pnl*10000>=base*p['target_value']
    else:reached=pnl>=p['target_value']*CENT
    if not reached:return None
    # No report-only reader can turn a goal into a verified notification.
    # If verification fails, an opted-in pause is still restrictive.
    if verify is not None:
        try:verify(db,now)
        except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):pass
        else:
            record(db,instance,day,'reached',dict(version=effective['version'],at=now,pause=pause,
                   realized_units=pnl,target_kind=p['target_kind'],target_value=p['target_value'],
                   baseline_receipt=r['baseline_receipt'],journal_head=r['journal_head']))
    return 'daily_goal_reached' if pause else None


def entry_guard(key_for,verify):
    from risk_gate import GateRefusal
    def check(db,instance,now,*,final=False):
        try:reason=evaluate(db,instance,now,key_for,verify=None if final else verify)
        except (ValueError,OSError,KeyError,TypeError,sqlite3.Error) as exc:raise GateRefusal('goal_control_unavailable') from exc
        if reason:raise GateRefusal(reason)
    return check
