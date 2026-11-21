"""Real Linux AF_UNIX kernel-peer / bounded-frame receipt tests.

Synthetic signer is unit-only. No network service, external provider,
credential key, real broker approval or installed worker is authorized.
"""
import hashlib
import hmac
import json
import os
import socket
import unittest
from vault_owner_hooks import Attempt,Refused
from worker_receipt_verifier import WORKER,_signed_bytes
from receipt_channel import read_authenticated_receipt

KEY=b'S'*32
A=Attempt('kraken_read','a'*32,'b'*32,'c'*64,1000,1000000)


def signed(**updates):
    data={'version':1,'worker':WORKER[A.role],'role':A.role,
        'generation':A.generation,'attempt_id':A.attempt_id,
        'policy':A.policy,'started':A.started,'nonce':A.nonce,
        'result':'permissions_verified','observed':1001,'mac':'0'*64}
    data.update(updates)
    data['mac']=hmac.new(KEY,_signed_bytes(data),hashlib.sha256).hexdigest()
    return data


class WorkerChannel(unittest.TestCase):
    def transfer(self,bytes_,*,uid=None,now=1002,attempt=A,key=KEY):
        left,right=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            right.sendall(bytes_)
            right.shutdown(socket.SHUT_WR)
            return read_authenticated_receipt(left,worker_uid=os.getuid() if uid is None else uid,
                attempt=attempt,signing_key=key,now=now)
        finally:left.close();right.close()

    def frame(self,data):
        return (json.dumps(data,sort_keys=True,separators=(',',':'))+'\n').encode('ascii')

    def test_actual_af_unix_valid_signed_worker(self):
        self.assertEqual(self.transfer(self.frame(signed())),'permissions_verified')
    def test_wrong_kernel_uid_refused(self):
        with self.assertRaises(Refused):self.transfer(self.frame(signed()),uid=os.getuid()+1)
    def test_bad_mac_refused(self):
        d=signed();d['mac']='0'*64
        with self.assertRaises(Refused):self.transfer(self.frame(d))
    def test_short_key_refused(self):
        with self.assertRaises(Refused):self.transfer(self.frame(signed()),key=b'A')
    def test_other_attempt_refused(self):
        b=Attempt('kraken_read','a'*32,'e'*32,'c'*64,1000,1000000)
        with self.assertRaises(Refused):self.transfer(self.frame(signed()),attempt=b)
    def test_stale_refused(self):
        with self.assertRaises(Refused):self.transfer(self.frame(signed()),now=1300)
    def test_duplicate_json_fields_refused(self):
        frame=self.frame(signed())
        frame=frame.replace(b'"role":"kraken_read",',
            b'"role":"kraken_read","role":"kraken_read",')
        with self.assertRaises(Refused):self.transfer(frame)
    def test_trailing_second_frame_refused(self):
        with self.assertRaises(Refused):self.transfer(self.frame(signed())+self.frame(signed()))
    def test_nul_refused(self):
        with self.assertRaises(Refused):self.transfer(self.frame(signed())[:-1]+b'\x00\n')
    def test_partial_and_missing_eof_refused(self):
        with self.assertRaises(Refused):self.transfer(b'{"version":')
    def test_overlong_frame_refused(self):
        with self.assertRaises(Refused):self.transfer(b'x'*2050+b'\n')
    def test_boolean_timestamp_refused(self):
        with self.assertRaises(Refused):self.transfer(self.frame(signed(observed=True)))
    def test_fake_client_chosen_uid_not_allowed_in_schema(self):
        d=signed();d['peer_uid']=os.getuid()
        with self.assertRaises(Refused):self.transfer(self.frame(d))
    def test_unknown_provider_result_refused(self):
        with self.assertRaises(Refused):self.transfer(self.frame(signed(result='withdrawal_ok')))

if __name__=='__main__':unittest.main()
