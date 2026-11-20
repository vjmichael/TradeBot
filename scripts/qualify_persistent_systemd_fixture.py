"""Offline, disposable systemd guest fixture: controlled book only.

The production service has no CLI factory selector. Qualification installs a
root-owned service drop-in which calls run() with this controlled-book factory.
The real Gate, peer-authenticated broker, native simulator and ledger remain.
"""
import os
import pwd
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'tests'),str(ROOT/'engine_strategies')]
assert Path('/proc/1/comm').read_text().strip()=='systemd' and sorted(p.name for p in Path('/sys/class/net').iterdir())==['lo']
I='a'*32


@contextmanager
def factory(instance,pair,cents,work):
    from qualify_independent_entry import driver_context
    assert (instance,pair,cents)==(I,'BTC/USD',10000)
    with driver_context(work) as d:
        d.clock=time.time
        # The next protective observation moves the synthetic book to target;
        # this is not market data or a return prediction.
        bid=d.bid
        def at_target(row):
            d.bot.exchange.mid=120.
            return bid(row)
        d.bid=at_target
        print('FIXTURE_INIT',flush=True)
        yield d


def admit():
    from reserve_runtime import desk_transaction
    from reserve_entry_worker import installed_gate
    from financial_ledger import SCALE
    from risk_gate import Policy,MarketEvidence,Proposal
    assert os.geteuid()==pwd.getpwnam('maj-vaj').pw_uid
    tx=desk_transaction('/var/lib/maj-vaj/data');g,_,_=installed_gate(tx)
    with tx() as db:
        assert g.account_projection(db,int(time.time()))[0].settled_cash_cents==10000
        db.execute('UPDATE desk SET paused=0,human_pause=0')
    now=int(time.time())
    g.trusted_policy(Policy(version='policy-1',owner='paper-worker',epoch=1,expires=now+300,strategy_revision='revision-1',max_order_cents=6000,max_total_exposure_cents=10000,max_pair_exposure_cents=6000,max_positions=1,max_loss_per_trade_cents=300,daily_loss_limit_cents=1000,weekly_loss_limit_cents=1500,drawdown_limit_cents=2000,market_max_age_sec=60,account_max_age_sec=60,max_spread_bps=25,max_volatility_bps=500,min_depth_cents=5000,cost_bps=100,min_edge_bps=10))
    g.trusted_market(MarketEvidence(receipt='native-market',pair='BTC/USD',observed=now,available=now,source_digest='a'*64,strategy_revision='revision-1',entry=100*SCALE,stop=90*SCALE,target=120*SCALE,spread_bps=10,volatility_bps=100,depth_cents=10000,min_notional_cents=100,estimated_cost_bps=80,gross_edge_bps=130,continuous=True,operational=True,trigger=True,blackout=False))
    result=g.admit(Proposal('installed-persistent-proposal',I,'BTC/USD','native-market',5000,now+60));assert result['state']=='AUTHORIZED',result
    print('PASS installed gate admission from automatic native wallet delivery',flush=True)


if __name__=='__main__':
    if sys.argv[1:] == ['worker']:
        from reserve_persistent_worker import run
        run(factory=factory)
    elif sys.argv[1:] == ['admit']:admit()
    else:raise SystemExit('unknown qualification role')
