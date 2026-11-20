import json
import unittest
from unittest.mock import Mock, patch
from telegram_verify import Pairing, Refused, TEXT, https

G='a'*32; H='b'*32; T='12345:'+ 'A'*32; U='67890:'+ 'B'*32; CHAT=111111

class Fake:
    def __init__(self): self.calls=[]; self.overrides={}
    def __call__(self, token, method, payload):
        self.calls.append((method,payload.copy())); bot=int(token.split(':')[0]); chat={'id':payload.get('chat_id',CHAT),'type':'private'}
        obj={'getMe':{'id':bot,'is_bot':True,'username':'SyntheticBot'},'getChat':chat,'sendMessage':{'message_id':2,'chat':chat,'from':{'id':bot,'is_bot':True},'text':payload.get('text')}}[method]
        result=self.overrides.get(method,(200,json.dumps({'ok':True,'result':obj}).encode()))
        if isinstance(result,Exception): raise result
        return result

class Tests(unittest.TestCase):
    def setUp(self): self.f=Fake(); self.p=Pairing(self.f)
    def preview(self,role='desk',gen=G,token=T,chat=CHAT,now=100): return self.p.preview(role,gen,token,chat,now)
    def confirm(self,p,approved=True,token=T,now=101,gen=None): return self.p.confirm_test(p.role,gen or p.generation,token,p.review_id,approved,now)
    def obj(self,method,data): self.f.overrides[method]=(200,json.dumps({'ok':True,'result':data}).encode())
    def sent(self): return [v for m,v in self.f.calls if m=='sendMessage']
    def test_preview_no_send(self): self.preview(); self.assertEqual(self.sent(),[]); self.assertEqual(self.p.status('desk',G),'awaiting_confirmation')
    def test_desk(self): self.assertEqual(self.confirm(self.preview()),'test_accepted_by_telegram'); self.assertEqual(self.sent(),[{'chat_id':CHAT,'text':TEXT['desk'],'protect_content':True,'allow_paid_broadcast':False}])
    def test_recovery(self): self.assertEqual(self.confirm(self.preview('recovery',H,U),token=U),'test_accepted_by_telegram'); self.assertEqual(self.sent()[0]['text'],TEXT['recovery'])
    def test_two_roles_same_recipient(self): self.confirm(self.preview()); self.confirm(self.preview('recovery',H,U),token=U); self.assertEqual(len(self.sent()),2)
    def test_same_bot_pending(self): self.preview(); self.assertRaises(Refused,self.preview,'recovery',H,T); self.assertFalse(self.sent())
    def test_same_bot_bound(self): self.confirm(self.preview()); self.assertRaises(Refused,self.preview,'recovery',H,T)
    def test_cancel(self): p=self.preview(); self.assertEqual(self.confirm(p,False),'cancelled'); self.assertFalse(self.sent()); self.assertRaises(Refused,self.confirm,p)
    def test_strict_confirmation(self): self.assertEqual(self.confirm(self.preview(),1),'cancelled'); self.assertFalse(self.sent())
    def test_generation(self): p=self.preview(); self.assertRaises(Refused,self.confirm,p,True,T,101,H); self.assertFalse(self.sent())
    def test_invalidate(self): p=self.preview(); self.p.invalidate('desk'); self.assertRaises(Refused,self.confirm,p); self.assertEqual(self.p.status('desk',G),'stored_unverified')
    def test_replacement(self): p=self.preview(); self.preview(gen=H); self.assertRaises(Refused,self.confirm,p); self.assertEqual(self.p.status('desk',G),'stored_unverified')
    def test_replay(self): p=self.preview(); self.confirm(p); self.assertRaises(Refused,self.confirm,p); self.assertEqual(len(self.sent()),1)
    def test_expiry(self): self.assertRaises(Refused,self.confirm,self.preview(),True,T,400); self.assertFalse(self.sent())
    def test_clock_rollback(self): self.assertRaises(Refused,self.confirm,self.preview(),True,T,99); self.assertFalse(self.sent())
    def test_recipient_mismatch(self): self.obj('getChat',{'id':99,'type':'private'}); self.assertRaises(Refused,self.preview); self.assertFalse(self.sent())
    def test_group_refusal(self): self.obj('getChat',{'id':CHAT,'type':'group'}); self.assertRaises(Refused,self.preview)
    def test_bad_ids(self):
        for chat in (-1,True,'@other',2**52,12345): self.assertRaises(Refused,self.preview,chat=chat)
    def test_bot_mismatch(self): self.obj('getMe',{'id':99,'is_bot':True,'username':'SyntheticBot'}); self.assertRaises(Refused,self.preview)
    def test_not_bot(self): self.obj('getMe',{'id':12345,'is_bot':False,'username':'SyntheticBot'}); self.assertRaises(Refused,self.preview)
    def test_changed_identity(self): p=self.preview(); self.assertRaises(Refused,self.confirm,p,True,U); self.assertFalse(self.sent())
    def test_recheck_recipient(self): p=self.preview(); self.obj('getChat',{'id':99,'type':'private'}); self.assertRaises(Refused,self.confirm,p); self.assertFalse(self.sent())
    def test_unknown_no_retry(self): p=self.preview(); self.f.overrides['sendMessage']=TimeoutError(T); self.assertEqual(self.confirm(p),'delivery_unknown'); self.assertRaises(Refused,self.confirm,p); self.assertEqual(len(self.sent()),1)
    def test_wrong_ack(self): p=self.preview(); self.obj('sendMessage',{'message_id':1,'chat':{'id':99,'type':'private'}}); self.assertEqual(self.confirm(p),'delivery_unknown')
    def test_bad_ack_sender(self): p=self.preview(); self.obj('sendMessage',{'message_id':1,'chat':{'id':CHAT,'type':'private'},'from':{'id':999,'is_bot':True},'text':TEXT['desk']}); self.assertEqual(self.confirm(p),'delivery_unknown')
    def test_malformed(self):
        for status,body in [(302,b''),(200,b'x'*65537),(200,b'{"ok":true,"ok":true,"result":{}}'),(200,b'{"ok":false}')]:
            self.f.overrides['getMe']=(status,body); self.assertRaises(Refused,self.preview)
    def test_error_is_fixed(self):
        self.f.overrides['getMe']=RuntimeError(T)
        with self.assertRaisesRegex(Refused,'^provider_unverified$') as e: self.preview()
        self.assertNotIn(T,str(e.exception)); self.assertIsNone(e.exception.__cause__)
    def test_invalid_token_no_call(self): self.assertRaises(Refused,self.preview,token='https://bad'); self.assertFalse(self.f.calls)
    def test_status_replacement(self): self.confirm(self.preview()); self.assertEqual(self.p.status('desk',H),'stored_unverified')
    def test_preview_repr(self): p=self.preview(); self.assertNotIn('SyntheticBot',repr(p)); self.assertNotIn(str(CHAT),repr(p)); self.assertNotIn(T,repr(p))
    def test_transport_fixed(self):
        conn=Mock(); conn.getresponse.return_value.status=302
        with patch('telegram_verify.http.client.HTTPSConnection',return_value=conn) as make:
            self.assertEqual(https(T,'getMe',{}),(302,b'')); make.assert_called_once(); self.assertEqual(make.call_args.args,('api.telegram.org',443)); conn.request.assert_called_once(); conn.close.assert_called_once(); conn.getresponse.return_value.read.assert_not_called()
    def test_no_arbitrary_method(self):
        with patch('telegram_verify.http.client.HTTPSConnection') as make:
            self.assertRaises(Refused,https,T,'getUpdates',{}); make.assert_not_called()

if __name__=='__main__': unittest.main()
