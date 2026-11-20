"""Standalone optional Grok inspection; metadata only, no inference or management calls."""
import http.client
import json
import re
import ssl
from dataclasses import dataclass

HOST = 'api.x.ai'
LIMIT = 262144
TTL = 300
PATHS = ('/v1/api-key', '/v1/language-models')


def unique(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError('duplicate field')
        d[k] = v
    return d


def https(key, path):
    if path not in PATHS:
        raise ValueError('endpoint refused')
    conn = http.client.HTTPSConnection(HOST, 443, timeout=5, context=ssl.create_default_context())
    try:
        conn.request('GET', path, headers={'Authorization':'Bearer '+key, 'Accept':'application/json', 'Accept-Encoding':'identity'})
        response = conn.getresponse()
        return response.status, response.read(LIMIT+1) if response.status == 200 else b''
    finally:
        conn.close()


@dataclass(frozen=True)
class Receipt:
    status: str
    generation: str
    model: str
    checked: int
    expires: int = 0

    def current(self, generation, model, now, opted_in):
        return (opted_in is True and self.status == 'metadata_verified' and self.generation == generation
                and self.model == model and type(now) is int and self.checked <= now < self.expires)


def inspect(key, generation, model, endpoint_acl, now, *, opted_in=False, approved=False, transport=None):
    """Trusted owner supplies current generation and reviewed exact endpoint ACL.
    No claim of inference readiness, billing balance, or financial authorization.
    """
    if (type(generation) is not str or not re.fullmatch('[0-9a-f]{32}', generation)
            or type(model) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', model)
            or type(endpoint_acl) is not str or not re.fullmatch(r'api-key:endpoint:[A-Za-z0-9_.-]{1,96}', endpoint_acl)
            or type(now) is not int or not 0 < now < 2**63-TTL):
        raise ValueError('invalid context')
    def out(status, expiry=0): return Receipt(status,generation,model,now,expiry)
    if opted_in is not True: return out('disabled')
    if approved is not True: return out('confirmation_required')
    if type(key) is not str or not re.fullmatch(r'xai-[A-Za-z0-9_-]{16,512}',key): return out('invalid_key_format')
    def request(path):
        code, raw = (transport or https)(key,path)
        if type(code) is not int: raise ValueError()
        if code != 200: return None, {401:'authentication_refused',403:'permission_refused',429:'rate_limited',402:'billing_unverified'}.get(code,'connection_unverified')
        if type(raw) is not bytes or len(raw)>LIMIT: raise ValueError()
        value=json.loads(raw,object_pairs_hook=unique)
        if type(value) is not dict: raise ValueError()
        return value,None
    try:
        info,error=request(PATHS[0])
        if error: return out(error)
        for flag in ('team_blocked','api_key_blocked','api_key_disabled'):
            if type(info.get(flag)) is not bool: return out('response_unverified')
        if any(info[f] for f in ('team_blocked','api_key_blocked','api_key_disabled')): return out('key_disabled_or_blocked')
        acls=info.get('acls')
        if type(acls) is not list or len(acls)>128 or any(type(a) is not str for a in acls) or len(set(acls))!=len(acls): return out('response_unverified')
        # Never infer least privilege from a model list or accept wildcard ACLs.
        required={endpoint_acl,'api-key:model:'+model}
        if set(acls)-required: return out('excess_or_unknown_permissions')
        if required-set(acls): return out('missing_permissions')
        catalog,error=request(PATHS[1])
        if error: return out(error)
        models=catalog.get('models')
        if type(models) is not list or len(models)>512: return out('response_unverified')
        ids=set(); match=False
        for m in models:
            if type(m) is not dict or type(m.get('id')) is not str or m['id'] in ids or m.get('object')!='model': return out('response_unverified')
            ids.add(m['id'])
            if m['id']==model:
                match=all(type(m.get(k)) is list and all(type(v) is str for v in m[k]) and 'text' in m[k] for k in ('input_modalities','output_modalities'))
        if not match: return out('model_unavailable')
        return out('metadata_verified',now+TTL)
    except Exception:
        return out('connection_unverified')
