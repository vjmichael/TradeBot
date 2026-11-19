"""TB-06C deterministic, isolated long-only research replay.

Hourly open marks, fractional units, hypothetical execution costs, fixed
signals, no live venue, order access, fitting or portfolio optimization.
"""
from dataclasses import dataclass
from decimal import Decimal,localcontext
import hashlib
import math
import statistics

STEP=3600
STRATEGIES=('cash','buy_hold','random','momentum','numerical_only','no_sessions')
SCENARIOS=('zero','base','double')
SEED='maj-vaj-tb06c-v1'
D=Decimal

@dataclass(frozen=True)
class Costs:
    fee:int
    spread:int
    slippage:int
    def validate(self):
        if any(type(x) is not int or not 0<=x<=2000 for x in (self.fee,self.spread,self.slippage)):
            raise ValueError('invalid scenario cost')
        return self
    @property
    def rate(self):return D(self.fee)/10000
    @property
    def friction(self):return (D(self.spread)/2+D(self.slippage))/10000

def signals(strategy,pair,decisions,features,predictions):
    if strategy not in STRATEGIES:raise ValueError('invalid strategy')
    result={}
    for t in decisions:
        if strategy=='cash':take=False
        elif strategy=='buy_hold':take=t==decisions[0]
        elif strategy=='random':
            take=hashlib.sha256(f'{SEED}:{pair}:{t}'.encode()).digest()[0]<128
        elif strategy=='momentum':
            take=features[t]['%-return_6h']>0 and features[t]['%-return_24h']>0
        else:
            row=predictions[strategy][t]
            take=row['predicted_net_return']>0 and row['calibrated_positive_probability']>=.5
        result[t]=take
    return result

def replay(prices,decisions,takes,horizon,costs,capital_cents,buy_hold=False):
    """A separate fully-funded sleeve. Execute at next-hour open; fixed exit.
    All events at an open process exit, then pending entry, then new signal.
    Mark at hypothetical liquidation value inclusive of exit costs.
    """
    costs.validate()
    if type(capital_cents) is not int or not 1<=capital_cents<=10**11 or type(horizon) is not int or not 1<=horizon<=168:
        raise ValueError('invalid sleeve configuration')
    if not decisions or list(decisions)!=sorted(set(decisions)) or set(takes)!=set(decisions) or any(type(x) is not bool for x in takes.values()):
        raise ValueError('invalid decision schedule')
    first=decisions[0];last=decisions[-1]+(1+horizon)*STEP
    if last-first>43800*STEP:raise ValueError('unbounded replay clock')
    if any(t%STEP or t not in prices for t in range(first,last+STEP,STEP)):
        raise ValueError('incomplete hourly replay marks')
    with localcontext() as ctx:
        ctx.prec=40
        fee=costs.rate;friction=costs.friction
        cash=D(capital_cents)/100;units=D(0);entry=None;pending=None;exit_at=None
        marks=[];trades=[];fees=D(0);frictions=D(0);turnover=D(0);exposure=0;blocked=0
        for t in range(first,last+STEP,STEP):
            price=D(str(prices[t]))
            if not price.is_finite() or price<=0:raise ValueError('invalid hourly mark')
            if units and t==exit_at:
                gross=units*price;exec_value=gross*(1-friction)
                sell_fee=exec_value*fee;proceeds=exec_value-sell_fee
                fees+=sell_fee;frictions+=gross-exec_value;turnover+=exec_value
                cash+=proceeds
                trades.append({'decision':entry['decision'],'entry_at':entry['at'],'exit_at':t,
                               'stake':float(entry['stake']),'proceeds':float(proceeds),
                               'net_return':float(proceeds/entry['stake']-1),
                               'fees':float(entry['fee']+sell_fee),'friction':float(entry['friction']+gross-exec_value)})
                units=D(0);entry=None;exit_at=None
            if pending is not None and t==pending+STEP:
                if units or cash<=0:raise ValueError('invalid reserved capital')
                stake=cash;exec_value=stake/(1+fee)
                units=exec_value/(price*(1+friction))
                buy_fee=stake-exec_value;buy_friction=exec_value-units*price
                fees+=buy_fee;frictions+=buy_friction;turnover+=exec_value
                entry={'decision':pending,'at':t,'stake':stake,'fee':buy_fee,'friction':buy_friction}
                cash=D(0);exit_at=last if buy_hold else t+horizon*STEP;pending=None
            if takes.get(t,False):
                if units or pending is not None:blocked+=1
                else:pending=t
            if units:exposure+=1
            equity=cash+units*price*(1-friction)*(1-fee)
            if not equity.is_finite() or equity<=0:raise ValueError('invalid marked equity')
            marks.append((t,float(equity)))
        if units or pending is not None or entry is not None:raise ValueError('unsettled closing exposure')
        return {'marks':marks,'trades':trades,'fees':float(fees),'friction':float(frictions),
                'turnover':float(turnover),'exposure_hours':exposure,'blocked_signals':blocked,
                'initial':capital_cents/100,'final':float(cash)}

def metrics(marks,initial,trades,fees,friction,turnover):
    values=[v for _,v in marks]
    if not values or not all(math.isfinite(v) and v>0 for v in values) or initial<=0:raise ValueError('invalid equity path')
    peak=initial;dd=0
    for value in values:
        peak=max(peak,value);dd=max(dd,1-value/peak)
    r=[b/a-1 for a,b in zip(values,values[1:])]
    std=statistics.stdev(r) if len(r)>1 else None
    mean=statistics.mean(r) if r else None
    return {'initial_equity':initial,'final_equity':values[-1],'net_return':values[-1]/initial-1,
            'max_hourly_liquidation_drawdown':dd,'hourly_return_stdev':std,
            'hourly_mean_over_stdev_zero_rf':mean/std if std and mean is not None else None,
            'hourly_return_samples':len(r),'closed_trades':len(trades),
            'win_rate':sum(t['net_return']>0 for t in trades)/len(trades) if trades else None,
            'gross_execution_turnover':turnover,'fees':fees,'spread_slippage_cost':friction,
            'total_modeled_cost':fees+friction}

def aggregate(sleeves):
    if not sleeves:raise ValueError('missing sleeves')
    clock=[t for t,_ in sleeves[0]['marks']]
    if any([t for t,_ in x['marks']]!=clock for x in sleeves):raise ValueError('unmatched portfolio clock')
    marks=[(t,math.fsum(x['marks'][i][1] for x in sleeves)) for i,t in enumerate(clock)]
    trades=[t for x in sleeves for t in x['trades']]
    result=metrics(marks,math.fsum(x['initial'] for x in sleeves),trades,math.fsum(x['fees'] for x in sleeves),
                   math.fsum(x['friction'] for x in sleeves),math.fsum(x['turnover'] for x in sleeves))
    result.update(exposure_sleeve_hours=sum(x['exposure_hours'] for x in sleeves),
                  blocked_signals=sum(x['blocked_signals'] for x in sleeves))
    return marks,result
