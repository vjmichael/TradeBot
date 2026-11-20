"""Offline-testable Telegram pairing candidate; no command or trading authority."""
import http.client
import json
import re
import secrets
import ssl
from dataclasses import dataclass, field

HOST = 'api.telegram.org'
LIMIT = 65536
ROLES = ('desk', 'recovery')
TTL = 300
TEXT = {'desk': 'Maj Vaj: Desk connection test.', 'recovery': 'Maj Vaj: Recovery connection test.'}

class Refused(Exception):
    pass


def valid_id(v):
    return type(v) is int and 0 < v < 2**52


def valid_context(role, generation, now):
    if role not in ROLES or type(generation) is not str or not re.fullmatch('[0-9a-f]{32}', generation) or type(now) is not int or not 0 < now < 2**63-TTL:
        raise Refused('invalid_context')


def pairs(items):
    d = {}
    for k, v in items:
        if k in d:
            raise ValueError('duplicate_field')
        d[k] = v
    return d


def https(token, method, payload):
    if method not in ('getMe', 'getChat', 'sendMessage'):
        raise Refused('invalid_method')
    conn = http.client.HTTPSConnection(HOST, 443, timeout=5, context=ssl.create_default_context())
    try:
        conn.request('POST', '/bot'+token+'/'+method, body=json.dumps(payload).encode(), headers={'Content-Type':'application/json','Accept-Encoding':'identity'})
        response = conn.getresponse()
        return response.status, response.read(LIMIT+1) if response.status == 200 else b''
    finally:
        conn.close()


@dataclass(frozen=True)
class Preview:
    role: str
    generation: str = field(repr=False)
    bot_id: int = field(repr=False)
    username: str = field(repr=False)
    chat_id: int = field(repr=False)
    expires: int
    review_id: str = field(repr=False)


class Pairing:
    """Single trusted owner, in-memory review state. Never expose objects via IPC.
    Caller supplies current vault-owned generation/time; serialize calls externally.
    """
    def __init__(self, transport=None):
        self._transport = transport or https
        self._pending = {}
        self._bound = {}
        self._status = {}

    def _call(self, token, method, payload):
        if type(token) is not str or not re.fullmatch(r'[0-9]{1,16}:[A-Za-z0-9_-]{20,128}', token):
            raise Refused('invalid_token')
        try:
            code, raw = self._transport(token, method, payload)
            if code != 200 or type(raw) is not bytes or len(raw)>LIMIT:
                raise ValueError()
            data = json.loads(raw, object_pairs_hook=pairs)
            if type(data) is not dict or data.get('ok') is not True or type(data.get('result')) is not dict:
                raise ValueError()
            return data['result']
        except Exception:
            raise Refused('provider_unverified') from None

    def _identity(self, token):
        b = self._call(token,'getMe',{})
        if not valid_id(b.get('id')) or b.get('is_bot') is not True or type(b.get('username')) is not str or not re.fullmatch(r'[A-Za-z0-9_]{5,32}',b['username']):
            raise Refused('identity_unverified')
        if int(token.split(':',1)[0]) != b['id']:
            raise Refused('identity_unverified')
        return b['id'], b['username']

    def _recipient(self, token, chat_id):
        c = self._call(token,'getChat',{'chat_id':chat_id})
        if not valid_id(c.get('id')) or c['id'] != chat_id or c.get('type') != 'private' or c.get('is_direct_messages') is True:
            raise Refused('recipient_unverified')

    def invalidate(self, role):
        if role not in ROLES: raise Refused('invalid_context')
        self._pending.pop(role,None); self._bound.pop(role,None); self._status.pop(role,None)

    def status(self, role, generation):
        entry=self._status.get(role)
        return entry[1] if entry and entry[0]==generation else 'stored_unverified'

    def preview(self, role, generation, token, chat_id, now):
        valid_context(role,generation,now)
        self.invalidate(role)
        if not valid_id(chat_id): raise Refused('recipient_unverified')
        bot_id, username = self._identity(token)
        if bot_id == chat_id: raise Refused('recipient_unverified')
        if any(p.bot_id==bot_id for r,p in {**self._bound,**self._pending}.items() if r!=role):
            raise Refused('bot_role_collision')
        self._recipient(token,chat_id)
        p=Preview(role,generation,bot_id,username,chat_id,now+TTL,secrets.token_hex(16))
        self._pending[role]=p
        self._status[role]=(generation,'awaiting_confirmation')
        return p

    def confirm_test(self, role, generation, token, review_id, approved, now):
        valid_context(role,generation,now)
        p=self._pending.get(role)
        if p is None or p.generation!=generation or p.review_id!=review_id:
            raise Refused('review_unavailable')
        self.invalidate(role)  # Consume before any network send; never auto retry.
        if approved is not True:
            return 'cancelled'
        if not p.expires-TTL <= now < p.expires:
            raise Refused('review_expired')
        if self._identity(token)!=(p.bot_id,p.username): raise Refused('identity_changed')
        self._recipient(token,p.chat_id)
        if any(v.bot_id==p.bot_id for r,v in {**self._bound,**self._pending}.items() if r!=role):
            raise Refused('bot_role_collision')
        self._status[role]=(generation,'delivery_unknown')
        try:
            m=self._call(token,'sendMessage',{'chat_id':p.chat_id,'text':TEXT[role],'protect_content':True,'allow_paid_broadcast':False})
            c=m.get('chat'); sender=m.get('from')
            if (type(c) is not dict or not valid_id(c.get('id')) or c['id']!=p.chat_id or c.get('type')!='private'
                    or type(sender) is not dict or not valid_id(sender.get('id')) or sender['id']!=p.bot_id or sender.get('is_bot') is not True
                    or not valid_id(m.get('message_id')) or m.get('text')!=TEXT[role]):
                return 'delivery_unknown'
        except Refused:
            return 'delivery_unknown'
        self._bound[role]=p
        self._status[role]=(generation,'test_accepted_by_telegram')
        return 'test_accepted_by_telegram'
