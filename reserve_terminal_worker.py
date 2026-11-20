"""Opt-in installed PAPER terminal readback and keyless settlement service.

No entry, cancel, exit, activation, arbitrary RPC or secret-bearing config.
An unresolved or incomplete native order remains held for protected recovery.
"""
import argparse
import json
import os
import pwd
import grp
import tempfile
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from reserve_checkpoint import AuthorityStore, require
from reserve_ipc import pinned_key
from reserve_native_evidence import NativeProducer
from reserve_native_handoff import open_reviewed
from reserve_native_wallet import rebound_bindings
from reserve_terminal_transport import TerminalMailbox, consume, produce
from financial_ledger import UNRELEASED


@contextmanager
def native_driver_context(instance,pair,cents,work):
    """Reopen the pinned PAPER runtime under the existing native custody lease."""
    from freqtrade.configuration import Configuration
    from freqtrade.enums import RunMode
    from freqtrade.freqtradebot import FreqtradeBot
    from engine import gate_paper_config
    from tradebot import InstanceSpec
    from paper_execution import validate_config
    from paper_custody import AuthenticatedNativeDriver, workspace_key
    require(not any(k.startswith('FREQTRADE__') for k in os.environ),'native_environment_override')
    spec=InstanceSpec(name='Protected terminal readback',pair=pair,virtual_cents=cents,provider='none',strategy_family='numerical-baseline')
    path=work/'paper.sqlite';root=Path(__file__).resolve().parent
    c=gate_paper_config(spec,path)
    c.update(cancel_open_orders_on_exit=False,user_data_dir=str(work),strategy_path=str(root/'engine_strategies'))
    with tempfile.NamedTemporaryFile(mode='w',dir=work,suffix='.json') as f:
        json.dump(c,f);f.flush()
        cfg=Configuration({'config':[f.name],'user_data_dir':str(work),'strategy_path':str(root/'engine_strategies')},RunMode.DRY_RUN).get_config()
    validate_config(cfg,pair)
    require(cfg.get('cancel_open_orders_on_exit') is False and not cfg.get('external_message_consumer',{}).get('enabled'),'unsafe_native_service')
    bot=FreqtradeBot(cfg)
    try:
        require(bot.state.name=='STOPPED','native_not_stopped')
        key,binding=workspace_key(work.parents[1],instance)
        driver=AuthenticatedNativeDriver(bot,path,instance,key)
        require(driver.binding==binding,'native_db_replaced')
        yield driver
    finally:bot.cleanup()


def native_step(producer,inbox,store,relay_pin,native_gid,mailbox,*,factory=native_driver_context):
    require(os.geteuid()==producer.uid==mailbox.owner,'terminal_native_identity')
    if mailbox.read() is not None:
        return 'retired' if mailbox.retire(producer,inbox,store,relay_pin,native_gid) else 'awaiting_commit'
    with open_reviewed(inbox,producer.workspace,store,relay_pin,native_uid=producer.uid,native_gid=native_gid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid,clock=producer.clock) as (db,_):
        rows=db.execute('SELECT id,pair,virtual_cents,mode FROM instances').fetchall()
        require(len(rows)==1 and rows[0][3]=='PAPER','terminal_single_workspace_required')
        instance,pair,cents,_=rows[0]
        pending=db.execute("SELECT id,instance_id FROM gate_intents WHERE state IN ('CLAIMED','UNKNOWN','FILLED_HELD') AND "+UNRELEASED).fetchall()
        if not pending:return 'idle'
        require(len(pending)==1 and pending[0][1]==instance,'terminal_unresolved_scope')
        intent=pending[0][0]
    packet=produce(producer,inbox,store,relay_pin,native_gid,instance,intent,partial(factory,instance,pair,cents))
    mailbox.publish(packet)
    return 'published'


def desk_step(transaction,mailbox,bindings,public,workspace,store,*,desk_uid,clock=None):
    require(os.geteuid()==desk_uid and desk_uid!=mailbox.owner,'terminal_desk_identity')
    packet=mailbox.read()
    if packet is None:return 'idle'
    args={} if clock is None else {'clock':clock}
    result=consume(transaction,packet,bindings,public,workspace,store=store,**args)
    return 'already_accounted' if result['duplicate'] else 'accounted'


def main():
    from reserve_runtime import desk_transaction
    p=argparse.ArgumentParser();p.add_argument('role',choices=('native','desk'));a=p.parse_args()
    desk=pwd.getpwnam('maj-vaj');native=pwd.getpwnam('maj-vaj-native');auth=pwd.getpwnam('maj-vaj-approval')
    group=grp.getgrnam('maj-vaj-transfer').gr_gid
    require(len({desk.pw_uid,native.pw_uid,auth.pw_uid})==3 and min(desk.pw_uid,native.pw_uid,auth.pw_uid,group)>0 and group in {os.getegid(),*os.getgroups()},'terminal_service_principals')
    require(group not in {desk.pw_gid,native.pw_gid,auth.pw_gid},'terminal_transfer_group')
    pins=Path('/etc/maj-vaj');native_pin=pins/'reserve-native.pub';human_pin=pins/'reserve-checkpoint.pub';relay_pin=pins/'reserve-relay.pub';rebind_pin=pins/'reserve-rebind.pub';records=pins/'reserve-rebind'
    require(len({pinned_key(x) for x in (native_pin,human_pin,relay_pin,rebind_pin)})==4,'terminal_independent_pins')
    store=AuthorityStore('/var/lib/maj-vaj-reserve-authority',authority_uid=auth.pw_uid,desk_uid=desk.pw_uid)
    mailbox=TerminalMailbox('/var/lib/maj-vaj-terminal-outbox',native.pw_uid,group)
    ids=dict(native_uid=native.pw_uid,authority_uid=auth.pw_uid,desk_uid=desk.pw_uid)
    workspace='installed-paper'
    if a.role=='native':
        producer=NativeProducer(workspace,'/var/lib/maj-vaj-native','/var/lib/maj-vaj-native-signing',native_pin=native_pin,human_pin=human_pin,rebind_dir=records,rebind_pin=rebind_pin,**ids)
        result=native_step(producer,'/var/lib/maj-vaj-native-inbox',store,relay_pin,native.pw_gid,mailbox)
    else:
        require(os.geteuid()==desk.pw_uid,'terminal_desk_identity')
        result=desk_step(desk_transaction('/var/lib/maj-vaj/data'),mailbox,rebound_bindings(records,rebind_pin,**ids),pinned_key(native_pin),workspace,store,desk_uid=desk.pw_uid)
    print('PAPER terminal:',result,flush=True)


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('PAPER terminal unavailable; unresolved execution held') from None
