"""R2B synthetic-only worker signature / peer acceptance.

Positive results are forbidden even if a trusted test signer has the key.
The worker's only semantic output is provider_refused, never permissions
verified or any LIVE/trading grant.
"""
import json
import os
import socket
import unittest
from io import BytesIO
from unittest.mock import patch
from vault_owner_hooks import Attempt,Refused
from synthetic_worker_protocol import (
    sign_denial,verify_denial,one_frame,receive_verified,WORKER)

A=Attempt('kraken_read','a'*32,'b'*32,'c'*64,1000,1111)
KEY=b'T'*32


class SyntheticWorker(unittest.TestCase):
    def valid(self):
        return sign_denial(A,KEY,1001)
    def test_only_negative_result(self):
        d=self.valid()
        self.assertEqual(d['worker'],WORKER)
        self.assertEqual(d['result'],'provider_refused')
        self.assertEqual(verify_denial(d,A,KEY,1001),'provider_refused')
    def test_modified_positive_result_rejected(self):
        d=self.valid();d['result']='permissions_verified'
        with self.assertRaises(Refused):verify_denial(d,A,KEY,1001)
    def test_wrong_key_rejected(self):
        with self.assertRaises(Refused):verify_denial(self.valid(),A,b'X'*32,1001)
    def test_stale_or_clock_change_rejected(self):
        for now in (999,1300,True):
            with self.subTest(now=now),self.assertRaises(Refused):
                verify_denial(self.valid(),A,KEY,now)
    def test_changed_nonce_refused(self):
        other=Attempt(A.role,A.generation,A.attempt_id,A.policy,A.started,1112)
        with self.assertRaises(Refused):
            verify_denial(self.valid(),other,KEY,1001)
    def test_changed_generation_and_policy_refused(self):
        for other in (
            Attempt(A.role,'d'*32,A.attempt_id,A.policy,A.started,A.nonce),
            Attempt(A.role,A.generation,A.attempt_id,'e'*64,A.started,A.nonce),
            Attempt(A.role,A.generation,'f'*32,A.policy,A.started,A.nonce)):
            with self.subTest(other=other),self.assertRaises(Refused):
                verify_denial(self.valid(),other,KEY,1001)
    def test_boolean_or_unknown_signing_input_refused(self):
        with self.assertRaises(Refused):
            sign_denial(Attempt('kraken_read','a'*32,'b'*32,'c'*64,1000,True),KEY,1001)
        with self.assertRaises(Refused):
            sign_denial(Attempt('telegram_desk','a'*32,'b'*32,'c'*64,1000,1111),KEY,1001)
    def test_extra_or_duplicate_field_refused(self):
        d=self.valid();d['endpoint']='https://bad.invalid'
        with self.assertRaises(Refused):verify_denial(d,A,KEY,1001)
        frame=b'{"role":"kraken_read","role":"kraken_read"}\n'
        l,r=socket.socketpair()
        try:
            r.sendall(frame);r.shutdown(socket.SHUT_WR)
            with self.assertRaises(Refused):one_frame(l)
        finally:l.close();r.close()
    def test_trailing_frames_and_nul_refused(self):
        for frame in (b'{}\n{}\n',b'{}\x00\n',b'a'*2000+b'\n'):
            l,r=socket.socketpair()
            try:
                r.sendall(frame);r.shutdown(socket.SHUT_WR)
                with self.assertRaises(Refused):one_frame(l)
            finally:l.close();r.close()
    def test_actual_af_unix_peer_required(self):
        l,r=socket.socketpair()
        try:
            r.sendall((json.dumps(self.valid(),sort_keys=True,separators=(',',':'))+'\n').encode())
            r.shutdown(socket.SHUT_WR)
            self.assertEqual(receive_verified(l,A,KEY,1001,worker_uid=os.getuid()),'provider_refused')
        finally:l.close();r.close()
    def test_wrong_kernel_uid_refused(self):
        l,r=socket.socketpair()
        try:
            r.sendall((json.dumps(self.valid())+'\n').encode())
            r.shutdown(socket.SHUT_WR)
            with self.assertRaises(Refused):
                receive_verified(l,A,KEY,1001,worker_uid=os.getuid()+42)
        finally:l.close();r.close()

if __name__=='__main__':unittest.main()
