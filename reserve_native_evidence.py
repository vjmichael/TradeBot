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
                 native_pin, human_pin, clock=time.time):
        require(all(type(u) is int and u>0 for u in (native_uid,authority_uid,desk_uid)) and
                len({native_uid,authority_uid,desk_uid})==3 and os.geteuid()==native_uid,
                'independent_native_principal_required')
        self.workspace=scope(workspace);self.data=Path(native_data);self.credentials=Path(key_dir)
        self.uid=native_uid;self.native_pin=Path(native_pin);self.human_pin=Path(human_pin);self.clock=clock

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
            installed_workspace(self.data,i,db)
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
