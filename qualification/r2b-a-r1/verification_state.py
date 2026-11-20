"""Vault-owned R2B-A-R1 metadata candidate; no network, secret read or trading capability.
All calls use the existing vault BEGIN IMMEDIATE transaction, not a second store.
"""
import json
import re
import secrets
from dataclasses import asdict, dataclass

DOMAIN = b'maj-vaj:paper:kraken-verification:v1\0'
ROLES = ('read', 'trade')
MAX = 2**63-1
LIMIT = 4096
TTL = 300
FAILURES = frozenset({'invalid_credentials','connection_unverified','provider_refused',
    'key_mismatch','unsafe_funding_permissions','excess_or_unknown_permissions',
    'missing_permissions','expired_key','restricted_history','response_unverified'})

@dataclass(frozen=True)
class Attempt:
    role: str
    generation: str
    attempt_id: str
    nonce: int
    started: int


def _hex(value, length):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{%d}' % length, value):
        raise ValueError('invalid opaque identity')
    return value


def _role(role):
    if role not in ROLES:
        raise ValueError('invalid role')


def _time(now):
    if type(now) is not int or not 0 < now <= MAX-TTL:
        raise ValueError('invalid clock')
    return now


class State:
    def __init__(self, db, cipher):
        if not db.in_transaction:
            raise ValueError('vault transaction required')
        self.db, self.cipher = db, cipher

    def initialize(self):
        """Explicit one-time migration only; never recreate missing established state."""
        self.db.execute('CREATE TABLE kraken_verification (id INTEGER PRIMARY KEY CHECK(id=1), sealed BLOB NOT NULL)')
        self.db.execute('INSERT INTO kraken_verification VALUES(1,?)', (self._seal({'roles':{},'nonces':{},'clock':0}),))

    def _seal(self, state):
        return self.cipher.encrypt(DOMAIN + json.dumps(state, sort_keys=True, separators=(',', ':')).encode())

    def _load(self):
        if not self.db.in_transaction:
            raise ValueError('vault transaction required')
        row = self.db.execute('SELECT sealed FROM kraken_verification WHERE id=1').fetchone()
        if not row:
            raise ValueError('verification state unavailable')
        raw = self.cipher.decrypt(row[0])
        if not raw.startswith(DOMAIN):
            raise ValueError('verification state refused')
        return json.loads(raw[len(DOMAIN):])

    def _put(self, s):
        if self.db.execute('UPDATE kraken_verification SET sealed=? WHERE id=1',(self._seal(s),)).rowcount != 1:
            raise ValueError('verification state unavailable')

    def _clock(self, s, now):
        _time(now)
        if now < s['clock']:
            raise ValueError('clock rollback')
        s['clock'] = now

    def replace(self, role, binding, key_tag, now):
        """Call in the same transaction as paired vault replacement. Opaque inputs only.
        binding commits to sealed pair bytes; key_tag is a stable vault-private keyed identity.
        """
        _role(role); _hex(binding,64); _hex(key_tag,64)
        s=self._load(); self._clock(s,now)
        if any(r!=role and v['tag']==key_tag for r,v in s['roles'].items()):
            raise ValueError('key role collision')
        if key_tag not in s['nonces'] and len(s['nonces']) >= LIMIT:
            raise ValueError('nonce history capacity reached')
        s['nonces'].setdefault(key_tag,0)
        s['roles'][role]={'binding':binding,'tag':key_tag,'generation':secrets.token_hex(16),'pending':None,'receipt':None}
        self._put(s)

    def remove(self, role, now):
        _role(role); s=self._load(); self._clock(s,now)
        s['roles'].pop(role,None)
        self._put(s)  # Retain nonce high-water marks even after removal.

    def _entry(self,s,role,binding):
        _role(role); _hex(binding,64)
        r=s['roles'].get(role)
        if r is None or r['binding'] != binding:
            raise ValueError('credential binding unavailable')
        return r

    def reserve(self, role, binding, now):
        s=self._load(); self._clock(s,now); r=self._entry(s,role,binding)
        if r['pending'] is not None:
            raise ValueError('verification already pending')
        nonce=max(now*1000,s['nonces'][r['tag']]+1)
        if nonce > MAX:
            raise ValueError('nonce exhausted')
        a=Attempt(role,r['generation'],secrets.token_hex(16),nonce,now)
        s['nonces'][r['tag']]=nonce; r['pending']=asdict(a); r['receipt']=None
        self._put(s)
        return a  # Internal owner only; commit before any inspector runs.

    def finish(self, attempt, binding, receipt, now):
        s=self._load(); self._clock(s,now); r=self._entry(s,attempt.role,binding)
        if r['pending'] != asdict(attempt) or r['generation'] != attempt.generation:
            raise ValueError('stale verification')
        if receipt.role != attempt.role or receipt.generation != attempt.generation or receipt.checked_at != attempt.started:
            raise ValueError('receipt context refused')
        if receipt.status == 'permissions_verified':
            if not attempt.started <= now < receipt.expires_at <= attempt.started+TTL:
                raise ValueError('receipt expiry refused')
        elif receipt.status not in FAILURES or receipt.expires_at != 0:
            raise ValueError('receipt result refused')
        r['pending']=None; r['receipt']={'status':receipt.status,'expires':receipt.expires_at}
        self._put(s)

    def invalidate(self, role, binding, now):
        """After interrupted attempt or revocation suspicion; never reset nonce history."""
        s=self._load(); self._clock(s,now); r=self._entry(s,role,binding)
        r['pending']=None; r['receipt']=None
        self._put(s)

    def status(self, role, binding, now):
        _role(role); _time(now); s=self._load()
        if now < s['clock']:
            return 'verification_unavailable'
        r=s['roles'].get(role)
        if r is None:
            return 'not_configured' if binding is None else 'verification_unavailable'
        if r['binding'] != binding:
            return 'verification_unavailable'
        if r['pending'] is not None:
            return 'verification_pending'
        v=r['receipt']
        if v is None:
            return 'stored_unverified'
        if v['status']=='permissions_verified':
            if now >= v['expires']:
                r['receipt']=None; s['clock']=now; self._put(s)
                return 'stored_unverified'
            if now > s['clock']:
                s['clock']=now; self._put(s)
        return v['status']
