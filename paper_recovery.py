"""Verified local dry-run cancellation; never infer no-fill from native absence."""
from copy import deepcopy
import json
import sqlite3
import time

from financial_ledger import AccountingError, observed, replay, require, sha
from paper_custody import AuthenticatedNativeDriver, authenticate, packet
from paper_execution import append_event, event_detail, units
from paper_supervisor import ProtectionWorker, events, released


def cancellation(raw,persisted,pair,intent,binding):
    """Freeze the pinned simulator's *observed* open order, not cancel fallback.

    Native dry-run cancel_order resets filled to zero. It also polls a limit
    again, so we never call it here. This is a local simulator operation only.
    """
    require(raw['id']==persisted['id'] and raw['symbol']==pair and raw['side']=='buy' and raw['type']=='limit' and str(raw['id']).startswith('dry_run_buy_'),'cancel_order_scope')
    require(raw['status']=='open' and persisted['tag']=='mv:'+intent,'cancel_not_open')
    q=units(raw['filled']); total=units(raw['amount']); price=units(raw['average'])
    require(total==persisted['amount'] and persisted['filled']<=q<=total and q<total and units(raw['remaining'])==total-q,'cancel_quantity_regression')
    require(price>0 and units(raw['price'])==persisted['price'],'cancel_price_mismatch')
    cost=0 if not q else units(raw['cost']);fee=raw.get('fee')
    if q:
        require(abs(cost-q*price//100000000)<=1 and type(fee) is dict and fee.get('currency')=='USD' and units(fee['cost'])<=cost,'cancel_partial_fee_unverified')
        rate=fee.get('rate');require(type(rate) in (int,float) and 0<=rate<=1,'cancel_partial_fee_rate')
        fee={'currency':'USD','cost':fee['cost'],'rate':rate}
    else:
        require(fee is None or (type(fee) is dict and fee.get('currency')=='USD' and units(fee['cost'])==0),'cancel_zero_fee_unverified')
        fee=None
    # For open zero-filled dry-run orders native cost is requested notional, not
    # executed cost. Normalize only the verified zero fill, not an unknown value.
    frozen={k:raw[k] for k in ('id','symbol','type','side','price','average','amount','filled','remaining','timestamp','datetime')}
    frozen.update(status='canceled',cost=cost/100000000,fee=fee,info={})
    return {'intent':intent,'binding':binding,'order':frozen}


class RecoveryDriver(AuthenticatedNativeDriver):
    def __init__(self,bot,path,instance,key,transaction,*,clock=time.time):
        super().__init__(bot,path,instance,key,clock=clock);self.tx=transaction
        # Do not call upstream fetch on a frozen cancelled limit: the pinned
        # simulator can re-fill a cancelled order on a later book crossing.
        self.cancel_cache={}
        with self.tx() as db:
            records=db.execute("SELECT detail FROM gate_events WHERE action='native_cancel_frozen' AND json_extract(detail,'$.body.binding')=?",(self.binding,)).fetchall()
        for record in records:
            body=authenticate(self.key,'frozen-cancel',json.loads(record[0]));oid=body['order']['id']
            require(oid not in self.cancel_cache,'duplicate_cancel_receipt');self.cancel_cache[oid]=body
        original=bot.exchange.fetch_order
        def fetch(order_id,pair,*args,**kwargs):
            body=self.cancel_cache.get(str(order_id))
            if body is not None:
                require(body['binding']==self.binding and body['order']['symbol']==pair,'cancel_binding')
                return deepcopy(body['order'])
            return original(order_id,pair,*args,**kwargs)
        bot.exchange.fetch_order=fetch

    def frozen(self,row):
        with self.tx() as db:record=event_detail(db,row['id'],'native_cancel_frozen')
        if record is None:return None
        body=authenticate(self.key,'frozen-cancel',record)
        require(body['intent']==row['id'] and body['binding']==self.binding and body['order']['symbol']==row['pair'],'cancel_binding')
        return body['order']

    def _apply_frozen(self,row,raw):
        from freqtrade.persistence import Trade
        self.verify(row)
        trades=[t for t in Trade.get_trades_proxy(pair=row['pair']) if t.enter_tag=='mv:'+row['id']]
        if not trades:
            require(units(raw['filled'])==0,'lost_partial_native_trade')
            return  # zero release still requires independently flat wallet
        require(len(trades)==1,'ambiguous_native_trade');t=trades[0]
        orders=[o for o in t.orders if o.order_id==raw['id'] and o.ft_order_side=='buy']
        require(len(orders)==1 and orders[0].ft_order_tag=='mv:'+row['id'],'cancel_order_scope');o=orders[0]
        require(units(o.filled or 0)<=units(raw['filled']),'cancel_quantity_regression')
        require(units(o.amount)==units(raw['amount']),'cancel_amount_changed')
        if not o.ft_is_open:
            require(o.status=='canceled' and units(o.filled or 0)==units(raw['filled']),'cancel_terminal_changed')
            if units(raw['filled']):return
        self.bot.exchange._dry_run_open_orders[raw['id']]=deepcopy(raw)
        if units(raw['filled']):
            self.journal.capture(raw)
            self.bot.update_trade_state(t,o.order_id,raw,send_msg=False)
        else:
            require(len(t.orders)==1 and not units(o.filled or 0),'cancel_zero_has_other_orders')
            # Native terminal-order handler removes the empty trade. Its
            # cancellation input is already terminal, so no venue cancel call.
            self.bot.handle_cancel_enter(t,raw,o,'maj-vaj-verified-paper-cancel')
        Trade.commit()

    def recover_cancel(self,row):
        raw=self.frozen(row)
        if raw is not None:self._apply_frozen(row,raw)
        return raw

    def poll(self,row):
        # Native restart can reconstruct an open order from ORM and create a
        # new simulated fill, even if pre-crash terminal evidence was lost.
        # Do not manufacture that additional exposure from missing cache.
        t=self.trade(row)
        require(all(o.order_id in self.bot.exchange._dry_run_open_orders or o.order_id in self.cancel_cache for o in t.open_orders),'native_open_state_missing_review_required')
        return super().poll(row)

    def cancel_before_poll(self,row):
        """Do not let a paused/expired pending buy gain exposure via polling."""
        t=self.trade(row)
        buys=[o for o in t.open_orders if o.ft_order_side=='buy']
        if not buys:return False
        require(len(buys)==1,'multiple_open_entries')
        o=buys[0]
        self.cancel_entry(row,{'id':'order:'+sha([t.pair,o.order_id]),'qty':units(o.filled or 0)})
        return True

    def cancel_entry(self,row,order):
        t=self.trade(row);found=[o for o in t.open_orders if 'order:'+sha([t.pair,o.order_id])==order['id'] and o.ft_order_side=='buy']
        require(len(found)==1 and len(t.orders)==1,'native_cancel_scope');o=found[0]
        old=self.frozen(row)
        if old is not None:return self._apply_frozen(row,old)
        # A fetch on a pending dry-run limit may itself create a new fill.
        # A cancellation must freeze the last actual simulator state instead.
        raw=self.bot.exchange._dry_run_open_orders.get(o.order_id)
        require(raw is not None,'cancel_native_state_missing_review_required')
        raw=deepcopy(raw)
        if raw.get('status')=='closed':
            # A fill raced the request. Persist the fill without zeroing it.
            require(raw['id']==o.order_id and raw['symbol']==t.pair and raw['side']=='buy' and units(raw['filled'])>=units(o.filled or 0) and units(raw['filled'])>0,'cancel_raced_fill_unverified')
            self.bot.update_trade_state(t,o.order_id,raw,send_msg=False)
            from freqtrade.persistence import Trade
            Trade.commit();return
        persisted=dict(id=o.order_id,tag=o.ft_order_tag,amount=units(o.amount),filled=max(units(o.filled or 0),order['qty']),price=units(o.price))
        body=cancellation(raw,persisted,t.pair,row['id'],self.binding)
        with self.tx() as db:
            require(event_detail(db,row['id'],'native_cancel_frozen') is None,'cancel_already_frozen')
            append_event(db,row['id'],'native_cancel_frozen',packet(self.key,'frozen-cancel',body),int(self.clock()))
        self.cancel_cache[body['order']['id']]=body
        self._apply_frozen(row,body['order'])


def finish_zero(transaction,driver,row,*,clock=time.time):
    """Release a proven terminal zero-fill only after exact independent wallet.

    The authenticated cancellation receipt is tied to a previously observed
    native entry; absence of a trade/order by itself never suffices.
    """
    raw=driver.frozen(row)
    if raw is None or units(raw['filled']):return None
    try:wallet=driver.observation(row)
    except (AccountingError,sqlite3.Error):return None
    with transaction() as db:
        current=db.execute('SELECT * FROM gate_intents WHERE id=?',(row['id'],)).fetchone()
        require(current is not None and current['state'] in ('CLAIMED','UNKNOWN') and current['claim_token'],'zero_claim_scope')
        bound=event_detail(db,row['id'],'native_bound')
        proof=event_detail(db,row['id'],'native_cancel_frozen')
        require(bound and bound['binding']==driver.binding and event_detail(db,row['id'],'native_attempt') and proof,'zero_proof_missing')
        body=authenticate(driver.key,'frozen-cancel',proof)
        require(body=={'intent':row['id'],'binding':driver.binding,'order':raw} and raw['status']=='canceled' and units(raw['filled'])==units(raw['cost'])==0 and raw['fee'] is None,'zero_proof_mismatch')
        require(not any(events(db,row['id'],a) for a in ('native_partial_accounted','native_accounted','native_exit_attempt','native_released')),'zero_prior_execution')
        states,_,_,_=replay(db);s=states[row['instance_id']];b=observed(s)
        require(not s['correction_locked'] and not db.execute('SELECT 1 FROM fin_conflicts LIMIT 1').fetchone(),'zero_financial_conflict')
        require(b['qty']==b['pending']==wallet['qty']==0 and b['active']+b['protected']==wallet['quote'],'zero_wallet_mismatch')
        receipt='zero:'+sha([proof,wallet,current['claim_token']])
        require(not db.execute('SELECT 1 FROM gate_intents WHERE resolution_receipt=?',(receipt,)).fetchone(),'zero_receipt_reused')
        db.execute("UPDATE gate_intents SET state='NO_FILL',resolution_receipt=? WHERE id=?",(receipt,row['id']))
        append_event(db,row['id'],'resolution',{'receipt':receipt,'outcome':'NO_FILL','proof':sha(proof),'wallet':wallet},int(clock()))
    return {'state':'NO_FILL','receipt':receipt}


class RecoveryWorker(ProtectionWorker):
    def tick(self,intent):
        with self.tx() as db:
            row=self._row(db,intent)
            if released(db,intent):return {'state':'ACCOUNTED'}
            paused=any(db.execute('SELECT paused,human_pause FROM desk WHERE id=1').fetchone())
        raw=self.driver.recover_cancel(row)
        if raw is not None and not units(raw['filled']):
            result=finish_zero(self.tx,self.driver,row,clock=self.clock)
            if result:return result
        if raw is None and (paused or int(self.clock())>=row['expires']) and self.driver.cancel_before_poll(row):
            return {'state':'CANCEL_REQUESTED'}
        return super().tick(intent)
