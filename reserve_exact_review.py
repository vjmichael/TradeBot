"""Exact human review of independently derived PAPER reserve events.

No desk RPC can invoke this signer. The trusted native producer must perform
real accounting, projection/risk checks and event derivation under independent
custody before attesting. This module does not create native wallet evidence.
"""
import copy
import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path
from dataclasses import asdict
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey, Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from reserve_checkpoint import CheckpointError, require
from reserve_ipc import canonical, pinned_key, checkpoint, sign_permit, scope
from reserve_enrollment import unlock
from reserve_recovery import event_checkpoint

DOMAIN=b'maj-vaj:PAPER:native-exact-event:v1\0'
MAX_AGE=30  # matches paper_custody flat-wallet receipt age


def evidence_body(workspace,event,observed,receipt_digest):
    require(type(observed) is int and observed>0,'evidence_clock')
    require(type(receipt_digest) is str and re.fullmatch('[0-9a-f]{64}',receipt_digest),'native_receipt_digest')
    return {'workspace':scope(workspace),'event':asdict(event_checkpoint(event)),
            'head':event['previous'],'observed':observed,'receipt_digest':receipt_digest}


def check_evidence(public,workspace,event,packet,now):
    require(type(packet) is dict and set(packet)=={'body','signature'},'native_evidence_required')
    b=packet['body'];sig=packet['signature']
    require(type(b) is dict and set(b)=={'workspace','event','head','observed','receipt_digest'},'native_evidence_schema')
    require(b==evidence_body(workspace,event,b['observed'],b['receipt_digest']),'native_evidence_scope')
    require(0<=now-b['observed']<=MAX_AGE,'native_evidence_stale')
    require(type(sig) is str and re.fullmatch('[0-9a-f]{128}',sig),'native_evidence_signature_format')
    try:Ed25519PublicKey.from_public_bytes(public).verify(bytes.fromhex(sig),DOMAIN+canonical(b))
    except (InvalidSignature,ValueError) as exc:raise CheckpointError('native_evidence_signature') from exc
    return b['receipt_digest']


def review_text(event):
    from datetime import datetime, timezone
    from decimal import Decimal
    payload=event['body']['payload']['payload']
    require(type(payload) is dict and type(payload['version']) is str,'review_policy_version')
    lines=['PAPER only — no real funds or orders.','Portfolio: '+event['instance'],
           'Policy version: '+payload['version']]
    if event['kind']=='reserve_revoke':
        require(set(payload)=={'version'},'review_policy_fields')
        return '\n'.join(lines+['Revoke this policy. Future releases stop; existing releases and protective exits are unchanged.'])
    p=payload['policy'];require(set(payload)=={'version','policy'} and set(p)=={'rate_bps','floor_mode','floor_fixed','floor_bps','ceiling','cycle_cap','lifetime_cap','anchor','start','end','expiry'},'review_policy_fields')
    def money(n):return '$'+format(Decimal(n)/100_000_000,',.2f')
    def pct(n):return format(Decimal(n)/100,'.2f')+'%'
    def utc(n):return datetime.fromtimestamp(n,timezone.utc).isoformat()
    return '\n'.join(lines+['Activate the exact saved policy (replaces any current policy).',
        'Release rate: '+pct(p['rate_bps']), 'Protected floor mode: '+p['floor_mode'],
        'Fixed floor: '+money(p['floor_fixed']), 'Percentage floor: '+pct(p['floor_bps']),
        'Capital ceiling: '+money(p['ceiling']), 'Per-cycle cap: '+money(p['cycle_cap']),
        'Lifetime cap: '+money(p['lifetime_cap']), 'Daily UTC cycle: '+str(p['anchor'])+' seconds after midnight',
        'Start: '+utc(p['start']), 'End: '+utc(p['end']), 'Approval expiry: '+utc(p['expiry']),
        'No funds are released on activation. Future releases require independent financial checks.'])


class ExactReview:
    """Trusted local process only; revalidate must independently derive the event.

    Revocation requires exact independently validated state and human consent,
    but not fresh wallet evidence: stale observations cannot obstruct stopping.
    Unattended ticks are deliberately not authorized by this human key path.
    """
    def __init__(self,workspace,credentials,*,authority_uid,desk_uid,permit_pin,native_pin,revalidate,clock=time.time,monotonic=time.monotonic):
        self.workspace=scope(workspace);self.credentials=Path(credentials)
        self.ids=dict(authority_uid=authority_uid,desk_uid=desk_uid)
        self.pin=Path(permit_pin);self.native_pin=Path(native_pin);self.revalidate=revalidate
        self.clock,self.monotonic=clock,monotonic;self.pending=None

    def cancel(self):self.pending=None

    def _check(self,event,evidence,now):
        cp=event_checkpoint(event)
        require(event['kind'] in ('reserve_adopt','reserve_revoke'),'human_action_required')
        public=pinned_key(self.pin);receipt=None
        if event['kind']=='reserve_adopt':
            native=pinned_key(self.native_pin)
            require(public!=native,'independent_native_key_required')
            receipt=check_evidence(native,self.workspace,event,evidence,now)
        else:require(evidence is None,'unexpected_revocation_evidence')
        return public,cp,receipt

    def begin(self,previous,event,evidence):
        self.cancel();now=int(self.clock());_,cp,receipt=self._check(event,evidence,now)
        require(cp.seq>previous.seq,'permit_sequence')
        exact=copy.deepcopy(event);proof=copy.deepcopy(evidence)
        # Both review AND confirmation compare bytes derived independently by
        # the trusted caller; never just sign a desk-supplied digest.
        require(self.revalidate(previous,copy.deepcopy(exact),copy.deepcopy(proof)) is True,'independent_event_validation_required')
        challenge=('REVOKE ' if event['kind']=='reserve_revoke' else 'APPROVE ')+secrets.token_hex(8)
        display=review_text(exact)
        self.pending=(previous,exact,proof,challenge,now,self.monotonic()+300)
        return {'challenge':challenge,'event':copy.deepcopy(exact),'native_receipt_digest':receipt,'display':display}

    def confirm(self,challenge,password):
        pending=self.pending;self.cancel();require(pending is not None,'review_missing_or_consumed')
        prev,event,evidence,expected,started,deadline=pending
        now=int(self.clock());require(started<=now<=started+300 and self.monotonic()<=deadline,'review_expired')
        require(type(challenge) is str and hmac.compare_digest(challenge,expected),'confirmation_mismatch')
        seed=unlock(self.credentials,password,**self.ids)
        public,cp,_=self._check(event,evidence,int(self.clock()))
        require(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)==public,'permit_pin_mismatch')
        require(self.revalidate(prev,copy.deepcopy(event),copy.deepcopy(evidence)) is True,'independent_event_validation_required')
        now=int(self.clock());require(started<=now<=started+300 and self.monotonic()<=deadline,'review_expired')
        _,_,_=self._check(event,evidence,now)
        expiry=now+30
        if evidence is not None:expiry=min(expiry,evidence['body']['observed']+MAX_AGE)
        require(expiry>now,'native_evidence_expired')
        return {'event':event,'permit':sign_permit(seed,self.workspace,prev,cp,expiry)}


class ExactReviewWindow:
    """Native Tk component; the trusted authority owns the result callback.

    This is not an installed launcher and does not write the desk or expose a
    remote approval endpoint. The embedding broker must supply trusted events.
    """
    def __init__(self,root,review,view,on_authorized):
        import tkinter as tk
        from tkinter import ttk
        self.root,self.review,self.on_authorized=root,review,on_authorized
        root.title('Maj Vaj — Review PAPER reserve authorization')
        self.text=tk.Text(root,height=18,width=80,wrap='word');self.text.pack(fill='both',expand=True,padx=12,pady=8)
        self.text.insert('1.0',view['display']+'\n\n'+('Native evidence verified for this review.' if view['native_receipt_digest'] else 'Revocation does not require fresh wallet evidence.'))
        self.text.configure(state='disabled')
        ttk.Label(root,text='Type: '+view['challenge']).pack(anchor='w',padx=12)
        self.challenge=tk.StringVar();ttk.Entry(root,textvariable=self.challenge).pack(fill='x',padx=12)
        ttk.Label(root,text='Approval passphrase').pack(anchor='w',padx=12)
        self.password=tk.StringVar();ttk.Entry(root,textvariable=self.password,show='*').pack(fill='x',padx=12)
        self.status=tk.StringVar(value='No change made.');ttk.Label(root,textvariable=self.status).pack(anchor='w',padx=12)
        self.confirm_button=ttk.Button(root,text='Confirm exact operation',command=self.confirm);self.confirm_button.pack(pady=6)
        ttk.Button(root,text='Cancel',command=self.cancel).pack(pady=6)
        root.protocol('WM_DELETE_WINDOW',self.cancel)

    def confirm(self):
        password=self.password.get();challenge=self.challenge.get();self.password.set('');self.challenge.set('');self.confirm_button.configure(state='disabled')
        try:
            packet=self.review.confirm(challenge,password)
        except (ValueError,OSError,KeyError,TypeError):self.status.set('Approval refused. No new permit issued.');return
        try:self.on_authorized(packet)
        except (ValueError,OSError,KeyError,TypeError):self.status.set('Delivery not confirmed. Reconcile before retrying.');return
        self.status.set('Exact permit issued. Transaction result is not yet confirmed.')

    def cancel(self):
        self.review.cancel();self.password.set('');self.challenge.set('');self.root.destroy()
