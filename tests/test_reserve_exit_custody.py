"""Focused authorization/attempt tests; genuine process death is tested separately."""
import copy
import tempfile
import unittest
from pathlib import Path
from financial_ledger import SCALE
from reserve_exit_custody import ExitJournal, check_requests, protect

ROW=dict(id='i',entry=100*SCALE,stop=90*SCALE,target=120*SCALE)


def view(qty=SCALE,price=100*SCALE,orders=None):
    buy=dict(id='buy',tag='mv:i',side='buy',terminal=True,qty=SCALE,gross=100*SCALE,fee=0,price=100*SCALE)
    return dict(orders=[buy]+(orders or []),position=qty,closed=qty==0,quote=200*SCALE)


def request(qty=SCALE,n=1,price=120*SCALE,reason='target'):
    return dict(tag='mv-exit:i:'+str(n),reason=reason,qty=qty,price=price,at=100)


def sell(qty=SCALE,n=1,terminal=True):
    return dict(id='sell'+str(n),tag='mv-exit:i:'+str(n),side='sell',terminal=terminal,qty=qty,gross=120*qty,fee=0,price=120*SCALE)


class Driver:
    def __init__(self,v,price=120*SCALE):self.v=v;self.price=price;self.calls=[]
    def view(self,row):return copy.deepcopy(self.v)
    def bid(self,row):return self.price,100
    def exit(self,row,r):self.calls.append(r)
    def poll(self,row):raise AssertionError('must not poll a potentially risk-increasing buy')


class ExitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.work=Path(self.tmp.name);self.j=ExitJournal(self.work,b'synthetic-32-byte-local-test-key!'[:32],'a'*64,'i')
    def test_ambiguous_cut_retains_attempt_and_never_retries(self):
        d=Driver(view());self.assertEqual(protect(d,ROW,self.j,clock=lambda:100),'exit_requested')
        self.assertEqual(d.calls,[request()]);self.assertEqual(self.j.read(),[request()])
        d2=Driver(view())
        with self.assertRaisesRegex(ValueError,'ambiguous_exit_attempt'):protect(d2,ROW,self.j,clock=lambda:100)
        self.assertFalse(d2.calls)
    def test_tagged_terminal_reopen_does_not_resubmit(self):
        self.j.append([],request());d=Driver(view(0,orders=[sell()]))
        self.assertEqual(protect(d,ROW,self.j,clock=lambda:100),'closed');self.assertFalse(d.calls)
    def test_open_buy_held_without_poll(self):
        v=view();v['orders'][0]['terminal']=False
        d=Driver(v);self.assertEqual(protect(d,ROW,self.j,clock=lambda:100),'open_order_held');self.assertFalse(d.calls);self.assertEqual(self.j.read(),[])
    def test_stop_trigger_and_no_trigger(self):
        d=Driver(view(),100*SCALE);self.assertEqual(protect(d,ROW,self.j,clock=lambda:100),'monitoring')
        d.price=89*SCALE;self.assertEqual(protect(d,ROW,self.j,clock=lambda:100),'exit_requested');self.assertEqual(d.calls,[request(price=89*SCALE,reason='stop')])
    def test_partial_terminal_exit_only_remainder(self):
        self.j.append([],request());d=Driver(view(SCALE//2,orders=[sell(SCALE//2)]),100*SCALE)
        self.assertEqual(protect(d,ROW,self.j,clock=lambda:100),'exit_requested');self.assertEqual(d.calls,[request(SCALE//2,2,100*SCALE)])
    def test_journal_gap_tamper_and_wrong_key_refused(self):
        self.j.append([],request())
        with self.assertRaises(ValueError):ExitJournal(self.work,b'x'*32,'a'*64,'i').read()
        f=self.work/(self.j.name+'.001');b=f.read_bytes();f.write_bytes(b.replace(b'target',b'stopxx'))
        with self.assertRaises(ValueError):self.j.read()
        f.rename(self.work/(self.j.name+'.002'))
        with self.assertRaisesRegex(ValueError,'exit_journal_gap'):self.j.read()
    def test_no_unattributed_or_untriggered_sell(self):
        v=view(0,orders=[sell()])
        for r in ([],[request(price=119*SCALE)],[request(qty=SCALE-1)],[request(n=2)]):
            with self.assertRaises(ValueError):check_requests(ROW,r,v,complete=True)
    def test_stale_quote_and_future_attempt_refused(self):
        d=Driver(view())
        with self.assertRaisesRegex(ValueError,'stale_protection_quote'):protect(d,ROW,self.j,clock=lambda:131)
        self.assertEqual(self.j.read(),[])
        with self.assertRaises(ValueError):check_requests(ROW,[request()],view(0,orders=[sell()]),now=99,complete=True)
