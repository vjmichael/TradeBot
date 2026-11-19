"""TB-07A2-R1: bounded, exclusively leased PAPER protection and reconciliation.

The installed desk stays PAUSED. This is an internal worker contract, not an
activation endpoint. Native orders and fees are observed, never model supplied.
"""
from contextlib import contextmanager, nullcontext
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from financial_ledger import UNRELEASED, SCALE, FinancialLedger, observed, replay, require, sha, ident, amount
from paper_execution import NativeDriver, append_event, event_detail, units, validate_config


@contextmanager
def custody(directory):
    """Exclusive cooperative process custody. Never steal a live worker's lock."""
    p=Path(directory)
    s=p.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==os.getuid() and not s.st_mode&0o077,'private_worker_directory')
    fd=os.open(p/'paper-worker.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and not s.st_mode&0o077,'private_worker_lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)


def events(db,intent,action):
    return [json.loads(r[0]) for r in db.execute('SELECT detail FROM gate_events WHERE intent_id=? AND action=? ORDER BY seq',(intent,action))]


def released(db,intent):
    receipt=event_detail(db,intent,'native_released')
    if receipt:
        require(db.execute('SELECT 1 FROM gate_intents WHERE id=? AND NOT '+UNRELEASED,(intent,)).fetchone(),'release_receipt_integrity')
    return receipt


def checked_view(view,row):
    require(type(view) is dict and set(view)=={'orders','position','closed','quote'},'invalid_native_view')
    require(type(view['closed']) is bool and type(view['orders']) is list and len(view['orders'])<=256,'invalid_native_view')
    amount(view['position']); amount(view['quote'])
    ids=set(); bought=sold=0
    for o in view['orders']:
        require(set(o)=={'id','tag','side','terminal','qty','gross','fee','price'},'invalid_native_order')
        ident(o['id']); require(o['id'] not in ids,'duplicate_native_order'); ids.add(o['id'])
        require(o['side'] in ('buy','sell') and type(o['terminal']) is bool,'invalid_native_order')
        for k in ('qty','gross','price'):amount(o[k])
        if o['fee'] is not None:amount(o['fee'])
        if o['qty'] and o['terminal']:
            require(o['price'] and o['gross'] and abs(o['gross']-o['qty']*o['price']//SCALE)<=1,'native_fill_arithmetic')
        elif not o['qty']:require(o['gross']==0,'zero_fill_cost')
        if o['side']=='buy':
            require(o['tag']=='mv:'+row['id'],'native_buy_scope'); bought+=o['qty']
        else:sold+=o['qty']
    require(sold<=bought and bought-sold==view['position'],'native_position_mismatch')
    require(not view['closed'] or (view['position']==0 and all(o['terminal'] for o in view['orders'])),'native_closed_mismatch')
    return view


class ProtectionWorker:
    """Only the exclusive supervisor may own a driver; ticks never create buys.

    Exit attempt is durable before native action. A missing tagged native result
    is ambiguous, not permission to resend. A terminal partial exit may be
    followed by a new bounded exit for the verified remaining quantity.
    """
    def __init__(self,transaction,driver,*,clock=time.time):
        self.tx=transaction; self.driver=driver; self.clock=clock

    def _row(self,db,intent):
        r=db.execute('SELECT * FROM gate_intents WHERE id=?',(intent,)).fetchone()
        require(r is not None and r['state'] in ('CLAIMED','UNKNOWN','FILLED_HELD'),'unresolved_claim_required')
        row=dict(r); bound=event_detail(db,intent,'native_bound')
        require(bound and bound['binding']==self.driver.binding,'native_binding_mismatch')
        return row

    def tick(self,intent):
        ident(intent)
        with self.tx() as db:
            row=self._row(db,intent)
            if released(db,intent):return {'state':'ACCOUNTED'}
            paused=any(db.execute('SELECT paused,human_pause FROM desk WHERE id=1').fetchone())
        # Native observation and exits do not depend on entry permission or the ledger.
        self.driver.poll(row)
        view=checked_view(self.driver.view(row),row)
        if view['closed']:
            return self.reconcile(intent)
        order_open=[o for o in view['orders'] if not o['terminal']]
        if order_open and (paused or int(self.clock())>=row['expires']):
            for o in order_open:
                if o['side']=='buy':self.driver.cancel_entry(row,o)
            return {'state':'OPEN_ORDER_HELD'}
        price,at=self.driver.bid(row)
        amount(price,True); now=int(self.clock())
        require(0<=now-at<=30,'stale_protection_quote')
        reason='stop' if price<=row['stop'] else 'target' if price>=row['target'] else None
        if order_open:
            # No sell while entry remainder can still fill; cancel risk-increasing
            # remainder on pause, expiry or triggered protection, then reobserve.
            if reason or paused or now>=row['expires']:
                for o in order_open:
                    if o['side']=='buy':self.driver.cancel_entry(row,o)
            return {'state':'OPEN_ORDER_HELD'}
        if not view['position']:return {'state':'NO_FILL_UNVERIFIED'}
        # Observe/account terminal fill portions even before a complete round trip.
        # Accounting discrepancies must not disable a verified protective exit.
        accounting='ok'
        try:self.reconcile(intent)
        except (ValueError,sqlite3.Error):accounting='held'
        with self.tx() as db:
            row=self._row(db,intent); prior=events(db,intent,'native_exit_attempt')
            by_tag={o['tag']:o for o in view['orders'] if o['side']=='sell'}
            require(all(p['tag'] in by_tag and by_tag[p['tag']]['terminal'] for p in prior),'ambiguous_exit_attempt')
            require(len(by_tag)==sum(o['side']=='sell' for o in view['orders']) and set(by_tag)=={p['tag'] for p in prior},'unattributed_native_exit')
            # Once protection triggers, a terminal partial exit must finish even
            # if the price subsequently moves back inside the original band.
            if prior:reason=prior[0]['reason']
            if not reason:return {'state':'MONITORING','accounting':accounting}
            tag='mv-exit:'+intent+':'+str(len(prior)+1)
            request={'tag':tag,'reason':reason,'qty':view['position'],'price':price,'at':at}
            append_event(db,intent,'native_exit_attempt',request,now)
        # SQL commit precedes side effect; on crash, recover only by tagged order.
        self.driver.exit(row,request)
        return {'state':'EXIT_REQUESTED','reason':reason,'accounting':accounting}

    def reconcile(self,intent):
        with self.tx() as db:row=self._row(db,intent)
        view=checked_view(self.driver.view(row),row)
        with self.tx() as db:
            row=self._row(db,intent)
            old=released(db,intent)
            if old:return {'state':'ACCOUNTED','duplicate':True}
            require(event_detail(db,intent,'native_attempt') is not None,'native_attempt_missing')
            require(not event_detail(db,intent,'native_accounted'),'legacy_import_requires_review')
            require(not db.execute('SELECT 1 FROM fin_conflicts LIMIT 1').fetchone(),'financial_conflict')
            states,_,_,_=replay(db); s=states[row['instance_id']]; instance=row['instance_id']
            require(not s['correction_locked'],'unresolved_correction')
            prior=events(db,intent,'native_partial_accounted'); known={p['id']:p for p in prior}
            require(len(known)==len(prior),'duplicate_partial_receipt')
            native={o['id']:o for o in view['orders']}
            require(all(k in native and native[k]==v for k,v in known.items()),'native_fill_changed')
            require(len([o for o in view['orders'] if o['side']=='buy'])==1,'multiple_native_entries')
            authorized={p['tag']:p for p in events(db,intent,'native_exit_attempt')}
            require(all(o['tag'] in authorized and o['qty']<=authorized[o['tag']]['qty'] for o in view['orders'] if o['side']=='sell'),'unattributed_native_exit')
            require(len({o['tag'] for o in view['orders'] if o['side']=='sell'})==sum(o['side']=='sell' for o in view['orders']),'duplicate_exit_tag')
            if not prior:require(not s['lots'] and not s['sales'],'unresolved_prior_inventory')
            ledger=FinancialLedger(lambda:nullcontext(db),clock=self.clock)
            for o in view['orders']:
                if not o['terminal'] or not o['qty'] or o['id'] in known:continue
                require(o['fee'] is not None,'native_fee_unverified')
                rid='native:'+sha([self.driver.binding,o['id']])
                require(not db.execute('SELECT 1 FROM fin_events WHERE id=?',(rid,)).fetchone(),'native_order_receipt_reused')
                if o['side']=='buy':
                    require(o['price']<=row['entry'],'native_entry_price_exceeded')
                    ledger.buy(rid,instance,intent,o['qty'],o['gross'],o['fee'])
                else:ledger.sell(rid,instance,o['id'],o['qty'],o['gross'],o['fee'])
                append_event(db,intent,'native_partial_accounted',o,int(self.clock()))
            db.execute("UPDATE gate_intents SET state='FILLED_HELD' WHERE id=?",(intent,))
            states,_,_,_=replay(db); s=states[instance]
            if not view['closed']:return {'state':'FILLED_HELD','position':view['position']}
            require(view['position']==0 and not s['lots'],'native_open_inventory')
            require(any(o['side']=='sell' and o['qty'] for o in view['orders']),'no_fill_unverified')
            require(set(s['sales'])=={'native:'+sha([self.driver.binding,o['id']]) for o in view['orders'] if o['side']=='sell' and o['qty']},'unexpected_pending_sales')
            # Exact wallet includes proceeds still pending in our financial journal.
            require(s['active']+s['protected']+s['pending']==view['quote'],'independent_wallet_mismatch')
            for sale,(net,_) in list(s['sales'].items()):
                ledger.settle('settle:'+sha([intent,sale]),instance,sale,net)
            states,_,_,head=replay(db); after=observed(states[instance])
            require(after['active']+after['protected']==view['quote'] and after['qty']==after['pending']==0,'terminal_accounting_mismatch')
            # Explicit terminal event; old readers conservatively retain FILLED_HELD.
            ledger._append('closed:'+intent,instance,'execution_closed',dict(intent=intent,binding=self.driver.binding,view=sha(view),balances=after))
            _,_,_,head=replay(db)
            append_event(db,intent,'native_released',{'binding':self.driver.binding,'view':sha(view),'financial_head':head},int(self.clock()))
            return {'state':'ACCOUNTED','duplicate':False}


class NativeProtectionDriver(NativeDriver):
    """Native stopped-bot protection; exclusive lease required from supervisor."""
    def __init__(self,bot,path,instance,*,clock=time.time):
        super().__init__(bot,path,instance); self.clock=clock

    def verify(self,row):
        require(row['instance_id']==self.instance and self.binding==sha([self.instance,str(self.path),self.path.stat().st_dev,self.path.stat().st_ino]),'native_db_replaced')
        b=self.bot; validate_config(b.config,row['pair']);validate_config(b.exchange._config,row['pair'])
        require(b.state.name=='STOPPED' and b.strategy.__class__.__name__=='GatePaper' and b.config['db_url']=='sqlite:///'+str(self.path),'native_not_exclusively_stopped')
        require(b.wallets._config.get('dry_run') is True and getattr(b.wallets._config.get('runmode','dry_run'),'value',b.wallets._config.get('runmode','dry_run'))=='dry_run','native_wallet_not_paper')

    def trade(self,row):
        from freqtrade.persistence import Trade
        self.verify(row)
        matches=[t for t in Trade.get_trades_proxy(pair=row['pair']) if t.enter_tag=='mv:'+row['id']]
        require(len(matches)==1,'ambiguous_native_trade');t=matches[0]
        require(not any(x.id!=t.id for x in Trade.get_trades_proxy(is_open=True)),'unattributed_open_native_trade')
        require(t.exchange=='kraken' and t.strategy=='GatePaper' and t.stake_currency=='USD' and not t.is_short and t.leverage==1 and getattr(t.trading_mode,'value',t.trading_mode)=='spot','native_trade_scope')
        return t

    def poll(self,row):
        t=self.trade(row)
        from freqtrade.persistence import Trade
        for o in list(t.open_orders):
            native=self.bot.exchange.fetch_order(o.order_id,t.pair)
            self.bot.update_trade_state(t,o.order_id,native,send_msg=False)
            Trade.commit()

    def view(self,row):
        # Native persistence + native Wallets, not Maj Vaj's account projection.
        t=self.trade(row); self.bot.wallets.update(require_update=True)
        orders=[]
        for o in t.orders:
            side=o.ft_order_side; require(side in ('buy','sell') and o.ft_pair==t.pair and o.side==side and str(o.order_id).startswith('dry_run_'+side+'_'),'native_order_scope')
            q=units(o.filled or 0); price=units(o.average or o.price); fee=None
            if q and not o.ft_is_open:
                try:raw=self.bot.exchange.fetch_order(o.order_id,t.pair)
                except Exception:raw={}  # unavailable fee evidence must not prevent protection
                if raw.get('fee') and raw['fee'].get('currency')=='USD' and units(raw['filled'])==q and units(raw['cost'])==units(o.cost):
                    fee=units(raw['fee']['cost'])
                else:
                    # Persisted trade-side fee is only unambiguous for one fill on that side.
                    k='open' if side=='buy' else 'close'
                    same=[x for x in t.orders if x.ft_order_side==side and x.filled]
                    if len(same)==1 and getattr(t,'fee_'+k+'_currency')=='USD':fee=units(getattr(t,'fee_'+k+'_cost'))
                if o.ft_fee_base not in (None,0) or not o.order_filled_date or o.status not in ('closed','canceled','expired') or (side=='buy' and o.order_type!='limit'):fee=None
            orders.append(dict(id='order:'+sha([t.pair,o.order_id]),tag=o.ft_order_tag,side=side,terminal=not o.ft_is_open,
                qty=q,price=price,gross=units(o.cost) if q else 0,fee=fee))
        pos=units(self.bot.wallets.get_total(t.pair.split('/')[0]))
        if any(not o['terminal'] for o in orders):
            pos=sum(o['qty']*(1 if o['side']=='buy' else -1) for o in orders)
        require(t.is_open or t.close_date is not None,'native_close_unverified')
        return dict(orders=orders,position=pos,closed=not t.is_open,quote=units(self.bot.wallets.get_total('USD')))

    def bid(self,row):
        self.verify(row); at=int(self.clock()); book=self.bot.exchange.fetch_l2_order_book(row['pair'],1)
        return units(book['bids'][0][0]),at

    def cancel_entry(self,row,order):
        t=self.trade(row); found=[o for o in t.open_orders if 'order:'+sha([t.pair,o.order_id])==order['id'] and o.ft_order_side=='buy']
        require(len(found)==1,'native_cancel_scope'); o=found[0]
        result=self.bot.exchange.cancel_order_with_result(o.order_id,t.pair,o.amount)
        self.bot.update_trade_state(t,o.order_id,result,send_msg=False)
        from freqtrade.persistence import Trade
        Trade.commit()

    def exit(self,row,request):
        from freqtrade.enums import ExitType, ExitCheckTuple
        t=self.trade(row); b=self.bot; ex=b.exchange
        require(t.is_open and not t.open_orders and units(t.amount)==request['qty'],'native_exit_scope')
        require(0<=int(self.clock())-request['at']<=30,'stale_protection_quote')
        old=ex.create_order; fired=[]
        def guard(**kw):
            require(not fired and kw['pair']==row['pair'] and kw['side']=='sell' and kw['ordertype']=='market' and kw['leverage']==1 and kw['initial_order'] is False,'risk_reduction_only')
            require(0<units(kw['amount'])<=request['qty'],'native_exit_amount')
            fired.append(True);return old(**kw)
        ex.create_order=guard
        try:
            reason=ExitType.STOP_LOSS if request['reason']=='stop' else ExitType.ROI
            ok=b.execute_trade_exit(t,request['price']/SCALE,ExitCheckTuple(exit_type=reason),exit_tag=request['tag'],ordertype='market',skip_custom_exit_price=True)
            require(ok and len(fired)==1,'native_exit_unconfirmed')
        finally:ex.create_order=old


def supervise(directory,transaction,driver_factory,*,stop,interval=2,clock=time.time,heartbeat=lambda ready:None):
    """Bounded poll cadence under OS lease, fresh driver per owned process.

    Supervisor must restart this process after a crash; no automatic entries.
    Exceptions intentionally exit nonzero rather than conceal missing protection.
    """
    require(0.25<=interval<=30,'invalid_worker_interval')
    with custody(directory):
        driver=driver_factory();worker=ProtectionWorker(transaction,driver,clock=clock)
        try:
            heartbeat(True)
            while not stop.is_set():
                with transaction() as db:
                    ids=[r[0] for r in db.execute("SELECT id FROM gate_intents WHERE instance_id=? AND state IN ('CLAIMED','UNKNOWN','FILLED_HELD') ORDER BY id",(driver.instance,)) if not released(db,r[0])]
                for intent in ids:worker.tick(intent)
                heartbeat(False)
                stop.wait(interval)
        finally:
            cleanup=getattr(driver,'close',None)
            if cleanup:cleanup()
