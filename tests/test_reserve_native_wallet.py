"""Cryptographic and SQLite boundary fixtures, not real native wallet acceptance."""
import copy
import os
import sqlite3
import sys
import types
import unittest
from contextlib import closing
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from reserve_native_wallet import _sign_observed, accounts, verify_observation

I='a'*32
HEAD='b'*64
ANCHOR='c'*64
BINDING='d'*64
SCOPE='fixture'


class PublicWalletTests(unittest.TestCase):
    def setUp(self):
        self.seed=os.urandom(32)
        self.public=Ed25519PrivateKey.from_private_bytes(self.seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.wallet={'instance':I,'pair':'BTC/USD','binding':BINDING,'head':HEAD,'anchor':ANCHOR,'boot':1,'at':100,'quote':10000,'qty':0}
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE instances(id TEXT,pair TEXT,mode TEXT)');self.db.execute('INSERT INTO instances VALUES(?,?,?)',(I,'BTC/USD','PAPER'))
        self.states={I:{'lots':[],'sales':[],'active':8000,'protected':2000,'pending':0}}
        fake=types.SimpleNamespace(replay=lambda db:(self.states,None,1,HEAD),observed=lambda s:{k:s[k] for k in ('active','protected','pending')}|{'qty':0},amount=lambda v:v)
        fixture=patch.dict(sys.modules,{'financial_ledger':fake});fixture.start();self.addCleanup(fixture.stop)

    def signed(self,**kw):return _sign_observed(self.seed,SCOPE,self.wallet|kw)
    def verify(self,p=None,now=100,bind=BINDING):return accounts(self.db,{I:p or self.signed()},lambda db,i:bind,self.public,SCOPE,now,HEAD,ANCHOR,1)

    def test_public_verification_without_native_secret(self):
        p=self.signed();self.assertEqual(verify_observation(self.public,SCOPE,p),self.wallet)
        self.assertEqual(self.verify(p)[I],{'active':8000,'protected':2000,'pending':0,'qty':0,'covered_qty':0})

    def test_tamper_wrong_pin_workspace_and_legacy_hmac(self):
        p=self.signed();p['body']['wallet']['quote']+=1
        for q in (p,{'body':self.wallet,'mac':'a'*64}):
            with self.assertRaises(ValueError):self.verify(q)
        for public,workspace in ((os.urandom(32),SCOPE),(self.public,'other')):
            with self.assertRaises(ValueError):verify_observation(public,workspace,self.signed())

    def test_exact_binding_head_boot_anchor_and_freshness(self):
        for change in ({'binding':'e'*64},{'head':'e'*64},{'anchor':'e'*64},{'boot':2},{'at':101},{'at':69}):
            with self.assertRaises(ValueError):self.verify(self.signed(**change))
        with self.assertRaises(ValueError):self.verify(bind='e'*64)
        with self.assertRaises(ValueError):self.verify(now=131)

    def test_unsafe_wallet_mismatch_nonflat_or_incomplete_scope(self):
        for change in ({'quote':9999},{'qty':1},{'pair':'ETH/USD'}):
            with self.assertRaises(ValueError):self.verify(self.signed(**change))
        self.states[I]['lots']=[1]
        with self.assertRaises(ValueError):self.verify()
        self.states[I]['lots']=[]
        self.db.execute('INSERT INTO instances VALUES(?,?,?)',('e'*32,'ETH/USD','PAPER'))
        with self.assertRaises(ValueError):self.verify()

if __name__=='__main__':unittest.main()
