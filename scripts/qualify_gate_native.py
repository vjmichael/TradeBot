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
from paper_supervisor import NativeProtectionDriver, ProtectionWorker
from paper_custody import AuthenticatedNativeDriver, observation, publish, authenticated_projection
from paper_provision import provision, installed_workspace, native_check
from paper_recovery import RecoveryDriver, RecoveryWorker
from test_paper_provision import ProvisionTests, I
from risk_gate import Gate
from test_risk_gate import GateTests


class BookFixture:
    """Real native simulation methods, deterministic public-market dependencies."""
    create_order=Exchange.create_order
    create_dry_run_order=Exchange.create_dry_run_order
    check_dry_limit_order_filled=Exchange.check_dry_limit_order_filled
    add_dry_order_fee=Exchange.add_dry_order_fee
    _dry_is_price_crossed=Exchange._dry_is_price_crossed
    get_dry_market_fill_price=Exchange.get_dry_market_fill_price
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
    def get_funding_fees(self,**kwargs):return 0.
    def fetch_order(self,order_id,pair):return self._dry_run_open_orders[order_id]


def qualify(protection=None, authenticated=False, cancel=None):
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
            if cancel:bot.exchange.mid=101.
            # Fixture surrounding price/size preparation and notifications only.
            # Upstream method still calls the real strategy, exchange and persistence.
            bot.get_valid_enter_price_and_stake=lambda pair,price,stake,*a:(price,stake,1.)
            bot._notify_enter=lambda *args,**kwargs:None
            def fee_update(trade,order_id,order,**kwargs):
                trade.update_order(order)
                trade.update_fee(order['fee']['cost'],order['fee']['currency'],order['fee']['rate'],order['side'])
                trade.update_trade(trade.select_order_by_order_id(order_id))
                return False
            bot.update_trade_state=fee_update
            if authenticated:
                key=b'native-qualification-only-key-32'[:32]
                assert len(key)==32
                driver=RecoveryDriver(bot,path,'a',key,h.tx,clock=lambda:h.now) if cancel else AuthenticatedNativeDriver(bot,path,'a',key,clock=lambda:h.now)
                # The other portfolio is an explicit independent flat wallet fixture.
                class Flat:
                    instance='b';binding='b'*64
                    def observation(self,row):return {'quote':60*SCALE,'qty':0}
                peer=Flat();peer.key=key
                key_for=lambda i:(key,driver.binding if i=='a' else peer.binding)
                gate=Gate(h.tx,clock=lambda:h.now,account_projection=authenticated_projection(key_for))
                denied=gate.admit(replace(base.req,proposal_id='unsigned-denied',evidence_id=market.receipt))
                assert denied['reason']=='financial:worker_evidence_required',denied
                publish(h.tx,{i:observation(h.tx,source,clock=lambda:h.now) for i,source in [('a',driver),('b',peer)]},key_for,clock=lambda:h.now)
            else:driver=NativeDriver(bot,path,'a')
            bridge=PaperBridge(h.tx,gate,clock=lambda:h.now)
            assert not bot.strategy.confirm_trade_entry('BTC/USD','limit',.1,100.,'GTC',datetime.now(timezone.utc),'unapproved','long')
            intent=gate.admit(replace(base.req,proposal_id='native-proposal',evidence_id=market.receipt))
            assert intent['state']=='AUTHORIZED',intent
            result=bridge.submit(intent['intent_id'],'paper-worker',1,driver)
            assert result['submitted'],result
            assert not bridge.submit(intent['intent_id'],'paper-worker',1,driver)['submitted']
            trade=Trade.get_trades_proxy(is_open=True)[0]
            assert trade.enter_tag=='mv:'+intent['intent_id'] and 0<trade.stake_amount<=intent['notional_cents']/100
            if cancel:
                assert authenticated and len(trade.open_orders)==1
                order=trade.open_orders[0]
                assert not order.filled and order.ft_order_tag=='mv:'+intent['intent_id']
                oid=order.order_id
                if cancel=='partial':
                    # Pinned dry simulator normally fills all-or-none. Explicitly
                    # inject a known cumulative partial observation for recovery;
                    # subsequent ORM and exit are actual upstream methods.
                    raw=bot.exchange._dry_run_open_orders[oid]
                    raw.update(filled=.2,remaining=raw['amount']-.2,cost=20.,average=100.,
                               fee={'currency':'USD','cost':.10,'rate':.005})
                bot.strategy.order_types={'entry':'limit','exit':'market','stoploss':'market','stoploss_on_exchange':False}
                bot._notify_enter_cancel=lambda *args,**kwargs:None
                bot._notify_exit=lambda *args,**kwargs:None
                bot.get_valid_price=lambda custom,proposed:proposed
                bot.cancel_stoploss_on_exchange=lambda trade,**kwargs:trade
                bot._exit_reason_cache={}
                worker=RecoveryWorker(h.tx,driver,clock=lambda:h.now)
                with h.tx() as db:db.execute('UPDATE desk SET paused=1,human_pause=1')
                # Interrupt immediately after signed cancellation commit but
                # before native ORM mutation; reopen the original native file.
                def interrupted(*args):raise RuntimeError('cancel-cut')
                driver._apply_frozen=interrupted
                try:worker.tick(intent['intent_id'])
                except RuntimeError as exc:assert str(exc)=='cancel-cut'
                else:raise AssertionError('missing simulated process interruption')
                Trade.session.remove()
                init_db(config['db_url'])
                bot.exchange=BookFixture(config);bot.exchange.mid=120.
                bot.wallets=Wallets(config,bot.exchange)
                driver=RecoveryDriver(bot,path,'a',key,h.tx,clock=lambda:h.now)
                worker=RecoveryWorker(h.tx,driver,clock=lambda:h.now)
                recovered=worker.tick(intent['intent_id'])
                if cancel=='zero':
                    assert recovered['state']=='NO_FILL',recovered
                    assert not Trade.get_trades_proxy(is_open=True)
                    state=f.snapshot('a');assert state['active']==60*SCALE and state['protected']==state['gate_reserved']==0,state
                else:
                    assert recovered['state']=='EXIT_REQUESTED' and recovered['reason']=='target',recovered
                    assert worker.tick(intent['intent_id'])['state']=='ACCOUNTED'
                    state=f.snapshot('a');assert state['qty']==state['pending']==state['gate_reserved']==0 and state['protected']>0,state
                print('RECEIPT: R3 native '+cancel+' cancellation recovery after ORM reopen, signed snapshot and verified wallet',flush=True)
                return
            assert trade.stop_loss>=95 and trade.stop_loss<100
            if protection:
                # Exercise upstream execute_trade_exit (native simulator, ORM and wallets).
                # Only ancillary callbacks are fixture implementations.
                bot.strategy.order_types={'entry':'limit','exit':'market','stoploss':'market','stoploss_on_exchange':False}
                bot._notify_exit=lambda *args,**kwargs:None
                bot.get_valid_price=lambda custom,proposed:proposed
                bot.cancel_stoploss_on_exchange=lambda trade,**kwargs:trade
                bot._exit_reason_cache={}
                if authenticated:
                    # Reopen real ORM persistence, discard in-memory dry-run book
                    # orders and construct a fresh authenticated native driver.
                    Trade.session.remove()
                    init_db(config['db_url'])
                    bot.exchange=BookFixture(config)
                    bot.wallets=Wallets(config,bot.exchange)
                    driver=AuthenticatedNativeDriver(bot,path,'a',key,clock=lambda:h.now)
                    worker=ProtectionWorker(h.tx,driver,clock=lambda:h.now)
                    for _ in range(100):
                        result=worker.tick(intent['intent_id'])
                        assert result['state']=='MONITORING' and result['accounting']=='ok',result
                        h.now+=2
                else:
                    worker=ProtectionWorker(h.tx,NativeProtectionDriver(bot,path,'a',clock=lambda:h.now),clock=lambda:h.now)
                with h.tx() as db:db.execute('UPDATE desk SET paused=1,human_pause=1')
                bot.exchange.mid=120. if protection=='target' else 90.
                action=worker.tick(intent['intent_id']);assert action['reason']==protection,action
                closed=worker.tick(intent['intent_id']);assert closed['state']=='ACCOUNTED',closed
                state=f.snapshot('a');assert state['qty']==state['pending']==state['gate_reserved']==0,state
                if protection=='target':assert state['active']==60*SCALE and state['protected']>0,state
                else:assert state['active']<60*SCALE and state['protected']==0,state
                assert worker.tick(intent['intent_id'])['state']=='ACCOUNTED'
                print('RECEIPT: native protected '+protection+' exit while paused, verified terminal wallet and released reservation',flush=True)
                if authenticated:
                    print('RECEIPT: authenticated native entry, reopened ORM and terminal fee evidence, 100 protection observations, verified settlement',flush=True)
                return
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


def qualify_provision():
    h=ProvisionTests('test_atomic_setup_and_duplicate_no_reset');h.setUp()
    try:
        result=provision(h.data,I,h.tx)
        assert result['state']=='provisioned',result
        with h.tx() as db:work=installed_workspace(h.data,I,db)
        native_check(work/'paper.sqlite',empty=True)
        assert provision(h.data,I,h.tx)['state']=='already_provisioned'
        print('RECEIPT: R3 real Freqtrade ORM workspace initialization and idempotent private provisioning',flush=True)
    finally:h.doCleanups()


if __name__=='__main__':
    qualify_provision()
    qualify()
    qualify('stop')
    qualify('target')
    qualify('target',authenticated=True)
    qualify(authenticated=True,cancel='zero')
    qualify(authenticated=True,cancel='partial')
