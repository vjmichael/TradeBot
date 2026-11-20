"""Staged independent native entry composition. No public API or activation."""
import os
import pwd
from pathlib import Path
from reserve_checkpoint import AuthorityStore,require
from reserve_entry_broker import connection,execute,listener,serve_request
from reserve_entry_fence import DATA,LOCK,MARKER,open_fence
from reserve_native_handoff import open_reviewed
from reserve_native_wallet import rebound_bindings,projection
from reserve_ipc import pinned_key
from risk_gate import Gate


def native_once(producer,inbox,store,relay_pin,native_gid,socket_dir,lock,*,factory,connect=connection):
    from reserve_native_rebind import verify
    from paper_custody import workspace,workspace_key,AuthenticatedNativeDriver
    from paper_supervisor import custody
    import hmac
    require(producer.rebind_dir is not None,'entry_rebind_required')
    require(len({pinned_key(x) for x in (relay_pin,producer.native_pin,producer.human_pin,producer.rebind_pin)})==4,'entry_independent_pins')
    with open_reviewed(inbox,producer.workspace,store,relay_pin,native_uid=producer.uid,native_gid=native_gid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid,clock=producer.clock) as (db,_):
        rows=db.execute('SELECT id,pair,virtual_cents,mode FROM instances').fetchall()
        require(len(rows)==1 and rows[0][3]=='PAPER','entry_single_workspace')
        instance,pair,cents,_=rows[0]
        candidates=db.execute("SELECT id FROM gate_intents WHERE instance_id=? AND state='AUTHORIZED'",(instance,)).fetchall()
        if not candidates:return 'idle'
        require(len(candidates)==1,'entry_multiple_authorizations')
        intent=candidates[0][0]
        work=workspace(producer.data,instance)
        with custody(work):
            b=verify(db,producer.data,producer.rebind_dir,producer.rebind_pin,instance,native_uid=producer.uid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid)
            key,binding=workspace_key(producer.data,instance)
            with factory(instance,pair,cents,work) as driver:
                require(isinstance(driver,AuthenticatedNativeDriver) and driver.binding==binding==b['binding'] and hmac.compare_digest(key,driver.key) and driver.path==work/'paper.sqlite','entry_native_driver')
                with connect(socket_dir,producer.desk_uid) as conn:
                    return execute(conn,driver,intent,work,lock,desk_uid=producer.desk_uid,native_uid=producer.uid,pair=pair,instance=instance,clock=producer.clock)


def installed_gate(transaction):
    """Use public native wallet receipts; do not access the old workspace keys."""
    from goal_control import entry_guard
    from risk_gate import GateRefusal
    desk,native,auth=(pwd.getpwnam(x) for x in ('maj-vaj','maj-vaj-native','maj-vaj-approval'))
    require(os.geteuid()==desk.pw_uid and len({desk.pw_uid,native.pw_uid,auth.pw_uid})==3,'entry_desk_identity')
    pins=Path('/etc/maj-vaj');np=pins/'reserve-native.pub';hp=pins/'reserve-checkpoint.pub';rp=pins/'reserve-rebind.pub';sp=pins/'reserve-relay.pub'
    require(len({pinned_key(x) for x in (np,hp,rp,sp)})==4,'entry_independent_pins')
    ids=dict(desk_uid=desk.pw_uid,native_uid=native.pw_uid,authority_uid=auth.pw_uid)
    store=AuthorityStore('/var/lib/maj-vaj-reserve-authority',authority_uid=auth.pw_uid,desk_uid=desk.pw_uid)
    bindings=rebound_bindings(pins/'reserve-rebind',rp,**ids)
    project=projection(bindings,pinned_key(np),'installed-paper')
    def verified(db,now):
        try:store.verify(db)
        except (OSError,ValueError,KeyError,TypeError) as exc:raise GateRefusal('entry_witness_unavailable') from exc
        return project(db,now)
    return Gate(transaction,account_projection=verified,entry_guard=entry_guard(None,verified)),bindings,store


def main():
    import argparse
    from reserve_runtime import _transaction,private_directory
    from reserve_native_evidence import NativeProducer
    from reserve_terminal_worker import native_driver_context
    p=argparse.ArgumentParser();p.add_argument('role',choices=('desk','native'));a=p.parse_args()
    require(MARKER.is_file() and not MARKER.is_symlink() and MARKER.stat().st_uid==0 and not MARKER.stat().st_mode&0o022,'entry_not_enabled')
    os.close(open_fence(LOCK))
    desk,native,auth=(pwd.getpwnam(x) for x in ('maj-vaj','maj-vaj-native','maj-vaj-approval'))
    require(len({desk.pw_uid,native.pw_uid,auth.pw_uid})==3 and min(desk.pw_uid,native.pw_uid,auth.pw_uid)>0,'entry_principals')
    ids=dict(desk_uid=desk.pw_uid,native_uid=native.pw_uid,authority_uid=auth.pw_uid)
    pins=Path('/etc/maj-vaj');np=pins/'reserve-native.pub';hp=pins/'reserve-checkpoint.pub';rp=pins/'reserve-rebind.pub';sp=pins/'reserve-relay.pub';records=pins/'reserve-rebind'
    require(len({pinned_key(x) for x in (np,hp,rp,sp)})==4,'entry_independent_pins')
    store=AuthorityStore('/var/lib/maj-vaj-reserve-authority',authority_uid=auth.pw_uid,desk_uid=desk.pw_uid)
    sock='/run/maj-vaj-entry';w='installed-paper'
    if a.role=='desk':
        require(os.geteuid()==desk.pw_uid,'entry_desk_identity')
        # Outer broker fence covers these internal transactions.
        path=private_directory(DATA)/'desk.sqlite3';tx=lambda:_transaction(path)
        gate,bindings,store=installed_gate(tx)
        with listener(sock,desk.pw_uid) as server:
            while True:
                conn,_=server.accept()
                with conn:
                    try:serve_request(conn,tx,gate,bindings,store,LOCK,native_uid=native.pw_uid,desk_uid=desk.pw_uid)
                    except Exception:pass  # fixed refusal by connection close
    else:
        require(os.geteuid()==native.pw_uid,'entry_native_identity')
        producer=NativeProducer(w,'/var/lib/maj-vaj-native','/var/lib/maj-vaj-native-signing',native_pin=np,human_pin=hp,rebind_dir=records,rebind_pin=rp,**ids)
        print('PAPER entry:',native_once(producer,'/var/lib/maj-vaj-native-inbox',store,sp,native.pw_gid,sock,LOCK,factory=native_driver_context))

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('PAPER entry unavailable; unresolved authority held') from None
