"""Cancellation invariants: native snapshots are explicit fixtures; desk is SQLite."""
from copy import deepcopy
import unittest
from financial_ledger import AccountingError, SCALE, start, projection_values
from paper_custody import packet
from paper_execution import append_event, event_detail
from paper_recovery import cancellation, finish_zero, RecoveryWorker, RecoveryDriver
import test_paper_execution as fixture

K=b'z'*32
OID='dry_run_buy_fixture'


def raw(q=0):
    return dict(id=OID,symbol='BTC/USD',type='limit',side='buy',status='open',price=100,average=100,amount=.5,filled=q,remaining=.5-q,cost=q*100 if q else 50,fee={'cost':.1,'currency':'USD','rate':.005} if q else None,timestamp=1000,datetime='1970-01-01T00:00:01Z')


def persisted(q=0):return dict(id=OID,tag='mv:i',amount=SCALE//2,filled=int(q*SCALE),price=100*SCALE)


class CancelTests(unittest.TestCase):
    def test_zero_open_notional_is_not_filled_cost(self):
        b=cancellation(raw(),persisted(),'BTC/USD','i','b'*64)
        self.assertEqual((b['order']['status'],b['order']['filled'],b['order']['cost']),('canceled',0,0))
        self.assertEqual(raw()['status'],'open')

    def test_partial_preserves_cumulative_and_fee(self):
        b=cancellation(raw(.25),persisted(.1),'BTC/USD','i','b'*64)
        self.assertEqual((b['order']['filled'],b['order']['remaining'],b['order']['cost']),(.25,.25,25))
        self.assertEqual(b['order']['fee']['cost'],.1)

    def test_missing_native_open_cache_refuses_fill_reconstruction(self):
        from types import SimpleNamespace
        d=object.__new__(RecoveryDriver);d.cancel_cache={}
        d.bot=SimpleNamespace(exchange=SimpleNamespace(_dry_run_open_orders={}))
        d.trade=lambda row:SimpleNamespace(open_orders=[SimpleNamespace(order_id=OID)])
        with self.assertRaisesRegex(AccountingError,'native_open_state_missing'):
            d.poll({'id':'i'})

    def test_regression_and_fabricated_cancel_rejected(self):
        for r,p in [(raw(),persisted(.1)),(raw()|{'status':'canceled'},persisted()),(raw()|{'id':'wrong'},persisted()),(raw(.25)|{'fee':None},persisted()),(raw(.25)|{'cost':50},persisted())]:
            with self.assertRaises(AccountingError):cancellation(r,p,'BTC/USD','i','b'*64)


class ZeroTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.BridgeTests('test_claim_then_submit_once_and_import_atomic');self.h.setUp();self.addCleanup(self.h.doCleanups);self.h.submit()
        with self.h.tx() as db:db.execute('ALTER TABLE gate_intents ADD COLUMN resolution_receipt TEXT');self.row=dict(db.execute('SELECT * FROM gate_intents').fetchone())
        h=self.h
        class Driver:
            key=K;binding='b'*64
            def __init__(self):self.quote=100*SCALE;self.flat=True;self.called=[]
            def frozen(self,row):
                with h.tx() as db:p=event_detail(db,'i','native_cancel_frozen')
                return p['body']['order'] if p else None
            def observation(self,row):
                if not self.flat:raise AccountingError('native_open_trade')
                return {'quote':self.quote,'qty':0}
            def recover_cancel(self,row):return self.frozen(row)
            def cancel_before_poll(self,row):self.called.append('cancel');return True
            def poll(self,row):raise AssertionError('unexpected fill-generating poll')
        self.d=Driver()

    def freeze(self,q=0):
        body=cancellation(raw(q),persisted(),'BTC/USD','i','b'*64)
        with self.h.tx() as db:append_event(db,'i','native_cancel_frozen',packet(K,'frozen-cancel',body),self.h.now)

    def finish(self):return finish_zero(self.h.tx,self.d,self.row,clock=lambda:self.h.now)

    def test_absence_is_not_proof_and_partial_not_zero(self):
        self.assertIsNone(self.finish());self.freeze(.25);self.assertIsNone(self.finish());self.assertEqual(self.h.state(),'CLAIMED')

    def test_verified_zero_after_restart_no_cash_mutation(self):
        self.freeze();before=self.h.h.snap()['journal_head']
        with self.h.tx() as db:start(db);db.execute("UPDATE gate_intents SET state='UNKNOWN'")
        self.assertEqual(self.finish()['state'],'NO_FILL');self.assertEqual(self.h.state(),'NO_FILL');s=self.h.h.snap();self.assertEqual(s['gate_reserved'],0);self.assertEqual(s['active'],100*SCALE);self.assertEqual(s['journal_head'],before)
        with self.assertRaisesRegex(AccountingError,'zero_claim_scope'):self.finish()

    def test_open_wallet_and_mismatch_hold(self):
        self.freeze();self.d.flat=False;self.assertIsNone(self.finish());self.d.flat=True;self.d.quote-=1
        with self.assertRaisesRegex(AccountingError,'zero_wallet_mismatch'):self.finish()
        self.assertEqual(self.h.state(),'CLAIMED')

    def test_tampered_cancel_evidence_stays_held(self):
        self.freeze()
        with self.h.tx() as db:db.execute("UPDATE gate_events SET detail=json_set(detail,'$.mac','bad') WHERE action='native_cancel_frozen'")
        with self.assertRaisesRegex(AccountingError,'authentication_failed'):self.finish()
        self.assertEqual(self.h.h.snap()['gate_reserved'],100*SCALE)

    def test_pause_cancels_before_fill_poll(self):
        with self.h.tx() as db:db.execute('UPDATE desk SET human_pause=1')
        worker=RecoveryWorker(self.h.tx,self.d,clock=lambda:self.h.now)
        self.assertEqual(worker.tick('i')['state'],'CANCEL_REQUESTED');self.assertEqual(self.d.called,['cancel'])


if __name__=='__main__':unittest.main()
