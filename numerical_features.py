"""TB-06A pure causal numerical feature and target contract.

No provider access, model fitting, order intent, or trading capability.
Retrospective reconstruction is not proof of historical local observability.
"""
import math
import statistics
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
from decimal import Decimal,localcontext
from zoneinfo import ZoneInfo

STEP=3600
LOOKBACK=25
MAX_HOURS=43800
SCHEMA='maj-vaj-numerical-v1'
MODES=('local-observed','provider-retrospective')
MACRO=('UNRATE','PAYEMS','CPIAUCNS','FEDFUNDS')
SESSIONS=(('tokyo','Asia/Tokyo',9,15),('hong_kong','Asia/Hong_Kong',9,16),
          ('london','Europe/London',8,16),('new_york','America/New_York',9,16))
PRICE_FEATURES=('%-return_1h','%-return_6h','%-return_24h','%-range_1h','%-body_1h',
                '%-volatility_24h','%-volume_ratio_24h','%-trade_count_ratio_24h','%-sma_ratio_24h')
SESSION_FEATURES=('%-utc_hour_sin','%-utc_hour_cos','%-weekday_sin','%-weekday_cos','%-weekend',
                  *('%-session_'+s[0] for s in SESSIONS),'%-session_overlap')
MACRO_FEATURES=tuple(k for series in MACRO for k in ('%-macro_'+series,'%-macro_'+series+'_missing','%-macro_'+series+'_age_days'))
FEATURES=PRICE_FEATURES+SESSION_FEATURES+MACRO_FEATURES
TARGETS=('&-net_return','&-net_positive')

@dataclass(frozen=True)
class Plan:
    start:int
    end:int
    train_end:int
    validation_end:int
    horizon_hours:int
    gap_hours:int
    fee_bps:int
    spread_bps:int
    slippage_bps:int
    availability_mode:str
    def validate(self):
        for v in (self.start,self.end,self.train_end,self.validation_end):
            if type(v) is not int or v<=0 or v%STEP:raise ValueError('hour-aligned UTC boundaries required')
        if not self.start<self.train_end<self.validation_end<self.end or self.end-self.start>MAX_HOURS*STEP:
            raise ValueError('invalid chronological dataset extent')
        if type(self.horizon_hours) is not int or not 1<=self.horizon_hours<=168:raise ValueError('invalid outcome horizon')
        if type(self.gap_hours) is not int or not 1<=self.gap_hours<=168:raise ValueError('invalid embargo')
        if any(type(v) is not int or not 0<=v<=1000 for v in (self.fee_bps,self.spread_bps,self.slippage_bps)):
            raise ValueError('explicit nonnegative scenario costs required (bps)')
        if self.availability_mode not in MODES:raise ValueError('explicit availability mode required')
        return self
    def as_dict(self):return asdict(self)

def split(plan,decision,label_available):
    """Global boundaries, strict label completion before the split and embargo."""
    if decision<plan.start or label_available<=decision:raise ValueError('invalid temporal row')
    for name,left,right in (('train',plan.start,plan.train_end),
                            ('validation',plan.train_end+plan.gap_hours*STEP,plan.validation_end),
                            ('test',plan.validation_end+plan.gap_hours*STEP,plan.end)):
        if left<=decision<right and label_available<right:return name
    return None

def session(decision):
    d=datetime.fromtimestamp(decision,timezone.utc)
    vals=[math.sin(2*math.pi*d.hour/24),math.cos(2*math.pi*d.hour/24),
          math.sin(2*math.pi*d.weekday()/7),math.cos(2*math.pi*d.weekday()/7),int(d.weekday()>=5)]
    active=[]
    for _,zone,left,right in SESSIONS:
        local=d.astimezone(ZoneInfo(zone))
        active.append(int(local.weekday()<5 and left<=local.hour<right))
    return dict(zip(SESSION_FEATURES,vals+active+[int(sum(active)>1)]))

def _ratio(n,d):
    return n/d-1 if d>0 else None

def known_at(row):
    # Earliest price availability alone does not establish when all retained
    # source/count evidence was known. Conservatively wait for all contributors.
    return max(row['available_at'],row.get('contributing_available_at',row['available_at']))

def technical(window,decision,mode):
    """Exactly 25 contiguous closed hours; no expanding or fitted transforms."""
    if len(window)!=LOOKBACK or mode not in MODES:raise ValueError('invalid feature context')
    if any(r['ts']!=decision-(LOOKBACK-i)*STEP or r['available_at']<r['ts']+STEP for i,r in enumerate(window)):
        raise ValueError('noncontiguous or forming feature context')
    if mode=='local-observed' and any(known_at(r)>decision for r in window):return None
    c=[float(x['c']) for x in window];v=[float(x['v']) for x in window];n=[float(x['trades']) for x in window]
    o,h,l=(float(window[-1][k]) for k in ('o','h','l'))
    if any(not math.isfinite(x) for x in c+v+n+[o,h,l]) or min(c+[o,h,l])<=0 or min(v+n)<0:raise ValueError('invalid numeric observation')
    returns=[math.log(b/a) for a,b in zip(c,c[1:])]
    values=[_ratio(c[-1],c[-2]),_ratio(c[-1],c[-7]),_ratio(c[-1],c[0]),
            (h-l)/o,(c[-1]-o)/o,statistics.stdev(returns),
            _ratio(v[-1],statistics.mean(v[:-1])),_ratio(n[-1],statistics.mean(n[:-1])),
            _ratio(c[-1],statistics.mean(c[:-1]))]
    if any(x is not None and not math.isfinite(x) for x in values):raise ValueError('nonfinite feature')
    return dict(zip(PRICE_FEATURES,values))|session(decision)

def outcome(plan,entry,exit):
    """Long-only scenario target; no fill or probability claim.

    Buy at entry open plus half spread and slippage; pay fee on executed
    notional. Sell at exit open less half spread and slippage; pay exit fee.
    """
    with localcontext() as ctx:
        ctx.prec=40
        fee=Decimal(plan.fee_bps)/10000
        friction=(Decimal(plan.spread_bps)/2+Decimal(plan.slippage_bps))/10000
        paid=Decimal(entry['o'])*(1+friction)*(1+fee)
        received=Decimal(exit['o'])*(1-friction)*(1-fee)
        if paid<=0 or received<=0:raise ValueError('invalid price or cost scenario')
        net=received/paid-1
        value=float(net)
        if not math.isfinite(value):raise ValueError('nonfinite return')
        return {'&-net_return':value,'&-net_positive':int(net>0)}

def model_columns(include_sessions=True,include_macro=True):
    if type(include_sessions) is not bool or type(include_macro) is not bool:raise ValueError('explicit feature switches required')
    return PRICE_FEATURES+(SESSION_FEATURES if include_sessions else ())+(MACRO_FEATURES if include_macro else ())
