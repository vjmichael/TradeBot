"""Real SQLite boundary/accounting tests; driver and native DB rows are fixtures."""
from contextlib import contextmanager, closing
from pathlib import Path
import sqlite3
import unittest
import test_financial_ledger as fixture
from financial_ledger import AccountingError, SCALE, sha
from paper_execution import PaperBridge, read_closed, units, validate_config


class Driver:
    binding='b'*64
    def __init__(self,host):self.host=host; self.calls=0
    def preflight(self,instance,pair):return self.binding
    def entry(self,row,guard):
        def operation():self.calls+=1; return {'observed':True}
        return guard(operation)
    def closed_receipt(self,intent,pair):return read_closed(self.host.native,intent,pair)


class GateFixture:
    """Only fixture claim transition; actual Gate tested separately in repository."""
    def __init__(self,tx):self.tx=tx
    def claim(self,intent,owner,epoch):
        with self.tx() as db:
            cur=db.execute("UPDATE gate_intents SET state='CLAIMED',claim_token='token' WHERE id=? AND state='AUTHORIZED'",(intent,))
            return {'claimed':bool(cur.rowcount),'claim_token':'token'}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.LedgerTests('test_profit_only_on_settlement_and_fee_basis');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.native=self.h.path.parent/'native.sqlite';self.now=self.h.now
        with self.tx() as db:
            for name,value in [('entry',100*SCALE),('stop',95*SCALE),('target',110*SCALE),('expires',self.now+60)]:
                db.execute(f'ALTER TABLE gate_intents ADD COLUMN {name} INTEGER DEFAULT {value}')
            db.execute('ALTER TABLE gate_intents ADD COLUMN claim_token TEXT');db.execute("UPDATE gate_intents SET state='AUTHORIZED'")
            db.execute('ALTER TABLE desk ADD COLUMN paused INTEGER DEFAULT 0');db.execute('ALTER TABLE desk ADD COLUMN human_pause INTEGER DEFAULT 0')
            db.execute('CREATE TABLE gate_clock(id INTEGER PRIMARY KEY,high_water INTEGER)');db.execute('INSERT INTO gate_clock VALUES(1,?)',(self.now,))
        self.bridge=PaperBridge(self.tx,GateFixture(self.tx),clock=lambda:self.now);self.d=Driver(self)

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.h.path,isolation_level=None)) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            try:yield db;db.commit()
            except BaseException:db.rollback();raise

    def populate(self):
        with closing(sqlite3.connect(self.native)) as db:
            db.executescript("""CREATE TABLE trades(id INTEGER PRIMARY KEY,pair TEXT,strategy TEXT,exchange TEXT,stake_currency TEXT,trading_mode TEXT,leverage REAL,is_short INTEGER,is_open INTEGER,close_date TEXT,enter_tag TEXT,fee_open_currency TEXT,fee_close_currency TEXT,fee_open_cost REAL,fee_close_cost REAL,close_profit_abs REAL);
            INSERT INTO trades VALUES(1,'BTC/USD','GatePaper','kraken','USD','SPOT',1,0,0,'date','mv:i','USD','USD',1,1,8);
            CREATE TABLE orders(id INTEGER PRIMARY KEY,ft_trade_id INTEGER,ft_pair TEXT,ft_order_side TEXT,side TEXT,ft_is_open INTEGER,status TEXT,order_filled_date TEXT,order_id TEXT,ft_fee_base REAL,filled REAL,average REAL,cost REAL,amount REAL,ft_order_tag TEXT,order_type TEXT);
            INSERT INTO orders VALUES(1,1,'BTC/USD','buy','buy',0,'closed','date','dry_run_buy_fixture',0,0.5,100,50,0.5,'mv:i','limit');
            INSERT INTO orders VALUES(2,1,'BTC/USD','sell','sell',0,'closed','date','dry_run_sell_fixture',0,0.5,120,60,0.5,'exit','market');""");db.commit()

    def submit(self):return self.bridge.submit('i','worker',1,self.d)
    def import_trade(self,wallet=108*SCALE):return self.bridge.import_closed('i',self.d,wallet_quote=wallet,wallet_quantity=0,evidence_digest='a'*64)
    def state(self):
        with self.tx() as db:return db.execute('SELECT state FROM gate_intents').fetchone()[0]

    def test_claim_then_submit_once_and_import_atomic(self):
        self.assertTrue(self.submit()['submitted']);self.assertFalse(self.submit()['submitted']);self.assertEqual(self.d.calls,1)
        self.populate()
        with self.assertRaisesRegex(AccountingError,'wallet_mismatch'):self.import_trade(109*SCALE)
        self.assertEqual(self.h.snap()['active'],100*SCALE);self.assertEqual(self.state(),'CLAIMED')
        self.assertEqual(self.import_trade()['realized_pl'],8*SCALE)
        self.assertEqual(self.state(),'FILLED_HELD');self.assertEqual(self.h.snap()['protected'],8*SCALE)
        self.assertTrue(self.import_trade()['duplicate']);self.assertEqual(self.h.snap()['protected'],8*SCALE)

    def test_pause_before_order_and_unknown_never_retries(self):
        def interrupted(row,guard):
            with self.tx() as db:db.execute('UPDATE desk SET human_pause=1')
            return guard(lambda:self.fail('order must not execute'))
        self.d.entry=interrupted
        self.assertEqual(self.submit()['reason'],'native_outcome_unknown');self.assertEqual(self.state(),'UNKNOWN');self.assertFalse(self.submit()['submitted'])

    def test_crash_after_submission_never_retries(self):
        def interrupted(row,guard):
            guard(lambda:None);raise RuntimeError('native crash')
        self.d.entry=interrupted
        self.assertEqual(self.submit()['reason'],'native_outcome_unknown');self.assertEqual(self.state(),'UNKNOWN');self.assertFalse(self.submit()['submitted'])
        self.populate();self.assertFalse(self.import_trade()['duplicate']);self.assertEqual(self.state(),'FILLED_HELD')

    def test_native_receipt_scope_and_unknown_fee(self):
        self.submit();self.populate()
        with closing(sqlite3.connect(self.native)) as db:db.execute("UPDATE trades SET fee_close_currency=NULL");db.commit()
        with self.assertRaisesRegex(AccountingError,'fee_unverified'):self.import_trade()
        self.assertEqual(self.h.snap()['protected'],0)
        with closing(sqlite3.connect(self.native)) as db:db.execute("UPDATE trades SET fee_close_currency='USD',is_open=1");db.commit()
        with self.assertRaisesRegex(AccountingError,'trade_scope'):self.import_trade()

    def test_wrong_native_database_cannot_import(self):
        self.submit();self.populate();self.d.binding='c'*64
        with self.assertRaisesRegex(AccountingError,'binding_mismatch'):self.import_trade()

    def test_precision_and_live_config_denial(self):
        self.assertEqual(units(0.30000000000000004),30_000_000)
        for value in (None,float('nan'),-1,0.000000001):
            with self.assertRaises((AccountingError,ValueError)):units(value)
        with self.assertRaisesRegex(AccountingError,'unsafe_native_config'):validate_config({'dry_run':False},'BTC/USD')


if __name__=='__main__':unittest.main()
