"""Read-only review and inactive draft tests on real SQLite PAPER fixtures."""
import unittest
from financial_ledger import SCALE, AccountingError, start
from reserve_preview import initialize, preview, save, status, review
from reserve_policy import DAY, ReserveManager
import test_reserve_policy as fixture

class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.ReserveTests('test_default_saved_inactive_and_nonmutating_preview');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.tx=self.h.h.tx;self.clock=lambda:self.h.h.now;self.p=self.h.p
        with self.tx() as db:initialize(db)
    def view(self):return status(self.tx,'a',clock=self.clock)
    def save(self,p=None,v=0):return save(self.tx,'a',p or self.p,v,clock=self.clock)
    def preview(self,p=None,gain=100*SCALE,verify=None):return preview(self.tx,'a',p or self.p,gain,clock=self.clock,verify=verify)
    def test_example_exact_floors_and_caps_no_mutation(self):
        before=self.h.h.snap();r=self.preview();self.assertIsNone(r['actual']);self.assertFalse(r['approval_available'])
        a=r['example'];self.assertEqual((a['release_usd'],a['active_after_usd'],a['protected_after_usd']),('5.00000000','105.00000000','95.00000000'))
        self.assertEqual(self.preview(self.p|{'ceiling':103*SCALE,'lifetime_cap':3*SCALE,'cycle_cap':3*SCALE})['example']['release_usd'],'3.00000000')
        self.assertEqual(self.h.h.snap(),before)
    def test_save_is_not_engine_policy_and_review_is_not_approval(self):
        before=self.h.h.snap();self.assertEqual(self.view()['current_state'],'off');self.assertEqual(self.view()['default_rate_bps'],0)
        a=self.save();r=review(self.tx,'a',1,clock=self.clock);self.assertEqual(r['draft_id'],a['id']);self.assertFalse(r['authorized']);self.assertFalse(r['approval_available'])
        self.assertEqual(self.h.h.snap(),before)
        with self.assertRaisesRegex(AccountingError,'approval_broker_required'):ReserveManager(self.tx,clock=self.clock).submit('attempt','a','activate',{})
    def test_new_version_and_active_policy_independent(self):
        self.h.activate();a=self.save();b=self.save(self.p|{'rate_bps':2000},1)
        self.assertNotEqual(a['id'],b['id']);self.assertEqual(self.view()['recorded_policy']['policy']['rate_bps'],1000)
        self.assertEqual(self.view()['draft']['policy']['rate_bps'],2000)
        with self.assertRaisesRegex(AccountingError,'draft_changed'):review(self.tx,'a',1,clock=self.clock)
        with self.tx() as db:start(db)
        self.assertEqual(self.view()['version'],2)
    def test_invalid_expired_cross_scope(self):
        for p in (self.p|{'rate_bps':0},self.p|{'ceiling':0},self.p|{'expiry':self.clock()-1}):
            with self.assertRaises(AccountingError):self.save(p)
        with self.assertRaises(AccountingError):save(self.tx,'missing',self.p,0,clock=self.clock)
        self.save();self.assertIsNone(status(self.tx,'b',clock=self.clock)['draft'])
        self.h.h.now+=22*DAY
        with self.assertRaises(AccountingError):review(self.tx,'a',1,clock=self.clock)
    def test_tamper_and_conflicts(self):
        self.save()
        with self.tx() as db:db.execute("UPDATE reserve_drafts SET body='{}'")
        with self.assertRaises(AccountingError):self.view()
    def test_existing_surplus_excluded_and_losses_block(self):
        self.h.profitable('gain');self.assertEqual(self.preview(gain=0)['example']['release_usd'],'0.00000000')
        self.assertEqual(self.preview()['example']['incremental_eligible_usd'],'100.00000000')
        self.h.profitable('loss',-20*SCALE);self.assertEqual(self.preview()['example']['release_usd'],'0.00000000')
    def test_signed_projection_required_and_actual_uses_engine(self):
        self.h.observe('ready');self.assertIsNone(self.preview()['actual'])
        # Explicit verifier fixture, not a native wallet authentication claim.
        def verify(db,now):return True
        a=self.preview(verify=verify)['actual'];self.assertEqual(a['release_usd'],'0.00000000');self.assertEqual(a['reason'],'missed_cycle')
        with self.tx() as db:db.execute('UPDATE desk SET human_pause=1')
        self.assertEqual(self.preview(verify=verify)['actual']['desk_room_usd'],'0.00000000')
    def test_disputed_balances_not_shown(self):
        with self.tx() as db:db.execute("INSERT INTO fin_conflicts VALUES('bad','proof')")
        self.assertEqual(self.view()['current_state'],'review_required');self.assertIsNone(self.preview()['example']);self.assertIsNone(self.view()['protected_cash_usd'])
