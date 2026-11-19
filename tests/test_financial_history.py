"""Real SQLite ledger history; explicit paper fill/approval fixtures."""
from datetime import datetime, timezone
import unittest
from financial_ledger import SCALE, AccountingError
from financial_goals import DEFAULT, initialize, save
from financial_history import report
import test_financial_goals as fixture
import test_reserve_policy as reserve_fixture


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.GoalTests('test_loss_and_no_cap_change');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.ledger=self.h.h;self.tx=self.h.tx;self.clock=self.h.clock
    def read(self,days=3,end=None,i='a'):return report(self.tx,i,days,end,clock=self.clock)
    def test_realized_and_settled_on_distinct_days(self):
        with self.tx() as db:db.execute("UPDATE gate_intents SET state='CLAIMED'")
        self.ledger.buy();self.ledger.sell(gross=110*SCALE)
        self.ledger.now+=86400
        self.ledger.f.settle('settle','a','sell',105*SCALE)
        r=self.read(2);a,b=r['days']
        self.assertEqual((a['realized_usd'],a['settled_usd'],a['pending_usd']),('5.00000000','0.00000000','105.00000000'))
        self.assertEqual((b['realized_usd'],b['settled_usd'],b['protected_usd']),('0.00000000','5.00000000','5.00000000'))
        self.assertEqual(r['totals'],dict(realized_usd='5.00000000',settled_usd='5.00000000',fees_usd='7.00000000',released_usd='0.00000000'))
        self.assertEqual(b['status'],'partial');self.assertFalse(r['real_income'])
        self.assertEqual(self.read(2,i='b')['totals']['realized_usd'],'0.00000000')
    def test_no_backfill_before_funding_and_no_empty_zero_claim(self):
        r=self.read(3);a,b,c=r['days'];self.assertEqual(a['status'],'not_funded');self.assertIsNone(a['active_usd']);self.assertIsNone(a['realized_usd'])
        self.assertEqual(b['status'],'partial');self.assertEqual(c['status'],'partial');self.assertEqual(r['reported_days'],2)
        older=self.read(1,a['day']);self.assertIsNone(older['totals']['realized_usd']);self.assertEqual(older['reported_days'],0)
    def test_past_window_does_not_use_later_gain_and_correction(self):
        self.h.trade();r=self.read(1);day=r['end_day'];original=r['days'][0]
        self.ledger.now+=86400
        self.ledger.f.late_correct('late','a','sell',109*SCALE,5*SCALE,settlement_received=104*SCALE,expected={'active':100*SCALE,'protected':4*SCALE,'pending':0,'qty':0},evidence_digest='c'*64)
        later=self.read(2);a,b=later['days'];self.assertEqual(a['protected_usd'],'5.00000000');self.assertEqual(b['realized_usd'],'-1.00000000');self.assertEqual(b['settled_usd'],'-1.00000000');self.assertEqual(b['protected_usd'],'4.00000000')
        old=self.read(1,day);self.assertEqual(old['days'][0]['protected_usd'],original['protected_usd']);self.assertEqual(old['totals']['realized_usd'],'5.00000000')
    def test_conflict_and_unresolved_correction_suppress_values(self):
        self.h.trade();self.ledger.f.late_correct('bad','a','sell',109*SCALE,5*SCALE,settlement_received=104*SCALE,expected={'active':100*SCALE,'protected':5*SCALE,'pending':0,'qty':0},evidence_digest='c'*64)
        r=self.read(1);self.assertTrue(r['review_required']);self.assertIsNone(r['totals']['realized_usd']);self.assertEqual(r['days'][0]['status'],'review_required')
        with self.tx() as db:db.execute("INSERT INTO fin_conflicts VALUES('fake','conflict')")
        self.assertTrue(self.read(1,i='b')['review_required'])
    def test_bad_range_scope_and_future_clock_rejected(self):
        for n in (0,32,True,1.2):
            with self.assertRaises(AccountingError):self.read(n)
        for day in ('2099-01-01','20261119','bad'):
            with self.assertRaises(ValueError):self.read(1,day)
        with self.assertRaises(AccountingError):self.read(i='unknown')
        self.h.trade();self.ledger.now-=1
        with self.assertRaisesRegex(AccountingError,'history_posting_clock'):self.read()
    def test_tamper_rejected_no_authority_mutation(self):
        self.h.trade()
        with self.tx() as db:before=list(db.execute('SELECT * FROM fin_head'));g=list(db.execute('SELECT * FROM gate_intents'))
        self.read()
        with self.tx() as db:
            self.assertEqual(list(db.execute('SELECT * FROM fin_head')),before);self.assertEqual(list(db.execute('SELECT * FROM gate_intents')),g)
            db.execute("UPDATE fin_events SET body='{}' WHERE seq=1")
        with self.assertRaisesRegex(AccountingError,'journal_integrity'):self.read()
    def test_dst_boundaries(self):
        self.h.save({'zone':'America/New_York'})
        self.ledger.now=int(datetime(2027,3,15,18,tzinfo=timezone.utc).timestamp())
        r=self.read(2);self.assertEqual(r['zone'],'America/New_York');self.assertEqual(r['days'][0]['end']-r['days'][0]['start'],23*3600)
        self.ledger.now=int(datetime(2027,11,8,18,tzinfo=timezone.utc).timestamp())
        self.assertEqual(self.read(2)['days'][0]['end']-self.read(2)['days'][0]['start'],25*3600)


class ReleaseTests(unittest.TestCase):
    def test_release_separate_from_earnings(self):
        h=reserve_fixture.ReserveTests('test_floor_rate_caps_and_concurrent_cycle');h.setUp();self.addCleanup(h.doCleanups)
        with h.h.tx() as db:initialize(db)
        h.activate();h.profitable('gain');h.cycle('next')
        r=report(h.h.tx,'a',3,clock=lambda:h.h.now)
        self.assertEqual(r['totals']['realized_usd'],'100.00000000');self.assertEqual(r['totals']['released_usd'],'5.00000000')
        self.assertEqual(r['days'][-1]['protected_usd'],'95.00000000');self.assertEqual(r['days'][-1]['active_usd'],'105.00000000')


if __name__=='__main__':unittest.main()
