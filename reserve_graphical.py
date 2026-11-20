"""Independent local PAPER approval window. No HTTP/password forwarding or orders."""
import argparse
import copy
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import nullcontext
from pathlib import Path

from financial_ledger import canon, replay, require, AccountingError
from reserve_runtime import desk_transaction, private_directory, unlock
from reserve_policy import ReserveManager
from reserve_preview import read, policy_view, preview
from financial_goals import scope, usd


class GraphicalBroker:
    """One exact, expiring review. UI owns password; dashboard only writes drafts."""
    def __init__(self, transaction, credentials, verify, *, clock=time.time, monotonic=time.monotonic):
        self.tx=transaction; self.credentials=credentials; self.verify=verify
        self.clock=clock; self.monotonic=monotonic; self.pending=None

    def portfolios(self):
        with self.tx() as db:
            return [(r[0],r[1],r[2]) for r in db.execute("SELECT id,name,pair FROM instances WHERE mode='PAPER' ORDER BY name,id")]

    def status(self,instance):
        with self.tx() as db:return read(db,instance,int(self.clock()))[-1]

    def cancel(self):self.pending=None

    def _revocation(self,db,instance):
        # Draft corruption or stale wallet evidence must not prevent OFF.
        scope(db,instance); states,_,_,head=replay(db); s=states.get(instance)
        require(s is not None,'unfunded_instance')
        return s,head,dict(protected_cash_usd=usd(s['protected']),active_cash_usd=usd(s['active']))

    def review(self,instance,action):
        self.cancel(); require(action in ('adopt','revoke'),'invalid_human_action')
        with self.tx() as db:
            now=int(self.clock())
            if action=='adopt':rows,states,s,head,disputed,view=read(db,instance,now)
            else:s,head,view=self._revocation(db,instance)
            require(s is not None,'unfunded_instance')
            active=s['reserve']['active'];prior=active['version'] if active else None
            if action=='adopt':
                require(rows and not disputed,'review_unavailable')
                r=rows[-1];p=r['policy'];require(now<min(p['end'],p['expiry']),'expired_draft')
                require(r['id'] not in s['reserve']['activated'],'policy_already_activated')
                self.verify(db,now)
                ReserveManager(lambda:nullcontext(db),clock=lambda:now)._limits(db,states,now)
                payload={'version':r['id'],'policy':p};version=r['version'];digest=r['digest']
                evidence=preview(lambda:nullcontext(db),instance,p,0,clock=lambda:now,verify=self.verify)
            else:
                require(active is not None,'reinvestment_off')
                payload={'version':prior};version=digest=None;evidence=None
            boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
            receipt=secrets.token_hex(16)
            v=dict(instance=instance,action=action,payload=payload,prior=prior,head=head,boot=boot,
                   draft_version=version,draft_digest=digest,receipt=receipt,created=now,expires=now+300,
                   confirmation=('REVOKE ' if action=='revoke' else 'APPROVE ')+receipt,view=view,preview=evidence)
            self.pending=(copy.deepcopy(v),self.monotonic()+300)
            return v

    def confirm(self,receipt,confirmation,password):
        pending=self.pending;self.cancel();now=int(self.clock())
        require(pending is not None,'review_expired_or_consumed');r,deadline=pending
        require(r['receipt']==receipt and r['created']<=now<=r['expires'] and self.monotonic()<=deadline,'review_expired_or_consumed')
        require(type(confirmation) is str and hmac.compare_digest(confirmation,r['confirmation']),'confirmation_mismatch')
        key=unlock(self.credentials,password)
        with self.tx() as db:
            now=int(self.clock());require(r['created']<=now<=r['expires'] and self.monotonic()<=deadline,'review_expired_or_consumed')
            i=r['instance'];action=r['action'];payload=r['payload']
            if action=='adopt':rows,_,s,head,disputed,_=read(db,i,now)
            else:s,head,_=self._revocation(db,i)
            require(s is not None,'unfunded_instance')
            active=s['reserve']['active'];require((active['version'] if active else None)==r['prior'],'active_policy_changed')
            if action=='adopt':
                require(not disputed and rows and rows[-1]['version']==r['draft_version'] and rows[-1]['digest']==r['draft_digest'] and rows[-1]['id']==payload['version'] and rows[-1]['policy']==payload['policy'],'draft_changed')
                require(head==r['head'] and db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]==r['boot'],'review_changed')
            # Revocation is restrictive and can survive intervening observations.
            # The exact active version must still match. All checks and commit
            # occur under the same desk transaction, racing ticks serially.
            grant={'head':head,'expiry':r['expires']}
            msg=canon(['maj-vaj:PAPER:reserve:v1',receipt,i,action,payload,head,r['expires']]).encode()
            grant['signature']=hmac.new(key,msg,hashlib.sha256).hexdigest()
            result=ReserveManager(lambda:nullcontext(db),key,clock=lambda:now,verify=self.verify).submit(receipt,i,action,payload,grant)
            result['state']=self._verified_status(db,i,now,key)
            return result

    def _verified_status(self,db,instance,now,key):
        states,_,_,_=replay(db);s=states.get(instance);require(s is not None,'unfunded_instance')
        active=s['reserve']['active']
        if active is None:return 'off'
        policy=active['policy'];version=active['version']
        receipt=db.execute("SELECT id,kind,body,previous FROM fin_events WHERE instance=? AND kind IN ('reserve_activate','reserve_adopt') AND json_extract(body,'$.payload.payload.version')=?",(instance,version)).fetchall()
        require(len(receipt)==1,'approval_receipt_missing')
        eid,kind,raw,head=receipt[0];event=json.loads(raw);grant=event['approval'];payload=event['payload']['payload']
        require(payload==active and grant['head']==head and event['recorded_at']<=min(grant['expiry'],now),'approval_receipt_scope')
        msg=canon(['maj-vaj:PAPER:reserve:v1',eid,instance,kind.removeprefix('reserve_'),payload,head,grant['expiry']]).encode()
        require(hmac.compare_digest(hmac.new(key,msg,hashlib.sha256).hexdigest(),grant['signature']),'approval_receipt_signature')
        return 'expired' if now>=min(policy['end'],policy['expiry']) else 'scheduled' if now<policy['start'] else 'on'

    def verify_status(self,instance,password):
        key=unlock(self.credentials,password)
        with self.tx() as db:return self._verified_status(db,instance,int(self.clock()),key)


def review_lines(r):
    from datetime import datetime,timezone
    def utc(n):return datetime.fromtimestamp(n,timezone.utc).isoformat()
    v=r['view']; lines=['PAPER only — no real funds or orders.', 'Portfolio: '+r['instance'],
        'Action: '+('Revoke current policy' if r['action']=='revoke' else 'Replace current policy' if r['prior'] else 'Activate saved policy'),
        'Current policy: '+str(r['prior'] or 'OFF'), 'Protected profit: '+str(v['protected_cash_usd']), 'Active cash: '+str(v['active_cash_usd'])]
    if r['action']=='adopt':
        p=policy_view(r['payload']['policy']);lines+=['Saved version: '+str(r['draft_version']), 'Release rate: '+str(p['rate_bps']/100)+'%', 'Floor: '+p['floor_mode'], 'Fixed floor: $'+format(p['floor_fixed']/100,'.2f'), 'Percentage floor: '+str(p['floor_bps']/100)+'%', 'Maximum capital: $'+format(p['ceiling']/100,'.2f'), 'Per-cycle cap: $'+format(p['cycle_cap']/100,'.2f'), 'Lifetime cap: $'+format(p['lifetime_cap']/100,'.2f'), 'Daily UTC anchor: '+str(p['anchor'])+' seconds after midnight', 'Start: '+utc(p['start']), 'End: '+utc(p['end']), 'Expiry: '+utc(p['expiry']), 'Existing surplus excluded; future releases require fresh evidence. No guaranteed returns.']
        a=r['preview']['actual'];lines+=['Release on activation: $0.00', 'Current protected floor: '+str(a['floor_usd'] if a else 'Unavailable'), 'Current desk capital room: '+str(a['desk_room_usd'] if a else 'Unavailable')]
    else:lines+=['Stop future releases only. Previously released funds and protective exits are unchanged.']
    return '\n'.join(lines)


class ApprovalWindow:
    def __init__(self,root,broker):
        import tkinter as tk
        from tkinter import ttk
        self.root=root;self.broker=broker;self.r=None;self.ids={};self.selection=tk.StringVar();self.confirmation=tk.StringVar();self.password=tk.StringVar();self.message=tk.StringVar(value='Choose a paper portfolio.')
        root.title('Maj Vaj — separate reinvestment approval');root.geometry('740x680');root.protocol('WM_DELETE_WINDOW',self.close)
        frame=ttk.Frame(root,padding=14);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Reinvestment approval — PAPER',font=('TkDefaultFont',16,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Separate local approval. Do not enter your passphrase in the ordinary dashboard.',wraplength=680).pack(anchor='w',pady=5)
        self.combo=ttk.Combobox(frame,textvariable=self.selection,state='readonly');self.combo.pack(fill='x');self.combo.bind('<<ComboboxSelected>>',lambda _:self.refresh())
        buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=7)
        self.on=ttk.Button(buttons,text='Review activation / replacement',command=lambda:self.begin('adopt'));self.on.pack(side='left')
        self.off=ttk.Button(buttons,text='Review OFF / revoke',command=lambda:self.begin('revoke'));self.off.pack(side='left')
        ttk.Button(buttons,text='Refresh',command=self.refresh).pack(side='left')
        self.details=tk.Text(frame,height=17,wrap='word',state='disabled');self.details.pack(fill='both',expand=True)
        self.challenge=ttk.Label(frame,text='');self.challenge.pack(anchor='w')
        ttk.Label(frame,text='Type exact confirmation').pack(anchor='w');ttk.Entry(frame,textvariable=self.confirmation).pack(fill='x')
        ttk.Label(frame,text='Separate approval passphrase').pack(anchor='w');ttk.Entry(frame,textvariable=self.password,show='*').pack(fill='x')
        row=ttk.Frame(frame);row.pack(fill='x',pady=6)
        self.commit=ttk.Button(row,text='Confirm reviewed action',command=self.finish,state='disabled');self.commit.pack(side='left')
        ttk.Button(row,text='Cancel review',command=self.refresh).pack(side='left')
        ttk.Button(row,text='Verify current authorization',command=self.verify_current).pack(side='left')
        ttk.Label(frame,textvariable=self.message,wraplength=680).pack(anchor='w')
        self.refresh()

    def display(self,text):
        self.details.configure(state='normal');self.details.delete('1.0','end');self.details.insert('1.0',text);self.details.configure(state='disabled')

    def clear(self):
        self.broker.cancel();self.r=None;self.confirmation.set('');self.password.set('');self.challenge.configure(text='');self.commit.configure(state='disabled')

    def refresh(self):
        self.clear()
        try:
            self.ids={f'{name} · {pair} · {i}':i for i,name,pair in self.broker.portfolios()};self.combo.configure(values=list(self.ids))
            if self.selection.get() not in self.ids:self.selection.set(next(iter(self.ids),''))
            if not self.ids:self.display('No paper portfolios.');return
            v=self.broker.status(self.ids[self.selection.get()]);self.display('Recorded state: '+v['current_state']+'\nSaved draft version: '+str(v['version'])+'\nProtected: '+str(v['protected_cash_usd'])+'\nActive cash: '+str(v['active_cash_usd']))
            self.message.set('Review the exact saved settings or request revocation. Recorded status is not a verified ON indication.')
        except (ValueError,OSError,KeyError,sqlite3.Error):self.display('Records unavailable.');self.message.set('No change made.')

    def begin(self,action):
        self.clear()
        try:
            r=self.broker.review(self.ids[self.selection.get()],action);self.r=r;self.display(review_lines(r));self.challenge.configure(text=r['confirmation']);self.commit.configure(state='normal');self.message.set('Review expires in five minutes or when evidence changes. No change yet.')
        except (ValueError,OSError,KeyError,sqlite3.Error):self.message.set('Review unavailable. Check financial evidence and saved settings.')

    def finish(self):
        if not self.r:return
        r=self.r;password=self.password.get();confirmation=self.confirmation.get();self.password.set('');self.confirmation.set('');self.commit.configure(state='disabled');self.r=None;self.challenge.configure(text='')
        try:
            result=self.broker.confirm(r['receipt'],confirmation,password);self.refresh();self.message.set('Confirmed '+result['state'].upper()+' at this check. Receipt '+result['receipt']+'. No funds moved.')
        except (ValueError,OSError,KeyError,sqlite3.Error):self.refresh();self.message.set('Action not confirmed. Refresh/review; no new authority was confirmed.')

    def verify_current(self):
        password=self.password.get();self.clear()
        try:self.message.set('Verified '+self.broker.verify_status(self.ids[self.selection.get()],password).upper()+' at this check. Refresh before acting.')
        except (ValueError,OSError,KeyError,sqlite3.Error):self.message.set('Authorization unknown. No change made.')

    def close(self):self.clear();self.root.destroy()


def main():
    import tkinter as tk
    from paper_custody import authenticated_projection, workspace_key
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('data_dir',type=Path);p.add_argument('--credentials',required=True,type=Path);a=p.parse_args()
    data=private_directory(a.data_dir);creds=private_directory(a.credentials)
    require(not creds.resolve().is_relative_to(data.resolve()) and not data.resolve().is_relative_to(creds.resolve()),'separate_approval_directory_required')
    broker=GraphicalBroker(desk_transaction(data),creds,authenticated_projection(lambda i:workspace_key(data,i)))
    root=tk.Tk();ApprovalWindow(root,broker);root.mainloop()

if __name__=='__main__':main()
