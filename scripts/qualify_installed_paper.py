"""Disposable hosted Linux qualification only. No production activation path."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from financial_ledger import initialize as init_fin, replay, require, SCALE
from paper_custody import authenticated_projection, workspace_key
from paper_execution import units
from paper_monitor import audit, public_book, read_health
from paper_provision import provision, installed_workspace, instance_id
from reserve_runtime import desk_transaction
from risk_gate import Gate, Policy, Proposal, MarketEvidence, initialize as init_gate

BASE = Path('/var/lib/maj-vaj')
PRODUCTION_FIXTURE = BASE / 'data'
EXECUTION_FIXTURE = BASE / 'qualification'
CAP = 100_000  # virtual USD 1,000; one bounded test intent, no actual funds
MAX_ORDER = 10_000
REVISION = 'public-execution-fixture-v1'


def guard():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and
            os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
            'disposable_hosted_runner_required')
    require(os.getuid() != 0, 'unprivileged_service_user_required')
    os.umask(0o077)


def prepare(data, execution=False):
    # Both roots must be new. This fixture never changes a production schema.
    require(data in (PRODUCTION_FIXTURE, EXECUTION_FIXTURE), 'qualification_path')
    data.mkdir(mode=0o700)
    if not execution:
        from tradebot import Store, InstanceSpec
        store = Store(data, CAP)
        store.boot()
        instance = store.add(InstanceSpec(name='Paused qualification', pair='BTC/USD',
                            virtual_cents=CAP, provider='none', strategy_family='numerical-baseline'))['id']
        tx = store.transaction
    else:
        import sqlite3
        instance = uuid4().hex
        path = data / 'desk.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript("""CREATE TABLE desk(id INTEGER PRIMARY KEY, paused INTEGER, human_pause INTEGER, virtual_cap_cents INTEGER);
                CREATE TABLE instances(id TEXT PRIMARY KEY, name TEXT, pair TEXT, virtual_cents INTEGER, mode TEXT CHECK(mode='PAPER'), strategy_family TEXT, state TEXT);
                PRAGMA user_version=4;""")
            db.execute('INSERT INTO desk VALUES(1,1,0,?)', (CAP,))
            db.execute("INSERT INTO instances VALUES(?,'Isolated paper execution fixture','BTC/USD',?,'PAPER','numerical-baseline','PAUSED')", (instance,CAP))
            init_gate(db); init_fin(db)
        tx = desk_transaction(data)
    provision(data, instance, tx)
    with tx() as db:
        installed_workspace(data, instance, db)
    if execution:
        # The isolated mechanism fixture is distinct from Store's immutable
        # PAUSED schema. Only this test harness has a RUN transition.
        with tx() as db:
            db.execute('UPDATE desk SET paused=0 WHERE id=1')
            db.execute("UPDATE instances SET state='RUN' WHERE id=?", (instance,))
    print(instance, flush=True)


def verify_health(data, instance, pid, *, paused=False):
    tx = desk_transaction(data)
    health = read_health(data, instance, int(time.time()))
    require(health['pid'] == pid, 'heartbeat_not_service_main_pid')
    with tx() as db:
        installed_workspace(data, instance, db)
        states,_,_,head = replay(db)
        boot = db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
        require(boot == health['boot'], 'heartbeat_boot_changed')
        if paused:
            s = states[instance]
            require(db.execute('SELECT paused FROM desk').fetchone()[0] == 1 and
                    db.execute('SELECT COUNT(*) FROM gate_intents').fetchone()[0] == 0 and
                    s['active'] == CAP * SCALE // 100 and s['protected'] == 0,
                    'paused_workspace_changed')
    print(json.dumps({'health':health,'head':head}, sort_keys=True), flush=True)


def scenario_levels(book):
    bid = units(book['bid']); ask = units(book['ask'])
    require(0 < bid < ask and ask-bid >= 2, 'no_distinct_public_spread')
    spread = int((Decimal(ask-bid)*10000/ask).to_integral_value(rounding=ROUND_CEILING))
    depth = int((min(Decimal(book['bid'])*Decimal(book['bid_qty']),
                     Decimal(book['ask'])*Decimal(book['ask_qty']))*100).to_integral_value(rounding=ROUND_FLOOR))
    require(spread <= 50 and depth >= 2000, 'public_book_outside_fixture_bounds')
    # Deliberate near-spread protective mechanism test, not an investment
    # signal or a claim that this level is economically optimal.
    return ask, (bid+ask)//2, ask+max(1,ask//1000), spread, depth


def authorize(instance):
    data = EXECUTION_FIXTURE; tx = desk_transaction(data)
    gate = Gate(tx, account_projection=authenticated_projection(lambda i:workspace_key(data,i)))
    with tx() as db:
        installed_workspace(data, instance, db)
        require(db.execute('SELECT COUNT(*) FROM gate_intents').fetchone()[0] == 0,'fixture_order_already_created')
    now = int(time.time())
    gate.trusted_policy(Policy(version='fixture-policy',owner='paper-worker',epoch=1,expires=now+3600,
        strategy_revision=REVISION,max_order_cents=MAX_ORDER,max_total_exposure_cents=MAX_ORDER,
        max_pair_exposure_cents=MAX_ORDER,max_positions=1,max_loss_per_trade_cents=1500,
        daily_loss_limit_cents=2000,weekly_loss_limit_cents=2000,drawdown_limit_cents=2000,
        market_max_age_sec=20,account_max_age_sec=25,max_spread_bps=50,max_volatility_bps=200,
        min_depth_cents=2000,cost_bps=200,min_edge_bps=1))
    # At most one actual authorized intent. Retrying refusals during the brief
    # startup reconciliation window does not submit orders.
    deadline = time.monotonic()+90
    reason = 'no_fresh_observation'
    while time.monotonic()<deadline:
        b=public_book('BTC/USD'); entry,stop,target,spread,depth=scenario_levels(b)
        now=int(time.time()); rid='public:'+uuid4().hex
        # Edge/trigger fields are deliberately fixture authorization inputs;
        # only book/price/depth/time are real. No strategy readiness asserted.
        evidence=MarketEvidence(receipt=rid,pair='BTC/USD',observed=b['at'],available=now,
            source_digest=b['digest'],strategy_revision=REVISION,entry=entry,stop=stop,target=target,
            spread_bps=spread,volatility_bps=0,depth_cents=depth,min_notional_cents=1000,
            estimated_cost_bps=200,gross_edge_bps=201,continuous=True,operational=True,trigger=True,blackout=False)
        gate.trusted_market(evidence)
        result=gate.admit(Proposal('probe:'+uuid4().hex,instance,'BTC/USD',rid,MAX_ORDER,now+20))
        reason=result['reason']
        if result['state']=='AUTHORIZED':
            require(result['notional_cents']<=MAX_ORDER,'fixture_cap_exceeded')
            print(json.dumps({'controlled_public_book_fixture':True,'book':b,'result':result},sort_keys=True),flush=True)
            return
        time.sleep(2)
    raise RuntimeError('fixture_admission_unavailable:'+reason)


def main():
    guard()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare-paused','prepare-execution','health','authorize','audit'])
    p.add_argument('--instance');p.add_argument('--pid',type=int);p.add_argument('--execution',action='store_true')
    p.add_argument('--output',type=Path);a=p.parse_args()
    if a.action.startswith('prepare-'):
        prepare(EXECUTION_FIXTURE if a.action=='prepare-execution' else PRODUCTION_FIXTURE,a.action=='prepare-execution')
        return 0
    instance=instance_id(a.instance)
    if a.action=='health':
        verify_health(EXECUTION_FIXTURE if a.execution else PRODUCTION_FIXTURE,instance,a.pid,paused=not a.execution)
    elif a.action=='authorize':authorize(instance)
    else:
        require(a.output is not None,'report_path_required')
        result=audit(EXECUTION_FIXTURE,instance,desk_transaction(EXECUTION_FIXTURE),a.output,seconds=3600,interval=15)
        print(json.dumps(result,sort_keys=True),flush=True)
        return 0 if result['paper_trading_qualified'] else 2
    return 0


if __name__=='__main__':raise SystemExit(main())
