"""Bounded read-only PAPER history of booked financial events, not venue returns."""
from datetime import date, datetime, time as midnight, timedelta
import json
import time
from zoneinfo import ZoneInfo

from financial_ledger import CENT, apply, qty, replay, require
from financial_goals import DEFAULT, scope, usd, versions

MAX_DAYS = 31
FLOWS = ('realized','settled','fees','released')
BALANCES = ('active','protected','pending','inventory_basis','principal')


def flows(s):
    return (-s['pl'],s['settled_pl'],s['fees'],s['reserve']['released']) if s else (0,0,0,0)


def balances(s):
    return (s['active'],s['protected'],s['pending'],s['inventory'],-s['capital']) if s else (None,)*len(BALANCES)


def money_fields(names,values):
    return {k+'_usd':usd(v) for k,v in zip(names,values)}


def report(transaction,instance,days=7,end_day=None,*,clock=time.time):
    require(type(days) is int and 1<=days<=MAX_DAYS,'history_window')
    with transaction() as db:
        now=int(clock());pair,principal,_=scope(db,instance)
        hist=versions(db,instance);zone=hist[-1]['settings']['zone'] if hist else DEFAULT['zone'];z=ZoneInfo(zone)
        today=datetime.fromtimestamp(now,z).date()
        end=today if end_day is None else date.fromisoformat(end_day)
        require(end_day is None or end.isoformat()==end_day,'history_date')
        require(date(1970,2,1)<=end<=today,'history_date')
        start=end-timedelta(days=days-1)
        dates=[start+timedelta(days=i) for i in range(days)]
        edges=[int(datetime.combine(d,midnight.min,z).timestamp()) for d in dates+[end+timedelta(days=1)]]
        require(all(a<b for a,b in zip(edges,edges[1:])),'history_calendar')
        # Verify the entire current journal, including events outside this window.
        verified,_,count,head=replay(db);state=verified.get(instance)
        require(not state or (state['pair']==pair.replace('/','-') and -state['capital']==principal*CENT),'history_scope_changed')
        disputed=bool(db.execute('SELECT 1 FROM fin_conflicts LIMIT 1').fetchone() or (state and state['correction_locked']))
        running,last={},{};rows=[];totals=[0]*len(FLOWS);index=0;prior_at=0
        def snapshot_row(i):
            s=running.get(instance);opening=opening_flows[0];closing=flows(s);known=bool(s) and not disputed
            deltas=[b-a for a,b in zip(opening,closing)] if known else [None]*len(FLOWS)
            if known:
                for j,v in enumerate(deltas):totals[j]+=v
            status='review_required' if disputed else 'not_funded' if not s else 'partial' if not opening_flows[1] or dates[i]==today else 'booked'
            return dict(day=dates[i].isoformat(),start=edges[i],end=min(edges[i+1],now),status=status,
                        **money_fields(FLOWS,deltas),**money_fields(BALANCES,balances(s) if known else (None,)*len(BALANCES)),
                        position_open=bool(qty(s)) if known else None)
        opening_flows=[(0,)*len(FLOWS),False]
        for _,eid,i,k,raw,_,_ in db.execute('SELECT * FROM fin_events ORDER BY seq'):
            event=json.loads(raw);at=event['recorded_at']
            require(type(at) is int and prior_at<=at<=now,'history_posting_clock');prior_at=at
            # First finalize all days whose exclusive end precedes this posting.
            while index<days and at>=edges[index+1]:
                rows.append(snapshot_row(index));index+=1
                opening_flows[:]=[flows(running.get(instance)),instance in running]
            apply(running,last,i,k,eid,event['payload'])
            if index==0 and at<edges[0]:opening_flows[:]=[flows(running.get(instance)),instance in running]
        while index<days:
            rows.append(snapshot_row(index));index+=1
            opening_flows[:]=[flows(running.get(instance)),instance in running]
        # Period totals include only rows reporting booked values. Explicit
        # coverage prevents interpreting pre-funding or a partial day as full.
        reported=sum(r['realized_usd'] is not None for r in rows)
        return dict(instance=instance,pair=pair,mode='PAPER',currency='USD',real_income=False,
                    basis='journal_posting_time',balance_basis='booked_balances_not_current_wallet',zone=zone,
                    start_day=start.isoformat(),end_day=end.isoformat(),as_of=now,journal_head=head,journal_count=count,
                    review_required=disputed,reported_days=reported,days=rows,
                    totals=money_fields(FLOWS,totals if reported else [None]*len(FLOWS)),
                    actual_service_costs_usd=None,withdrawal_status='not_available')
