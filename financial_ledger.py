"""TB-FIN-01B: internal PAPER accounting, no orders or reserve-release API."""
import copy
import hashlib
import json
import re
import time

SCALE = 100_000_000  # USD/base quantity units, 8 decimals; cents = 1_000_000 units
CENT = SCALE // 100
MAX = 10**18
LIMIT = 20_000  # bounded full replay; no silent truncation
ZERO = '0' * 64


class AccountingError(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise AccountingError(reason)


def amount(x, positive=False):
    require(type(x) is int and int(positive) <= x <= MAX, 'invalid_fixed_point_amount')
    return x


def ident(x):
    require(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', x), 'invalid_receipt_id')
    return x


def canon(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(x):
    return hashlib.sha256(canon(x).encode()).hexdigest()


def initialize(db):
    db.execute('''CREATE TABLE fin_events(seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
        instance TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, previous TEXT NOT NULL,
        digest TEXT NOT NULL UNIQUE)''')
    db.execute("CREATE TABLE fin_head(id INTEGER PRIMARY KEY CHECK(id=1), count INTEGER NOT NULL, digest TEXT NOT NULL, boot INTEGER NOT NULL)")
    db.execute("INSERT INTO fin_head VALUES(1,0,?,0)",(ZERO,))
    db.execute('''CREATE TABLE fin_conflicts(id TEXT NOT NULL, attempted_hash TEXT NOT NULL,
        PRIMARY KEY(id,attempted_hash))''')


def verify_schema(db):
    for table in ('fin_events','fin_conflicts','fin_head'):
        require(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(), 'financial_ledger_missing')


def fresh():
    return dict(active=0, inventory=0, pending=0, protected=0, capital=0, pl=0,
                lots=[], sales={}, used={}, fees=0, settled_pl=0, gains=0, losses=0,
                reconciled=False, reconciled_at=None, matched_reserve=None, matched_boot=None, mark=None, pair=None, history=[], correction_locked=False, correction_reason=None)


def balances(s):
    return {k:s[k] for k in ('active','inventory','pending','protected','capital','pl')}


def qty(s):
    return sum(l[0] for l in s['lots'])


def invariants(s):
    b = balances(s)
    require(sum(b.values()) == 0, 'unbalanced_journal')
    require(all(0 <= b[k] <= MAX for k in ('active','inventory','pending','protected')), 'negative_or_excess_balance')
    require(s['inventory'] == sum(l[1] for l in s['lots']), 'lot_basis_mismatch')
    require(s['pending'] == sum(l[0] for l in s['sales'].values()), 'settlement_mismatch')
    require(s['protected'] == s['gains'], 'protected_gain_mismatch')
    require(s['settled_pl'] == s['gains']-s['losses'], 'settled_pl_mismatch')
    require(-s['pl'] == s['settled_pl']+sum(net-basis for net,basis in s['sales'].values()), 'realized_pl_mismatch')
    require(s['active']+s['inventory']+sum(min(net,basis) for net,basis in s['sales'].values()) <= -s['capital'], 'principal_expansion')
    require(qty(s) <= MAX and all(0 <= s[k] <= MAX for k in ('fees','gains','losses')), 'ledger_bound')


def economic(s, kind, event_id, p):
    if kind == 'fund':
        require(s['capital'] == 0, 'already_funded')
        n = amount(p['principal'], True)
        s['pair'] = ident(p['pair'].replace('/','-'))
        s['active'] += n
        s['capital'] -= n
    elif kind in ('buy','sell'):
        q, gross, fee = amount(p['qty'],True), amount(p['gross'],True), amount(p['fee'])
        require(fee <= gross, 'fee_exceeds_gross')
        if kind == 'buy':
            order = ident(p['intent'])
            old = s['used'].get(order, [0,0])
            cost = gross+fee
            require(old[0]+gross <= p['max_gross'] and old[1]+cost <= p['max_total'], 'intent_budget_exceeded')
            require(cost <= s['active'], 'insufficient_active_cash')
            s['used'][order] = [old[0]+gross, old[1]+cost]
            s['active'] -= cost
            s['inventory'] += cost
            s['lots'].append([q,cost])
        else:
            ident(p['order'])
            require(q <= qty(s), 'oversell')
            remaining, basis = q, 0
            while remaining:
                lot = s['lots'][0]
                take = min(lot[0],remaining)
                cost = lot[1]*take//lot[0]
                basis += cost
                lot[0] -= take
                lot[1] -= cost
                remaining -= take
                if not lot[0]:
                    s['lots'].pop(0)
            net = gross-fee
            s['inventory'] -= basis
            s['pending'] += net
            s['pl'] -= net-basis
            s['sales'][event_id] = [net,basis]
        s['fees'] += fee
    elif kind == 'settle':
        ref = ident(p['sale'])
        require(ref in s['sales'], 'unknown_or_already_settled_sale')
        net,basis = s['sales'][ref]
        require(amount(p['received']) == net, 'settlement_amount_mismatch')
        del s['sales'][ref]
        gain = max(0,net-basis)
        loss = max(0,basis-net)
        s['pending'] -= net
        s['active'] += min(net,basis)
        s['protected'] += gain
        s['settled_pl'] += net-basis
        s['gains'] += gain
        s['losses'] += loss
    else:
        raise AccountingError('unsupported_economic_event')
    s['reconciled'] = False
    s['reconciled_at'] = None
    s['matched_reserve'] = None
    s['matched_boot'] = None
    s['mark'] = None
    invariants(s)


def observed(s):
    return {k:s[k] for k in ('active','protected','pending')} | {'qty':qty(s)}


def apply(states, last, instance, kind, event_id, body):
    if kind == 'desk_reconcile':
        return  # desk observation is not an instance or an economic event
    s = states.setdefault(instance,fresh())
    require(kind == 'fund' or s['capital'] < 0, 'unfunded_instance')
    if kind == 'late_correct':
        s, accepted, reason = review_correction(s, body)
        if not accepted:
            s['correction_locked'] = True
            s['correction_reason'] = reason
            s['reconciled'] = False
        states[instance] = s
        last[instance] = (event_id,kind,body,None)
    elif kind == 'reconcile':
        s['reconciled'] = body['balances'] == observed(s) and body['reported_reserved'] == body['expected_reserved']
        s['reconciled_at'] = body['at']
        s['matched_reserve'] = body['expected_reserved']
        s['matched_boot'] = body['boot']
    elif kind == 'mark':
        require(not s['mark'] or body['observed'] >= s['mark'][1], 'out_of_order_mark')
        s['mark'] = [amount(body['price'],True),amount(body['observed'],True)]
    elif kind == 'correct':
        prev = last.get(instance)
        require(prev is not None and prev[0] == body['fill'] and prev[1] in ('buy','sell'), 'dependent_or_unknown_fill')
        original,prior_kind,p,before = prev
        s = copy.deepcopy(before)
        revised = p | {'gross':body['gross'],'fee':body['fee']}
        economic(s,prior_kind,original,revised)
        s['history'].append((prior_kind,original,revised))
        states[instance] = s
        last[instance] = (event_id,'correct',body,None)
    else:
        before = copy.deepcopy(s) if kind in ('buy','sell') else None
        economic(s,kind,event_id,body)
        s['history'].append((kind,event_id,body))
        last[instance] = (event_id,kind,body,before)
    invariants(s)


def replay(db):
    verify_schema(db)
    states,last,previous = {},{},ZERO
    count = 0
    for seq,event_id,instance,kind,raw,prev,h in db.execute('SELECT * FROM fin_events ORDER BY seq'):
        count += 1
        require(count <= LIMIT and seq == count and prev == previous, 'journal_sequence_or_limit')
        p = json.loads(raw)
        require(raw == canon(p) and h == sha([seq,event_id,instance,kind,p,prev]), 'journal_integrity')
        apply(states,last,instance,kind,event_id,p['payload'])
        previous = h
    head = db.execute('SELECT count,digest FROM fin_head WHERE id=1').fetchone()
    require(head is not None and tuple(head) == (count,previous), 'journal_head_mismatch')
    return states,last,count,previous


def start(db):
    """Verify the complete journal and invalidate prior balance reconciliation."""
    replay(db)
    db.execute('UPDATE fin_head SET boot=boot+1 WHERE id=1')


def gate_reserve(db, instance):
    return db.execute("SELECT COALESCE(SUM(reserved),0) FROM gate_intents WHERE instance_id=? AND state IN ('AUTHORIZED','CLAIMED','UNKNOWN','FILLED_HELD')",(instance,)).fetchone()[0]*CENT


class FinancialLedger:
    """Trusted internal receipt ingestion; not a runtime Gate publisher."""
    def __init__(self, transaction, *, clock=time.time):
        self.transaction = transaction
        self.clock = clock

    def _append(self, event_id, instance, kind, payload):
        ident(event_id); ident(instance)
        request = {'instance':instance,'kind':kind,'payload':payload}
        request_hash = sha(request)
        conflict = False
        with self.transaction() as db:
            states,last,count,previous = replay(db)
            old = db.execute('SELECT body FROM fin_events WHERE id=?',(event_id,)).fetchone()
            if old:
                if json.loads(old[0])['request_hash'] == request_hash:
                    return {'event_id':event_id,'duplicate':True}
                db.execute('INSERT OR IGNORE INTO fin_conflicts VALUES(?,?)',(event_id,request_hash))
                conflict = True
            else:
                require(count < LIMIT, 'journal_capacity')
                row = db.execute('SELECT pair,virtual_cents,mode FROM instances WHERE id=?',(instance,)).fetchone()
                if kind != 'desk_reconcile':
                    require(row is not None and row[2] == 'PAPER','unknown_or_nonpaper_instance')
                p = dict(payload)
                if kind != 'desk_reconcile' and instance in states and states[instance]['capital'] < 0:
                    require(states[instance]['pair'] == row[0].replace('/','-') and -states[instance]['capital'] == row[1]*CENT,'instance_scope_changed')
                if kind == 'fund':
                    cap = db.execute('SELECT virtual_cap_cents FROM desk WHERE id=1').fetchone()[0]
                    allocated = db.execute('SELECT COALESCE(SUM(virtual_cents),0) FROM instances').fetchone()[0]
                    require(allocated <= cap,'desk_cap_exceeded')
                    p = {'principal':amount(row[1]*CENT,True),'pair':row[0]}
                elif kind == 'buy':
                    auth = db.execute('''SELECT instance_id,pair,notional,reserved,state FROM gate_intents WHERE id=?''',(p['intent'],)).fetchone()
                    require(auth is not None and auth[0] == instance and auth[1] == row[0] and auth[4] in ('CLAIMED','UNKNOWN','FILLED_HELD'),'unverified_intent')
                    p |= {'max_gross':amount(auth[2]*CENT,True),'max_total':amount(auth[3]*CENT,True)}
                elif kind == 'reconcile':
                    p = {'balances':{k:p[k] for k in ('active','protected','pending','qty')},
                         'reported_reserved':p['reserved'],'expected_reserved':gate_reserve(db,instance),
                         'at':int(self.clock()),'boot':db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]}
                elif kind == 'desk_reconcile':
                    p |= {'boot':db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0],
                          'execution_anchor':execution_anchor(db)}
                    require(p['observed'] <= int(self.clock()),'future_observation')
                    prior=db.execute("SELECT body FROM fin_events WHERE kind='desk_reconcile' ORDER BY seq DESC LIMIT 1").fetchone()
                    require(prior is None or p['observed']>=json.loads(prior[0])['payload']['observed'],'out_of_order_desk_observation')
                elif kind == 'mark':
                    require(p['observed'] <= int(self.clock()),'future_mark')
                apply(states,last,instance,kind,event_id,p)
                body = {'request_hash':request_hash,'payload':p,'recorded_at':int(self.clock())}
                n = count+1
                h = sha([n,event_id,instance,kind,body,previous])
                db.execute('INSERT INTO fin_events VALUES(?,?,?,?,?,?,?)',(n,event_id,instance,kind,canon(body),previous,h))
                db.execute('UPDATE fin_head SET count=?,digest=? WHERE id=1',(n,h))
        require(not conflict,'receipt_conflict_quarantined')
        return {'event_id':event_id,'duplicate':False}

    def fund(self, receipt, instance):
        return self._append(receipt,instance,'fund',{})

    def buy(self, receipt, instance, intent, quantity, gross, fee):
        return self._append(receipt,instance,'buy',dict(intent=ident(intent),qty=amount(quantity,True),gross=amount(gross,True),fee=amount(fee)))

    def sell(self, receipt, instance, order, quantity, gross, fee):
        return self._append(receipt,instance,'sell',dict(order=ident(order),qty=amount(quantity,True),gross=amount(gross,True),fee=amount(fee)))

    def settle(self, receipt, instance, sale, received):
        return self._append(receipt,instance,'settle',dict(sale=ident(sale),received=amount(received)))

    def correct_last_fill(self, receipt, instance, fill, gross, fee):
        return self._append(receipt,instance,'correct',dict(fill=ident(fill),gross=amount(gross,True),fee=amount(fee)))

    def late_correct(self, receipt, instance, fill, gross, fee, *, settlement_received, expected, evidence_digest):
        validate_digest(evidence_digest)
        require(type(expected) is dict and set(expected)=={'active','protected','pending','qty'},'invalid_observed_balances')
        return self._append(receipt,instance,'late_correct',dict(fill=ident(fill),gross=amount(gross,True),fee=amount(fee),
            settlement_received=None if settlement_received is None else amount(settlement_received),
            expected={k:amount(v) for k,v in expected.items()},evidence_digest=evidence_digest))

    def reconcile_desk(self, receipt, *, observed_at, evidence_digest, accounts, open_orders):
        validate_digest(evidence_digest)
        require(type(accounts) is dict and len(accounts) <= 1000,'invalid_desk_scope')
        clean={}
        for key,row in accounts.items():
            ident(key)
            require(type(row) is dict and set(row)=={'active','protected','pending','qty','covered_qty'},'invalid_account_observation')
            clean[key]={k:amount(v) for k,v in row.items()}
        require(type(open_orders) is list and len(open_orders)<=1000,'invalid_open_orders')
        orders=sorted(ident(x) for x in open_orders)
        require(len(set(orders))==len(orders),'duplicate_open_order')
        return self._append(receipt,'desk','desk_reconcile',dict(observed=amount(observed_at,True),
            evidence_digest=evidence_digest,accounts=clean,open_orders=orders))

    def reconcile(self, receipt, instance, *, active, protected, pending, quantity, reserved):
        return self._append(receipt,instance,'reconcile',dict(active=amount(active),protected=amount(protected),pending=amount(pending),qty=amount(quantity),reserved=amount(reserved)))

    def mark(self, receipt, instance, price, observed_at):
        return self._append(receipt,instance,'mark',dict(price=amount(price,True),observed=amount(observed_at,True)))

    def snapshot(self, instance, *, max_mark_age=60):
        ident(instance)
        require(type(max_mark_age) is int and 1 <= max_mark_age <= 3600,'invalid_mark_age')
        with self.transaction() as db:
            states,_,count,head = replay(db)
            require(instance in states and states[instance]['capital'] < 0,'unfunded_instance')
            s = states[instance]
            conflicts = db.execute('SELECT COUNT(*) FROM fin_conflicts').fetchone()[0]
            reserved = gate_reserve(db,instance)
            now = int(self.clock())
            boot = db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
            row = db.execute('SELECT pair,virtual_cents,mode FROM instances WHERE id=?',(instance,)).fetchone()
            scope_ok = row is not None and row[2]=='PAPER' and row[0].replace('/','-')==s['pair'] and row[1]*CENT==-s['capital']
            mark = s['mark']
            value = None if qty(s) else 0
            if mark and 0 <= int(self.clock())-mark[1] <= max_mark_age:
                value = qty(s)*mark[0]//SCALE
            return observed(s) | {'currency':'USD','scale':SCALE,'mode':'PAPER','principal':-s['capital'],
                'inventory_basis':s['inventory'],'realized_pl':-s['pl'],'settled_pl':s['settled_pl'],
                'settled_gains':s['gains'],'settled_losses':s['losses'],'fees':s['fees'],
                'unrealized_pl':None if value is None else value-s['inventory'],
                'gate_reserved':reserved,'unencumbered_upper_bound':max(0,s['active']-reserved),
                'reconciled':bool(s['reconciled'] and scope_ok and not conflicts and not s['correction_locked'] and s['matched_reserve']==reserved and s['matched_boot']==boot and 0 <= now-s['reconciled_at'] <= 60),'conflicts':conflicts,
                'correction_locked':s['correction_locked'],'correction_reason':s['correction_reason'],'pending_settlements':len(s['sales']),'journal_count':count,'journal_head':head,'execution_ready':False}


def validate_digest(value):
    require(isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value),'invalid_evidence_digest')


def review_correction(old, body):
    """Rebuild FIFO history from original events with one revised gross/fee.

    A failed review remains journaled and locks the account. Evidence is an
    independently supplied observation, not an assertion of venue authenticity.
    """
    try:
        revised=fresh()
        found=False
        settled=False
        for kind,event_id,p in old['history']:
            if event_id==body['fill'] and kind in ('buy','sell'):
                require(not found,'duplicate_fill_history')
                found=True
                p=p | {'gross':body['gross'],'fee':body['fee']}
            if kind=='settle' and p['sale']==body['fill']:
                settled=True
                require(body['settlement_received'] is not None,'missing_revised_settlement')
                p=p | {'received':body['settlement_received']}
            economic(revised,kind,event_id,p)
            revised['history'].append((kind,event_id,p))
        require(found and settled==(body['settlement_received'] is not None),'invalid_correction_scope')
        require(observed(revised)==body['expected'],'independent_balance_mismatch')
        # Don't automatically reclassify a protected profit as active capital,
        # or restore depleted active capital through a historical correction.
        require(revised['active']<=old['active'],'correction_would_expand_active_cash')
        revised['correction_locked']=old['correction_locked']
        revised['correction_reason']=old['correction_reason']
        return revised,True,None
    except AccountingError as exc:
        return old,False,str(exc)


def execution_anchor(db):
    # Exclude unsubmitted reservations: Gate must be able to claim its own
    # authorized intent without treating reservation as an external trade.
    return sha([list(r) for r in db.execute("SELECT id,state FROM gate_intents WHERE state IN ('CLAIMED','UNKNOWN','FILLED_HELD','NO_FILL') ORDER BY id")])


def projection_values(db, now):
    """Fail closed unless the latest whole-desk receipt still matches.

    Return integer cents for Gate; reservations remain owned by Gate itself.
    Lifetime gross settled losses conservatively bound day/week/drawdown until
    calendar loss accounting and authenticated execution integration qualify.
    """
    states,_,_,head=replay(db)
    require(not db.execute('SELECT 1 FROM fin_conflicts LIMIT 1').fetchone(),'financial_conflict')
    row=db.execute('SELECT id,kind,body FROM fin_events ORDER BY seq DESC LIMIT 1').fetchone()
    require(row is not None and row[1]=='desk_reconcile','fresh_desk_reconciliation_required')
    receipt,_,raw=row
    p=json.loads(raw)['payload']
    validate_digest(p['evidence_digest'])
    require(0<=now-p['observed']<=60,'desk_observation_stale')
    boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
    require(p['boot']==boot,'desk_reconciliation_restart')
    require(p['open_orders']==[],'unresolved_open_orders')
    require(not db.execute("SELECT 1 FROM gate_intents WHERE state IN ('CLAIMED','UNKNOWN','FILLED_HELD') LIMIT 1").fetchone(),'unresolved_execution')
    require(p['execution_anchor']==execution_anchor(db),'desk_execution_changed')
    scope={r[0]:r[1:] for r in db.execute('SELECT id,pair,virtual_cents,mode FROM instances')}
    require(bool(scope) and set(scope)==set(states)==set(p['accounts']),'account_scope_mismatch')
    cap=db.execute('SELECT virtual_cap_cents FROM desk WHERE id=1').fetchone()[0]
    require(sum(r[1] for r in scope.values())<=cap,'desk_cap_exceeded')
    for key,s in states.items():
        pair,cents,mode=scope[key]
        require(mode=='PAPER' and s['pair']==pair.replace('/','-') and -s['capital']==cents*CENT,'instance_scope_changed')
        require(not s['correction_locked'],'unresolved_correction')
        require(not s['sales'],'unsettled_proceeds')
        require(p['accounts'][key]==observed(s)|{'covered_qty':qty(s)},'independent_balance_or_protection_mismatch')
    active={key:s['active']//CENT for key,s in states.items()}
    exposure={key:(s['inventory']+CENT-1)//CENT for key,s in states.items()}
    cash=sum(s['active']+s['protected'] for s in states.values())//CENT
    protected=min(cash,(sum(s['protected'] for s in states.values())+CENT-1)//CENT)
    excess=max(0,sum(active.values())-(cash-protected))
    for key in sorted(active):
        take=min(active[key],excess)
        active[key]-=take
        excess-=take
    loss=(sum(s['losses'] for s in states.values())+CENT-1)//CENT
    return dict(receipt='fin:'+head,observed=p['observed'],settled_cash_cents=cash,protected_cash_cents=protected,
                daily_loss_cents=loss,weekly_loss_cents=loss,drawdown_cents=loss,open_risk_cents=sum(exposure.values()),
                active_by_instance=active,exposure_by_instance=exposure,reconciled=True,protection_ok=True)


def gate_projection(db, now):
    from dataclasses import asdict
    from risk_gate import RiskState, GateRefusal, digest
    try:
        account=RiskState(**projection_values(db,now))
        account.check(now)
        return account,digest(asdict(account))
    except AccountingError as exc:
        raise GateRefusal('financial:'+str(exc)) from exc
