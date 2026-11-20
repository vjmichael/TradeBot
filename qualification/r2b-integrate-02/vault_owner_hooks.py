"""R2B integration candidate: transactional hooks for the existing vault owner.

No independent vault, no IPC, no direct network. Caller must hold PaperVault's
existing BEGIN IMMEDIATE transaction, and supply a separately protected HMAC
key. Verification results are never trading or inference authorizations.
"""
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass

ROLES = {
    'kraken_read': ('kraken_read_key', 'kraken_read_secret'),
    'telegram_desk': ('telegram_desk',),
    'telegram_recovery': ('telegram_recovery',),
    'grok': ('grok',),
}
PROVIDER = {
    'kraken_read': 'kraken',
    'telegram_desk': 'telegram',
    'telegram_recovery': 'telegram',
    'grok': 'grok',
}
SUCCESS = {
    'kraken_read': frozenset({'permissions_verified'}),
    'telegram_desk': frozenset({'test_accepted_by_telegram', 'human_acknowledged'}),
    'telegram_recovery': frozenset({'test_accepted_by_telegram', 'human_acknowledged'}),
    'grok': frozenset({'metadata_verified'}),
}
ALLOWED = {
    'kraken_read': SUCCESS['kraken_read'] | {'unsafe_funding_permissions','missing_permissions','excess_or_unknown_permissions','provider_refused','connection_unverified','expired_key','invalid_credentials','key_mismatch','restricted_history','response_unverified'},
    'telegram_desk': SUCCESS['telegram_desk'] | {'delivery_unknown','provider_unverified','recipient_unverified','identity_unverified','identity_changed','bot_role_collision','review_expired','cancelled'},
    'telegram_recovery': SUCCESS['telegram_recovery'] | {'delivery_unknown','provider_unverified','recipient_unverified','identity_unverified','identity_changed','bot_role_collision','review_expired','cancelled'},
    'grok': SUCCESS['grok'] | {'authentication_refused','permission_refused','key_disabled_or_blocked','model_unavailable','rate_limited','billing_unverified','connection_unverified','response_unverified','excess_or_unknown_permissions','missing_permissions','invalid_key_format','confirmation_required'},
}
TTL = 300
DOMAIN = b'maj-vaj:provider-lifecycle:v1\0'
MAX_NONCE = 2**63 - 1


class Refused(ValueError):
    """Non-secret, fixed failure; do not log request contents."""


@dataclass(frozen=True)
class Attempt:
    role: str
    generation: str
    attempt_id: str
    policy: str
    started: int
    nonce: int = 0


def _time(now):
    if type(now) is not int or not 0 < now < (MAX_NONCE - TTL) // 1000:
        raise Refused('invalid_clock')
    return now


def _role(role):
    if role not in ROLES:
        raise Refused('unsupported_role')
    return role


def _policy(policy):
    # This is a version/fingerprint, never a free-form remote target or a command.
    if type(policy) is not str or not re.fullmatch('[0-9a-f]{64}', policy):
        raise Refused('policy_unavailable')
    return policy


def _keyed(tag_key, label, *parts):
    m = hmac.new(tag_key, b'maj-vaj:r2b:' + label + b'\0', hashlib.sha256)
    for part in parts:
        if type(part) is not bytes:
            raise Refused('invalid_sealed_source')
        m.update(len(part).to_bytes(4, 'big'))
        m.update(part)
    return m.hexdigest()


class OwnerHooks:
    """Never instantiate from dashboard or research worker; owner-only service hook.

    In production invoke after existing paired enroll/remove inside its SAME
    vault transaction, not from a separate mutable process or credential export.
    """
    def __init__(self, db, cipher, tag_key):
        if not db.in_transaction:
            raise Refused('vault_transaction_required')
        if type(tag_key) is not bytes or len(tag_key) < 32:
            raise Refused('separate_tag_key_required')
        self.db, self.cipher, self.tag_key = db, cipher, tag_key

    def initialize(self):
        """Explicit one-time migration; never silently recreate missing state."""
        self.db.execute('CREATE TABLE provider_metadata (id INTEGER PRIMARY KEY CHECK(id=1), sealed BLOB NOT NULL)')
        self.db.execute('INSERT INTO provider_metadata VALUES (1, ?)', (self._seal({'clock':0,'roles':{},'nonces':{}}),))

    def _seal(self, state):
        return self.cipher.encrypt(DOMAIN + json.dumps(state, sort_keys=True, separators=(',', ':')).encode())

    def _load(self):
        row = self.db.execute('SELECT sealed FROM provider_metadata WHERE id=1').fetchone()
        if row is None:
            raise Refused('verification_state_missing')
        raw = self.cipher.decrypt(row[0])
        if not raw.startswith(DOMAIN):
            raise Refused('verification_state_invalid')
        state = json.loads(raw[len(DOMAIN):]);
        if type(state) is not dict or set(state) != {'clock','roles','nonces'}:
            raise Refused('verification_state_invalid')
        return state

    def _put(self, state):
        if self.db.execute('UPDATE provider_metadata SET sealed=? WHERE id=1', (self._seal(state),)).rowcount != 1:
            raise Refused('verification_state_missing')

    def _clock(self, state, now):
        _time(now)
        if now < state['clock']:
            raise Refused('clock_rollback')
        state['clock'] = now

    def _bindings(self, role, sealed_items):
        """Input is exact authenticated vault-stored ciphertext, never plain secrets."""
        slots = ROLES[_role(role)]
        if type(sealed_items) is not dict or set(sealed_items) != set(slots):
            raise Refused('incomplete_credential_pair')
        # Identity binding to exact stored ciphertext, not only slot presence.
        ciphertexts=[]
        for slot in slots:
            blob=sealed_items[slot]
            if type(blob) is not bytes or not blob:
                raise Refused('invalid_sealed_source')
            ciphertexts.append(blob)
        binding=_keyed(self.tag_key, b'bound:'+role.encode(), *ciphertexts)
        # For role collision, compare a vault-private commitment to the public
        # identity slot alone (Kraken API key, Telegram token); it must be
        # decrypted only inside owner process and never returned or logged.
        slot=slots[0]
        raw=self.cipher.decrypt(sealed_items[slot])
        expected=b'maj-vaj:PAPER:credential-vault:v1\0' + slot.encode() + b'\0'
        if not raw.startswith(expected) or len(raw)<=len(expected):
            raise Refused('invalid_sealed_source')
        key_tag=_keyed(self.tag_key, b'identity', raw[len(expected):])
        return binding, key_tag

    def replace(self, role, sealed_items, policy, now):
        _role(role); _policy(policy)
        binding, tag=self._bindings(role,sealed_items)
        state=self._load();self._clock(state,now)
        for other, record in state['roles'].items():
            if other!=role and record['tag']==tag:
                raise Refused('role_collision')
        if tag not in state['nonces'] and len(state['nonces'])>=4096:
            raise Refused('nonce_capacity_reached')
        state['nonces'].setdefault(tag,0)
        state['roles'][role]={'binding':binding,'tag':tag,'generation':secrets.token_hex(16),
                              'policy':policy,'pending':None,'receipt':None,'disabled':False}
        self._put(state)

    def remove(self, role, now):
        _role(role);state=self._load();self._clock(state,now)
        state['roles'].pop(role,None)
        self._put(state)  # Never reset nonce history.

    def disable(self, role, now):
        _role(role);state=self._load();self._clock(state,now)
        record=state['roles'].get(role)
        if record is None: raise Refused('not_configured')
        record.update(disabled=True, pending=None, receipt=None)
        self._put(state)

    def _entry(self, state, role, sealed_items, policy):
        binding,tag=self._bindings(role,sealed_items)
        record=state['roles'].get(role)
        if record is None or record['binding']!=binding or record['tag']!=tag:
            raise Refused('binding_changed')
        if record['disabled'] or record['policy']!=_policy(policy):
            raise Refused('policy_changed_or_disabled')
        return record

    def begin(self, role, sealed_items, policy, now):
        state=self._load();self._clock(state,now)
        rec=self._entry(state,role,sealed_items,policy)
        if rec['pending'] is not None: raise Refused('attempt_pending')
        nonce=0
        if PROVIDER[role]=='kraken':
            nonce=max(now*1000, state['nonces'][rec['tag']]+1)
            if nonce>MAX_NONCE: raise Refused('nonce_exhausted')
            state['nonces'][rec['tag']]=nonce
        a=Attempt(role,rec['generation'],secrets.token_hex(16),policy,now,nonce)
        rec['pending']=a.__dict__;rec['receipt']=None
        self._put(state)
        return a

    def finish(self, attempt, sealed_items, result, now):
        _role(attempt.role);_time(now)
        state=self._load();self._clock(state,now)
        rec=self._entry(state,attempt.role,sealed_items,attempt.policy)
        if rec['pending']!=attempt.__dict__ or rec['generation']!=attempt.generation:
            raise Refused('stale_attempt')
        if type(result) is not str or result not in ALLOWED[attempt.role]:
            raise Refused('unknown_result')
        rec['pending']=None
        rec['receipt']={'result':result,'checked':attempt.started,'expires':attempt.started+TTL if result in SUCCESS[attempt.role] else 0}
        if result in SUCCESS[attempt.role] and now>=rec['receipt']['expires']:
            rec['receipt']=None
        self._put(state)

    def recover_interrupted(self, now):
        """Owner startup after trusted clock restoration; no retry or auto approval."""
        state=self._load();self._clock(state,now)
        for role,rec in state['roles'].items():
            if rec['pending'] is not None:
                rec['pending']=None
                rec['receipt']={'result':'delivery_unknown','checked':now,'expires':0} if PROVIDER[role]=='telegram' else None
        self._put(state)

    def status(self, role, sealed_items, policy, now):
        _role(role);_time(now)
        state=self._load()
        if now < state['clock']:
            return 'verification_unavailable'
        rec=state['roles'].get(role)
        if rec is None: return 'not_configured'
        if rec['disabled']: return 'disabled'
        try:self._entry(state,role,sealed_items,policy)
        except (Refused,ValueError): return 'stored_unverified'
        if rec['pending'] is not None:return 'verification_pending'
        receipt=rec['receipt']
        if receipt is None: return 'stored_unverified'
        if receipt['expires'] and now>=receipt['expires']:
            rec['receipt']=None;state['clock']=now;self._put(state)
            return 'stored_unverified'
        if receipt['result'] not in ALLOWED[role]:return 'verification_unavailable'
        return receipt['result']