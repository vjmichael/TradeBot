"""Pinned native method-level rehearsal; synthetic book, no venue or worker activation.
Run only inside the selected Freqtrade image with network disabled. The actual
FreqtradeBot.execute_entry, Exchange.create_order/dry-run engine, SQLAlchemy
Trade/Order and Wallets are exercised. Public book/fee and bot ancillary
callbacks are explicitly fixtures; not a real market or installed worker test.
"""
from dataclasses import replace
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone

from ccxt import TICK_SIZE
from freqtrade.enums import State, TradingMode, RunMode
from freqtrade.exchange import Exchange
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade, Order, init_db
from freqtrade.wallets import Wallets

from GatePaper import GatePaper
from financial_ledger import FinancialLedger, SCALE, initialize, gate_projection
from paper_execution import NativeDriver, PaperBridge
from risk_gate import Gate
from test_risk_gate import GateTests


class BookFixture:
    """Real native simulation methods, deterministic public-market dependencies."""
    create_order=Exchange.create_order
    create_dry_run_order=Exchange.create_dry_run_order
    check_dry_limit_order_filled=Exchange.check_dry_limit_order_filled
    add_dry_order_fee=Exchange.add_dry_order_fee
    _dry_is_price_crossed=Exchange._dry_is_price_crossed
    id='kraken'; name='Kraken'; precisionMode=TICK_SIZE; precision_mode_price=TICK_SIZE
    def __init__(self,config):
        self._config=config; self._dry_run_open_orders={}; self.mid=100.0
    def _amount_to_contracts(self,pair,amount):return amount
    def _contracts_to_amount(self,pair,amount):return amount
    def amount_to_precision(self,pair,amount):return float(Decimal(str(amount)).quantize(Decimal('.001'),rounding=ROUND_DOWN))
    def price_to_precision(self,pair,price):return float(Decimal(str(price)).quantize(Decimal('.01'),rounding=ROUND_DOWN))
    def exchange_has(self,name):return name=='fetchL2OrderBook'
    def fetch_l2_order_book(self,pair,limit):return {'asks':[[self.mid,100]],'bids':[[self.mid,100]]}
    def get_fee(self,symbol,taker_or_maker='maker'):return .005
    def get_pair_base_currency(self,pair):return pair.split('/')[0]
    def get_pair_quote_currency(self,pair):return 'USD'
    def get_proxy_coin(self):return 'USD'
    def get_precision_amount(self,pair):return .001
    def get_precision_price(self,pair):return .01
    def get_contract_size(self,pair):return 1


def qualify():
    base=GateTests('test_size_and_exact_idempotency');base.setUp()
    try:
        h=base.h
        with h.tx() as db:initialize(db)
        f=FinancialLedger(h.tx,clock=lambda:h.now)
        f.fund('native-fund-a','a');f.fund('native-fund-b','b')
        gate=Gate(h.tx,clock=lambda:h.now,account_projection=gate_projection)
        market=replace(base.m,receipt='native-market',entry=100*SCALE,stop=95*SCALE,target=110*SCALE)
        gate.trusted_market(market)
        accounts={k:dict(active=60*SCALE,protected=0,pending=0,qty=0,covered_qty=0) for k in ('a','b')}
        f.reconcile_desk('native-initial',observed_at=h.now,evidence_digest='a'*64,accounts=accounts,open_orders=[])
        with TemporaryDirectory(dir='/work') as tmp:
            path=Path(tmp)/'native.sqlite'
            config=dict(dry_run=True,trading_mode=TradingMode.SPOT,runmode=RunMode.DRY_RUN,stake_currency='USD',
                dry_run_wallet=60.,initial_state='stopped',strategy='GatePaper',force_entry_enable=False,
                max_open_trades=1,timeframe='1h',db_url='sqlite:///'+str(path),
                exchange=dict(name='kraken',key='',secret='',pair_whitelist=['BTC/USD']),
                telegram={'enabled':False},api_server={'enabled':False},freqai={'enabled':False})
            init_db(config['db_url'])
            bot=object.__new__(FreqtradeBot);bot.config=config;bot.state=State.STOPPED
            bot.trading_mode=TradingMode.SPOT;bot.exchange=BookFixture(config);bot.strategy=GatePaper(config)
            bot.strategy.order_time_in_force={'entry':'GTC','exit':'GTC'}
            bot.wallets=Wallets(config,bot.exchange)
            # Fixture surrounding price/size preparation and notifications only.
            # Upstream method still calls the real strategy, exchange and persistence.
            bot.get_valid_enter_price_and_stake=lambda pair,price,stake,*a:(price,stake,1.)
            bot._notify_enter=lambda *args,**kwargs:None
            def fee_update(trade,order_id,order):
                trade.update_fee(order['fee']['cost'],order['fee']['currency'],order['fee']['rate'],'buy')
                trade.update_trade(trade.select_order_by_order_id(order_id))
                return False
            bot.update_trade_state=fee_update
            driver=NativeDriver(bot,path,'a');bridge=PaperBridge(h.tx,gate,clock=lambda:h.now)
            assert not bot.strategy.confirm_trade_entry('BTC/USD','limit',.1,100.,'GTC',datetime.now(timezone.utc),'unapproved','long')
            intent=gate.admit(replace(base.req,proposal_id='native-proposal',evidence_id=market.receipt))
            assert intent['state']=='AUTHORIZED',intent
            result=bridge.submit(intent['intent_id'],'paper-worker',1,driver)
            assert result['submitted'],result
            assert not bridge.submit(intent['intent_id'],'paper-worker',1,driver)['submitted']
            trade=Trade.get_trades_proxy(is_open=True)[0]
            assert trade.enter_tag=='mv:'+intent['intent_id'] and 0<trade.stake_amount<=intent['notional_cents']/100
            assert trade.stop_loss>=95 and trade.stop_loss<100
            # Controlled terminal sell uses real native simulator and ORM. This
            # tests reconciliation, not autonomous protective-exit scheduling.
            bot.exchange.mid=120.
            sold=bot.exchange.create_order(pair='BTC/USD',ordertype='limit',side='sell',amount=trade.amount,rate=120.,leverage=1.)
            assert sold['status']=='closed'
            order=Order.parse_from_ccxt_object(sold,'BTC/USD','sell');trade.orders.append(order)
            trade.update_fee(sold['fee']['cost'],'USD',sold['fee']['rate'],'sell')
            trade.update_trade(order)
            Trade.commit()
            assert not trade.is_open
            receipt=bridge.import_native(intent['intent_id'],driver)
            assert receipt['held'] and receipt['realized_pl']>0,receipt
            state=f.snapshot('a')
            assert state['active']==60*SCALE and state['protected']==receipt['realized_pl']
            assert bridge.import_native(intent['intent_id'],driver)['duplicate']
            with h.tx() as db:assert db.execute('SELECT state FROM gate_intents WHERE id=?',(intent['intent_id'],)).fetchone()[0]=='FILLED_HELD'
            print('RECEIPT: native Freqtrade method-level paper round trip, actual native ORM and wallet, synthetic book, held after exact ledger import',flush=True)
    finally:base.doCleanups()


if __name__=='__main__':qualify()
