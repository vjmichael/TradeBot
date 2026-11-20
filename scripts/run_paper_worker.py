"""Explicit Linux leased paper service. No activation or autonomous strategy entries."""
import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import time
import socket
import sys
from threading import Event

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from financial_ledger import ident, require
from paper_execution import validate_config
from paper_custody import AuthenticatedNativeDriver, IdlePublisher, authenticated_projection, run_owned, workspace_key
from risk_gate import Gate
import financial_goals
from goal_control import evaluate, entry_guard
from goal_evidence import GoalRecoveryDriver, capture_open
from paper_provision import instance_id, installed_workspace
from paper_recovery import RecoveryDriver, RecoveryWorker
from paper_monitor import heartbeat
from reserve_runtime import desk_transaction, private_directory, private_open


def notify(ready):
    address=os.environ.get('NOTIFY_SOCKET')
    if address:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
            sock.sendto(b'READY=1\nWATCHDOG=1' if ready else b'WATCHDOG=1', '\0'+address[1:] if address.startswith('@') else address)


def run(data,instance):
    """Existing private native workspace only; no auto-provision, no credentials."""
    instance_id(instance); data=private_directory(data)
    work=private_directory(private_directory(data/'paper')/instance)
    path=work/'paper.sqlite'
    os.close(private_open(path,os.O_RDONLY))
    tx=desk_transaction(data)
    stop=Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())

    owned=[]
    def create():
        # Never allow an environment overlay to broaden the fixed configuration.
        require(not any(k.startswith('FREQTRADE__') for k in os.environ),'native_environment_override')
        from freqtrade.configuration import Configuration
        from freqtrade.enums import RunMode
        from freqtrade.freqtradebot import FreqtradeBot
        from engine import gate_paper_config
        from tradebot import InstanceSpec
        with tx() as db:
            installed_workspace(data,instance,db)
            financial_goals.initialize(db)
            row=db.execute('SELECT id,name,pair,virtual_cents,mode FROM instances WHERE id=?',(instance,)).fetchone()
            require(row is not None and row['mode']=='PAPER','paper_instance_required')
            pair=row['pair']; spec=InstanceSpec(name=row['name'],pair=pair,virtual_cents=row['virtual_cents'],provider='none',strategy_family='numerical-baseline')
        c=gate_paper_config(spec,path)
        c.update(cancel_open_orders_on_exit=False,user_data_dir=str(work),strategy_path=str(ROOT/'engine_strategies'))
        # Exact generated config, never accept arbitrary user-supplied JSON.
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w',dir=work,suffix='.json') as f:
            json.dump(c,f);f.flush()
            config=Configuration({'config':[f.name],'user_data_dir':str(work),'strategy_path':str(ROOT/'engine_strategies')},RunMode.DRY_RUN).get_config()
        validate_config(config,pair)
        require(not config.get('external_message_consumer',{}).get('enabled'),'unsafe_native_service')
        bot=FreqtradeBot(config)
        key,binding=workspace_key(data,instance)
        driver=GoalRecoveryDriver(bot,path,instance,key,tx)
        require(driver.binding==binding,'native_db_replaced')
        driver.close=bot.cleanup
        owned.append(driver)
        return driver
    keys=lambda i:workspace_key(data,i)
    from reserve_protected import guard_installed_projection
    projection=guard_installed_projection(data,authenticated_projection(keys))
    def report(ready):
        # Notification processing never interrupts risk-reducing protection.
        if not ready:
            try:owned[0].sync_goal_times()
            except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):
                print('Execution interval reporting unavailable.',file=sys.stderr,flush=True)
            try:capture_open(tx,owned[0],clock=time.time)
            except Exception:
                print('Opening equity observation unavailable.',file=sys.stderr,flush=True)
            try:
                with tx() as db:evaluate(db,instance,int(time.time()),keys,verify=projection)
            except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):
                print("Daily goal evaluation unavailable; new-entry checks remain enforced.",file=sys.stderr,flush=True)
        heartbeat(data,instance,tx,ready)
        notify(ready)
    run_owned(work,tx,create,lambda:Gate(tx,account_projection=projection,entry_guard=entry_guard(keys,projection)),
              stop=stop,heartbeat=report,on_idle=IdlePublisher(data,tx),worker_factory=RecoveryWorker)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data',type=Path);parser.add_argument('instance')
    args=parser.parse_args();run(args.data,args.instance)
