"""Read-only normalized health snapshot from *existing* local supervision.

No network request here. Only the already-authorized dashboard backend may
publish this status; authentication/caching and caller permissions are enforced
by the surrounding existing web gateway. A running systemd unit != readiness.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

REQUIRED = ('dashboard','vault','risk_gate','reconciliation','market_data')
LOCAL = {'dashboard':'dashboard','backend':'backend','vault':'vault'}
FRESH_SEC = 45

@dataclass(frozen=True)
class Witness:
    state: str
    observed_monotonic: float


def fresh(w, now):
    return (isinstance(w,Witness) and type(w.state) is str
            and w.state in ('ready','failed','unknown')
            and type(w.observed_monotonic) in (float,int)
            and type(now) in (float,int)
            and 0 <= now-w.observed_monotonic < FRESH_SEC)


def compose(local, witnesses: Mapping[str,Witness], network_evidence, now_monotonic):
    """Build a fixed status-only contract for the existing authenticated API.

    `network_evidence` must be a separately authenticated recent observation
    projected by connection_status.py, never inferred from kernel route alone.
    """
    if type(now_monotonic) not in (int,float):
        raise ValueError('clock_unavailable')
    if not hasattr(local,'services') or type(local.services) is not dict or type(witnesses) is not dict:
        raise ValueError('health_observation_unavailable')
    if any(k not in LOCAL for k in local.services):
        raise ValueError('unexpected_service_name')
    allowed = {'online','no_network_route','internet_unreachable','dns_problem','tls_problem','unknown','conflicting_evidence'}
    if network_evidence not in allowed:
        raise ValueError('network_evidence_unavailable')
    states={}
    for name in REQUIRED:
        w=witnesses.get(name)
        state=w.state if fresh(w,now_monotonic) else 'unknown'
        if name == 'dashboard':
            if local.services.get('dashboard') == 'failed' or local.services.get('backend') == 'failed':
                state='failed'
            elif local.services.get('dashboard')!='active' or local.services.get('backend')!='active':
                state='unknown'
        if name == 'vault':
            if local.services.get('vault')=='failed': state='failed'
            elif local.services.get('vault')!='active': state='unknown'
        states[name]=state
    # A network claim from the backend is observational only and never a Gate admission.
    return {'version':1, 'internet':network_evidence, 'services':states}


def no_provider_readiness(local,now_monotonic):
    """Offline fallback: process states only, no green status ever."""
    return compose(local,{},'no_network_route' if getattr(local,'internet',None)=='no_network_route' else 'unknown',now_monotonic)