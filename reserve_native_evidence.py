"""Independent native PAPER event producer; no desk-facing signing RPC.

The native service must own its real provisioned workspaces and wallet receipt
keys. Current legacy desk-owned workspaces are deliberately not accepted.
This is a trusted local composition point, not a replacement wallet engine.
"""
import os
import json
import stat
import time
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from reserve_checkpoint import require
from reserve_ipc import protected_dir, pinned_key, canonical, scope, sign_permit
from reserve_exact_review import evidence_body, DOMAIN, MAX_AGE
from reserve_recovery import event_checkpoint
from reserve_protected import derive_event


class NativeProducer:
    def __init__(self, workspace, native_data, key_dir, *, native_uid, authority_uid, desk_uid,
                 native_pin, human_pin, rebind_dir=None, rebind_pin=None, clock=time.time):
        require(all(type(u) is int and u>0 for u in (native_uid,authority_uid,desk_uid)) and
                len({native_uid,authority_uid,desk_uid})==3 and os.geteuid()==native_uid,
                'independent_native_principal_required')
        self.workspace=scope(workspace);self.data=Path(native_data);self.credentials=Path(key_dir)
        self.uid=native_uid;self.desk_uid=desk_uid;self.authority_uid=authority_uid;self.native_pin=Path(native_pin);self.human_pin=Path(human_pin);self.clock=clock
        require((rebind_dir is None)==(rebind_pin is None),'rebind_configuration_required')
        self.rebind_dir=Path(rebind_dir) if rebind_dir is not None else None
        self.rebind_pin=Path(rebind_pin) if rebind_pin is not None else None

    def _key(self):
        with protected_dir(self.credentials,self.uid,0o700) as fd:
            raw=os.open('native-ed25519.key',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
            with os.fdopen(raw,'rb') as f:
                s=os.fstat(f.fileno())
                require(stat.S_ISREG(s.st_mode) and s.st_uid==self.uid and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and s.st_size==32,'native_signer_custody')
                key=f.read(32)
        public=pinned_key(self.native_pin)
        require(public!=pinned_key(self.human_pin),'independent_native_key_required')
        require(Ed25519PrivateKey.from_private_bytes(key).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)==public,'native_pin_mismatch')
        return key

    def propose(self,db,previous,instance,action,receipt,*,recorded_at=None):
        """Only called by trusted native code holding an independently reviewed
        ledger snapshot and native execution lease; never expose to desk RPC.
        Native receipt authentication uses existing actual wallet/provision
        bindings and keys, not caller-provided totals or a desk-owned key.
        """
        require(action in ('adopt','tick'),'native_action_refused')
        require(os.geteuid()==self.uid,'native_identity_changed')
        with protected_dir(self.data,self.uid,0o700):pass
        key=self._key(); now=int(self.clock()); at=now if recorded_at is None else recorded_at
        from paper_custody import authenticated_projection, workspace_key
        from paper_provision import installed_workspace
        # Each native workspace must be owned by this identity and bound to
        # independent provision receipts. Legacy desk-owned files cannot pass.
        def keys(i):
            if self.rebind_dir is None:
                installed_workspace(self.data,i,db)
            else:
                from reserve_native_rebind import verify
                require(pinned_key(self.rebind_pin) not in (pinned_key(self.native_pin),pinned_key(self.human_pin)),
                        'independent_rebind_pin_required')
                verify(db,self.data,self.rebind_dir,self.rebind_pin,i,native_uid=self.uid,
                       desk_uid=self.desk_uid,authority_uid=self.authority_uid)
            return workspace_key(self.data,i)
        projection=authenticated_projection(keys)
        verified=[]
        def verify(d,t):
            risk,digest=projection(d,t);verified.append((risk,digest))
        e=derive_event(db,previous,instance,action,receipt,at,now,verify)
        require(len(verified)==1,'native_projection_missing')
        risk,_=verified[0]
        raw=db.execute("SELECT body FROM fin_events ORDER BY seq DESC LIMIT 1").fetchone()[0]
        digest=json.loads(raw)['payload']['evidence_digest']
        observed=risk.observed
        body=evidence_body(self.workspace,e,observed,digest)
        end=min(observed+MAX_AGE,at+30)
        require(end>int(self.clock()),'native_evidence_expired')
        if action=='adopt':
            return {'event':e,'evidence':{'body':body,'signature':Ed25519PrivateKey.from_private_bytes(key).sign(DOMAIN+canonical(body)).hex()}}
        return {'event':e,'permit':sign_permit(key,self.workspace,previous,event_checkpoint(e),end)}

    def propose_from_handoff(self, directory, store, relay_pin, native_gid, instance, action, receipt):
        """Trusted local composition of signed snapshot + independent workspace leases.

        No live snapshot publisher or worker transfer is implied. All native
        enclosing directories must be independently owned; existing desk-owned
        provisioning markers require an authenticated rebinding.
        """
        from contextlib import ExitStack
        from paper_custody import workspace
        from paper_supervisor import custody
        from reserve_native_handoff import open_reviewed
        require(pinned_key(relay_pin) not in (pinned_key(self.native_pin),pinned_key(self.human_pin)), 'independent_relay_pin_required')
        with open_reviewed(directory,self.workspace,store,relay_pin,native_uid=self.uid,native_gid=native_gid,desk_uid=self.desk_uid,authority_uid=self.authority_uid,clock=self.clock) as (db,previous):
            with ExitStack() as leases:
                rows=db.execute('SELECT id FROM instances ORDER BY id').fetchall()
                require(rows and len(rows)<=128,'native_scope_bound')
                for (i,) in rows:leases.enter_context(custody(workspace(self.data,i)))
                packet=self.propose(db,previous,instance,action,receipt)
        return packet
