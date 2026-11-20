"""R2B-A isolated Spot key inspection candidate; no order/funding endpoints.
Only a trusted vault owner may supply credentials/nonce. Not a LIVE authorization.
"""
import base64
import hashlib
import hmac
import http.client
import json
import re
import ssl
from dataclasses import dataclass

HOST = 'api.kraken.com'
PATH = '/0/private/GetApiKeyInfo'
MAX_RESPONSE = 65536
READ = frozenset({'query-funds', 'query-open-trades', 'query-closed-trades', 'query-ledger'})
TRADE = READ | {'modify-trades', 'close-trades'}
FORBIDDEN = frozenset({'add-funds', 'withdraw-funds', 'earn-funds', 'add-withdraw-address', 'update-withdraw-address'})
MAX_NONCE = 2**63-1

@dataclass(frozen=True)
class Receipt:
    status: str
    role: str
    generation: str
    checked_at: int
    expires_at: int = 0

    def current(self, role, generation, now):
        return (self.status == 'permissions_verified' and self.role == role
                and self.generation == generation and type(now) is int
                and self.checked_at <= now < self.expires_at)


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,19}', value):
        raise ValueError('invalid_timestamp')
    n = int(value)
    if n > MAX_NONCE:
        raise ValueError('invalid_timestamp')
    return n


def _unique_object(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError('duplicate_field')
        result[k] = v
    return result


def _fixed_request(key, secret, nonce):
    if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9+/=]{1,512}', key)
            or not isinstance(secret, str) or not 1 <= len(secret) <= 1024
            or type(nonce) is not int or not 0 < nonce <= MAX_NONCE):
        raise ValueError('invalid_credentials')
    decoded = base64.b64decode(secret, validate=True)
    if not decoded:
        raise ValueError('invalid_credentials')
    body = json.dumps({'nonce': nonce}, separators=(',', ':')).encode('ascii')
    digest = hashlib.sha256(str(nonce).encode('ascii') + body).digest()
    signature = base64.b64encode(hmac.new(decoded, PATH.encode('ascii') + digest, hashlib.sha512).digest()).decode('ascii')
    return body, {'API-Key': key, 'API-Sign': signature, 'Content-Type': 'application/json', 'Accept': 'application/json', 'Accept-Encoding': 'identity'}


def _https(body, headers):
    # Direct validated TLS, no environment proxies, redirects, retry, or configurable destination.
    conn = http.client.HTTPSConnection(HOST, 443, timeout=8, context=ssl.create_default_context())
    try:
        conn.request('POST', PATH, body=body, headers=headers)
        resp = conn.getresponse()
        if resp.status != 200:
            return resp.status, b''
        data = resp.read(MAX_RESPONSE + 1)
        return resp.status, data
    finally:
        conn.close()


def verify(key, secret, role, generation, nonce, now, *, transport=None):
    """Single inspection, sanitized receipt only. Caller must serialize durable nonces.
    `transport` is an in-process test seam, never a user-supplied URI or IPC field.
    A receipt is not a trading capability; generation must be issued by the vault.
    """
    if role not in ('read', 'trade') or not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{32}', generation) or type(now) is not int or not 0 < now <= MAX_NONCE-300:
        raise ValueError('invalid_verification_context')
    def result(status, expiry=0):
        return Receipt(status, role, generation, now, expiry)
    try:
        body, headers = _fixed_request(key, secret, nonce)
    except (ValueError, TypeError):
        return result('invalid_credentials')
    try:
        code, raw = (transport or _https)(body, headers)
    except Exception:
        return result('connection_unverified')
    if code != 200 or not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE:
        return result('connection_unverified')
    try:
        response = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(response, dict) or not isinstance(response.get('error'), list):
            raise ValueError('invalid_response')
        if response['error']:
            return result('provider_refused')
        info = response['result']
        returned = info['apiKey']
        if not isinstance(returned, str) or not hmac.compare_digest(returned.encode('utf-8'), key.encode('ascii')):
            return result('key_mismatch')
        permissions = info['permissions']
        if (not isinstance(permissions, list) or len(permissions) > 32
                or any(not isinstance(p, str) for p in permissions)
                or len(set(permissions)) != len(permissions)):
            raise ValueError('invalid_permissions')
        actual = frozenset(permissions)
        required = READ if role == 'read' else TRADE
        if actual & FORBIDDEN:
            return result('unsafe_funding_permissions')
        if actual - required:
            return result('excess_or_unknown_permissions')
        if required - actual:
            return result('missing_permissions')
        expiry = _timestamp(info['validUntil'])
        if expiry and expiry <= now:
            return result('expired_key')
        # Full-history query restriction and IP list shape are explicit, not inferred.
        if _timestamp(info['queryFrom']) or _timestamp(info['queryTo']):
            return result('restricted_history')
        ips = info['ipAllowlist']
        if not isinstance(ips, list) or len(ips) > 128:
            raise ValueError('invalid_ip_policy')
        import ipaddress
        for ip in ips:
            if not isinstance(ip, str):
                raise ValueError('invalid_ip_policy')
            ipaddress.ip_network(ip, strict=False)
        return result('permissions_verified', min(now+300, expiry) if expiry else now+300)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return result('response_unverified')
