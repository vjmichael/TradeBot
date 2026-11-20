"""Pure, non-secret diagnostic projection. No I/O or financial authority."""
from dataclasses import dataclass

ROLES = ('kraken_read','kraken_trade','telegram_desk','telegram_recovery','grok')
TTL = 60
NETWORK = frozenset(('reachable','no_route','dns_failure','tls_failure','timeout','unknown'))
RESULTS = {
    'kraken_read': frozenset(('permissions_verified','unsafe_funding_permissions','missing_permissions','excess_or_unknown_permissions','provider_refused','connection_unverified','expired_key','invalid_credentials','key_mismatch','restricted_history','response_unverified')),
    'telegram_desk': frozenset(('test_accepted_by_telegram','human_acknowledged','delivery_unknown','provider_unverified','recipient_unverified','identity_unverified','identity_changed','bot_role_collision','awaiting_confirmation','cancelled','invalid_token','review_expired')),
    'grok': frozenset(('metadata_verified','authentication_refused','permission_refused','key_disabled_or_blocked','model_unavailable','rate_limited','billing_unverified','connection_unverified','response_unverified','excess_or_unknown_permissions','missing_permissions','invalid_key_format','confirmation_required')),
}
RESULTS['kraken_trade'] = RESULTS['kraken_read']
RESULTS['telegram_recovery'] = RESULTS['telegram_desk']

@dataclass(frozen=True)
class Observation:
    result: str
    at: int

@dataclass(frozen=True)
class Test:
    role: str
    generation: str
    policy: str
    result: str
    at: int
    expires: int


def fresh(at, now, ttl):
    return type(at) is int and type(now) is int and 0 <= at <= now and now-at < ttl


def network_status(now, dashboard, route, probes):
    """Only trusted observations; probes from distinct approved origins.
    Dashboard freshness is client-side; route and probes are host-side.
    A failed probe cannot prove a total internet outage.
    """
    if dashboard.result != 'reachable' or not fresh(dashboard.at,now,TTL):
        return ('server_unreachable','unknown')
    if not fresh(route.at,now,TTL) or route.result not in NETWORK:
        return ('server_reachable','unknown')
    useful = [p.result for p in probes.values() if fresh(p.at,now,TTL) and p.result in NETWORK]
    if 'reachable' in useful:
        host = 'online' if route.result != 'no_route' else 'conflicting_evidence'
    elif route.result == 'no_route':
        host = 'no_network_route'
    elif len(useful) >= 2 and all(v == 'dns_failure' for v in useful):
        host = 'dns_problem'
    elif len(useful) >= 2 and all(v == 'tls_failure' for v in useful):
        host = 'tls_problem'
    elif len(useful) >= 2 and all(v in ('timeout','dns_failure','tls_failure','no_route') for v in useful):
        host = 'internet_unreachable'
    else:
        host = 'unknown'
    return ('server_reachable',host)


def provider_status(role, enabled, generation, policy, test, now):
    """Return diagnostic status only, never an admission or trading decision."""
    if role not in ROLES:
        raise ValueError('unknown role')
    if enabled is not True:
        return 'disabled'
    if generation is None:
        return 'not_configured'
    if test is None:
        return 'stored_unverified'
    if (test.role != role or test.generation != generation or test.policy != policy
            or test.result not in RESULTS[role] or type(test.expires) is not int
            or not fresh(test.at,now,300) or not test.at <= now < test.expires <= test.at+300):
        return 'stored_unverified'
    return test.result
