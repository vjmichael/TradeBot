"""R2 tests: native runtime fixture + real desk/receipt SQLite and OS processes."""
import copy
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from financial_ledger import AccountingError, SCALE, replay, start, execution_anchor
from paper_custody import ReceiptJournal, IdlePublisher, workspace_key, authenticate, observation, packet, private_key, publish, run_owned
from paper_supervisor import ProtectionWorker
import test_paper_supervisor as fixture
import test_paper_execution as bridge_fixture

KEY=b'x'*32


def raw(side='buy',filled=.5,fee=1.):
    return dict(id='dry_run_'+side+'_native',symbol='BTC/USD',side=side,status='closed',filled=filled,cost=filled*100,average=100,fee={'currency':'USD','cost':fee})


class JournalTests(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);self.p=Path(d.name)/'receipts.sqlite'
        self.j=ReceiptJournal(self.p,KEY,'b'*64)
        from financial_ledger import sha
        self.oid='order:'+sha(['BTC/USD','dry_run_buy_native'])

    def fee(self,j=None):return (j or self.j).fee(self.oid,'BTC/USD','buy',SCALE//2,50*SCALE,100*SCALE)

    def test_terminal_fee_restart_and_idempotency(self):
        self.j.capture(raw());self.j.capture(raw())
        self.assertEqual(self.fee(ReceiptJournal(self.p,KEY,'b'*64)),SCALE)
        with self.assertRaisesRegex(AccountingError,'authentication_failed'):self.fee(ReceiptJournal(self.p,b'y'*32,'b'*64))
        with self.assertRaisesRegex(AccountingError,'receipt_mismatch'):self.fee(ReceiptJournal(self.p,KEY,'c'*64))

    def test_partial_open_not_booked_conflict_durable(self):
        self.j.capture(raw()|{'status':'open'});self.assertIsNone(self.fee())
        self.j.capture(raw())
        with self.assertRaisesRegex(AccountingError,'receipt_conflict'):self.j.capture(raw(fee=2.))
        with self.assertRaisesRegex(AccountingError,'receipt_conflict'):self.fee(ReceiptJournal(self.p,KEY,'b'*64))

    def test_tamper_and_missing_raw_fee_refused(self):
        with self.assertRaisesRegex(AccountingError,'fee_unverified'):self.j.capture(raw()|{'fee':None})
        self.assertIsNone(self.fee());self.j.capture(raw())
        from contextlib import closing
        with closing(sqlite3.connect(self.p)) as db:
            db.execute("UPDATE receipts SET mac='0'");db.commit()
        with self.assertRaisesRegex(AccountingError,'authentication_failed'):self.fee()

    def test_receipt_survives_process_sigkill(self):
        # Wait for the exact durable receipt, then kill the writer without cleanup.
        script="from paper_custody import ReceiptJournal; import sys,json,time; ReceiptJournal(sys.argv[1],b'x'*32,'b'*64).capture(json.loads(sys.argv[2])); print('COMMITTED',flush=True);time.sleep(60)"
        p=subprocess.Popen([sys.executable,'-c',script,str(self.p),__import__('json').dumps(raw())],stdout=subprocess.PIPE,text=True)
        try:self.assertEqual(p.stdout.readline().strip(),'COMMITTED')
        finally:p.kill();p.wait(timeout=5);p.stdout.close()
        self.assertEqual(self.fee(ReceiptJournal(self.p,KEY,'b'*64)),SCALE)

    def test_private_key_custody(self):
        p=self.p.parent/'key';p.write_bytes(KEY);p.chmod(0o600);self.assertEqual(private_key(p),KEY)
        p.chmod(0o644)
        with self.assertRaisesRegex(AccountingError,'private_receipt_key'):private_key(p)
        p.chmod(0o600);link=p.with_name('link');link.symlink_to(p)
        with self.assertRaises(OSError):private_key(link)


class OwnedTests(unittest.TestCase):
    def setUp(self):
        self.h=bridge_fixture.BridgeTests('test_claim_then_submit_once_and_import_atomic');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.clock=lambda:self.h.now
        with self.h.tx() as db:
            db.execute("ALTER TABLE gate_intents ADD COLUMN owner TEXT DEFAULT 'worker'")
            db.execute("ALTER TABLE gate_intents ADD COLUMN epoch INTEGER DEFAULT 1")
        self.stop=threading.Event()

    def test_one_owner_entry_then_protection_when_paused(self):
        h=self.h;stop=self.stop
        class Driver(fixture.Driver):
            def __init__(self):super().__init__(h);self.entered=0;self.price=120*SCALE
            def preflight(self,instance,pair):return self.binding
            def entry(self,row,guard):
                self.entered+=1
                return guard(lambda:{'observed':True})
            def exit(self,row,request):
                super().exit(row,request)
            def poll(self,row):
                with h.tx() as db:db.execute('UPDATE desk SET human_pause=1')
        d=Driver()
        def heart(ready):
            if not ready and h.h.snap()['gate_reserved']==0:stop.set()
        run_owned(h.h.path.parent,h.tx,lambda:d,lambda:bridge_fixture.GateFixture(h.tx),stop=stop,clock=self.clock,interval=.25,heartbeat=heart)
        self.assertEqual(d.entered,1);self.assertEqual(len(d.calls),1)
        self.assertEqual(h.h.snap()['protected'],8*SCALE+SCALE//2)

    def test_signed_scope_head_boot_wallet_and_replay(self):
        h=self.h
        class Flat:
            def __init__(self,i):self.instance=i;self.key=KEY;self.binding=i*64
            def observation(self,row):return {'quote':100*SCALE,'qty':0}
        def gather():return {i:observation(h.tx,Flat(i),clock=self.clock) for i in ('a','b')}
        def key_for(i):return KEY,i*64
        with h.tx() as db:db.execute("UPDATE gate_intents SET state='VOID'")
        packets=gather();forged=copy.deepcopy(packets);forged['a']['body']['quote']+=1
        with self.assertRaisesRegex(AccountingError,'authentication_failed'):publish(h.tx,forged,key_for,clock=self.clock)
        with self.assertRaisesRegex(AccountingError,'observation_scope'):publish(h.tx,{'a':packets['a']},key_for,clock=self.clock)
        self.assertFalse(publish(h.tx,packets,key_for,clock=self.clock)['duplicate'])
        with self.assertRaisesRegex(AccountingError,'observation_stale'):publish(h.tx,packets,key_for,clock=self.clock)
        packets=gather()
        with h.tx() as db:start(db)
        with self.assertRaisesRegex(AccountingError,'observation_stale'):publish(h.tx,packets,key_for,clock=self.clock)
        packets=gather();b=packets['a']['body']|{'quote':99*SCALE};packets['a']=packet(KEY,'flat-wallet',b)
        with self.assertRaisesRegex(AccountingError,'wallet_mismatch'):publish(h.tx,packets,key_for,clock=self.clock)
        packets=gather();h.now+=31
        with self.assertRaisesRegex(AccountingError,'observation_stale'):publish(h.tx,packets,key_for,clock=self.clock)

    def test_idle_spool_requires_every_workspace_and_does_not_refresh_inflight(self):
        h=self.h;data=h.h.path.parent
        (data/'paper').mkdir(mode=0o700)
        for i in ('a','b'):
            w=data/'paper'/i;w.mkdir(mode=0o700)
            for name,body in [('paper.sqlite',b'dummy'),('receipt.key',KEY)]:
                f=w/name;f.write_bytes(body);f.chmod(0o600)
        class Flat:
            def __init__(self,i):self.instance=i;self.key,self.binding=workspace_key(data,i)
            def observation(self,row):return {'quote':100*SCALE,'qty':0}
        collector=IdlePublisher(data,h.tx,clock=self.clock)
        before=h.h.snap()['journal_head'];collector(Flat('a'));self.assertEqual(h.h.snap()['journal_head'],before)
        with h.tx() as db:db.execute("UPDATE gate_intents SET state='VOID'")
        collector(Flat('a'));self.assertEqual(h.h.snap()['journal_head'],before)
        collector(Flat('b'));head=h.h.snap()['journal_head'];self.assertNotEqual(head,before)
        collector(Flat('a'));self.assertEqual(h.h.snap()['journal_head'],head)

    def test_sustained_repeated_protection_observations(self):
        h=self.h;h.submit();d=fixture.Driver(h);worker=ProtectionWorker(h.tx,d,clock=self.clock)
        for _ in range(100):
            self.assertEqual(worker.tick('i')['state'],'MONITORING')
            h.now+=2;d.at=h.now
        d.price=90*SCALE;self.assertEqual(worker.tick('i')['reason'],'stop')
        self.assertEqual(worker.tick('i')['state'],'ACCOUNTED')
        self.assertEqual(len(d.calls),1);self.assertEqual(h.h.snap()['gate_reserved'],0)


if __name__=='__main__':unittest.main()
