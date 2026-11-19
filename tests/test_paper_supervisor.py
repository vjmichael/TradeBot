"""Real desk transactions and an explicitly synthetic native driver."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from financial_ledger import AccountingError, SCALE, projection_values, start
from paper_supervisor import ProtectionWorker, custody, supervise
import test_paper_execution as fixture


def order(i,tag,side,q,price,fee,terminal=True):
    return dict(id=i,tag=tag,side=side,terminal=terminal,qty=q,gross=q*price//SCALE,fee=fee,price=price)


class Driver:
    binding='b'*64
    instance='a'
    def __init__(self,h):
        self.h=h; self.price=100*SCALE; self.at=h.now; self.calls=[]; self.cancelled=[]
        self.orders=[order('entry','mv:i','buy',SCALE//2,100*SCALE,SCALE)]
        self.position=SCALE//2; self.closed=False; self.quote=49*SCALE
        self.fail=False; self.partial=False
    def poll(self,row):pass
    def view(self,row):return copy.deepcopy(dict(orders=self.orders,position=self.position,closed=self.closed,quote=self.quote))
    def bid(self,row):return self.price,self.at
    def cancel_entry(self,row,o):
        self.cancelled.append(o['id']); self.orders[0]['terminal']=True
    def exit(self,row,request):
        self.calls.append(copy.deepcopy(request))
        if self.fail:raise RuntimeError('worker lost after exit attempt')
        q=request['qty']//2 if self.partial else request['qty']; self.partial=False
        o=order('exit'+str(len(self.calls)),request['tag'],'sell',q,self.price,SCALE//2)
        self.orders.append(o);self.position-=q;self.quote+=o['gross']-o['fee'];self.closed=self.position==0


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.BridgeTests('test_claim_then_submit_once_and_import_atomic');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.assertTrue(self.h.submit()['submitted']);self.d=Driver(self.h)
        self.worker=ProtectionWorker(self.h.tx,self.d,clock=lambda:self.h.now)
    def tick(self):return self.worker.tick('i')
    def snapshot(self):return self.h.h.snap()
    def observe(self):
        f=self.h.h.f; a=self.snapshot();b=f.snapshot('b')
        f.reconcile_desk('observation',observed_at=self.h.now,evidence_digest='a'*64,accounts={k:{n:s[n] for n in ('active','protected','pending','qty')}|{'covered_qty':s['qty']} for k,s in [('a',a),('b',b)]},open_orders=[])
    def test_stop_while_paused_and_expired_preserves_loss_isolation(self):
        with self.h.tx() as db:db.execute('UPDATE desk SET paused=1,human_pause=1');db.execute('UPDATE gate_intents SET expires=1')
        self.d.price=90*SCALE
        self.assertEqual(self.tick()['reason'],'stop');self.assertEqual(self.tick()['state'],'ACCOUNTED')
        s=self.snapshot();self.assertEqual((s['active'],s['protected'],s['gate_reserved']),(93*SCALE+SCALE//2,0,0))
        self.assertEqual(self.tick()['state'],'ACCOUNTED');self.assertEqual(len(self.d.calls),1)
    def test_target_partial_exit_finishes_even_when_price_retreats(self):
        self.d.price=120*SCALE;self.d.partial=True
        self.assertEqual(self.tick()['reason'],'target');self.d.price=100*SCALE
        self.assertEqual(self.tick()['reason'],'target')
        s=self.snapshot();self.assertEqual(s['qty'],SCALE//4);self.assertGreater(s['pending'],0);self.assertEqual(s['protected'],0);self.assertEqual(s['gate_reserved'],100*SCALE)
        self.assertEqual(self.tick()['state'],'ACCOUNTED');s=self.snapshot()
        self.assertEqual(s['qty'],0);self.assertEqual(s['pending'],0);self.assertEqual(s['protected'],4*SCALE);self.assertEqual(s['active'],99*SCALE)
        # Each settled gain protected independently; loss is never replenished.
    def test_wallet_conflict_rolls_back_release_and_can_reconcile(self):
        self.d.price=120*SCALE;self.tick();self.d.quote+=1
        with self.assertRaisesRegex(AccountingError,'wallet_mismatch'):self.tick()
        self.assertEqual(self.snapshot()['gate_reserved'],100*SCALE);self.assertEqual(self.snapshot()['protected'],0)
        self.d.quote-=1;self.assertEqual(self.tick()['state'],'ACCOUNTED')
        self.assertEqual(self.snapshot()['protected'],8*SCALE+SCALE//2)
    def test_unknown_exit_never_resends_after_restart(self):
        self.d.price=90*SCALE;self.d.fail=True
        with self.assertRaises(RuntimeError):self.tick()
        with self.h.tx() as db:start(db)
        self.worker=ProtectionWorker(self.h.tx,self.d,clock=lambda:self.h.now);self.d.fail=False
        with self.assertRaisesRegex(AccountingError,'ambiguous_exit_attempt'):self.tick()
        self.assertEqual(len(self.d.calls),1);self.assertEqual(self.snapshot()['gate_reserved'],100*SCALE)
    def test_open_partial_entry_cancel_before_protective_exit(self):
        self.d.orders[0]['terminal']=False;self.d.price=90*SCALE
        self.assertEqual(self.tick()['state'],'OPEN_ORDER_HELD');self.assertEqual(self.d.cancelled,['entry']);self.assertEqual(self.d.calls,[])
        self.assertEqual(self.tick()['reason'],'stop');self.assertEqual(self.tick()['state'],'ACCOUNTED')
    def test_missing_fee_never_prevents_exit_but_keeps_hold(self):
        self.d.orders[0]['fee']=None;self.d.price=90*SCALE
        self.assertEqual(self.tick()['accounting'],'held')
        with self.assertRaisesRegex(AccountingError,'fee_unverified'):self.tick()
        self.assertEqual(self.snapshot()['gate_reserved'],100*SCALE)
    def test_release_requires_matching_journal_head_and_fresh_desk_observation(self):
        self.d.price=120*SCALE;self.tick();self.tick()
        with self.h.tx() as db:
            with self.assertRaisesRegex(AccountingError,'fresh_desk'):projection_values(db,self.h.now)
        self.observe()
        with self.h.tx() as db:self.assertEqual(projection_values(db,self.h.now)['protected_cash_cents'],850)
        with self.assertRaisesRegex(AccountingError,'unverified_intent'):self.h.h.f.buy('latebuy','a','i',1,1,0)
        with self.h.tx() as db:db.execute("UPDATE gate_events SET detail='{}' WHERE action='native_released'")
        self.assertEqual(self.snapshot()['gate_reserved'],100*SCALE)
        with self.h.tx() as db:
            with self.assertRaisesRegex(AccountingError,'unresolved_execution'):projection_values(db,self.h.now)
    def test_os_custody_and_real_supervisor_shutdown(self):
        p=self.h.h.path.parent
        with custody(p):
            r=subprocess.run([sys.executable,'-c','from paper_supervisor import custody; import sys;\nwith custody(sys.argv[1]): pass',str(p)],capture_output=True,timeout=10)
            self.assertNotEqual(r.returncode,0)
        stop=threading.Event();d=self.d
        class StopDriver(Driver):
            def poll(self,row):stop.set()
        supervise(p,self.h.tx,lambda:StopDriver(self.h),stop=stop,interval=.25,clock=lambda:self.h.now)
        self.assertTrue(stop.is_set());self.assertEqual(self.snapshot()['qty'],SCALE//2)
    def test_stale_quote_cannot_trigger_order(self):
        self.d.price=90*SCALE;self.d.at-=31
        with self.assertRaisesRegex(AccountingError,'stale_protection_quote'):self.tick()
        self.assertEqual(self.d.calls,[])


if __name__=='__main__':unittest.main()
