"""Offline systemd fixture only: synthetic credentials, controlled non-invocation.
No production endpoint, no live permissions; fixed role behavior selected by the
qualification-only root-owned service override.
"""
import os
import pwd
import sys
import time
from pathlib import Path
from qualify_persistent_systemd_fixture import factory, I
assert Path('/proc/1/comm').read_text().strip()=='systemd' and sorted(p.name for p in Path('/sys/class/net').iterdir())==['lo']


def admit(epoch):
    from reserve_runtime import desk_transaction
    from reserve_entry_worker import installed_gate
    from financial_ledger import SCALE
    from risk_gate import Policy,MarketEvidence,Proposal
    tx=desk_transaction('/var/lib/maj-vaj/data');g,_,_=installed_gate(tx)
    assert os.geteuid()==pwd.getpwnam('maj-vaj').pw_uid
    with tx() as db:
        assert g.account_projection(db,int(time.time()))[0].settled_cash_cents==10000
        assert tuple(db.execute('SELECT paused,human_pause FROM desk').fetchone())==(1,1)
        assert db.execute("SELECT COUNT(*) FROM gate_intents WHERE state='NO_FILL'").fetchone()[0]==epoch-1
        db.execute('UPDATE desk SET paused=0,human_pause=0')
    now=int(time.time());receipt='review-market-'+str(epoch)
    g.trusted_policy(Policy(version='review-policy-'+str(epoch),owner='paper-worker',epoch=epoch,expires=now+300,strategy_revision='revision-1',max_order_cents=6000,max_total_exposure_cents=10000,max_pair_exposure_cents=6000,max_positions=1,max_loss_per_trade_cents=300,daily_loss_limit_cents=1000,weekly_loss_limit_cents=1500,drawdown_limit_cents=2000,market_max_age_sec=60,account_max_age_sec=60,max_spread_bps=25,max_volatility_bps=500,min_depth_cents=5000,cost_bps=100,min_edge_bps=10))
    g.trusted_market(MarketEvidence(receipt=receipt,pair='BTC/USD',observed=now,available=now,source_digest='a'*64,strategy_revision='revision-1',entry=100*SCALE,stop=90*SCALE,target=120*SCALE,spread_bps=10,volatility_bps=100,depth_cents=10000,min_notional_cents=100,estimated_cost_bps=80,gross_edge_bps=130,continuous=True,operational=True,trigger=True,blackout=False))
    assert g.admit(Proposal('review-proposal-'+str(epoch),I,'BTC/USD',receipt,5000,now+60))['state']=='AUTHORIZED'
    print('PASS fixture admission',epoch,flush=True)


def worker():
    import reserve_no_effect
    from reserve_persistent_worker import run
    def cut(*a,**kw):
        print('CUT_PREPARED',flush=True)
        time.sleep(90)
        raise AssertionError('expected supervised interruption')
    reserve_no_effect.possible=cut
    run(factory=factory)


def review(mode):
    from reserve_no_effect_worker import main,review_window
    # Same actual Tk widgets and approval callbacks; simulated human input is
    # challenge and passphrase for the fixture only, never in production code.
    ran=[]
    def window(root,review,proof,publish):
        v=review_window(root,review,proof,publish)
        def act():
            if mode=='cancel':
                v['close'].invoke();ran.append(True);return
            v['challenge'].set(v['review']['challenge'] if mode=='confirm' else 'WRONG')
            v['password'].set('synthetic-guest-only-qualification')
            v['confirm'].invoke()
            ran.append(('Approval recorded' if mode=='confirm' else 'Approval refused') in v['status'].get())
            v['close'].invoke()
        root.after(200,act)
    modearg=sys.argv[1];sys.argv=[sys.argv[0],'review']
    main(window=window)
    if mode!='archive':assert ran==[True]
    else:assert not ran
    print('PASS protected authority GUI/archival role',modearg,flush=True)


if __name__=='__main__':
    role=sys.argv[1]
    if role=='worker':worker()
    elif role=='admit':admit(int(sys.argv[2]))
    elif role=='proof':
        from reserve_no_effect_worker import main
        sys.argv=[sys.argv[0],'native'];main(factory=factory)
    elif role in ('cancel','wrong','confirm','archive'):review(role)
    else:raise SystemExit('invalid disposable role')
