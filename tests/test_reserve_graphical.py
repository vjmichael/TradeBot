"""Actual SQLite, encrypted fixture key and optional native Tk widgets (Xvfb)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from financial_ledger import AccountingError, SCALE, projection_values, start
from reserve_policy import DAY, ReserveManager
from reserve_runtime import provision, tick_all
from reserve_preview import initialize, save
from reserve_graphical import GraphicalBroker, ApprovalWindow
import test_reserve_policy as fixture

PASSWORD='local-fixture-passphrase-not-real'

class GraphicalTests(unittest.TestCase):
    def setUp(self):
        h=fixture.ReserveTests('test_default_saved_inactive_and_nonmutating_preview');h.setUp();self.addCleanup(h.doCleanups);self.h=h
        self.tx=h.h.tx;self.clock=lambda:h.h.now;self.mono=0
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.creds=Path(tmp.name);provision(self.creds,PASSWORD)
        with self.tx() as db:
            initialize(db);db.execute('ALTER TABLE instances ADD COLUMN name TEXT');db.execute('UPDATE instances SET name=id')
        self.b=GraphicalBroker(self.tx,self.creds,projection_values,clock=self.clock,monotonic=lambda:self.mono)
        self.d=save(self.tx,'a',h.p,0,clock=self.clock);h.observe('ready')
    def review(self,action='adopt'):return self.b.review('a',action)
    def confirm(self,r,password=PASSWORD):return self.b.confirm(r['receipt'],r['confirmation'],password)
    def snap(self):return self.h.h.snap()
    def activate(self):return self.confirm(self.review())
    def test_atomic_adoption_and_keyless_cycle_with_profit_protection(self):
        self.h.profitable('prior');self.h.observe('fresh')
        before=self.snap();r=self.review();self.assertEqual(self.snap(),before)
        result=self.confirm(r);self.assertEqual(result['state'],'on');self.assertEqual(self.b.verify_status('a',PASSWORD),'on')
        after=self.snap();self.assertEqual(after['journal_count'],before['journal_count']+1);self.assertEqual(after['active'],before['active']);self.assertEqual(after['protected'],before['protected'])
        self.assertEqual(after['reserve_policy']['saved'],after['reserve_policy']['active'])
        self.h.h.now+=DAY;self.h.observe('day1');self.assertEqual(tick_all(self.tx,clock=self.clock,verify=projection_values)['a']['release'],0)
        self.h.profitable('new');self.h.h.now+=DAY;self.h.observe('day2');self.assertEqual(tick_all(self.tx,clock=self.clock,verify=projection_values)['a']['release'],10*SCALE)
        self.assertEqual(self.snap()['protected'],190*SCALE)
    def test_wrong_password_confirmation_cancel_and_consumption(self):
        before=self.snap()
        r=self.review()
        with self.assertRaisesRegex(AccountingError,'authentication_failed'):self.confirm(r,'wrong-fixture-password')
        with self.assertRaisesRegex(AccountingError,'review_expired_or_consumed'):self.confirm(r)
        r=self.review();self.b.cancel()
        with self.assertRaises(AccountingError):self.confirm(r)
        r=self.review()
        with self.assertRaisesRegex(AccountingError,'confirmation_mismatch'):self.b.confirm(r['receipt'],'NO',PASSWORD)
        self.assertEqual(self.snap(),before)
    def test_stale_draft_and_boot_or_head(self):
        r=self.review();save(self.tx,'a',self.h.p|{'rate_bps':2000},1,clock=self.clock)
        with self.assertRaisesRegex(AccountingError,'draft_changed'):self.confirm(r)
        r=self.review();self.h.observe('changed')
        with self.assertRaisesRegex(AccountingError,'review_changed'):self.confirm(r)
        r=self.review()
        with self.tx() as db:start(db)
        with self.assertRaisesRegex(AccountingError,'review_changed'):self.confirm(r)
        self.assertIsNone(self.snap()['reserve_policy']['active'])
    def test_replacement_atomic_and_failed_risk_recheck_keeps_old(self):
        self.activate();old=self.snap()['reserve_policy']['active'];new=save(self.tx,'a',self.h.p|{'rate_bps':2000},1,clock=self.clock)
        self.h.observe('replace');r=self.review()
        with self.tx() as db:db.execute('UPDATE desk SET human_pause=1')
        before=self.snap()
        with self.assertRaisesRegex(AccountingError,'desk_paused'):self.confirm(r)
        self.assertEqual(self.snap(),before);self.assertEqual(before['reserve_policy']['active'],old)
        with self.tx() as db:db.execute('UPDATE desk SET human_pause=0')
        self.assertEqual(self.confirm(self.review())['state'],'on')
        self.assertEqual(self.snap()['reserve_policy']['active']['version'],new['id'])
        self.h.observe('same')
        with self.assertRaisesRegex(AccountingError,'policy_already_activated'):self.review()
    def test_revocation_races_observation_and_bad_draft_and_stale_tick(self):
        self.activate();old=self.snap()['reserve_policy']['active']['version']
        with self.tx() as db:db.execute("UPDATE reserve_drafts SET body='{}'")
        r=self.review('revoke');self.h.observe('changed');self.h.h.now+=1
        self.assertEqual(self.confirm(r)['state'],'off');self.assertEqual(self.b.verify_status('a',PASSWORD),'off')
        self.assertIsNone(self.snap()['reserve_policy']['active'])
        with self.assertRaisesRegex(AccountingError,'reinvestment_off'):ReserveManager(self.tx,clock=self.clock).submit('old-cycle','a','tick',{'version':old,'cycle':0})
    def test_review_expiry_monotonic_and_wall_rollback(self):
        r=self.review();self.mono=301
        with self.assertRaises(AccountingError):self.confirm(r)
        self.mono=0;r=self.review();self.h.h.now-=1
        with self.assertRaises(AccountingError):self.confirm(r)
    def test_verifier_failure_blocks_activation_and_tick(self):
        r=self.review();self.b.verify=lambda db,now:(_ for _ in ()).throw(AccountingError('evidence_missing'))
        with self.assertRaisesRegex(AccountingError,'evidence_missing'):self.confirm(r)
        self.assertIsNone(self.snap()['reserve_policy']['active'])
        self.b.verify=projection_values;self.activate();self.h.profitable('gain');self.h.h.now+=DAY;self.h.observe('fresh')
        self.assertEqual(tick_all(self.tx,clock=self.clock,verify=lambda db,now:(_ for _ in ()).throw(AccountingError('evidence_missing')))['a']['release'],0)
    def test_old_scheduled_policy_cannot_execute_replacement(self):
        self.activate();old=self.snap()['reserve_policy']['active']['version'];save(self.tx,'a',self.h.p|{'rate_bps':2000},1,clock=self.clock)
        self.h.observe('replace');self.confirm(self.review());self.h.h.now+=DAY;self.h.observe('cycle')
        p=self.snap()['reserve_policy']['active']['policy'];cycle=(self.clock()-p['anchor'])//DAY
        with self.assertRaisesRegex(AccountingError,'cycle_authority_changed'):ReserveManager(self.tx,clock=self.clock).submit('stale','a','tick',{'version':old,'cycle':cycle})
    @unittest.skipUnless(os.environ.get('DISPLAY'),'native graphical display required')
    def test_native_widgets_activate_and_revoke_and_cancel(self):
        import tkinter as tk
        root=tk.Tk();self.addCleanup(lambda:root.destroy() if root.winfo_exists() else None)
        ui=ApprovalWindow(root,self.b);root.update();ui.on.invoke();root.update()
        self.assertIsNotNone(ui.r);self.assertIn('Maximum capital: $125.00',ui.details.get('1.0','end'))
        ui.confirmation.set(ui.r['confirmation']);ui.password.set(PASSWORD);ui.commit.invoke();root.update()
        self.assertIn('Confirmed ON',ui.message.get());self.assertEqual(ui.password.get(),'');self.assertIsNotNone(self.snap()['reserve_policy']['active'])
        ui.off.invoke();root.update();self.assertIsNotNone(ui.r);ui.refresh();self.assertIsNone(ui.r);self.assertIsNotNone(self.snap()['reserve_policy']['active'])
        ui.off.invoke();ui.confirmation.set(ui.r['confirmation']);ui.password.set(PASSWORD);ui.commit.invoke();root.update()
        self.assertIn('Confirmed OFF',ui.message.get());self.assertIsNone(self.snap()['reserve_policy']['active'])

if __name__=='__main__':unittest.main()
