"""Staged local reviewed PAPER recovery. No public endpoint or activation."""
import argparse
import grp
import os
import pwd
from pathlib import Path
from reserve_checkpoint import AuthorityStore, require
from reserve_ipc import pinned_key
from reserve_terminal_transport import TerminalMailbox
from reserve_native_evidence import NativeProducer
from reserve_native_wallet import rebound_bindings
from reserve_no_effect import Review, consume, produce


def review_window(root,review,proof,publish):
    from tkinter import ttk, StringVar, Text
    v=review.begin(proof);root.title('Maj Vaj — Review interrupted PAPER order')
    text=Text(root,height=12,width=76,wrap='word');text.pack(fill='both',expand=True,padx=12,pady=8)
    text.insert('1.0',v['display']);text.configure(state='disabled')
    challenge,password,status=StringVar(),StringVar(),StringVar(value='No change made. Trading remains paused.')
    ttk.Label(root,text='Type: '+v['challenge']).pack(anchor='w',padx=12)
    ttk.Entry(root,textvariable=challenge).pack(fill='x',padx=12)
    ttk.Label(root,text='Approval passphrase').pack(anchor='w',padx=12)
    ttk.Entry(root,textvariable=password,show='*').pack(fill='x',padx=12)
    ttk.Label(root,textvariable=status,wraplength=580).pack(padx=12,pady=8)
    def cancel():review.cancel();password.set('');challenge.set('');root.destroy()
    def confirm():
        pw,answer=password.get(),challenge.get();password.set('');challenge.set('');button.configure(state='disabled')
        try:approved=review.confirm(answer,pw)
        except (ValueError,OSError,KeyError,TypeError):status.set('Approval refused. No reservation released.');return
        try:publish(approved)
        except (ValueError,OSError,KeyError,TypeError):status.set('Delivery not confirmed. No retry until reviewed.');return
        status.set('Approval recorded. Await reconciliation. Trading remains paused.')
    button=ttk.Button(root,text='Confirm release of unused reservation',command=confirm);button.pack(pady=4)
    close=ttk.Button(root,text='Close',command=cancel);close.pack(pady=4)
    root.protocol('WM_DELETE_WINDOW',cancel)
    return dict(confirm=button,close=close,challenge=challenge,password=password,status=status,review=v)


def desk_once(transaction,proofbox,approvalbox,bindings,native,human,workspace,store,*,clock=None):
    proof,review=proofbox.read(),approvalbox.read()
    if review is None:return 'review_required' if proof is not None else 'idle'
    require(proof is not None and review.get('proof')==proof,'recovery_delivery_mismatch')
    return consume(transaction,review,bindings,native,human,workspace,store=store,**({} if clock is None else {'clock':clock}))


def main(*,factory=None,window=review_window):
    from reserve_runtime import desk_transaction
    from reserve_entry_fence import open_fence,LOCK
    from reserve_terminal_worker import native_driver_context
    factory=factory or native_driver_context
    from reserve_recovery_archive import native_step as archive_native, authority_step, publish_review
    p=argparse.ArgumentParser();p.add_argument('role',choices=('native','review','desk'));a=p.parse_args()
    marker=Path('/etc/maj-vaj/reviewed-recovery-enabled')
    require(marker.is_file() and not marker.is_symlink() and marker.stat().st_uid==0 and not marker.stat().st_mode&0o022,'recovery_disabled')
    desk,native,auth=(pwd.getpwnam(x) for x in ('maj-vaj','maj-vaj-native','maj-vaj-approval'))
    group=grp.getgrnam('maj-vaj-recovery').gr_gid
    require(len({desk.pw_uid,native.pw_uid,auth.pw_uid})==3 and group>0 and group in {os.getegid(),*os.getgroups()} and group not in {desk.pw_gid,native.pw_gid,auth.pw_gid},'recovery_roles')
    require(os.geteuid()=={'desk':desk.pw_uid,'native':native.pw_uid,'review':auth.pw_uid}[a.role],'recovery_service_identity')
    if a.role!='review':os.close(open_fence(LOCK))
    pins=Path('/etc/maj-vaj');np=pins/'reserve-native.pub';hp=pins/'reserve-checkpoint.pub';rp=pins/'reserve-rebind.pub';sp=pins/'reserve-relay.pub';records=pins/'reserve-rebind'
    require(len({pinned_key(x) for x in (np,hp,rp,sp)})==4,'recovery_independent_pins')
    ids=dict(desk_uid=desk.pw_uid,native_uid=native.pw_uid,authority_uid=auth.pw_uid);w='installed-paper'
    store=AuthorityStore('/var/lib/maj-vaj-reserve-authority',authority_uid=auth.pw_uid,desk_uid=desk.pw_uid)
    proofbox=TerminalMailbox('/var/lib/maj-vaj-recovery-native',native.pw_uid,group)
    approvalbox=TerminalMailbox('/var/lib/maj-vaj-recovery-human',auth.pw_uid,group)
    if a.role=='native':
        producer=NativeProducer(w,'/var/lib/maj-vaj-native','/var/lib/maj-vaj-native-signing',native_pin=np,human_pin=hp,rebind_dir=records,rebind_pin=rp,**ids)
        outcome=archive_native(producer,'/var/lib/maj-vaj-native-inbox',store,sp,native.pw_gid,proofbox,approvalbox)
        if outcome!='idle':print(outcome,flush=True);return
        # Cold startup happens BEFORE reading the short-lived signed snapshot.
        # The warm service must have been explicitly stopped by the operator.
        from reserve_persistent_worker import WarmRuntime
        with WarmRuntime(producer,sp,factory).start() as warm:
            proofbox.publish(produce(producer,'/var/lib/maj-vaj-native-inbox',store,sp,native.pw_gid,factory=warm.borrow,lease=warm.lease))
    elif a.role=='review':
        outcome=authority_step(proofbox,approvalbox,pinned_key(np),pinned_key(hp),w)
        if outcome!='idle':print(outcome,flush=True);return
        proof=proofbox.read();require(proof is not None and approvalbox.read() is None,'recovery_no_pending_review')
        review=Review(w,'/var/lib/maj-vaj-reserve-approval',np,hp,authority_uid=auth.pw_uid,desk_uid=desk.pw_uid)
        import tkinter as tk
        root=tk.Tk();window(root,review,proof,lambda approved:publish_review(proofbox,approvalbox,proof,approved));root.mainloop()
    else:
        print(desk_once(desk_transaction('/var/lib/maj-vaj/data'),proofbox,approvalbox,rebound_bindings(records,rp,**ids),pinned_key(np),pinned_key(hp),w,store),flush=True)

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('PAPER recovery unavailable; unresolved reservation held') from None
