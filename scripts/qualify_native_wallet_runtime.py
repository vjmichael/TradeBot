"""Actual pinned Freqtrade wallet and SQLite regression; offline synthetic book.

No venue credentials, public market session, installed worker or reserve authority.
The exchange price/fee inputs are fixtures, not engine or wallet replacements.
"""
import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from unittest.mock import patch
from ccxt import TICK_SIZE
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from freqtrade.enums import TradingMode, RunMode
from freqtrade.exchange import Exchange
from freqtrade.persistence import Trade, Order, init_db
from freqtrade.wallets import Wallets
from reserve_native_wallet import _sign_observed, verify_observation
from reserve_native_migration import pristine_native

INSTANCE='a'*32
WORKSPACE='native-wallet-qualification'
SCALE=100_000_000

class Book:
    # Genuine Freqtrade exchange simulation methods, no venue I/O.
    create_order=Exchange.create_order
    create_dry_run_order=Exchange.create_dry_run_order
    check_dry_limit_order_filled=Exchange.check_dry_limit_order_filled
    add_dry_order_fee=Exchange.add_dry_order_fee
    _dry_is_price_crossed=Exchange._dry_is_price_crossed
    get_dry_market_fill_price=Exchange.get_dry_market_fill_price
    id='kraken';name='Kraken';precisionMode=TICK_SIZE;precision_mode_price=TICK_SIZE
    def __init__(self,c):self._config=c;self._dry_run_open_orders={};self.mid=100.
    def _amount_to_contracts(self,pair,amount):return amount
    def _contracts_to_amount(self,pair,amount):return amount
    def amount_to_precision(self,pair,amount):return float(Decimal(str(amount)).quantize(Decimal('.001'),rounding=ROUND_DOWN))
    def price_to_precision(self,pair,price):return float(Decimal(str(price)).quantize(Decimal('.01'),rounding=ROUND_DOWN))
    def exchange_has(self,name):return name=='fetchL2OrderBook'
    def fetch_l2_order_book(self,pair,limit):return {'asks':[[self.mid,100]],'bids':[[self.mid,100]]}
    def get_fee(self,symbol,taker_or_maker='maker'):return .005
    def get_proxy_coin(self):return 'USD'
    def get_pair_base_currency(self,pair):return pair.split('/')[0]
    def get_pair_quote_currency(self,pair):return 'USD'

class NativeWalletRuntime(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.path=Path(tmp.name)/'paper.sqlite'
        self.config=dict(dry_run=True,dry_run_wallet=100.,trading_mode=TradingMode.SPOT,runmode=RunMode.DRY_RUN)
        self.network=patch('socket.socket.connect',side_effect=AssertionError('network forbidden'));self.network.start();self.addCleanup(self.network.stop)
        self.reopen();self.addCleanup(self.close_db)
    def close_db(self):
        engine=Trade.session.get_bind();Trade.session.remove();engine.dispose()
    def reopen(self):
        init_db('sqlite:///'+str(self.path));self.exchange=Book(self.config);self.wallet=Wallets(self.config,self.exchange)
    def order(self,side,rate,kind):
        self.exchange.mid=rate
        result=self.exchange.create_order(pair='BTC/USD',ordertype=kind,side=side,amount=.5,rate=rate,leverage=1.)
        self.assertEqual(result['status'],'closed');self.assertEqual(result['filled'],.5)
        return result
    def entry(self):
        raw=self.order('buy',100.,'limit')
        self.assertEqual(raw['type'],'limit');self.assertEqual(raw['fee']['cost'],.25)
        t=Trade(exchange='kraken',pair='BTC/USD',base_currency='BTC',stake_currency='USD',open_rate=100.,stake_amount=50.,amount=.5,leverage=1.,is_open=True,trading_mode=TradingMode.SPOT,fee_open=.005,fee_close=.005,amount_precision=.001,price_precision=.01,precision_mode=TICK_SIZE,precision_mode_price=TICK_SIZE,contract_size=1.,open_date=datetime.now(timezone.utc))
        t.orders.append(Order.parse_from_ccxt_object(raw,'BTC/USD','buy'))
        Trade.session.add(t);Trade.commit();t.update_trade(t.orders[0]);Trade.commit();self.wallet.update()
        self.assertEqual(self.wallet.get_total('BTC'),.5);self.assertTrue(Trade.get_trades_proxy(is_open=True));return t
    def settle(self,rate=120.):
        t=self.entry();raw=self.order('sell',rate,'market');t.orders.append(Order.parse_from_ccxt_object(raw,'BTC/USD','sell'));Trade.commit();t.update_trade(t.orders[-1]);Trade.commit();self.wallet.update()
        self.assertFalse(t.is_open);self.assertEqual(self.wallet.get_total('BTC'),0)
        expected=100+.5*(rate-100)-.25-raw['fee']['cost'];self.assertAlmostEqual(self.wallet.get_total('USD'),expected,places=7)
        self.assertAlmostEqual(t.close_profit_abs,expected-100,places=7);return expected
    def observation(self):
        # Isolated test composition of the actual Wallets/ORM with the candidate
        # public signature primitive, not NativeProducer.observe_from_handoff.
        if Trade.get_trades_proxy(is_open=True):raise ValueError('open trade')
        with closing(sqlite3.connect(self.path)) as db:
            if db.execute('SELECT 1 FROM orders WHERE ft_is_open=1 LIMIT 1').fetchone():raise ValueError('open order')
        self.wallet.update(require_update=True)
        return {'instance':INSTANCE,'pair':'BTC/USD','binding':'d'*64,'head':'b'*64,'anchor':'c'*64,'boot':1,'at':100,'quote':int((Decimal(str(self.wallet.get_total('USD')))*SCALE).quantize(Decimal(1))),'qty':int((Decimal(str(self.wallet.get_total('BTC')))*SCALE).quantize(Decimal(1)))}
    def test_pristine_native_schema_and_balance(self):
        self.assertEqual(self.observation()['quote'],100*SCALE);self.assertEqual(self.observation()['qty'],0)
        self.close_db()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.assertEqual(db.execute('PRAGMA journal_mode=DELETE').fetchone(),('delete',))
        pristine_native(self.path.read_bytes());self.reopen()
    def test_open_position_refused(self):
        self.entry()
        with self.assertRaisesRegex(ValueError,'open trade'):self.observation()
    def test_profit_native_settlement_and_durable_reopen(self):
        expected=self.settle();self.assertAlmostEqual(expected,109.45,places=7)
        self.close_db();self.reopen();self.assertEqual(self.observation()['quote'],10_945_000_000)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM trades WHERE is_open=0').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM orders WHERE ft_is_open=0').fetchone()[0],2)
    def test_loss_native_settlement_and_durable_reopen(self):
        expected=self.settle(80.);self.assertAlmostEqual(expected,89.55,places=7)
        self.close_db();self.reopen();self.assertEqual(self.observation()['quote'],8_955_000_000)
    def test_public_signature_of_genuine_wallet_and_tamper_refusal(self):
        self.settle();self.close_db();self.reopen();body=self.observation()
        seed=os.urandom(32);public=Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        packet=_sign_observed(seed,WORKSPACE,body);self.assertEqual(verify_observation(public,WORKSPACE,packet)['quote'],10_945_000_000)
        packet['body']['wallet']['quote']+=1
        with self.assertRaises(ValueError):verify_observation(public,WORKSPACE,packet)

if __name__=='__main__':unittest.main()
