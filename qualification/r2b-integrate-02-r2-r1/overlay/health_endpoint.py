"""Authenticated status-only FastAPI integration for the existing dashboard.

Mount this on the already-authenticated dashboard FastAPI app, reusing its
`authorize` dependency. Never expose raw provider/credential/service output.
Only a trusted local supervisor may provide positive business readiness.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import time

from fastapi import Depends
from fastapi.responses import JSONResponse
from health_backend import compose, no_provider_readiness

@dataclass(frozen=True)
class StatusSources:
    local: object
    witnesses: dict
    internet: str
    collected_monotonic: float


def attach_connection_health(app, authorize: Callable, local_observer: Callable,
                             readiness_observer: Callable|None = None,
                             clock: Callable = time.monotonic):
    """No secondary authorization route; always reuse dashboard `authorize`.

    `local_observer` reads a BOUNDED cache of fixed local supervision data,
    never performs I/O on the request handler. A missing/read-only cached
    sample is unknown. `readiness_observer` is a separately privileged existing
    supervisor observation; it must not accept arbitrary browser-supplied data.
    """
    if not callable(authorize) or not callable(local_observer) or not callable(clock):
        raise ValueError('status integration unavailable')

    @app.get('/api/connection-health', dependencies=[Depends(authorize)], include_in_schema=False)
    def connection_health():
        try:
            now=clock()
            local=local_observer()
            # A stale local source is no evidence even if systemd once said active.
            if (not isinstance(local, StatusSources) or type(local.collected_monotonic) not in (float,int)
                    or not 0 <= now-local.collected_monotonic < 45):
                raise ValueError('local status stale')
            if readiness_observer is None:
                health=no_provider_readiness(local.local,now)
            else:
                witnesses=readiness_observer()
                if type(witnesses) is not dict:
                    raise ValueError('readiness missing')
                health=compose(local.local,witnesses,local.internet,now)
            return JSONResponse({'kind':'ok','health':health},headers={'Cache-Control':'no-store'})
        except Exception:
            return JSONResponse({'kind':'ok','health':{'version':1,'internet':'unknown',
                'services':{k:'unknown' for k in ('dashboard','vault','risk_gate','reconciliation','market_data')}}},
                headers={'Cache-Control':'no-store'})
    return connection_health