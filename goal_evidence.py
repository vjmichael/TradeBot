"""PAPER-only historical evidence. Not an entry or market-data authority."""
from contextlib import closing
import json
import sqlite3

from financial_ledger import SCALE, amount, canon, quote_units, qty, replay, require, sha
from paper_custody import authenticate, packet
from paper_execution import event_detail, units
from paper_supervisor import checked_view, released
from paper_recovery import RecoveryDriver


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS goal_open_snapshots(instance TEXT NOT NULL, cutoff INTEGER NOT NULL, at INTEGER NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(instance,cutoff,at))')
    db.execute('CREATE TABLE IF NOT EXISTS goal_fill_times(id TEXT PRIMARY KEY, instance TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL)')


def open_value(s,b):
    """Only the independently observed single fully booked buy; no pending sales.

    Freqtrade's dry-run USD wallet excludes open stake but defers the entry
    fee until close. Explicitly reconstruct that convention, not a tolerance.
    """
    require(s and qty(s)>0 and not s['sales'] and not s['correction_locked'],'opening_inventory_unverified')
    o=b['buy'];amount(o['price'],True);amount(o['qty'],True);amount(o['gross'],True);amount(o['fee'])
    rid='native:'+sha([b['binding'],o['id']])
    matches=[p for k,i,p in s['history'] if k=='buy' and i==rid]
    require(len(matches)==1 and all(matches[0][k]==o[k] for k in ('qty','gross','fee')),'opening_fill_mismatch')
    require(s['lots']==[[o['qty'],o['gross']+o['fee']]] and b['qty']==o['qty'],'opening_quantity_mismatch')
    require(b['quote']==s['active']+s['protected']+o['fee'],'opening_wallet_mismatch')
    return amount(s['active']+b['qty']*amount(b['bid'],True)//SCALE)


def capture_open(transaction,driver,*,clock):
    """Worker-only pre-midnight snapshot; never fetch an open order for reporting."""
    from financial_goals import bounds, versions
    with transaction() as db:
        hist=versions(db,driver.instance)
        if not hist:return
        now=int(clock());zone=hist[-1]['settings']['zone'];_,_,cutoff,_,_=bounds(now,zone)
        if not 0<cutoff-now<=30:return
        states,_,_,head=replay(db);s=states.get(driver.instance)
        if not s or not qty(s) or s['sales']:return
        candidates=db.execute("SELECT * FROM gate_intents WHERE instance_id=? AND state IN ('AUTHORIZED','CLAIMED','UNKNOWN','FILLED_HELD')",(driver.instance,)).fetchall()
        # A completed intent remains historically FILLED_HELD. Only a verified
        # terminal release can exclude it; unresolved competing claims block the
        # baseline rather than letting a single booked position hide other risk.
        held=[r for r in candidates if not released(db,r['id'])]
        if len(held)!=1 or held[0]['state']!='FILLED_HELD':return
        row=dict(held[0]);bound=event_detail(db,row['id'],'native_bound')
        require(bound and bound['binding']==driver.binding and event_detail(db,row['id'],'native_attempt'),'opening_binding_unverified')
        boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
        seq=db.execute('SELECT COALESCE(MAX(seq),0) FROM gate_events').fetchone()[0]
    # Network quote is outside the desk lock. The same exclusive worker owns
    # native mutation; verify journal/boot/event identity again before commit.
    t=driver.trade(row)
    require(t.is_open and not t.open_orders,'opening_order_unresolved')
    v=checked_view(driver.view(row),row)
    require(not v['closed'] and len(v['orders'])==1 and v['orders'][0]['side']=='buy' and v['orders'][0]['terminal'] and v['orders'][0]['fee'] is not None,'opening_order_unverified')
    o=v['orders'][0]
    price,at=driver.bid(row);end=int(clock())
    require(now<=at<=end<cutoff and cutoff-at<=30 and end-at<=5,'opening_quote_outside_window')
    body=dict(instance=driver.instance,binding=driver.binding,pair=row['pair'],cutoff=cutoff,at=at,observed=end,head=head,boot=boot,gate_seq=seq,bid=price,qty=v['position'],quote=v['quote'],buy={k:o[k] for k in ('id','qty','gross','fee','price')})
    open_value(s,body)
    signed=packet(driver.key,'goal-open-equity',body)
    with transaction() as db:
        require(int(clock())<cutoff and replay(db)[3]==head and db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]==boot and db.execute('SELECT COALESCE(MAX(seq),0) FROM gate_events').fetchone()[0]==seq,'opening_snapshot_changed')
        db.execute('INSERT OR IGNORE INTO goal_open_snapshots VALUES(?,?,?,?,?)',(driver.instance,cutoff,at,canon(signed),sha(signed)))


def opening(db,instance,s,head,cutoff,key_for):
    if not s or not qty(s) or not head or key_for is None:return None,None
    try:
        key,binding=key_for(instance)
        for at,raw,digest in db.execute('SELECT at,body,digest FROM goal_open_snapshots WHERE instance=? AND cutoff=? ORDER BY at DESC',(instance,cutoff)):
            signed=json.loads(raw);require(raw==canon(signed) and digest==sha(signed),'opening_snapshot_integrity')
            b=authenticate(key,'goal-open-equity',signed)
            require(b['binding']==binding and b['instance']==instance and b['pair']==s['pair'].replace('-','/') and b['cutoff']==cutoff and b['at']==at and 0<cutoff-at<=30 and at<=b['observed']<cutoff,'opening_snapshot_scope')
            if b['head']!=head or db.execute('SELECT 1 FROM gate_events WHERE seq>? AND at<? LIMIT 1',(b['gate_seq'],cutoff)).fetchone():continue
            return open_value(s,b),digest
    except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):pass
    return None,None


def timing_packet(driver,raw,start,end):
    """Full-fill transition interval, not order creation or a guessed fill date."""
    require(type(start) is int and type(end) is int and 0<start<=end<=start+30,'fill_time_window')
    require(raw['status']=='closed' and units(raw['filled'])==units(raw['amount'])>0,'fill_time_not_full')
    oid='order:'+sha([raw['symbol'],str(raw['id'])]);side=raw['side']
    detail=driver.journal.detail(oid,raw['symbol'],side,units(raw['filled']),quote_units(raw['cost']),units(raw['average']))
    require(detail is not None and detail['status']=='closed','fill_time_receipt_missing')
    body=dict(instance=driver.instance,binding=driver.binding,id='native:'+sha([driver.binding,oid]),order=oid,pair=raw['symbol'],side=side,qty=detail['qty'],gross=detail['gross'],fee=detail['fee'],start=start,end=end)
    return packet(driver.key,'goal-fill-interval',body)


class GoalRecoveryDriver(RecoveryDriver):
    """Capture only a newly observed native full-fill transition, never refetch history."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        with closing(sqlite3.connect(self.journal.path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS goal_times(id TEXT PRIMARY KEY, body TEXT NOT NULL)');db.commit()
        ex=self.bot.exchange
        for name in ('create_order','fetch_order'):
            original=getattr(ex,name)
            def track(*a,_name=name,_original=original,**kw):
                start=int(self.clock());before=False
                if _name=='fetch_order':
                    oid=a[0] if a else kw.get('order_id');old=ex._dry_run_open_orders.get(oid)
                    before=bool(old and old.get('status')=='open' and old.get('filled')==0)
                raw=_original(*a,**kw)
                if (_name=='create_order' or before) and raw.get('status')=='closed':
                    try:
                        signed=timing_packet(self,raw,start,int(self.clock()))
                        with closing(sqlite3.connect(self.journal.path)) as db:
                            db.execute('PRAGMA synchronous=FULL');db.execute('INSERT OR IGNORE INTO goal_times VALUES(?,?)',(signed['body']['id'],canon(signed)));db.commit()
                    except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):pass
                return raw
            setattr(ex,name,track)

    def sync_goal_times(self):
        with closing(sqlite3.connect(self.journal.path.resolve().as_uri()+'?mode=ro',uri=True)) as native,self.tx() as db:
            for event_id,raw in db.execute("SELECT id,body FROM fin_events WHERE instance=? AND kind='sell' AND id LIKE 'native:%' AND id NOT IN (SELECT id FROM goal_fill_times)",(self.instance,)):
                found=native.execute('SELECT body FROM goal_times WHERE id=?',(event_id,)).fetchone()
                if found:
                    signed=json.loads(found[0]);b=validate_time(self.instance,event_id,json.loads(raw),signed,self.key,self.binding)
                    require(db.execute('SELECT pair FROM instances WHERE id=?',(self.instance,)).fetchone()[0]==b['pair'],'fill_time_pair')
                    require(not native.execute('SELECT 1 FROM conflicts WHERE id=?',(b['order'],)).fetchone(),'fill_time_receipt_conflict')
                    db.execute('INSERT INTO goal_fill_times VALUES(?,?,?,?)',(event_id,self.instance,canon(signed),sha(signed)))


def validate_time(instance,e,ledger,signed,key,binding):
    b=authenticate(key,'goal-fill-interval',signed);p=ledger['payload']
    require(b['instance']==instance and b['binding']==binding and b['id']==e=='native:'+sha([binding,b['order']]) and b['side']=='sell' and b['order']==p['order'] and all(b[k]==p[k] for k in ('qty','gross','fee')),'fill_time_scope')
    require(type(b['start']) is int and type(b['end']) is int and 0<b['start']<=b['end']<=b['start']+30 and b['end']<=ledger['recorded_at'],'fill_time_invalid')
    return b


def attributed(db,instance,e,ledger,key_for):
    if key_for is None:return None
    try:
        key,binding=key_for(instance);r=db.execute('SELECT body,digest FROM goal_fill_times WHERE id=? AND instance=?',(e,instance)).fetchone()
        if r is None:return None
        signed=json.loads(r[0]);require(r[0]==canon(signed) and r[1]==sha(signed),'fill_time_integrity')
        b=validate_time(instance,e,ledger,signed,key,binding)
        require(db.execute('SELECT pair FROM instances WHERE id=?',(instance,)).fetchone()[0]==b['pair'],'fill_time_pair')
        return b['start'],b['end']
    except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):return None
