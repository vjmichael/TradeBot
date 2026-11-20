import json
import unittest
from unittest.mock import Mock, patch
from grok_verify import inspect, https, PATHS, LIMIT

KEY='xai-'+ 'S'*32; GEN='a'*32; MODEL='grok-synthetic'; ACL='api-key:endpoint:TestReviewedEndpoint'

class Tests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        self.info={'team_blocked':False,'api_key_blocked':False,'api_key_disabled':False,'acls':[ACL,'api-key:model:'+MODEL],'name':'PRIVATE-NAME','user_id':'PRIVATE-ID'}
        self.catalog={'models':[{'id':MODEL,'object':'model','input_modalities':['text'],'output_modalities':['text']}]}
        self.override={}
    def transport(self,key,path):
        self.calls.append(path)
        v=self.override.get(path)
        if isinstance(v,Exception): raise v
        return v if v is not None else (200,json.dumps(self.info if path==PATHS[0] else self.catalog).encode())
    def runcheck(self,**kw):
        args=dict(key=KEY,generation=GEN,model=MODEL,endpoint_acl=ACL,now=100,opted_in=True,approved=True,transport=self.transport);args.update(kw)
        return inspect(**args)
    def test_success(self): self.assertEqual(self.runcheck().status,'metadata_verified'); self.assertEqual(self.calls,list(PATHS))
    def test_disabled(self): self.assertEqual(self.runcheck(opted_in=False).status,'disabled');self.assertFalse(self.calls)
    def test_explicit_approval(self): self.assertEqual(self.runcheck(approved=False).status,'confirmation_required');self.assertFalse(self.calls)
    def test_strict_bools(self): self.assertEqual(self.runcheck(opted_in=1).status,'disabled');self.assertFalse(self.calls)
    def test_invalid_key(self): self.assertEqual(self.runcheck(key='bad').status,'invalid_key_format');self.assertFalse(self.calls)
    def test_header_injection(self): self.assertEqual(self.runcheck(key=KEY+'\r\n').status,'invalid_key_format');self.assertFalse(self.calls)
    def test_wildcards(self): self.info['acls']=['api-key:model:*','api-key:endpoint:*'];self.assertEqual(self.runcheck().status,'excess_or_unknown_permissions');self.assertEqual(self.calls,[PATHS[0]])
    def test_extra_permissions(self): self.info['acls'].append('api-key:endpoint:Other');self.assertEqual(self.runcheck().status,'excess_or_unknown_permissions')
    def test_missing_permissions(self): self.info['acls']=[ACL];self.assertEqual(self.runcheck().status,'missing_permissions')
    def test_duplicate_permissions(self): self.info['acls'].append(ACL);self.assertEqual(self.runcheck().status,'response_unverified')
    def test_missing_flags(self): del self.info['team_blocked'];self.assertEqual(self.runcheck().status,'response_unverified')
    def test_blocked_or_disabled(self):
        for f in ('team_blocked','api_key_blocked','api_key_disabled'):
            self.info[f]=True;self.assertEqual(self.runcheck().status,'key_disabled_or_blocked');self.info[f]=False
    def test_bad_flags(self): self.info['team_blocked']=0;self.assertEqual(self.runcheck().status,'response_unverified')
    def test_model_unavailable(self): self.catalog['models']=[];self.assertEqual(self.runcheck().status,'model_unavailable')
    def test_nontext_model(self): self.catalog['models'][0]['output_modalities']=['image'];self.assertEqual(self.runcheck().status,'model_unavailable')
    def test_duplicate_model(self): self.catalog['models']*=2;self.assertEqual(self.runcheck().status,'response_unverified')
    def test_http_failures(self):
        for code,status in [(401,'authentication_refused'),(403,'permission_refused'),(429,'rate_limited'),(402,'billing_unverified'),(302,'connection_unverified'),(500,'connection_unverified')]:
            self.override[PATHS[0]]=(code,b'PRIVATE');self.assertEqual(self.runcheck().status,status)
        self.assertEqual(self.calls,[PATHS[0]]*6)
    def test_second_call_failure(self): self.override[PATHS[1]]=(403,b'');self.assertEqual(self.runcheck().status,'permission_refused')
    def test_duplicate_json(self): self.override[PATHS[0]]=(200,b'{"acls":[],"acls":[]}');self.assertEqual(self.runcheck().status,'connection_unverified')
    def test_oversize(self): self.override[PATHS[0]]=(200,b'x'*(LIMIT+1));self.assertEqual(self.runcheck().status,'connection_unverified')
    def test_error_redaction(self): self.override[PATHS[0]]=RuntimeError(KEY);self.assertNotIn(KEY,repr(self.runcheck()))
    def test_metadata_redaction(self): r=repr(self.runcheck());self.assertNotIn(KEY,r);self.assertNotIn('PRIVATE',r)
    def test_receipt_binding(self):
        r=self.runcheck();self.assertTrue(r.current(GEN,MODEL,ACL,399,True));self.assertFalse(r.current('b'*32,MODEL,ACL,399,True));self.assertFalse(r.current(GEN,'other',ACL,399,True));self.assertFalse(r.current(GEN,MODEL,ACL,399,False));self.assertFalse(r.current(GEN,MODEL,ACL,400,True));self.assertFalse(r.current(GEN,MODEL,ACL,99,True))
    def test_policy_change(self): self.assertFalse(self.runcheck().current(GEN,MODEL,'api-key:endpoint:Other',100,True))
    def test_refused_receipt(self): self.inspect_disabled=self.runcheck(opted_in=False);self.assertFalse(self.inspect_disabled.current(GEN,MODEL,ACL,100,True))
    def test_bad_context(self):
        for args in [dict(endpoint_acl='api-key:endpoint:*'),dict(model='*'),dict(generation='oops'),dict(now=True)]:
            with self.assertRaises(ValueError):self.runcheck(**args)
        self.assertFalse(self.calls)
    def test_fixed_transport(self):
        conn=Mock();conn.getresponse.return_value.status=302
        with patch('grok_verify.http.client.HTTPSConnection',return_value=conn) as make:
            self.assertEqual(https(KEY,PATHS[0]),(302,b''));self.assertEqual(make.call_args.args,('api.x.ai',443));self.assertEqual(conn.request.call_args.args,('GET',PATHS[0]));conn.request.assert_called_once();conn.close.assert_called_once();conn.getresponse.return_value.read.assert_not_called()
    def test_no_other_endpoint(self):
        with patch('grok_verify.http.client.HTTPSConnection') as make:
            with self.assertRaises(ValueError):https(KEY,'/v1/responses')
            make.assert_not_called()

if __name__=='__main__': unittest.main()
