"""TB-07A real SQLite tests; RUN is a deliberately isolated paper fixture.

No runtime order submission or production RUN switch is exercised.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from risk_gate import (Gate, GateRefusal, MarketEvidence, Policy, Proposal,
                       RiskState, initialize, restrictive_transition)

NOW = 1_800_000_000


class Rehearsal:
    def __init__(self, path):
        self.path = path
        self.now = NOW
        self.gate = Gate(self.tx, clock=lambda: self.now)
        with self.tx() as db:
            db.execute("CREATE TABLE desk(id INTEGER PRIMARY KEY, paused INTEGER, human_pause INTEGER, virtual_cap_cents INTEGER)")
            db.execute("INSERT INTO desk VALUES(1,0,0,12000)")
            db.execute("CREATE TABLE instances(id TEXT PRIMARY KEY,pair TEXT,virtual_cents INTEGER,mode TEXT,strategy_family TEXT,state TEXT)")
            db.executemany("INSERT INTO instances VALUES(?,?,6000,'PAPER','numerical-baseline','RUN')",
                           [('a','BTC/USD'),('b','ETH/USD')])
            initialize(db)

    @contextmanager
    def tx(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def restart(self):
        with self.tx() as db:
            initialize(db)
            restrictive_transition(db,'restart',self.now)
        self.gate = Gate(self.tx,clock=lambda:self.now)


class GateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.h = Rehearsal(Path(tmp.name)/"rehearsal.sqlite")
        self.g = self.h.gate
        self.p = Policy(version='policy-1',owner='paper-worker',epoch=1,expires=NOW+1000,
            strategy_revision='revision-1',max_order_cents=6000,max_total_exposure_cents=10000,
            max_pair_exposure_cents=6000,max_positions=2,max_loss_per_trade_cents=300,
            daily_loss_limit_cents=1000,weekly_loss_limit_cents=1500,drawdown_limit_cents=2000,
            market_max_age_sec=60,account_max_age_sec=60,max_spread_bps=25,
            max_volatility_bps=500,min_depth_cents=5000,cost_bps=100,min_edge_bps=10)
        self.a = RiskState(receipt='account-1',observed=NOW,settled_cash_cents=18000,
            protected_cash_cents=6000,daily_loss_cents=0,weekly_loss_cents=0,drawdown_cents=0,
            open_risk_cents=0,active_by_instance={'a':6000,'b':6000},
            exposure_by_instance={'a':0,'b':0},reconciled=True,protection_ok=True)
        self.m = MarketEvidence(receipt='market-1',pair='BTC/USD',observed=NOW,available=NOW,
            source_digest='a'*64,strategy_revision='revision-1',entry=10000,stop=9500,target=11000,
            spread_bps=10,volatility_bps=100,depth_cents=10000,min_notional_cents=100,
            estimated_cost_bps=80,gross_edge_bps=130,continuous=True,operational=True,trigger=True,blackout=False)
        self.g.trusted_policy(self.p)
        self.g.trusted_account(self.a)
        self.g.trusted_market(self.m)
        self.g.trusted_market(replace(self.m,pair='ETH/USD',receipt='market-eth'))
        self.req = Proposal('proposal-1','a','BTC/USD','market-1',6000,NOW+300)

    def admit(self, **changes):
        return self.g.admit(replace(self.req,**changes))

    def second(self, **changes):
        return self.admit(**({'proposal_id':'proposal-b','instance_id':'b','pair':'ETH/USD',
                             'evidence_id':'market-eth'} | changes))

    def claim(self, receipt):
        return self.g.claim(receipt['intent_id'],'paper-worker',1)

    def test_entry_restriction_at_admission_claim_and_manual_pause(self):
        blocked=set()
        def guard(db,instance,now):
            if instance in blocked:raise GateRefusal('daily_goal_reached')
        self.g.entry_guard=guard
        first=self.admit()
        blocked.add('a')
        self.assertEqual(self.claim(first)['reason'],'daily_goal_reached')
        self.assertEqual(self.admit(proposal_id='goal-blocked')['reason'],'daily_goal_reached')
        self.assertEqual(self.second()['state'],'AUTHORIZED')
        blocked.clear()
        with self.h.tx() as db:db.execute('UPDATE desk SET human_pause=1')
        self.assertEqual(self.admit(proposal_id='manual-pause')['reason'],'desk_paused')

    def test_conservative_ceiling_claim_and_final_boundary(self):
        factor={'a':None,'b':None}
        self.g.entry_guard=lambda db,i,now,**kw:factor[i]
        first=self.admit()
        factor['a']=5000
        self.assertEqual(self.claim(first)['reason'],'authorization_changed')
        # A fresh signal, because Gate does not reuse old authorized evidence.
        self.g.trusted_market(replace(self.m,receipt='market-reduced'))
        reduced=self.admit(proposal_id='reduced',evidence_id='market-reduced')
        self.assertEqual((reduced['state'],reduced['notional_cents'],reduced['reserved_cents']),('AUTHORIZED',2500,2525))
        self.assertTrue(self.claim(reduced)['claimed'])
        with self.h.tx() as db:
            row=db.execute('SELECT * FROM gate_intents WHERE id=?',(reduced['intent_id'],)).fetchone()
            self.g.check_entry_boundary(db,row,self.h.now)
            factor['a']=2500
            with self.assertRaisesRegex(GateRefusal,'goal_or_policy_changed'):self.g.check_entry_boundary(db,row,self.h.now)
            factor['a']=None
            with self.assertRaisesRegex(GateRefusal,'goal_or_policy_changed'):self.g.check_entry_boundary(db,row,self.h.now)

    def test_conservative_declines_below_min_and_preserves_manual_pause(self):
        self.g.entry_guard=lambda db,i,now,**kw:100 if i=='a' else None
        self.assertEqual(self.admit()['reason'],'insufficient_risk_or_active_cash')
        self.assertEqual(self.second()['notional_cents'],5000)
        with self.h.tx() as db:db.execute('UPDATE desk SET human_pause=1')
        self.assertEqual(self.admit(proposal_id='still-paused')['reason'],'desk_paused')

    def test_conservative_daily_loss_ceiling(self):
        self.g.entry_guard=lambda db,i,now,**kw:5000 if i=='a' else None
        self.g.trusted_account(replace(self.a,receipt='loss-500',daily_loss_cents=500))
        self.assertEqual(self.admit()['reason'],'loss_lock')
        self.assertEqual(self.second()['state'],'AUTHORIZED')

    def test_size_and_exact_idempotency(self):
        first=self.admit()
        self.assertEqual((first['state'],first['notional_cents'],first['reserved_cents']),
                         ('AUTHORIZED',5000,5050))
        self.assertEqual(first,self.admit())
        with self.assertRaisesRegex(GateRefusal,'proposal_id_conflict'):
            self.admit(max_notional_cents=2000)
        self.assertEqual(self.admit(proposal_id='duplicate')['state'],'REJECT')
        claimed=self.claim(first)
        self.assertTrue(claimed['claimed'])
        self.assertEqual((claimed['entry'],claimed['stop'],claimed['target']),(10000,9500,11000))
        self.assertFalse(self.claim(first)['claimed'])
        self.assertEqual(self.admit()['state'],'CLAIMED')

    def test_verified_released_cap_is_bounded_by_desk_authority(self):
        from risk_gate import digest
        from dataclasses import asdict
        with self.h.tx() as db:
            db.execute("UPDATE desk SET virtual_cap_cents=12500 WHERE id=1")
        account=replace(self.a,settled_cash_cents=18000,protected_cash_cents=5500,
                        active_by_instance={'a':6500,'b':6000},
                        released_by_instance={'a':500,'b':0})
        def project(db,now):
            return account,digest(asdict(account))
        from financial_ledger import initialize as initialize_financial
        with self.h.tx() as db:initialize_financial(db)
        self.g=Gate(self.h.tx,clock=lambda:self.h.now,account_projection=project)
        first=self.admit()
        self.assertEqual(first['state'],'AUTHORIZED')
        account=replace(account,released_by_instance={'a':0,'b':0})
        self.assertEqual(self.admit(proposal_id='unbacked')['reason'],'instance_accounting_cap')
        account=replace(account,released_by_instance={'a':500,'b':100})
        self.assertEqual(self.admit(proposal_id='over-cap')['reason'],'desk_authorized_cap')

    def test_protected_cash_and_reservation(self):
        self.g.trusted_account(replace(self.a,receipt='limited',settled_cash_cents=10000,
            protected_cash_cents=8000,active_by_instance={'a':1000,'b':1000}))
        a,b=self.admit(),self.second()
        self.assertEqual((a['notional_cents'],a['reserved_cents']),(990,1000))
        self.assertEqual((b['notional_cents'],b['reserved_cents']),(990,1000))
        self.assertLessEqual(a['reserved_cents']+b['reserved_cents'],2000)

    def test_pair_and_total_exposure(self):
        with self.h.tx() as db:
            db.execute("UPDATE instances SET pair='BTC/USD' WHERE id='b'")
        a=self.admit()
        b=self.second(pair='BTC/USD',evidence_id='market-1')
        self.assertEqual((a['notional_cents'],b['notional_cents']),(5000,1000))
        self.assertEqual(a['notional_cents']+b['notional_cents'],6000)

    def test_account_and_loss_guards(self):
        self.g.trusted_account(replace(self.a,receipt='daily',daily_loss_cents=950))
        self.assertEqual(self.admit()['notional_cents'],833)  # fifty cents risk room
        self.assertEqual(self.second()['state'],'REJECT')  # pending risk already consumes room
        with self.assertRaisesRegex(GateRefusal,'unreconciled'):
            self.g.trusted_account(replace(self.a,receipt='bad',protected_cash_cents=17000))
        # Invalidate the previous intent, then exercise a hard loss lock.
        self.g.trusted_policy(replace(self.p,version='policy-2',epoch=2))
        self.g.trusted_account(replace(self.a,receipt='lock',weekly_loss_cents=1500))
        self.assertEqual(self.admit(proposal_id='locked')['reason'],'loss_lock')

    def test_cash_cannot_expand_or_round_up_to_venue_minimum(self):
        self.g.trusted_account(replace(self.a,receipt='small',
            active_by_instance={'a':100,'b':100},settled_cash_cents=6200))
        self.assertEqual(self.admit()['reason'],'insufficient_risk_or_active_cash')

    def test_freshness_and_quality(self):
        self.h.now+=61
        self.g.trusted_account(replace(self.a,receipt='fresh',observed=self.h.now))
        self.assertEqual(self.admit()['reason'],'market_stale_or_changed')
        self.g.trusted_market(replace(self.m,receipt='wide',observed=self.h.now,
            available=self.h.now,spread_bps=30))
        self.assertEqual(self.admit(proposal_id='wide',evidence_id='wide')['reason'],'market_quality')
        self.g.trusted_market(replace(self.m,receipt='blackout',observed=self.h.now,
            available=self.h.now,blackout=True))
        self.assertEqual(self.admit(proposal_id='blackout',evidence_id='blackout')['reason'],'market_or_trigger_block')
        self.g.trusted_market(replace(self.m,receipt='weak',observed=self.h.now,
            available=self.h.now,gross_edge_bps=100))
        self.assertEqual(self.admit(proposal_id='weak',evidence_id='weak')['reason'],'insufficient_validated_edge')
        self.g.trusted_market(replace(self.m,receipt='costly',observed=self.h.now,
            available=self.h.now,estimated_cost_bps=120))
        self.assertEqual(self.admit(proposal_id='costly',evidence_id='costly')['reason'],'cost_budget_exceeded')
        with self.assertRaises(GateRefusal):
            self.g.trusted_market(replace(self.m,receipt='future',observed=self.h.now+1,available=self.h.now+1))
        with self.assertRaises(GateRefusal):
            self.g.trusted_market(replace(self.m,receipt='future-availability',observed=self.h.now,available=self.h.now+1))

    def test_claim_rechecks_snapshot_policy_pause_and_time(self):
        a=self.admit()
        self.assertFalse(self.g.claim(a['intent_id'],'other-worker',1)['claimed'])
        self.g.trusted_market(replace(self.m,receipt='new'))
        self.assertEqual(self.claim(a)['reason'],'market_stale_or_changed')
        self.assertEqual(self.admit()['state'],'VOID')
        b=self.second()
        self.g.trusted_policy(replace(self.p,version='policy-2',epoch=2))
        self.assertFalse(self.claim(b)['claimed'])
        self.assertEqual(self.second()['state'],'VOID')
        c=self.admit(proposal_id='expiry',evidence_id='new',expires=NOW+1)
        self.h.now+=1
        self.assertFalse(self.g.claim(c['intent_id'],'paper-worker',2)['claimed'])
        self.assertEqual(self.admit(proposal_id='expiry',evidence_id='new',expires=NOW+1)['state'],'VOID')
        self.h.now-=1
        with self.assertRaisesRegex(GateRefusal,'clock_rollback'):
            self.admit(proposal_id='rollback')

    def test_restart_unknown_and_verified_no_fill(self):
        a,b=self.admit(),self.second()
        claim=self.claim(a)
        self.assertTrue(claim['claimed'])
        self.h.restart()
        self.g=self.h.gate
        self.assertEqual(self.admit()['state'],'UNKNOWN')
        self.assertEqual(self.second()['state'],'VOID')
        self.assertEqual(self.second(proposal_id='new-b')['reason'],'unresolved_execution')
        with self.assertRaisesRegex(GateRefusal,'unresolved_execution'):
            self.g.trusted_account(replace(self.a,receipt='cannot-erase'))
        with self.assertRaisesRegex(GateRefusal,'claim_mismatch'):
            self.g.trusted_resolution(a['intent_id'],'wrong-token','reconcile','NO_FILL')
        token=claim['claim_token']
        self.assertEqual(self.g.trusted_resolution(a['intent_id'],token,'unknown-receipt','UNKNOWN'),'UNKNOWN')
        self.assertEqual(self.g.trusted_resolution(a['intent_id'],token,'verified-no-fill','NO_FILL'),'NO_FILL')
        self.assertEqual(self.g.trusted_resolution(a['intent_id'],token,'verified-no-fill','NO_FILL'),'NO_FILL')
        self.g.trusted_account(replace(self.a,receipt='fresh'))
        self.g.trusted_market(replace(self.m,receipt='fresh-market'))
        self.assertEqual(self.admit(proposal_id='fresh-proposal',evidence_id='fresh-market')['state'],'AUTHORIZED')
        self.assertEqual(self.admit()['state'],'NO_FILL')

    def test_filled_or_partial_never_releases_without_accounting(self):
        a=self.admit()
        claim=self.claim(a)
        self.g.trusted_resolution(a['intent_id'],claim['claim_token'],'fill-receipt','FILLED_HELD')
        self.h.restart()
        self.g=self.h.gate
        self.assertEqual(self.admit()['state'],'FILLED_HELD')
        self.assertEqual(self.second()['reason'],'unresolved_execution')
        with self.assertRaises(GateRefusal):
            self.g.trusted_resolution(a['intent_id'],claim['claim_token'],'false-release','NO_FILL')
        with self.assertRaises(GateRefusal):
            self.g.trusted_account(replace(self.a,receipt='new-account'))

    def test_concurrent_duplicate_admission_and_claim(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.admit(),range(12)))
        self.assertEqual(len({r['intent_id'] for r in results}),1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims=list(pool.map(lambda _:self.claim(results[0]),range(12)))
        self.assertEqual(sum(r['claimed'] for r in claims),1)

    def test_scope_missing_pause_and_research_refusal(self):
        with self.h.tx() as db:
            db.execute("UPDATE desk SET human_pause=1")
        self.assertEqual(self.admit()['reason'],'desk_paused')
        with self.h.tx() as db:
            db.execute("UPDATE desk SET human_pause=0")
            db.execute("UPDATE instances SET strategy_family='research-assisted' WHERE id='a'")
        self.assertEqual(self.admit(proposal_id='research')['reason'],'strategy_or_instance_not_running')
        self.assertEqual(self.admit(proposal_id='wrong',pair='ETH/USD')['reason'],'instance_scope')
        with self.h.tx() as db:
            db.execute("DELETE FROM gate_current WHERE kind='policy'")
        self.assertEqual(self.second()['reason'],'missing_policy')

    def test_receipt_identity_and_old_replay(self):
        with self.assertRaisesRegex(GateRefusal,'receipt_conflict'):
            self.g.trusted_market(replace(self.m,spread_bps=11))
        self.g.trusted_market(replace(self.m,receipt='replacement'))
        self.g.trusted_market(self.m)
        self.assertEqual(self.admit()['reason'],'market_stale_or_changed')
        with self.assertRaisesRegex(GateRefusal,'ownership_epoch_not_advanced'):
            self.g.trusted_policy(replace(self.p,version='new'))

    def test_actual_desk_keeps_original_pause_contract(self):
        from tradebot import Store, InstanceSpec
        with tempfile.TemporaryDirectory() as name:
            store=Store(Path(name),12000)
            store.boot()
            row=store.add(InstanceSpec(name='paper',pair='BTC/USD',virtual_cents=6000,
                                       provider='none',strategy_family='numerical-baseline'))
            self.assertEqual(store.gate.admit(Proposal('real-desk',row['id'],'BTC/USD','none',
                1000,4_000_000_000))['reason'],'desk_paused')
            store.pause()
            store.boot()
            self.assertTrue(store.status()['paused'])
            self.assertTrue(store.status()['human_pause'])
            self.assertFalse(store.status()['live_available'])
            self.assertEqual(store.instances()[0]['id'],row['id'])
            with store.transaction() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM gate_decisions").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM gate_intents").fetchone()[0],0)


    def test_actual_store_pause_and_boot_restrict_existing_ledger(self):
        from tradebot import Store
        a,b=self.admit(),self.second()
        self.assertTrue(self.claim(a)['claimed'])
        with self.h.tx() as db:
            rows=[tuple(r) for r in db.execute("SELECT * FROM gate_intents")]
        with tempfile.TemporaryDirectory() as name:
            store=Store(Path(name),12000)
            store.boot()
            # Only a fixture transplant: the actual runtime is never unpaused.
            with store.transaction() as db:
                db.executemany("INSERT INTO gate_intents VALUES("+",".join("?" for _ in rows[0])+")",rows)
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],4)
            store.pause()
            with store.transaction() as db:
                states=dict(db.execute("SELECT id,state FROM gate_intents").fetchall())
                self.assertEqual(states,{a['intent_id']:'UNKNOWN',b['intent_id']:'VOID'})
                db.execute("UPDATE gate_intents SET state='CLAIMED' WHERE id=?",(a['intent_id'],))
                db.execute("UPDATE gate_intents SET state='AUTHORIZED' WHERE id=?",(b['intent_id'],))
            store.boot()
            with store.transaction() as db:
                self.assertEqual(dict(db.execute("SELECT id,state FROM gate_intents").fetchall()),states)
                db.execute("DROP TABLE gate_events")
            with self.assertRaisesRegex(RuntimeError,'risk ledger incomplete'):
                store.boot()

    def test_v2_desk_migration_preserves_existing_allocations(self):
        from tradebot import Store,InstanceSpec
        with tempfile.TemporaryDirectory() as name:
            store=Store(Path(name),12000)
            store.boot()
            existing=store.add(InstanceSpec(name='existing',pair='BTC/USD',virtual_cents=5000))
            with store.transaction() as db:
                for table in ('gate_clock','gate_inputs','gate_current','gate_decisions','gate_intents','gate_events','fin_events','fin_conflicts','fin_head'):
                    db.execute("DROP TABLE "+table)
                db.execute("PRAGMA user_version=2")
            store.boot()
            self.assertEqual(store.instances()[0]['id'],existing['id'])
            self.assertEqual(store.status()['allocated_cents'],5000)
            self.assertTrue(store.status()['paused'])
            with store.transaction() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],4)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM gate_intents").fetchone()[0],0)

if __name__ == '__main__':
    unittest.main()
