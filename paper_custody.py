"""TB-07A2-R2: authenticated native paper receipts and one leased entry/protection owner.

No live route, public listener, operator activation, or automatic no-fill proof.
HMAC protects receipt provenance from callers without the worker key; it is not
an independent exchange statement or a defense against a compromised worker UID.
"""
from contextlib import closing, nullcontext
import hmac
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import tempfile

from financial_ledger import (AccountingError, FinancialLedger, amount, canon, execution_anchor,
                              ident, observed, projection_values, replay, require, sha,
                              UNRELEASED)
from paper_execution import PaperBridge, units
from paper_supervisor import NativeProtectionDriver, ProtectionWorker, custody, released


def private_key(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and not st.st_mode&0o077,'private_receipt_key_required')
        key=f.read(33)
    require(len(key)==32,'invalid_receipt_key');return key


def sign(key,domain,body):
    require(type(key) is bytes and len(key)==32,'invalid_receipt_key')
    return hmac.new(key,canon(['maj-vaj:PAPER:R2',domain,body]).encode(),hashlib.sha256).hexdigest()


def authenticate(key,domain,packet):
    require(type(packet) is dict and set(packet)=={'body','mac'},'invalid_signed_packet')
    require(type(packet['mac']) is str and hmac.compare_digest(packet['mac'],sign(key,domain,packet['body'])),'native_receipt_authentication_failed')
    return packet['body']


def packet(key,domain,body):return {'body':body,'mac':sign(key,domain,body)}


class ReceiptJournal:
    """Durable single-writer native fee evidence across process death.

    A terminal order is immutable; conflicting evidence refuses reconciliation.
    Nothing is inferred from absent receipts. Store only the bounded typed subset.
    """
    def __init__(self,path,key,binding):
        self.path=Path(path);self.key=key;self.binding=binding
        require(not self.path.is_symlink(),'unsafe_receipt_path')
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        st=os.fstat(fd);os.close(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and not st.st_mode&0o077,'private_receipt_journal_required')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, body TEXT NOT NULL, mac TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS conflicts(id TEXT PRIMARY KEY)');db.commit()

    def capture(self,raw):
        if raw.get('status') not in ('closed','canceled','expired','rejected') or not raw.get('filled'):return
        fee=raw.get('fee');require(type(fee) is dict and fee.get('currency')=='USD','native_fee_unverified')
        side=raw['side'];pair=raw['symbol'];oid=str(raw['id'])
        require(side in ('buy','sell') and pair in ('BTC/USD','ETH/USD','SOL/USD') and oid.startswith('dry_run_'+side+'_'),'native_receipt_scope')
        body=dict(binding=self.binding,id='order:'+sha([pair,oid]),pair=pair,side=side,status=raw['status'],
                  qty=units(raw['filled']),gross=units(raw['cost']),fee=units(fee['cost']),price=units(raw['average']))
        require(body['qty']>0 and body['gross']>0 and body['fee']<=body['gross'],'native_fee_unverified')
        mac=sign(self.key,'terminal-order',body)
        with closing(sqlite3.connect(self.path,timeout=5,isolation_level=None)) as db:
            db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT body,mac FROM receipts WHERE id=?',(body['id'],)).fetchone()
            conflict=old is not None and old!=(canon(body),mac)
            if conflict:db.execute('INSERT OR IGNORE INTO conflicts VALUES(?)',(body['id'],))
            elif not old:db.execute('INSERT INTO receipts VALUES(?,?,?)',(body['id'],canon(body),mac))
            db.commit()
            require(not conflict,'native_terminal_receipt_conflict')

    def fee(self,oid,pair,side,qty,gross,price):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            require(not db.execute('SELECT 1 FROM conflicts WHERE id=?',(oid,)).fetchone(),'native_terminal_receipt_conflict')
            old=db.execute('SELECT body,mac FROM receipts WHERE id=?',(oid,)).fetchone()
        if old is None:return None
        body=authenticate(self.key,'terminal-order',{'body':json.loads(old[0]),'mac':old[1]})
        require(all(body[k]==v for k,v in dict(binding=self.binding,id=oid,pair=pair,side=side,qty=qty,gross=gross,price=price).items()),'native_terminal_receipt_mismatch')
        return body['fee']


class AuthenticatedNativeDriver(NativeProtectionDriver):
    def __init__(self,bot,path,instance,key,*,clock=time.time):
        super().__init__(bot,path,instance,clock=clock)
        self.key=key
        self.journal=ReceiptJournal(self.path.parent/'terminal-receipts.sqlite',key,self.binding)
        # Wrap native observation before ORM callbacks consume it. Failures are
        # retained as a reconciliation block, not a reason to hide a paper fill.
        for name in ('create_order','fetch_order','cancel_order_with_result'):
            original=getattr(bot.exchange,name,None)
            if original is None:continue
            def capture(*a,_original=original,**kw):
                raw=_original(*a,**kw)
                try:self.journal.capture(raw)
                except (ValueError,KeyError,TypeError,sqlite3.Error):pass
                return raw
            setattr(bot.exchange,name,capture)

    def view(self,row):
        view=super().view(row)
        # Never synthesize fees from a rate or an aggregate trade-side fee.
        t=self.trade(row); native={'order:'+sha([t.pair,x.order_id]):x for x in t.orders}
        for o in view['orders']:
            if o['terminal'] and o['qty']:
                n=native[o['id']];o['fee']=None
                if n.ft_fee_base not in (None,0) or not n.order_filled_date or n.status not in ('closed','canceled','expired') or (o['side']=='buy' and n.order_type!='limit'):continue
                try:o['fee']=self.journal.fee(o['id'],row['pair'],o['side'],o['qty'],o['gross'],o['price'])
                except (ValueError,KeyError,sqlite3.Error):pass
        return view

    def observation(self,row):
        self.verify(row)
        from freqtrade.persistence import Trade
        # Only an entirely flat native workspace can authorize more risk.
        require(not Trade.get_trades_proxy(is_open=True),'native_open_trade')
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            require(not db.execute('SELECT 1 FROM orders WHERE ft_is_open=1 LIMIT 1').fetchone(),'native_open_order')
        self.bot.wallets.update(require_update=True)
        return {'quote':units(self.bot.wallets.get_total('USD')),'qty':units(self.bot.wallets.get_total(row['pair'].split('/')[0]))}


def observation(transaction,driver,*,clock=time.time):
    with transaction() as db:
        states,_,_,head=replay(db); instance=driver.instance
        require(instance in states,'unfunded_instance')
        row=db.execute('SELECT id,pair,mode FROM instances WHERE id=?',(instance,)).fetchone()
        require(row is not None and row[2]=='PAPER','paper_instance_required')
        native=driver.observation({'instance_id':instance,'pair':row[1]})
        body=dict(instance=instance,pair=row[1],binding=driver.binding,head=head,
                  anchor=execution_anchor(db),boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0],
                  at=int(clock()),quote=amount(native['quote']),qty=amount(native['qty']))
        return packet(driver.key,'flat-wallet',body)


def _accounts(db,packets,key_for,now,head,anchor,boot):
    states,_,_,_=replay(db)
    scope={r[0]:r[1:] for r in db.execute('SELECT id,pair,mode FROM instances')}
    require(type(packets) is dict and set(packets)==set(scope)==set(states) and bool(scope),'worker_observation_scope')
    accounts={}
    for instance in sorted(scope):
        key,binding=key_for(instance)
        body=authenticate(key,'flat-wallet',packets[instance]);s=states[instance]
        require(set(body)=={'instance','pair','binding','head','anchor','boot','at','quote','qty'},'worker_observation_fields')
        require(body['instance']==instance and body['pair']==scope[instance][0] and scope[instance][1]=='PAPER' and body['binding']==binding,'worker_observation_binding')
        require((body['head'],body['anchor'],body['boot'])==(head,anchor,boot) and 0<=now-body['at']<=30,'worker_observation_stale')
        require(not s['lots'] and not s['sales'] and body['qty']==0 and body['quote']==s['active']+s['protected'],'worker_wallet_mismatch')
        accounts[instance]=observed(s)|{'covered_qty':0}
    return accounts


def publish(transaction,packets,key_for,*,clock=time.time):
    """Collect all authenticated idle-native observations atomically.

    Quote total is observed independently of the desk; protected classification
    is necessarily Maj Vaj's own ledger, not separate simulator custody.
    """
    with transaction() as db:
        now=int(clock()); _,_,_,head=replay(db);anchor=execution_anchor(db);boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
        require(not db.execute("SELECT 1 FROM gate_intents WHERE state IN ('CLAIMED','UNKNOWN','FILLED_HELD') AND "+UNRELEASED+" LIMIT 1").fetchone(),'unresolved_execution')
        require(not db.execute("SELECT 1 FROM gate_intents WHERE state='AUTHORIZED' LIMIT 1").fetchone(),'authorization_in_flight')
        accounts=_accounts(db,packets,key_for,now,head,anchor,boot)
        ledger=FinancialLedger(lambda:nullcontext(db),clock=clock)
        body=dict(observed=min(p['body']['at'] for p in packets.values()),evidence_digest=sha(packets),accounts=accounts,open_orders=[],worker_evidence=packets)
        return ledger._append('workers:'+sha(packets),'desk','desk_reconcile',body)


def authenticated_projection(key_for):
    """Used by integrated worker Gate, never trust unsigned desk reconciliation."""
    from dataclasses import asdict
    from risk_gate import RiskState, GateRefusal, digest
    def project(db,now):
        try:
            values=projection_values(db,now)
            r=db.execute('SELECT previous,body FROM fin_events ORDER BY seq DESC LIMIT 1').fetchone()
            p=json.loads(r[1])['payload']; packets=p.get('worker_evidence')
            require(type(packets) is dict and sha(packets)==p['evidence_digest'],'worker_evidence_required')
            accounts=_accounts(db,packets,key_for,now,r[0],p['execution_anchor'],p['boot'])
            require(accounts==p['accounts'],'worker_evidence_mismatch')
            a=RiskState(**values);a.check(now);return a,digest(asdict(a))
        except (ValueError,OSError,KeyError,TypeError,sqlite3.Error) as e:raise GateRefusal('financial:'+str(e)) from e
    return project


def run_owned(directory,transaction,driver_factory,gate_factory,*,stop,interval=2,clock=time.time,heartbeat=lambda ready:None,on_idle=lambda driver:None,worker_factory=ProtectionWorker):
    """One custody lease covers native construction, entry, protection, cleanup.

    No strategy or UI can submit native orders. Only already-authorized intents
    may enter; claim rechecks current independently authenticated account state.
    """
    require(.25<=interval<=30,'invalid_worker_interval')
    with custody(directory):
        driver=driver_factory();worker=worker_factory(transaction,driver,clock=clock)
        bridge=PaperBridge(transaction,gate_factory(),clock=clock)
        try:
            heartbeat(True)
            while not stop.is_set():
                with transaction() as db:
                    held=[r[0] for r in db.execute("SELECT id FROM gate_intents WHERE instance_id=? AND state IN ('CLAIMED','UNKNOWN','FILLED_HELD') ORDER BY id",(driver.instance,)) if not released(db,r[0])]
                    ready=db.execute("SELECT id,owner,epoch FROM gate_intents WHERE instance_id=? AND state='AUTHORIZED' ORDER BY id",(driver.instance,)).fetchall() if not held else []
                for intent in held:worker.tick(intent)
                if not held and not stop.is_set():
                    require(len(ready)<=1,'multiple_authorized_instance_intents')
                    if ready:bridge.submit(*ready[0],driver)
                    else:on_idle(driver)
                heartbeat(False);stop.wait(interval)
        finally:
            cleanup=getattr(driver,'close',None)
            if cleanup:cleanup()


def workspace(data,instance):
    ident(instance);p=Path(data)/'paper'/instance
    for directory in (Path(data),p.parent,p):
        st=directory.lstat()
        require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and not st.st_mode&0o077,'private_worker_directory')
    return p


def workspace_key(data,instance):
    p=workspace(data,instance);path=p/'paper.sqlite';st=path.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and not st.st_mode&0o077,'private_native_database')
    return private_key(p/'receipt.key'),sha([instance,str(path.resolve()),st.st_dev,st.st_ino])


class IdlePublisher:
    """Private atomic spool; all registered native wallets must agree on one head."""
    def __init__(self,data,transaction,*,clock=time.time):
        self.data=Path(data);self.tx=transaction;self.clock=clock

    def __call__(self,driver):
        now=int(self.clock());p=workspace(self.data,driver.instance)
        with self.tx() as db:
            # Do not refresh an account underneath an admitted intent.
            if db.execute("SELECT 1 FROM gate_intents WHERE state='AUTHORIZED' LIMIT 1").fetchone():return
            if db.execute("SELECT 1 FROM gate_intents WHERE state IN ('CLAIMED','UNKNOWN','FILLED_HELD') AND "+UNRELEASED+" LIMIT 1").fetchone():return
            row=db.execute('SELECT kind,body FROM fin_events ORDER BY seq DESC LIMIT 1').fetchone()
            if row and row[0]=='desk_reconcile':
                body=json.loads(row[1])['payload']
                if 'worker_evidence' in body and 0<=now-body['observed']<15:return
            instances=[r[0] for r in db.execute('SELECT id FROM instances ORDER BY id')]
            if not db.execute("SELECT 1 FROM fin_events WHERE instance=? AND kind='fund'",(driver.instance,)).fetchone():return
        signed=observation(self.tx,driver,clock=self.clock)
        with tempfile.NamedTemporaryFile(mode='w',dir=p,prefix='.wallet-',delete=False) as f:
            temp=Path(f.name)
            try:
                f.write(canon(signed));f.flush();os.fsync(f.fileno())
                os.replace(temp,p/'wallet.json')
                fd=os.open(p,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
            finally:temp.unlink(missing_ok=True)
        packets={}
        try:
            for i in instances:
                fpath=workspace(self.data,i)/'wallet.json'
                fd=os.open(fpath,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
                with os.fdopen(fd) as f:
                    st=os.fstat(f.fileno())
                    require(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and not st.st_mode&0o077 and st.st_size<=8192,'invalid_wallet_spool')
                    packets[i]=json.load(f)
            return publish(self.tx,packets,lambda i:workspace_key(self.data,i),clock=self.clock)
        except (FileNotFoundError,AccountingError):
            # Missing/stale peer evidence leaves Gate closed; never substitute its balances.
            return None
