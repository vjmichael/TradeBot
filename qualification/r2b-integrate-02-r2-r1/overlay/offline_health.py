"""Offline-safe service-health collection, independent of Internet/provider APIs.

Only local service metadata is inspected. No secret/config/credentials are read,
no app/service is started and this never authorizes money-affecting actions.
"""
from __future__ import annotations
import subprocess
from dataclasses import dataclass
from typing import Callable

# A systemd ActiveState alone is *not* business readiness.
UNITS = {
    'dashboard': 'maj-vaj-paper-dashboard.service',
    'backend': 'maj-vaj-paper-dashboard-backend.service',
    'vault': 'maj-vaj-paper-vault.service',
}
STATES = frozenset({'active','inactive','failed','activating','deactivating','reloading','unknown'})

@dataclass(frozen=True)
class LocalHealth:
    services: dict
    internet: str = 'unknown'  # internet observation is independent; never guessed

    def summary(self):
        if any(s == 'failed' for s in self.services.values()):
            return 'Services need attention'
        if all(s == 'active' for s in self.services.values()) and self.services:
            return 'Local services running'  # NOT "Services available"
        return 'Checking services'


def probe_local(command: Callable = subprocess.run) -> LocalHealth:
    """No network packets: only local systemd state and kernel route lookup."""
    statuses = {}
    for label, unit in UNITS.items():
        try:
            process = command(['/usr/bin/systemctl','show',unit,'--property=ActiveState','--value'],
                              capture_output=True,text=True,timeout=1,check=False)
            raw = process.stdout.strip() if process.returncode == 0 else 'unknown'
            statuses[label] = raw if raw in STATES else 'unknown'
        except (OSError, subprocess.TimeoutExpired):
            statuses[label] = 'unknown'
    internet = 'unknown'
    try:
        route = command(['/usr/bin/ip', 'route', 'get', '1.1.1.1'],
                        capture_output=True, text=True, timeout=1, check=False)
        if route.returncode != 0:
            internet = 'no_network_route'
    except (OSError, subprocess.TimeoutExpired):
        pass
    return LocalHealth(statuses, internet)