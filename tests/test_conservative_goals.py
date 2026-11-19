"""Real SQLite goal latch, explicit fixture wallets and deterministic cap arithmetic."""
from dataclasses import make_dataclass, replace
import unittest
from financial_ledger import AccountingError, SCALE, start, replay
from financial_goals import DEFAULT, validate, save
from goal_control import evaluate, restrict_policy, scoped_policy, LIMIT_FIELDS
import test_goal_control as fixture
import test_paper_execution as bridge_fixture


class LimitTests(unittest.TestCase):
    def test_ceilings_floor_and_only_shrink(self):
        Policy=make_dataclass('Policy',[(k,int) for k in LIMIT_FIELDS]+[('cost_bps',int),('max_positions',int),('strategy_revision',str)],frozen=True)
        normal=Policy(*([101]*len(LIMIT_FIELDS)),100,2,'original')
        for bps in range(100,10000,100):
            reduced=restrict_policy(normal,bps)
            for k in LIMIT_FIELDS:self.assertEqual(getattr(reduced,k),101*bps//10000)
            self.assertEqual((reduced.cost_bps,reduced.max_positions,reduced.strategy_revision),(100,2,'original'))
        self.assertIs(restrict_policy(normal,None),normal)
        self.assertEqual(restrict_policy(replace(normal,max_order_cents=1),100).max_order_cents,0)
        for invalid in (True,0,-100,9999,10000,10001,1.5):
            with self.assertRaises(AccountingError):restrict_policy(normal,invalid)
        normal_hash='a'*64
        self.assertEqual(scoped_policy(normal,normal_hash,None),(normal,normal_hash))
        a,ha=scoped_policy(normal,normal_hash,5000);b,hb=scoped_policy(normal,normal_hash,2500)
        self.assertNotEqual(ha,hb);self.assertNotEqual(ha,normal_hash)
        self.assertEqual(scoped_policy(normal,normal_hash,5000),(a,ha))
        self.assertNotEqual(scoped_policy(normal,'b'*64,5000)[1],ha)

    def test_reviewed_positive_goal_and_mutually_exclusive_modes(self):
        p=DEFAULT|{'target_kind':'usd','target_value':1000}
        for bad in ({'conservative_bps':True},{'conservative_bps':10000},{'conservative_bps':5050},{'conservative_bps':-100},{'conservative_bps':5000,'pause_on_goal':True},{'conservative_bps':5000,'target_value':0},{'conservative_bps':5000,'target_kind':'none','target_value':0}):
            with self.assertRaisesRegex(AccountingError,'invalid_conservative_limit'):validate(p|bad)
        self.assertEqual(validate(p|{'conservative_bps':5000})['conservative_bps'],5000)
        old={k:v for k,v in p.items() if k not in ('conservative_bps','pause_on_goal')}
        self.assertEqual(validate(old),old)


class ConservativeTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.ControlTests('test_notification_only_never_pauses');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.g=self.h.h
    def settings(self,**changes):return self.g.save({'target_kind':'usd','target_value':400,'conservative_bps':5000}|changes)
    def reached(self):self.settings();self.g.trade();self.h.flat()

    def test_verified_goal_only_restricts_and_preserves_capital(self):
        self.settings();self.assertIsNone(self.h.eval());self.g.trade()
        self.assertEqual(self.h.eval(),'goal_verification_pending')
        self.assertIsNone(self.g.report()['goal_notification'])
        self.h.flat()
        before=self.g.h.f.snapshot('a');self.assertEqual(self.h.eval(),5000)
        r=self.g.report();self.assertEqual(r['conservative_active_bps'],5000)
        self.assertFalse(r['goal_pause_latched']);self.assertEqual(r['protected_usd'],'5.00000000')
        self.assertEqual(self.g.h.f.snapshot('a'),before)
        self.assertEqual(self.g.report('b')['conservative_active_bps'],0)

    def test_no_verifier_waits_and_no_fake_notice(self):
        self.reached();self.assertEqual(self.h.eval(False),'goal_verification_pending')
        self.assertIsNone(self.g.report()['goal_notification']);self.assertEqual(self.h.eval(),5000)
        self.assertEqual(self.h.eval(False),5000)

    def test_restriction_survives_correction_restart_and_next_day_edit(self):
        self.reached();self.assertEqual(self.h.eval(),5000)
        self.g.save({'target_kind':'none','target_value':0},1)
        self.g.h.f.late_correct('late','a','sell',108*SCALE,5*SCALE,settlement_received=103*SCALE,expected={'active':100*SCALE,'protected':3*SCALE,'pending':0,'qty':0},evidence_digest='c'*64)
        with self.g.tx() as db:start(db)
        self.assertFalse(self.g.report()['target_reached']);self.assertEqual(self.h.eval(),5000)
        self.g.h.now+=86400;self.assertIsNone(self.h.eval());self.assertEqual(self.g.report()['conservative_active_bps'],0)

    def test_unknown_point_baseline_fails_closed_with_conservative_enabled(self):
        self.settings(target_kind='points',target_value=400)
        self.g.h.now+=86400;self.assertEqual(self.h.eval(),'goal_baseline_unavailable')

    def test_legacy_settings_do_not_enable_conservative_mode(self):
        old={k:v for k,v in DEFAULT.items() if k not in ('conservative_bps','pause_on_goal')}
        save(self.g.tx,'a',old|{'target_kind':'usd','target_value':100},0,clock=self.g.clock)
        self.g.trade();self.h.flat();self.assertIsNone(self.h.eval());self.assertEqual(self.g.report()['conservative_active_bps'],0)

    def test_actual_bridge_checks_final_authority_before_native_call(self):
        h=bridge_fixture.BridgeTests('test_claim_then_submit_once_and_import_atomic');h.setUp();self.addCleanup(h.doCleanups)
        seen=[]
        def changed(db,row,now):
            seen.append(row['state']);raise AccountingError('goal_policy_changed')
        h.bridge.gate.check_entry_boundary=changed
        self.assertEqual(h.submit()['reason'],'native_outcome_unknown');self.assertEqual(seen,['CLAIMED']);self.assertEqual(h.d.calls,0)
        with h.tx() as db:self.assertIsNone(db.execute("SELECT 1 FROM gate_events WHERE action='native_attempt'").fetchone())


if __name__=='__main__':unittest.main()
