"""Heartbeat and acceptance rules; public responses and elapsed time are fixtures."""
import json
import unittest
from financial_ledger import AccountingError, sha
from paper_monitor import heartbeat, read_health, validate_book, audit
import test_paper_provision as fixture


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.ProvisionTests('test_atomic_setup_and_duplicate_no_reset');self.h.setUp();self.addCleanup(self.h.doCleanups);self.h.provision();self.t=1800000000
        with self.h.tx() as db:db.execute("ALTER TABLE gate_intents ADD COLUMN state TEXT")
    def clock(self):return self.t
    def stamp(self,ready=False):heartbeat(self.h.data,fixture.I,self.h.tx,ready,clock=self.clock)
    def test_signed_health_staleness_and_process_binding(self):
        self.stamp(True)
        with self.assertRaisesRegex(AccountingError,'stale_or_unready'):read_health(self.h.data,fixture.I,self.t)
        self.stamp();self.assertEqual(read_health(self.h.data,fixture.I,self.t)['stage'],'tick')
        with self.assertRaisesRegex(AccountingError,'process_changed'):read_health(self.h.data,fixture.I,self.t,process_identity=lambda p:-1)
        with self.assertRaisesRegex(AccountingError,'stale_or_unready'):read_health(self.h.data,fixture.I,self.t+31)
        p=self.h.work/'health.json';b=json.loads(p.read_text());b['body']['at']+=1;p.write_text(json.dumps(b))
        with self.assertRaisesRegex(AccountingError,'authentication_failed'):read_health(self.h.data,fixture.I,self.t)
    def test_public_order_book_rejects_stale_crossed_or_nonpositive_quotes(self):
        b={'bids':[['100','1',self.t]],'asks':[['101','2',self.t]]}
        self.assertEqual(validate_book('BTC/USD',b,self.t,sha(b))['bid'],'100')
        for bad in [b|{'bids':[['100','1',self.t-121]]},b|{'asks':[['99','1',self.t]]},b|{'asks':[['101','0',self.t]]}]:
            with self.assertRaises(AccountingError):validate_book('BTC/USD',bad,self.t,sha(bad))
    def test_sustained_idle_health_is_not_trading_acceptance(self):
        origin=self.t;path=self.h.data/'audit.jsonl';self.stamp()
        def advance(n):self.t+=n;self.stamp()
        def book(pair,clock):return validate_book(pair,{'bids':[['100','1',self.t]],'asks':[['101','2',self.t]]},self.t,'a'*64)
        r=audit(self.h.data,fixture.I,self.h.tx,path,seconds=3600,interval=60,book=book,clock=self.clock,monotonic=lambda:self.t-origin,sleep=advance)
        self.assertEqual(r['samples'],61);self.assertTrue(r['public_observation_passed']);self.assertFalse(r['paper_trading_qualified'])
        self.assertEqual(json.loads(path.read_text().splitlines()[-1]),r)
    def test_public_failure_cannot_emit_pass(self):
        path=self.h.data/'audit.jsonl';self.stamp()
        def fail(*a,**kw):raise OSError('no network')
        with self.assertRaises(OSError):audit(self.h.data,fixture.I,self.h.tx,path,book=fail,clock=self.clock)
        self.assertFalse(json.loads(path.read_text().splitlines()[-1])['paper_trading_qualified'])


if __name__=='__main__':unittest.main()
