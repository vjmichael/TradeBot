"""PAPER-only SQLite reserve qualification; approval key and RUN are fixtures."""
import hashlib
import hmac
import json
import unittest
from concurrent.futures import ThreadPoolExecutor

from financial_ledger import AccountingError, SCALE, canon, sha, projection_values, start
from reserve_policy import ReserveManager, DAY
import test_financial_ledger as fixture

KEY=b'test-only-human-approval-key-00000000'


class ReserveTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.LedgerTests('test_profit_only_on_settlement_and_fee_basis')
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.f=self.h.f
        self.m=ReserveManager(self.h.tx,KEY,clock=lambda:self.h.now)
        with self.h.tx() as db:
            db.execute('ALTER TABLE desk ADD COLUMN paused INTEGER DEFAULT 0')
            db.execute('ALTER TABLE desk ADD COLUMN human_pause INTEGER DEFAULT 0')
            db.execute('UPDATE desk SET virtual_cap_cents=30000')
            db.execute('CREATE TABLE gate_inputs(kind TEXT,scope TEXT,receipt TEXT,body TEXT,hash TEXT)')
            db.execute('CREATE TABLE gate_current(kind TEXT,scope TEXT,receipt TEXT)')
            p=dict(expires=self.h.now+30*DAY,daily_loss_limit_cents=1000,weekly_loss_limit_cents=2000,drawdown_limit_cents=3000)
            db.execute("INSERT INTO gate_inputs VALUES('policy','desk','p',?,?)",(canon(p),sha(p)))
            db.execute("INSERT INTO gate_current VALUES('policy','desk','p')")
            db.execute("DELETE FROM gate_intents")
        self.p=dict(rate_bps=1000,floor_mode='fixed',floor_fixed=50*SCALE,floor_bps=0,
                    ceiling=125*SCALE,cycle_cap=20*SCALE,lifetime_cap=25*SCALE,
                    anchor=self.h.now%DAY,start=self.h.now,end=self.h.now+20*DAY,expiry=self.h.now+21*DAY)

    def observe(self,receipt):
        accounts={}
        for key in ('a','b'):
            s=self.f.snapshot(key)
            accounts[key]={k:s[k] for k in ('active','protected','pending','qty')}|{'covered_qty':s['qty']}
        self.f.reconcile_desk(receipt,observed_at=self.h.now,evidence_digest='a'*64,accounts=accounts,open_orders=[])

    def profitable(self,receipt,profit=100*SCALE,instance='a'):
        with self.h.tx() as db:
            pair='BTC/USD' if instance=='a' else 'ETH/USD'
            db.execute('INSERT INTO gate_intents VALUES(?,?,?,?,?,?)',(receipt,instance,pair,9900,10000,'CLAIMED'))
        self.f.buy(receipt+'b',instance,receipt,SCALE,98*SCALE,2*SCALE)
        self.f.sell(receipt+'s',instance,receipt+'exit',SCALE,100*SCALE+profit,0)
        self.f.settle(receipt+'t',instance,receipt+'s',100*SCALE+profit)
        with self.h.tx() as db:
            db.execute('DELETE FROM gate_intents WHERE id=?',(receipt,))

    def signed(self,receipt,action,payload,instance='a'):
        head=self.f.snapshot(instance)['journal_head']; expiry=self.h.now+60
        msg=canon(['maj-vaj:PAPER:reserve:v1',receipt,instance,action,payload,head,expiry]).encode()
        return dict(head=head,expiry=expiry,signature=hmac.new(KEY,msg,hashlib.sha256).hexdigest())

    def human(self,receipt,action,payload,instance='a'):
        return self.m.submit(receipt,instance,action,payload,self.signed(receipt,action,payload,instance))

    def activate(self,version='v1',instance='a',policy=None):
        p=dict(version=version,policy=policy or self.p)
        self.m.submit('save-'+version,instance,'save',p)
        self.observe('observe-'+version)
        return self.human('on-'+version,'activate',p,instance)

    def cycle(self,receipt,days=1,offset=0,observe=True,instance='a'):
        self.h.now+=days*DAY+offset
        if observe:self.observe('obs-'+receipt)
        return self.m.submit(receipt,instance,'tick',{})

    def test_default_saved_inactive_and_nonmutating_preview(self):
        self.profitable('gain')
        s=self.h.snap()
        self.assertIsNone(s['reserve_policy']['active'])
        self.assertEqual(s['reserve_policy']['released'],0)
        self.m.submit('save','a','save',dict(version='v',policy=self.p))
        self.observe('funds')
        before=self.h.snap()['journal_head']
        self.m.preview('a',self.p)
        self.assertEqual(before,self.h.snap()['journal_head'])
        with self.assertRaisesRegex(AccountingError,'reinvestment_off'):
            self.m.submit('tick','a','tick',{})
        self.assertEqual(self.h.snap()['protected'],100*SCALE)

    def test_signed_activation_and_no_existing_surplus(self):
        self.profitable('gain')
        self.activate()
        self.assertEqual(self.cycle('first')['release'],0)
        self.profitable('new',100*SCALE)
        self.assertEqual(self.cycle('second')['release'],10*SCALE)
        s=self.h.snap()
        self.assertEqual((s['active'],s['protected'],s['settled_pl']),(110*SCALE,190*SCALE,200*SCALE))
        self.observe('after')
        with self.h.tx() as db:
            p=projection_values(db,self.h.now)
        self.assertEqual(p['released_by_instance']['a'],1000)

    def test_forged_expired_and_changed_approval_rejected(self):
        p=dict(version='v',policy=self.p)
        self.m.submit('save','a','save',p)
        self.observe('first')
        signed=self.signed('on','activate',p)
        for grant in (None,signed|{'signature':'0'*64},signed|{'expiry':self.h.now-1}):
            with self.assertRaises(AccountingError):self.m.submit('on','a','activate',p,grant)
        self.observe('changed')
        with self.assertRaisesRegex(AccountingError,'stale_human_approval'):
            self.m.submit('on','a','activate',p,signed)
        self.assertIsNone(self.h.snap()['reserve_policy']['active'])

    def test_saved_update_does_not_replace_active_and_revoke(self):
        self.activate()
        new=self.p|{'rate_bps':2000}
        self.m.submit('new-save','a','save',dict(version='v2',policy=new))
        self.assertEqual(self.h.snap()['reserve_policy']['active']['policy']['rate_bps'],1000)
        with self.assertRaisesRegex(AccountingError,'policy_version_reused'):
            self.m.submit('reuse','a','save',dict(version='v2',policy=self.p))
        self.human('off','revoke',dict(version='v1'))
        self.assertIsNone(self.h.snap()['reserve_policy']['active'])
        self.assertEqual(self.h.snap()['reserve_policy']['saved']['version'],'v2')

    def test_revocation_survives_concurrent_observation(self):
        self.activate()
        p=dict(version='v1'); grant=self.signed('off','revoke',p)
        self.observe('new-observation')
        self.m.submit('off','a','revoke',p,grant)
        self.assertIsNone(self.h.snap()['reserve_policy']['active'])

    def test_expired_policy_and_hard_pause_release_zero(self):
        self.activate(policy=self.p|{'end':self.h.now+2*DAY,'expiry':self.h.now+2*DAY})
        self.profitable('gain')
        with self.h.tx() as db:db.execute('UPDATE desk SET human_pause=1')
        self.assertEqual(self.cycle('paused')['release'],0)
        with self.h.tx() as db:db.execute('UPDATE desk SET human_pause=0')
        self.profitable('gain2')
        self.assertEqual(self.cycle('expired')['release'],0)
        self.assertEqual(self.h.snap()['protected'],200*SCALE)

    def test_floor_rate_caps_and_concurrent_cycle(self):
        self.activate(policy=self.p|{'ceiling':105*SCALE})
        self.profitable('gain')
        self.h.now+=DAY; self.observe('cycle')
        with ThreadPoolExecutor(max_workers=8) as pool:
            rs=list(pool.map(lambda i:self.m.submit('tick'+str(i),'a','tick',{}),range(12)))
        self.assertEqual(sum(r.get('release',0) for r in rs),5*SCALE)
        self.profitable('gain2')
        self.assertEqual(self.cycle('next')['release'],0)
        self.assertEqual(self.h.snap()['protected'],195*SCALE)

    def test_earmark_and_exact_release_with_floor(self):
        self.profitable('gain')
        self.human('tax','earmark',dict(tax=40*SCALE,household=0,emergency=0,savings=0))
        self.observe('ready')
        p=dict(amount=11*SCALE,floor=50*SCALE,ceiling=125*SCALE)
        with self.assertRaisesRegex(AccountingError,'one_time_limit'):
            self.human('too-much','once',p)
        p['amount']=10*SCALE
        result=self.human('exact','once',p)
        self.assertEqual(result['release'],10*SCALE)
        self.assertEqual(self.h.snap()['reserve_policy']['earmarks']['tax'],40*SCALE)
        self.assertEqual(self.h.snap()['protected'],90*SCALE)

    def test_missed_and_stale_cycles_never_catch_up(self):
        self.activate()
        self.profitable('gain')
        self.assertEqual(self.cycle('skip',days=2)['release'],0)
        self.assertEqual(self.cycle('no-catch-up')['release'],0)
        self.profitable('gain2')
        self.assertEqual(self.cycle('stale',observe=False)['release'],0)
        self.assertEqual(self.cycle('no-stale-catch-up')['release'],0)
        self.profitable('gain3')
        self.assertEqual(self.cycle('fresh')['release'],10*SCALE)

    def test_loss_and_restart_lock(self):
        self.activate()
        self.profitable('gain')
        self.profitable('loss',-20*SCALE)
        self.assertEqual(self.cycle('loss-cycle')['release'],0)
        self.assertEqual((self.h.snap()['active'],self.h.snap()['protected']),(80*SCALE,100*SCALE))
        with self.h.tx() as db:start(db)
        self.assertEqual(self.cycle('post-restart',observe=False)['release'],0)
        self.assertEqual(self.h.snap()['reserve_policy']['released'],0)

    def test_combined_percentage_floor_and_shared_cap(self):
        with self.h.tx() as db:db.execute('UPDATE desk SET virtual_cap_cents=20300')
        p=self.p|{'floor_mode':'combined','floor_bps':7500}
        self.activate('v1','a',p); self.activate('v2','b',p)
        self.profitable('a-gain'); self.profitable('b-gain',instance='b')
        self.assertEqual(self.cycle('a-cycle')['release'],250_000_000)
        self.observe('after-a')
        self.assertEqual(self.m.submit('b-cycle','b','tick',{})['release'],50_000_000)
        self.assertEqual(self.f.snapshot('b')['reserve_policy']['released'],50_000_000)
        self.observe('after-b')
        with self.h.tx() as db:
            self.assertEqual(sum(projection_values(db,self.h.now)['released_by_instance'].values()),300)

    def test_correction_after_reserve_action_preserves_funds_and_locks(self):
        self.profitable('gain')
        self.activate()
        self.f.late_correct('late','a','gains',199*SCALE,0,settlement_received=199*SCALE,
            expected=dict(active=100*SCALE,protected=99*SCALE,pending=0,qty=0),evidence_digest='b'*64)
        self.assertTrue(self.h.snap()['correction_locked'])
        self.assertEqual(self.h.snap()['protected'],100*SCALE)
        self.assertEqual(self.cycle('locked')['release'],0)

    def test_restart_and_rollback_do_not_repeat_release(self):
        self.activate(); self.profitable('gain')
        self.assertEqual(self.cycle('first')['release'],5*SCALE)
        with self.h.tx() as db:start(db)
        self.assertTrue(self.m.submit('new-id','a','tick',{})['duplicate'])
        self.h.now-=1
        with self.assertRaisesRegex(AccountingError,'reserve_clock_rollback'):
            self.m.submit('rollback','a','tick',{})
        self.assertEqual(self.h.snap()['protected'],95*SCALE)


if __name__=='__main__':unittest.main()
