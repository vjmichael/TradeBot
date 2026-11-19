"""Signed worker heartbeat and read-only sustained PUBLIC-paper qualification."""
from contextlib import closing
from decimal import Decimal
import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from financial_ledger import UNRELEASED, canon, replay, require, sha
from paper_custody import authenticate, packet, workspace_key
from paper_provision import instance_id, sync_dir, installed_workspace, private_open

PAIRS={'BTC/USD':('XBTUSD',{'XXBTZUSD','XBTUSD'}),'ETH/USD':('ETHUSD',{'XETHZUSD','ETHUSD'}),'SOL/USD':('SOLUSD',{'SOLUSD'})}


def start_ticks(pid):
    # /proc stat comm may contain spaces and parentheses; parse after final ')'.
    return int(Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19])


def heartbeat(data,instance,transaction,ready,*,clock=time.time):
    key,binding=workspace_key(data,instance);work=Path(data)/'paper'/instance
    with transaction() as db:
        _,_,_,head=replay(db);boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
    body=dict(instance=instance,binding=binding,at=int(clock()),head=head,boot=boot,pid=os.getpid(),start_ticks=start_ticks(os.getpid()),stage='ready' if ready else 'tick')
    with tempfile.NamedTemporaryFile(mode='w',dir=work,prefix='.health-',delete=False) as f:
        tmp=Path(f.name)
        try:
            f.write(canon(packet(key,'worker-health',body)));f.flush();os.fsync(f.fileno());os.replace(tmp,work/'health.json');sync_dir(work)
        finally:tmp.unlink(missing_ok=True)


def read_health(data,instance,now,*,process_identity=start_ticks):
    key,binding=workspace_key(data,instance)
    with os.fdopen(private_open(Path(data)/'paper'/instance/'health.json',os.O_RDONLY),'rb') as f:raw=f.read(8193)
    require(len(raw)<=8192,'invalid_health_packet');b=authenticate(key,'worker-health',json.loads(raw))
    require(b['instance']==instance and b['binding']==binding and b['stage']=='tick' and 0<=now-b['at']<=30,'stale_or_unready_worker')
    require(type(b['pid']) is int and b['pid']>0 and process_identity(b['pid'])==b['start_ticks'],'worker_process_changed')
    return b


def public_book(pair,*,clock=time.time):
    """Independent public Kraken HTTPS depth; no credential, private or order API."""
    symbol,aliases=PAIRS[pair];t0=clock()
    req=Request('https://api.kraken.com/0/public/Depth?'+urlencode({'pair':symbol,'count':1}),headers={'User-Agent':'MajVaj-PAPER-qualification/1'})
    with urlopen(req,timeout=10) as r:
        require(r.geturl()==req.full_url,'unexpected_public_redirect');raw=r.read(32769)
    now=clock();require(len(raw)<=32768 and 0<=now-t0<=10,'public_book_transport')
    doc=json.loads(raw);require(doc['error']==[] and len(doc['result'])==1,'public_book_error')
    market,book=next(iter(doc['result'].items()));require(market in aliases,'public_book_pair')
    return validate_book(pair,book,now,sha(doc))


def validate_book(pair,book,now,digest):
    require(pair in PAIRS and type(book) is dict,'public_book_scope')
    bid,ask=book['bids'][0],book['asks'][0]
    require(len(bid)==len(ask)==3,'public_book_shape')
    bp,bq,ap,aq=(Decimal(str(v)) for v in (bid[0],bid[1],ask[0],ask[1]))
    require(all(v.is_finite() and v>0 for v in (bp,bq,ap,aq)) and bp<=ap,'public_book_values')
    require(all(0<=now-float(v)<=120 for v in (bid[2],ask[2])),'public_book_stale')
    return dict(pair=pair,at=int(now),bid=str(bp),ask=str(ap),bid_qty=str(bq),ask_qty=str(aq),digest=digest)


def audit(data,instance,transaction,output,*,seconds=3600,interval=15,book=public_book,clock=time.time,monotonic=time.monotonic,sleep=time.sleep):
    """No entries, policy publication, activation or test-price injection on CLI.

    Passing public observations alone is not trading acceptance. Requires a
    newly completed, verified trade and no residual unresolved intent; does not
    claim profitability or guarantee continuity outside observed samples.
    """
    instance_id(instance);require(3600<=seconds<=86400 and 10<=interval<=60,'invalid_sustained_window')
    data=Path(data);start=monotonic();last=None;sample_count=0;first_seq=None
    with transaction() as db:
        installed_workspace(data,instance,db)
        pair=db.execute('SELECT pair FROM instances WHERE id=?',(instance,)).fetchone()[0]
        first_seq=db.execute('SELECT COALESCE(MAX(seq),0) FROM gate_events').fetchone()[0]
    with Path(output).open('x',encoding='utf-8') as f:
        def record(body):f.write(canon(body)+'\n');f.flush();os.fsync(f.fileno())
        try:
            while True:
                sample_start=monotonic();now=int(clock());market=book(pair,clock=clock)
                health=read_health(data,instance,int(clock()))
                require(last is None or sample_start-last<=interval+15,'public_observation_gap');last=sample_start
                with transaction() as db:
                    _,_,_,head=replay(db);boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
                    require(health['boot']==boot,'worker_boot_changed')
                    require(db.execute('SELECT mode FROM instances WHERE id=?',(instance,)).fetchone()[0]=='PAPER','paper_scope_changed')
                record(dict(kind='sample',elapsed=round(sample_start-start,3),market=market,health=health,ledger_head=head));sample_count+=1
                if monotonic()-start>=seconds:break
                sleep(interval)
            with transaction() as db:
                replay(db)
                closed=db.execute("SELECT i.id FROM gate_intents i JOIN gate_events e ON e.intent_id=i.id AND e.action='native_released' WHERE i.instance_id=? AND e.seq>? AND EXISTS (SELECT 1 FROM gate_events a WHERE a.intent_id=i.id AND a.action='native_attempt' AND a.seq>?) AND NOT "+UNRELEASED.replace('gate_intents.','i.'),(instance,first_seq,first_seq)).fetchall()
                held=db.execute("SELECT COUNT(*) FROM gate_intents WHERE instance_id=? AND state IN ('AUTHORIZED','CLAIMED','UNKNOWN','FILLED_HELD') AND "+UNRELEASED,(instance,)).fetchone()[0]
            qualified=bool(closed and held==0 and sample_count>=seconds//(interval+15))
            result=dict(kind='summary',public_observation_passed=True,paper_trading_qualified=qualified,samples=sample_count,elapsed=round(monotonic()-start,3),closed_intents=[r[0] for r in closed],remaining_holds=held)
            record(result);return result
        except Exception as exc:
            record(dict(kind='summary',public_observation_passed=False,paper_trading_qualified=False,samples=sample_count,reason=type(exc).__name__+':'+str(exc)));raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('data',type=Path);p.add_argument('instance');p.add_argument('output',type=Path);p.add_argument('--seconds',type=int,default=3600);p.add_argument('--interval',type=int,default=15);a=p.parse_args()
    from reserve_runtime import desk_transaction
    result=audit(a.data,a.instance,desk_transaction(a.data),a.output,seconds=a.seconds,interval=a.interval)
    print(canon(result));return 0 if result['paper_trading_qualified'] else 2


if __name__=='__main__':raise SystemExit(main())
