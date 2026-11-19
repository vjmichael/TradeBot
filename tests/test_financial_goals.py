"""Goal reporting: real SQLite ledger; explicitly synthetic signed wallet evidence."""
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from financial_ledger import SCALE, AccountingError, start, replay
from financial_goals import DEFAULT, bounds, initialize, plan, report, save, validate
from paper_custody import observation, publish
import test_financial_ledger as fixture

KEY=b'g'*32


class FormulaTests(unittest.TestCase):
    def test_monthly_plan_and_actual_calendar_days(self):
        p=DEFAULT|dict(expenses_cents=250000,savings_cents=50000,income_cents=150000,tax_bps=2000)
        self.assertEqual(plan(p,30),dict(shortfall_usd='1500.00000000',monthly_pre_tax_usd='1875.00000000',daily_plan_usd='62.50000000',days=30))
        self.assertEqual(plan(p|{'extra_costs_cents':10000},30)['monthly_pre_tax_usd'],'2000.00000000')
        self.assertEqual(plan(p|{'income_cents':350000},31)['daily_plan_usd'],'0.00000000')
    def test_leap_and_dst_calendar(self):
        def b(y,m,d,z):return bounds(int(datetime(y,m,d,18,tzinfo=timezone.utc).timestamp()),z)
        self.assertEqual(b(2028,2,29,'America/Phoenix')[-1],29)
        for y,m,d,h in [(2026,3,8,23),(2026,11,1,25)]:
            a=b(y,m,d,'America/New_York');self.assertEqual(a[2]-a[1],h*3600)
            a=b(y,m,d,'America/Phoenix');self.assertEqual(a[2]-a[1],86400)
    def test_validation(self):
        for bad in [{'tax_bps':10000},{'tax_bps':True},{'expenses_cents':-1},{'zone':'not-a-zone'},{'target_kind':'none','target_value':1},{'target_kind':'points','target_value':1000001},{'income_cents':1.2},{'live':True}]:
            with self.assertRaises(AccountingError):validate(DEFAULT|bad)


class GoalTests(unittest.TestCase):
    def setUp(self):
        h=fixture.LedgerTests('test_profit_only_on_settlement_and_fee_basis');h.setUp();self.addCleanup(h.doCleanups);self.h=h
        self.tx=h.tx;self.clock=lambda:h.now
        with self.tx() as db:initialize(db);db.execute("UPDATE gate_intents SET state='VOID'")
        self.cut=bounds(h.now,'America/Phoenix')[2];h.now=self.cut-10
        class Flat:
            def __init__(self,i):self.instance=i;self.key=KEY;self.binding=i*64
            def observation(self,row):return {'quote':100*SCALE,'qty':0}
        publish(self.tx,{i:observation(self.tx,Flat(i),clock=self.clock) for i in ('a','b')},self.key_for,clock=self.clock)
        h.now=self.cut+1
    def key_for(self,i):return KEY,i*64
    def report(self,i='a',key=True):return report(self.tx,i,clock=self.clock,key_for=self.key_for if key else None)
    def save(self,patch,version=0):return save(self.tx,'a',DEFAULT|patch,version,clock=self.clock)
    def trade(self,proceeds=105):
        with self.tx() as db:db.execute("UPDATE gate_intents SET state='CLAIMED'")
        self.h.buy();self.h.sell(gross=(proceeds+5)*SCALE);self.h.f.settle('settle','a','sell',proceeds*SCALE)
    def test_points_frozen_and_separate_realized_settled(self):
        self.save({'target_kind':'points','target_value':200})
        a=self.report();self.assertEqual((a['day_start_active_usd'],a['target_usd'],a['target_points']),('100.00000000','2.00000000','2.00000000'))
        with self.tx() as db:db.execute("UPDATE gate_intents SET state='CLAIMED'")
        self.h.buy();self.h.sell(gross=110*SCALE)
        a=self.report();self.assertEqual((a['daily_realized_usd'],a['daily_points'],a['daily_settled_usd']),('5.00000000','5.00000000','0.00000000'))
        self.h.f.settle('settle','a','sell',105*SCALE)
        a=self.report();self.assertEqual((a['daily_settled_usd'],a['protected_usd'],a['day_start_active_usd']),('5.00000000','5.00000000','100.00000000'))
        self.assertTrue(a['target_reached']);self.assertEqual(a['target_shortfall_usd'],'0.00000000');self.assertFalse(a['real_income'])
        self.assertEqual(self.report('b')['daily_realized_usd'],'0.00000000')
        with self.tx() as db:start(db)
        self.assertEqual(self.report()['baseline_receipt'],a['baseline_receipt'])
    def test_loss_and_no_cap_change(self):
        self.save({'target_kind':'usd','target_value':200});self.trade(97)
        a=self.report();self.assertEqual((a['daily_points'],a['target_shortfall_usd'],a['active_cash_usd']),('-3.00000000','5.00000000','97.00000000'))
        self.assertFalse(a['target_reached']);self.assertFalse(a['goal_pause_enabled']);self.assertEqual(a['authorized_principal_usd'],'100.00000000')
    def test_missing_baseline_never_backfills_and_dollar_visible(self):
        self.trade();self.save({'target_kind':'points','target_value':200})
        a=self.report(key=False);self.assertIsNone(a['daily_points']);self.assertIsNone(a['target_usd']);self.assertEqual(a['daily_realized_usd'],'5.00000000')
        self.h.now+=86400
        a=self.report();self.assertIsNone(a['day_start_active_usd']);self.assertEqual(a['daily_realized_usd'],'0.00000000')
    def test_version_day_activation_and_stale_update(self):
        a=self.save({'target_kind':'usd','target_value':200})
        b=self.save({'target_kind':'usd','target_value':500},1)
        self.assertNotEqual(a['effective_day'],b['effective_day']);self.assertEqual(self.report()['target_usd'],'2.00000000')
        with self.assertRaisesRegex(AccountingError,'settings_changed'):self.save({},1)
        with self.assertRaisesRegex(AccountingError,'zone_locked'):self.save({'zone':'UTC'},2)
        self.h.now+=86400;self.assertEqual(self.report()['target_usd'],'5.00000000')
    def test_save_and_report_do_not_mutate_authority(self):
        with self.tx() as db:before=tuple(db.execute('SELECT * FROM fin_head').fetchone());g=list(db.execute('SELECT * FROM gate_intents'))
        self.save({'target_kind':'usd','target_value':0});self.assertTrue(self.report()['target_reached'])
        with self.tx() as db:self.assertEqual(tuple(db.execute('SELECT * FROM fin_head').fetchone()),before);self.assertEqual(list(db.execute('SELECT * FROM gate_intents')),g)
    def test_forged_or_changed_opening_evidence_unavailable(self):
        with self.tx() as db:db.execute("INSERT INTO gate_events(intent_id,action,at,detail) VALUES('i','test',?,'{}')",(self.cut-1,))
        self.assertIsNone(self.report()['day_start_active_usd'])
    def test_posted_corrections_and_rollover_month(self):
        self.trade();self.h.now+=86400
        # Correct a past sell's USD gross downward; post the adjustment today.
        self.h.f.late_correct('late','a','sell',109*SCALE,5*SCALE,settlement_received=104*SCALE,expected={'active':100*SCALE,'protected':4*SCALE,'pending':0,'qty':0},evidence_digest='c'*64)
        a=self.report();self.assertEqual(a['daily_realized_usd'],'-1.00000000');self.assertEqual(a['protected_usd'],'4.00000000')
    def test_clock_rollback_disables_progress(self):
        self.trade();self.h.now-=1
        a=self.report();self.assertFalse(a['posting_time_valid']);self.assertIsNone(a['daily_realized_usd']);self.assertIsNone(a['day_start_active_usd'])


if __name__=='__main__':unittest.main()
