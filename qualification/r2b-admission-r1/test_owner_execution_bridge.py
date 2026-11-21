"""R2B R1 authoritative owner transaction bridge synthetic receipts.

Source-only tests; the broker is NOT positively GUI-authorized in Debian.
A synthetic protected review callback proves transactional ordering only.
No provider URL, credential export or real worker is available.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from paper_credential_vault import PaperVault
from managed_paper_vault import ManagedPaperVault
from vault_owner_hooks import Attempt, Refused, ROLES
from approval_ledger import Ledger, Context, Witness
from owner_execution_bridge import begin_once,finish_verified
from worker_receipt_verifier import WORKER,_signed_bytes

KEY=b'R'*32
BOOT='f'*8+'-'+'f'*4+'-'+'f'*4+'-'+'f'*4+'-'+'f'*12
VALUES={'kraken_read_key':'synthetic-read-api','kraken_read_secret':'synthetic-read-secret'}


class OwnerBridge(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        path=Path(self.tmp.name)
        path.chmod(0o700)
        self.p1=patch('managed_paper_vault._require_owner',return_value=None)
        self.p2=patch('owner_execution_bridge._require_owner',return_value=None)
        self.p1.start();self.p2.start()
        vault=PaperVault(path,Fernet.generate_key())
        policies={role:hashlib.sha256(('maj-vaj:r2b:stored-unverified:v1:'+role).encode()).hexdigest() for role in ROLES}
        self.vault=ManagedPaperVault(vault,b't'*32,policies)
        self.vault.migrate_empty(100)
        self.vault.enroll('kraken_read',VALUES,101)
        with vault.transaction() as db:
            owner=self.vault._owner(db)
            state=self.vault._consistent(db,owner)
            gen=state['roles']['kraken_read']['generation']
        self.ctx=Context('provider_test','kraken_read',gen,policies['kraken_read'])
        self.clock=[1_000_000_000_000]
        self.ledger=Ledger(path/'approvals.sqlite',
             clock=lambda:self.clock[0],boot=lambda:BOOT)
        self.witness=Witness('c42',4312)
    def tearDown(self):
        self.p2.stop();self.p1.stop();self.tmp.cleanup()
    def approve(self):
        rid=self.ledger.prepare(self.ctx)
        self.ledger.review_with_native_prompt(rid,
             witness=lambda:self.witness,prompt=lambda:True,
             reattest=lambda:self.witness)
        return rid
    def consume(self,rid,ctx):
        return self.ledger.consume(rid,ctx,live_session=lambda w:w==self.witness)==ctx
    def attempt(self,rid):
        return begin_once(self.vault,self.ctx,
              consume=lambda c:self.consume(rid,c),now=103)
    @staticmethod
    def receipt(a,result='provider_refused',key=KEY):
        d={'version':1,'worker':WORKER[a.role],'role':a.role,
           'generation':a.generation,'attempt_id':a.attempt_id,
           'policy':a.policy,'started':a.started,'nonce':a.nonce,
           'result':result,'observed':a.started+1,'mac':'0'*64}
        d['mac']=hmac.new(key,_signed_bytes(d),hashlib.sha256).hexdigest()
        return (json.dumps(d,sort_keys=True,separators=(',',':'))+'\n').encode()
    def finish(self,a,*,worker_uid=None,payload=None):
        l,r=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            r.sendall(self.receipt(a) if payload is None else payload)
            r.shutdown(socket.SHUT_WR)
            return finish_verified(self.vault,a,worker_conn=l,
                 worker_uid=os.getuid() if worker_uid is None else worker_uid,
                 signing_key=KEY,now=104)
        finally:l.close();r.close()

    def test_approval_spent_before_owner_begin_and_signed_result(self):
        rid=self.approve();attempt=self.attempt(rid)
        self.assertEqual(self.ledger.state_for_test(rid),'consumed')
        self.assertEqual(attempt.generation,self.ctx.generation)
        self.assertGreater(attempt.nonce,0)
        self.assertEqual(self.finish(attempt),'provider_refused')
        status=self.vault.status()
        self.assertEqual(status['credentials']['kraken_read_key'],'stored_unverified')
        self.assertFalse(status['connections_verified'])
        self.assertFalse(status['deployment_ready'])

    def test_reservation_cannot_reuse_approval(self):
        rid=self.approve();self.attempt(rid)
        with self.assertRaises(Refused):self.attempt(rid)
        self.assertEqual(self.ledger.state_for_test(rid),'consumed')

    def test_unsigned_or_wrong_worker_result_never_finishes(self):
        rid=self.approve();attempt=self.attempt(rid)
        with self.assertRaises(Refused):
            self.finish(attempt,worker_uid=os.getuid()+1)
        self.assertEqual(self.finish(attempt),'provider_refused')

    def test_duplicate_signed_result_refused_after_transaction(self):
        rid=self.approve();attempt=self.attempt(rid)
        self.assertEqual(self.finish(attempt),'provider_refused')
        with self.assertRaises(Refused):self.finish(attempt)

    def test_unreviewed_approval_cannot_reserve(self):
        rid=self.ledger.prepare(self.ctx)
        with self.assertRaises(Refused):self.attempt(rid)

    def test_untrusted_truthy_consume_does_not_reserve(self):
        with self.assertRaises(Refused):
            begin_once(self.vault,self.ctx,consume=lambda _:'approved',now=103)

    def test_stale_credential_generation_refused_before_consume(self):
        rid=self.approve()
        self.vault.enroll('kraken_read',
            {'kraken_read_key':'new-read-only',
             'kraken_read_secret':'new-read-secret'},102)
        with self.assertRaises(Refused):self.attempt(rid)
        self.assertEqual(self.ledger.state_for_test(rid),'approved')

    def test_concurrent_re_enroll_after_spending_loses_approval(self):
        rid=self.approve()
        def race(ctx):
            authorized=self.consume(rid,ctx)
            self.vault.enroll('kraken_read',
                {'kraken_read_key':'new-read-only',
                 'kraken_read_secret':'new-read-secret'},102)
            return authorized
        with self.assertRaises(Refused):
            begin_once(self.vault,self.ctx,consume=race,now=103)
        self.assertEqual(self.ledger.state_for_test(rid),'consumed')

    def test_interrupted_attempt_not_auto_retried(self):
        rid=self.approve();attempt=self.attempt(rid)
        self.vault.startup_invalidate(104)
        with self.assertRaises(Refused):self.finish(attempt)

    def test_other_provider_roles_refused_before_consuming(self):
        for role in ('telegram_desk','telegram_recovery','grok'):
            ctx=Context('provider_test',role,'a'*32,'b'*64)
            with self.subTest(role=role),self.assertRaises(Refused):
                begin_once(self.vault,ctx,consume=lambda _:True,now=103)

if __name__=='__main__':unittest.main()
