"""TB-07A2: deliberately bounded native paper-entry and terminal receipt bridge.

No activation, network listener, LIVE configuration or automatic hold release.
The caller must own the native bot and its isolated database exclusively.
"""
from contextlib import closing, nullcontext
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import json
from pathlib import Path
import sqlite3
import time
from threading import Lock

from financial_ledger import (CENT, SCALE, FinancialLedger, observed, replay,
                              require, sha, ident, amount, quote_units)


def units(value):
    """Reject unknown fee/quantity and precision beyond ledger support."""
    require(type(value) in (int, float, str), 'unknown_native_amount')
    try:
        n=Decimal(str(value))*SCALE
        rounded=n.to_integral_value(rounding=ROUND_HALF_EVEN)
        require(n.is_finite() and n>=0 and abs(n-rounded)<=Decimal('0.01'),'unsupported_native_precision')
        return amount(int(rounded))
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('invalid_native_amount') from exc


def append_event(db,intent,action,detail,now):
    db.execute('INSERT INTO gate_events(intent_id,action,at,detail) VALUES(?,?,?,?)',
               (intent,action,now,json.dumps(detail,sort_keys=True,separators=(',',':'))))


def event_detail(db,intent,action):
    rows=db.execute('SELECT detail FROM gate_events WHERE intent_id=? AND action=?',(intent,action)).fetchall()
    require(len(rows)<=1,'duplicate_execution_event')
    return json.loads(rows[0][0]) if rows else None


def validate_config(config,pair):
    require(config.get('dry_run') is True and getattr(config.get('trading_mode'),'value',config.get('trading_mode'))=='spot'
        and config.get('stake_currency')=='USD' and config.get('force_entry_enable') is False
        and config.get('initial_state')=='stopped' and config.get('strategy')=='GatePaper'
        and config.get('max_open_trades')==1
        and getattr(config.get('runmode','dry_run'),'value',config.get('runmode','dry_run'))=='dry_run','unsafe_native_config')
    exchange=config.get('exchange',{})
    require(exchange.get('name')=='kraken' and exchange.get('pair_whitelist')==[pair]
        and not any(exchange.get(k) for k in ('key','secret','password','uid','apiKey','privateKey'))
        and not exchange.get('ccxt_config') and not exchange.get('ccxt_async_config'),'unsafe_exchange_config')
    require(all(config.get(k,{}).get('enabled') is False for k in ('telegram','api_server','freqai')),'unsafe_native_service')


class PaperBridge:
    def __init__(self,transaction,gate,*,clock=time.time):
        self.tx=transaction; self.gate=gate; self.clock=clock; self.lock=Lock()

    def submit(self,intent,owner,epoch,driver):
        """Claim once, persist worker binding, then enter the native driver.

        A driver exception, refusal, or uncertain persistence is UNKNOWN. No
        automatic retry, even when a native database contains no trade.
        """
        ident(intent); ident(owner)
        with self.lock:
            with self.tx() as db:
                row=db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone()
                require(row is not None,'unknown_intent')
                if row['state']!='AUTHORIZED':return {'submitted':False,'reason':'not_authorized'}
                pair=row['pair']; instance=row['instance_id']
            binding=driver.preflight(instance,pair)
            require(type(binding) is str and len(binding)==64,'invalid_native_binding')
            claim=self.gate.claim(intent,owner,epoch)
            if not claim.get('claimed'):return {'submitted':False,'reason':claim.get('reason','claim_refused')}
            token=claim['claim_token']
            try:
                with self.tx() as db:
                    row=dict(db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone())
                    require(row['state']=='CLAIMED' and row['claim_token']==token,'claim_changed')
                    require(event_detail(db,intent,'native_bound') is None,'native_already_bound')
                    append_event(db,intent,'native_bound',{'binding':binding,'instance':instance,'pair':pair},int(self.clock()))
                def guard(order_call):
                    # Final serialization with pause/restart and claim expiry,
                    # including the actual paper exchange call. No network/live order.
                    with self.tx() as db:
                        now=int(self.clock()); row2=db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone()
                        paused,human=db.execute('SELECT paused,human_pause FROM desk WHERE id=1').fetchone()
                        high=db.execute('SELECT high_water FROM gate_clock WHERE id=1').fetchone()[0]
                        require(now>=high,'clock_rollback')
                        db.execute('UPDATE gate_clock SET high_water=? WHERE id=1',(now,))
                        require(not paused and not human and row2['state']=='CLAIMED'
                            and row2['claim_token']==token and now<row2['expires'],'execution_authority_revoked')
                        check=getattr(self.gate,'entry_guard',None)
                        if check is not None:check(db,instance,now,final=True)
                        require(event_detail(db,intent,'native_attempt') is None,'native_attempt_repeated')
                        append_event(db,intent,'native_attempt',{'binding':binding},now)
                        return order_call()
                result=driver.entry(row,guard)
                require(type(result) is dict and result.get('observed') is True,'native_submission_unverified')
                with self.tx() as db:
                    require(event_detail(db,intent,'native_attempt') is not None,'native_attempt_missing')
                    append_event(db,intent,'native_return',{'binding':binding,'result':result},int(self.clock()))
                return {'submitted':True,'intent':intent,'reconciliation_required':True}
            except Exception as exc:
                with self.tx() as db:
                    db.execute("UPDATE gate_intents SET state='UNKNOWN' WHERE id=? AND state='CLAIMED'",(intent,))
                    append_event(db,intent,'native_uncertain',{'reason':type(exc).__name__},int(self.clock()))
                return {'submitted':False,'reason':'native_outcome_unknown','intent':intent}

    def import_native(self,intent,driver):
        """Read the isolated native wallet, never a model-supplied RiskState."""
        with self.tx() as db:
            row=db.execute('SELECT pair FROM gate_intents WHERE id=?',(intent,)).fetchone()
            require(row is not None,'unknown_intent'); pair=row[0]
        observation=driver.wallet_observation(intent,pair)
        return self.import_closed(intent,driver,**observation)

    def import_closed(self,intent,driver,*,wallet_quote, wallet_quantity, evidence_digest):
        """Import a complete single-entry/single-exit native round trip atomically.

        Caller supplies an independently observed whole-instance paper wallet
        (not synthesized from the ledger). Native readback comes from a separate
        read-only SQLite connection. Does NOT clear FILLED_HELD or activate trading.
        """
        ident(intent); amount(wallet_quote); amount(wallet_quantity)
        require(type(evidence_digest) is str and len(evidence_digest)==64 and all(c in '0123456789abcdef' for c in evidence_digest),'invalid_wallet_evidence')
        with self.lock,self.tx() as db:
            row=db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone()
            require(row is not None and row['state'] in ('CLAIMED','UNKNOWN','FILLED_HELD'),'unresolved_claim_required')
            binding=event_detail(db,intent,'native_bound')
            require(binding and binding['binding']==driver.binding and event_detail(db,intent,'native_attempt'),'native_binding_mismatch')
            receipt=driver.closed_receipt(intent,row['pair'])
            receipt_hash=sha([receipt,wallet_quote,wallet_quantity,evidence_digest])
            prior=event_detail(db,intent,'native_accounted')
            if prior:
                require(prior['hash']==receipt_hash,'native_receipt_changed')
                return {'duplicate':True,'held':True}
            buy,sell=receipt['buy'],receipt['sell']; instance=row['instance_id']
            require(buy['qty']==sell['qty'] and wallet_quantity==0,'unclosed_native_position')
            require(buy['gross']<=row['notional']*CENT and buy['gross']+buy['fee']<=row['reserved']*CENT,'native_fill_over_budget')
            require(buy['price']<=row['entry'],'native_entry_price_exceeded')
            states,_,_,_=replay(db); s=states[instance]
            require(not s['lots'] and not s['sales'] and not s['correction_locked'],'unresolved_prior_inventory')
            ledger=FinancialLedger(lambda:nullcontext(db),clock=self.clock)
            b='native:'+sha([binding['binding'],buy['id']]); e='native:'+sha([binding['binding'],sell['id']])
            require(not db.execute('SELECT 1 FROM fin_events WHERE id IN (?,?)',(b,e)).fetchone(),'native_order_receipt_reused')
            ledger.buy(b,instance,intent,buy['qty'],buy['gross'],buy['fee'])
            ledger.sell(e,instance,sell['id'],sell['qty'],sell['gross'],sell['fee'])
            ledger.settle('settle:'+sha([binding['binding'],sell['id']]),instance,e,sell['gross']-sell['fee'])
            states,_,_,_=replay(db); after=observed(states[instance])
            require(after['active']+after['protected']==wallet_quote and after['qty']==wallet_quantity and after['pending']==0,'independent_wallet_mismatch')
            db.execute("UPDATE gate_intents SET state='FILLED_HELD' WHERE id=?",(intent,))
            append_event(db,intent,'native_accounted',{'hash':receipt_hash,'native':receipt,'wallet_digest':evidence_digest},int(self.clock()))
            return {'duplicate':False,'held':True,'realized_pl':receipt['net_pl']}


def read_closed(path,intent,pair):
    """Pinned 2026.10 order/trade schema subset. Refuse ambiguous partials.

    Supported: exactly one terminal buy and sell, including a single canceled
    partial buy subsequently fully closed; USD fee evidence only.
    """
    path=Path(path); require(path.is_file() and not path.is_symlink(),'missing_native_db')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5,isolation_level=None)) as db:
        db.row_factory=sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        require(db.execute('PRAGMA quick_check').fetchone()[0]=='ok','native_db_integrity')
        rows=db.execute('SELECT * FROM trades WHERE enter_tag=?',('mv:'+intent,)).fetchall()
        require(len(rows)==1,'ambiguous_native_trade'); t=dict(rows[0])
        require(t['pair']==pair and t['strategy']=='GatePaper' and t['exchange']=='kraken'
            and t['stake_currency']=='USD' and t['trading_mode'] in ('SPOT','spot')
            and t['leverage']==1 and not t['is_short'] and not t['is_open'] and t['close_date'],'native_trade_scope')
        require(not db.execute('SELECT 1 FROM trades WHERE is_open=1 LIMIT 1').fetchone(),'native_open_trade')
        require(not db.execute('SELECT 1 FROM orders WHERE ft_is_open=1 LIMIT 1').fetchone(),'native_open_order')
        orders=db.execute('SELECT * FROM orders WHERE ft_trade_id=? ORDER BY id',(t['id'],)).fetchall()
        require(len(orders)==2,'native_multiple_or_missing_orders')
        out={}
        for o,side,k in zip(orders,('buy','sell'),('open','close')):
            require(o['ft_pair']==pair and o['ft_order_side']==side and o['side']==side
                and not o['ft_is_open'] and o['status'] in ('closed','canceled','expired')
                and o['order_filled_date'] and str(o['order_id']).startswith('dry_run_'+side+'_'),'native_order_scope')
            if side=='buy':require(o['order_type']=='limit','native_entry_not_limit')
            require(o['ft_fee_base'] in (None,0) and t['fee_'+k+'_currency']=='USD','native_fee_unverified')
            q=units(o['filled']); price=units(o['average']); gross=quote_units(o['cost']); fee=quote_units(t['fee_'+k+'_cost'])
            require(q>0 and price>0 and gross>0 and q<=units(o['amount']) and abs(gross-q*price//SCALE)<=1,'native_fill_arithmetic')
            if side=='buy':require(o['ft_order_tag']=='mv:'+intent,'native_buy_tag')
            out[side]={'id':'order:'+sha([pair,o['order_id']]),'qty':q,'price':price,'gross':gross,'fee':fee}
        net=out['sell']['gross']-out['sell']['fee']-out['buy']['gross']-out['buy']['fee']
        # Freqtrade stores floating P/L; compare at ledger precision, never derive fees from profit.
        p=Decimal(str(t['close_profit_abs']))*SCALE
        require(p.is_finite() and abs(p-net)<=Decimal('1'),'native_profit_mismatch')
        return out|{'net_pl':net,'trade_id':t['id'],'pair':pair}


class NativeDriver:
    """Developer-only adapter for a dedicated, exclusively owned FreqtradeBot.

    The native bot must not have a concurrently running trade loop. This does
    not provide installed worker custody or persistent protection supervision.
    """
    def __init__(self,bot,path,instance):
        from freqtrade.freqtradebot import FreqtradeBot
        require(isinstance(bot,FreqtradeBot),'native_freqtrade_required')
        require(not Path(path).is_symlink(),'unsafe_native_db_path')
        self.bot=bot; self.path=Path(path).resolve(); self.instance=ident(instance)
        self.binding=sha([self.instance,str(self.path),self.path.stat().st_dev,self.path.stat().st_ino])

    def preflight(self,instance,pair):
        require(instance==self.instance,'native_instance_scope')
        require(self.binding==sha([self.instance,str(self.path),self.path.stat().st_dev,self.path.stat().st_ino]),'native_db_replaced')
        b=self.bot; validate_config(b.config,pair); validate_config(b.exchange._config,pair)
        require(b.strategy.__class__.__name__=='GatePaper' and b.state.name=='STOPPED','native_not_exclusively_stopped')
        require(b.config['db_url']=='sqlite:///'+str(self.path),'native_db_path_mismatch')
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            require(not db.execute('SELECT 1 FROM trades WHERE is_open=1 LIMIT 1').fetchone(),'native_open_trade')
            require(not db.execute('SELECT 1 FROM orders WHERE ft_is_open=1 LIMIT 1').fetchone(),'native_open_order')
        return self.binding

    def entry(self,row,guard):
        b=self.bot; self.preflight(row['instance_id'],row['pair']); exchange=b.exchange
        rate=row['entry']/SCALE
        # Reserve 5% price-conversion headroom; upstream paper limit may become market.
        # Never round stake upward to satisfy a venue minimum.
        stake=row['notional']/100/1.05
        fee_rate=max(Decimal(str(exchange.get_fee(symbol=row['pair'],taker_or_maker=x))) for x in ('maker','taker'))
        require(fee_rate.is_finite() and fee_rate>=0 and fee_rate*row['notional']<=row['reserved']-row['notional'],'native_fee_budget')
        old=exchange.create_order; calls=[]; violations=[]
        def guarded(**kw):
            require(not calls and kw['pair']==row['pair'] and kw['side']=='buy' and kw['ordertype']=='limit'
                and kw['leverage']==1 and kw['reduceOnly'] is False and kw['initial_order'] is True,'native_order_not_authorized')
            require(units(kw['rate'])==row['entry'],'native_rate_changed')
            actual=Decimal(str(exchange.amount_to_precision(kw['pair'],kw['amount'])))
            proposed=Decimal(str(kw['amount'])); require(0<actual<=proposed,'native_amount_rounded_up')
            require(actual*Decimal(str(rate))*Decimal('1.05')*100<=row['notional'],'native_notional_bound')
            require(units(exchange.price_to_precision(kw['pair'],kw['rate']))==row['entry'],'native_price_precision_changed')
            calls.append(True)
            result=guard(lambda:old(**kw))
            try:
                require(result.get('type')=='limit' and units(result['price'])==row['entry'],'native_limit_converted')
                if result.get('filled'):
                    require(units(result['average'])<=row['entry'] and quote_units(result['cost'])<=row['notional']*CENT,'native_unbounded_fill')
            except ValueError as exc:
                # Let Freqtrade persist what actually happened before quarantining.
                violations.append(str(exc))
            return result
        b.strategy.stoploss=-(row['entry']-row['stop'])/row['entry']
        b.strategy.arm('mv:'+row['id'],row['pair'],row['entry'])
        exchange.create_order=guarded
        try:
            ok=b.execute_entry(row['pair'],stake,price=rate,ordertype='limit',enter_tag='mv:'+row['id'])
            require(ok is True and len(calls)==1,'native_entry_not_confirmed')
            require(not violations,'native_fill_quarantined')
            return {'observed':True}
        finally:
            exchange.create_order=old; b.strategy.disarm()

    def wallet_observation(self,intent,pair):
        # Native Wallets derives its paper balances independently of Maj Vaj's
        # ledger. Never query a live wallet, even if dry_run was misconfigured.
        self.preflight(self.instance,pair)
        wallets=self.bot.wallets
        require(wallets._config.get('dry_run') is True and getattr(wallets._config.get('runmode','dry_run'),'value',wallets._config.get('runmode','dry_run'))=='dry_run'
            and getattr(wallets._config.get('trading_mode'),'value',wallets._config.get('trading_mode'))=='spot','native_wallet_not_paper')
        before=self.closed_receipt(intent,pair)
        wallets.update(require_update=True)
        quote=quote_units(wallets.get_total('USD')); quantity=units(wallets.get_total(pair.split('/')[0]))
        require(before==self.closed_receipt(intent,pair),'native_wallet_observation_changed')
        return dict(wallet_quote=quote,wallet_quantity=quantity,
                    evidence_digest=sha([self.binding,before,quote,quantity]))

    def closed_receipt(self,intent,pair):
        require(self.binding==sha([self.instance,str(self.path),self.path.stat().st_dev,self.path.stat().st_ino]),'native_db_replaced')
        return read_closed(self.path,intent,pair)
