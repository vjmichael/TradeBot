import base64, hashlib, hmac, json, unittest
from unittest.mock import patch, Mock
from kraken_verify import *
KEY='SyntheticKey123'; SECRET=base64.b64encode(b'synthetic-test-secret').decode(); GEN='a'*32; NOW=10000
class TestVerifier(unittest.TestCase):
    def response(self, scopes=READ, **overrides):
        v=dict(apiKey=KEY, permissions=sorted(scopes), validUntil='0', queryFrom='0', queryTo='0', ipAllowlist=[], iban='SYNTHETIC-PII', apiKeyName='SYNTHETIC-NAME'); v.update(overrides)
        return 200,json.dumps({'error':[],'result':v}).encode()
    def verify(self, response=None, role='read', key=KEY, secret=SECRET, nonce=123, transport=None):
        return verify(key,secret,role,GEN,nonce,NOW,transport=transport or (lambda b,h: response or self.response()))
    def test_exact_read(self): self.assertEqual(self.verify().status,'permissions_verified')
    def test_exact_trade(self): self.assertEqual(self.verify(self.response(TRADE),'trade').status,'permissions_verified')
    def test_read_cannot_trade(self): self.assertEqual(self.verify(self.response(TRADE)).status,'excess_or_unknown_permissions')
    def test_funding_refusals(self):
        for scope in FORBIDDEN:
            self.assertEqual(self.verify(self.response(READ|{scope})).status,'unsafe_funding_permissions')
    def test_unknown_scope(self): self.assertEqual(self.verify(self.response(READ|{'transfer-funds'})).status,'excess_or_unknown_permissions')
    def test_missing_scope(self): self.assertEqual(self.verify(self.response(READ-{'query-funds'})).status,'missing_permissions')
    def test_missing_trade(self): self.assertEqual(self.verify(role='trade').status,'missing_permissions')
    def test_no_extra_export(self): self.assertEqual(self.verify(self.response(READ|{'export-data'})).status,'excess_or_unknown_permissions')
    def test_wrong_key(self): self.assertEqual(self.verify(self.response(apiKey='other')).status,'key_mismatch')
    def test_expiry(self): self.assertEqual(self.verify(self.response(validUntil=str(NOW))).status,'expired_key')
    def test_short_expiry(self):
        receipt=self.verify(self.response(validUntil=str(NOW+5))); self.assertTrue(receipt.current('read',GEN,NOW+4)); self.assertFalse(receipt.current('read',GEN,NOW+5))
    def test_receipt_binding(self):
        r=self.verify(); self.assertTrue(r.current('read',GEN,NOW)); self.assertFalse(r.current('trade',GEN,NOW)); self.assertFalse(r.current('read','b'*32,NOW)); self.assertFalse(r.current('read',GEN,NOW-1)); self.assertFalse(r.current('read',GEN,NOW+300))
    def test_history(self): self.assertEqual(self.verify(self.response(queryFrom='2')).status,'restricted_history')
    def test_ip_format(self): self.assertEqual(self.verify(self.response(ipAllowlist=['bad'])).status,'response_unverified'); self.assertEqual(self.verify(self.response(ipAllowlist=['192.0.2.0/24'])).status,'permissions_verified')
    def test_bad_timestamp(self): self.assertEqual(self.verify(self.response(validUntil=0)).status,'response_unverified')
    def test_duplicate_permission(self): self.assertEqual(self.verify(self.response(permissions=list(READ)+['query-funds'])).status,'response_unverified')
    def test_bad_scope_type(self): self.assertEqual(self.verify(self.response(permissions=[{}])).status,'response_unverified')
    def test_duplicate_json(self): self.assertEqual(self.verify((200,b'{"error":[],"error":[],"result":{}}')).status,'response_unverified')
    def test_missing_fields(self): self.assertEqual(self.verify((200,b'{"error":[],"result":{}}')).status,'response_unverified')
    def test_oversize(self): self.assertEqual(self.verify((200,b' '*65537)).status,'connection_unverified')
    def test_redirect(self): self.assertEqual(self.verify((302,b'')).status,'connection_unverified')
    def test_provider_refusal(self): self.assertEqual(self.verify((200,b'{"error":["synthetic-sensitive"]}')).status,'provider_refused')
    def test_transport_error_redacted(self):
        def fail(b,h): raise RuntimeError(SECRET)
        result=self.verify(transport=fail); self.assertEqual(result.status,'connection_unverified'); self.assertNotIn(SECRET,repr(result))
    def test_bad_input_no_transport(self):
        fail=Mock(side_effect=AssertionError); self.assertEqual(self.verify(secret='!!!',transport=fail).status,'invalid_credentials'); fail.assert_not_called()
    def test_bool_nonce(self): self.assertEqual(self.verify(nonce=True).status,'invalid_credentials')
    def test_fixed_signing(self):
        def capture(body,headers):
            self.assertEqual(body,b'{"nonce":123}'); self.assertEqual(headers['API-Key'],KEY)
            msg=b'/0/private/GetApiKeyInfo'+hashlib.sha256(b'123{"nonce":123}').digest()
            self.assertEqual(headers['API-Sign'],base64.b64encode(hmac.digest(base64.b64decode(SECRET),msg,'sha512')).decode()); self.assertNotIn(SECRET,str(headers)); return self.response()
        self.assertEqual(self.verify(transport=capture).status,'permissions_verified')
    def test_sanitized_receipt(self):
        s=repr(self.verify()); self.assertNotIn(KEY,s); self.assertNotIn('SYNTHETIC-PII',s); self.assertNotIn('SYNTHETIC-NAME',s); self.assertNotIn(SECRET,s)
    def test_fixed_transport(self):
        conn=Mock(); response=conn.getresponse.return_value; response.status=302
        with patch('kraken_verify.http.client.HTTPSConnection',return_value=conn) as factory:
            self.assertEqual(verify(KEY,SECRET,'read',GEN,123,NOW).status,'connection_unverified')
            self.assertEqual(factory.call_args.args,('api.kraken.com',443)); self.assertEqual(conn.request.call_args.args,('POST','/0/private/GetApiKeyInfo')); conn.request.assert_called_once(); conn.close.assert_called_once(); response.read.assert_not_called()
if __name__=='__main__': unittest.main()
