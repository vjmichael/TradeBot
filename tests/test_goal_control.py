"""Real SQLite; authenticated flat wallet and execution inputs are fixtures."""
import json
import unittest
from contextlib import nullcontext
from financial_ledger import AccountingError, SCALE, replay, start, projection_values
from financial_goals import DEFAULT, report, save
from goal_control import evaluate
from paper_custody import observation, publish, _accounts
import test_financial_goals as fixture
import test_paper_execution as bridge_fixture


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.GoalTests('test_loss_and_no_cap_change');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.tx=self.h.tx;self.clock=self.h.clock

    def eval(self,verify=True):
        with self.tx() as db:return evaluate(db,'a',self.clock(),self.h.key_for,verify=self.verify if verify else None)

    def verify(self,db,now):
        projection_values(db,now)
        prev,raw=db.execute('SELECT previous,body FROM fin_events ORDER BY seq DESC LIMIT 1').fetchone();p=json.loads(raw)['payload']
        self.assertEqual(_accounts(db,p['worker_evidence'],self.h.key_for,now,prev,p['execution_anchor'],p['boot']),p['accounts'])

    def flat(self):
        # Explicitly synthetic closure for this accounting fixture, NOT venue proof.
        with self.tx() as db:db.execute("UPDATE gate_intents SET state='VOID'")
        h=self.h
        balances={i:h.h.f.snapshot(i) for i in ('a','b')}
        class Flat:
            def __init__(self,i):self.instance=i;self.key=fixture.KEY;self.binding=i*64
            def observation(self,row):
                s=balances[self.instance]
                return {'quote':s['active']+s['protected'],'qty':0}
        publish(self.tx,{i:observation(self.tx,Flat(i),clock=self.clock) for i in ('a','b')},h.key_for,clock=self.clock)

    def test_opt_in_pause_and_notification_after_authenticated_close(self):
        self.h.save({'target_kind':'points','target_value':200,'pause_on_goal':True})
        self.assertIsNone(self.eval());r=self.h.report();self.assertTrue(r['baseline_persisted']);self.assertIsNone(r['goal_notification'])
        self.h.trade();self.assertEqual(self.eval(),'daily_goal_reached');self.assertIsNone(self.h.report()['goal_notification'])
        self.flat();self.assertEqual(self.eval(),'daily_goal_reached');r=self.h.report();self.assertTrue(r['goal_pause_latched']);self.assertEqual(r['goal_notification']['realized_units'],5*SCALE)
        with self.tx() as db:start(db)
        self.assertEqual(self.eval(),'daily_goal_reached');self.assertEqual(self.h.report()['goal_notification'],r['goal_notification'])
        with self.tx() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM goal_daily WHERE kind='reached'").fetchone()[0],1)

    def test_notification_only_never_pauses(self):
        self.h.save({'target_kind':'usd','target_value':200});self.h.trade();self.flat()
        self.assertIsNone(self.eval());self.assertFalse(self.h.report()['goal_pause_latched']);self.assertIsNotNone(self.h.report()['goal_notification'])

    def test_missing_baseline_and_zero_target_validation(self):
        with self.assertRaisesRegex(AccountingError,'invalid_goal_pause'):self.h.save({'target_kind':'usd','target_value':0,'pause_on_goal':True})
        self.h.save({'target_kind':'points','target_value':200,'pause_on_goal':True});self.h.h.now+=86400
        self.assertEqual(self.eval(),'goal_baseline_unavailable');self.assertIsNone(self.h.report()['goal_notification'])

    def test_latch_cannot_be_cleared_by_next_day_settings_or_correction(self):
        self.h.save({'target_kind':'usd','target_value':400,'pause_on_goal':True});self.h.trade();self.flat();self.eval()
        self.h.save({'target_kind':'none','target_value':0},1)
        self.h.h.f.late_correct('late','a','sell',108*SCALE,5*SCALE,settlement_received=103*SCALE,expected={'active':100*SCALE,'protected':3*SCALE,'pending':0,'qty':0},evidence_digest='c'*64)
        self.assertFalse(self.h.report()['target_reached']);self.assertEqual(self.eval(),'daily_goal_reached')
        self.h.h.now+=86400;self.assertIsNone(self.eval());self.assertFalse(self.h.report()['goal_pause_latched'])

    def test_corrupted_daily_receipt_refused(self):
        self.h.save({'target_kind':'usd','target_value':200,'pause_on_goal':True});self.h.trade();self.flat();self.eval()
        with self.tx() as db:db.execute("UPDATE goal_daily SET body='{}' WHERE kind='reached'")
        with self.assertRaisesRegex(AccountingError,'goal_daily_integrity'):self.eval()

    def test_no_verifier_never_claims_verified_notification(self):
        self.h.save({'target_kind':'usd','target_value':200,'pause_on_goal':True});self.h.trade();self.flat()
        self.assertEqual(self.eval(False),'daily_goal_reached');self.assertIsNone(self.h.report()['goal_notification'])

    def test_final_entry_boundary_rechecks_restriction(self):
        h=bridge_fixture.BridgeTests('test_claim_then_submit_once_and_import_atomic');h.setUp();self.addCleanup(h.doCleanups)
        seen=[]
        def deny(db,instance,now,*,final=False):
            seen.append((instance,final));raise AccountingError('daily_goal_reached')
        h.bridge.gate.entry_guard=deny
        result=h.submit();self.assertEqual(result['reason'],'native_outcome_unknown');self.assertEqual(seen,[('a',True)]);self.assertEqual(h.d.calls,0);self.assertEqual(h.state(),'UNKNOWN')


if __name__=='__main__':unittest.main()
