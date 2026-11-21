"""Private authoritative vault execution coordinator — not an installed RPC.

The ONLY permissible future operator surface is three fixed no-argument
actions: prepare, cancel, finish the Kraken READ-ONLY synthetic check.
Even those methods remain disabled until installed vault handler,
native UI, broker and independent worker pass same-SHA qualification.
No caller-specified request ID, role, endpoint, credentials, nonce or
result can enter. The real vault data and worker signing key never
leave the dedicated service.
"""
from __future__ import annotations
import threading
import time
from approval_ledger import Context,Refused
from managed_paper_vault import ManagedPaperVault,_require_owner
from owner_synthetic_bridge import broker_request,reserve_after_approval,finish_fixed_synthetic


class FixedReadOnlyCoordinator:
    def __init__(self,managed:ManagedPaperVault, *,clock=time.time):
        _require_owner()
        if type(managed) is not ManagedPaperVault or not callable(clock):
            raise Refused('managed_vault_required')
        self._managed=managed
        self._clock=clock
        self._lock=threading.RLock()
        self._pending=None

    def _now(self):
        now=self._clock()
        if type(now) not in (int,float) or now<=0 or now>=2**50:
            raise Refused('trusted_clock_unavailable')
        return int(now)

    def prepare(self):
        _require_owner()
        with self._lock:
            if self._pending is not None:
                raise Refused('read_only_review_already_pending')
            with self._managed._vault.transaction() as db:
                owner=self._managed._owner(db)
                state=self._managed._consistent(db,owner)
                rec=state['roles'].get('kraken_read')
                if (rec is None or rec['disabled'] or rec['pending'] is not None
                        or rec['policy']!=self._managed._policies['kraken_read']):
                    raise Refused('read_only_credential_unavailable')
                context=Context('provider_test','kraken_read',
                                rec['generation'],rec['policy'])
            rid=broker_request('prepare',None,context)
            if type(rid) is not str or len(rid)!=32:
                raise Refused('broker_request_id_unavailable')
            self._pending=(rid,context)
            return {'ok':True,'status':'awaiting_native_review'}

    def cancel(self):
        _require_owner()
        with self._lock:
            prior=self._pending
            self._pending=None
            if prior is None:
                return {'ok':False,'reason':'review_unavailable'}
            try:
                broker_request('cancel',prior[0],prior[1])
            except (Refused,OSError):
                pass
            # An uncertain cancellation is still not reusable by the
            # owner service, even if the broker did not receive it.
            return {'ok':True,'status':'review_cancelled'}

    def finish(self):
        _require_owner()
        with self._lock:
            prior=self._pending
            self._pending=None
            if prior is None:
                raise Refused('review_unavailable')
            rid,context=prior
            now=self._now()
            # A reviewed context may be consumed at most once before
            # OwnerHooks.begin reserves the real encrypted-vault attempt.
            attempt=reserve_after_approval(self._managed,rid,context,now)
            result=finish_fixed_synthetic(self._managed,attempt,now=self._now())
            if result!='provider_refused':
                raise Refused('synthetic_result_not_negative')
            return {'ok':True,'status':'stored_unverified'}
