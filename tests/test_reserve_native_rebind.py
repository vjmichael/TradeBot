"""Actual UID and SQLite custody checks; fixture production-ledger replay."""
import json
import os
import shutil
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch
from test_reserve_native_migration import NativeMigrationTests, AUTH, DESK, NATIVE, INSTANCE, SCOPE, ROOT, sql
from reserve_native_migration import stage_pristine
from reserve_native_rebind import issue

OLD={'binding':'a'*64,'key_sha':'b'*64}
HEAD='c'*64
PRIOR={'old_provision':OLD,'ledger_count':1,'ledger_head':HEAD}

def fixture_ledger(db, scope):
    assert scope==SCOPE
    return PRIOR

# Deliberately fixture replay; production replay remains a separate test gate.
FAKE='from types import SimpleNamespace;sys.modules["financial_ledger"]=SimpleNamespace(replay=lambda db: ({},None,1,"'+HEAD+'"));'

class RebindTests(NativeMigrationTests):
    def setUp(self):
        super().setUp()
        for name in ('reserve_native_rebind.py',):shutil.copyfile(ROOT/name,self.root/name)
        self.records=self.root/'records';self.records.mkdir(mode=0o755)
        with sqlite3.connect(self.export/'desk.sqlite3') as db:
            db.execute('CREATE TABLE instances(id TEXT,pair TEXT,virtual_cents INTEGER,mode TEXT)')
            db.execute('INSERT INTO instances VALUES(?,?,?,?)',(INSTANCE,'BTC/USD',10000,'PAPER'))
            db.execute('CREATE TABLE gate_events(intent_id TEXT,action TEXT,detail TEXT)')
            db.execute('INSERT INTO gate_events VALUES(?,?,?)',(INSTANCE,'workspace_provisioned',json.dumps(OLD)))
            db.execute('INSERT INTO fin_events VALUES(1,?,?)',('fund',HEAD))
            db.execute('UPDATE fin_head SET count=1,digest=?',(HEAD,))
        with patch('reserve_native_migration.pristine_ledger',fixture_ledger):
            self.candidate=stage_pristine(self.export,self.root/'native-data',self.review(),native_uid=NATIVE,desk_uid=DESK,authority_uid=AUTH)
    def sign(self):
        with patch('reserve_native_rebind.pristine_ledger',fixture_ledger):
            return issue(self.export,self.root/'native-data',self.records,self.review(),self.store,self.seed,native_uid=NATIVE,desk_uid=DESK,authority_uid=AUTH)
    def check_code(self,mutate=''):
        # Native reads a separate SQLite ledger fixture, not the root cold export.
        p=self.root/'native-data'/'test-desk.sqlite3'
        if not p.exists():shutil.copyfile(self.export/'desk.sqlite3',p);os.chown(p,NATIVE,NATIVE);p.chmod(0o600)
        return FAKE+'from reserve_native_rebind import verify;c=sqlite3.connect(str(r/"native-data"/"test-desk.sqlite3"),isolation_level=None);c.row_factory=sqlite3.Row;c.execute("BEGIN");'+mutate+f'print(verify(c,r/"native-data",r/"records",r/"relay.pub",{INSTANCE!r},native_uid={NATIVE},desk_uid={DESK},authority_uid={AUTH})["binding"])'
    def test_rebind_preserves_original_and_refuses_overwrite(self):
        before=self.review();body=self.sign();self.assertEqual(self.review(),before)
        self.assertEqual(body['prior'],PRIOR);self.assertEqual(body['new']['binding'],self.candidate['binding'])
        self.assertEqual(self.run_as(NATIVE,self.check_code()),body['new']['binding'])
        with self.assertRaises(FileExistsError):self.sign()
        for uid in (DESK,AUTH):self.refusal(uid,self.check_code())
        self.refusal(NATIVE,f'(r/"records"/{INSTANCE+".json"!r}).unlink()')
    def test_public_binding_independent_of_native_private_files(self):
        body=self.sign()
        # Desk/authority get their own read-only ledger copy, never native keys.
        for uid in (DESK,AUTH):
            p=self.root/f'ledger-{uid}.sqlite3';shutil.copyfile(self.export/'desk.sqlite3',p);os.chown(p,uid,uid);p.chmod(0o400)
            code=FAKE+f'from reserve_native_rebind import verify_record;c=sqlite3.connect((r/"ledger-{uid}.sqlite3").as_uri()+"?mode=ro",uri=True);c.row_factory=sqlite3.Row;c.execute("BEGIN");print(verify_record(c,r/"records",r/"relay.pub",{INSTANCE!r},native_uid={NATIVE},desk_uid={DESK},authority_uid={AUTH})["new"]["binding"])'
            self.assertEqual(self.run_as(uid,code),body['new']['binding'])
            self.refusal(uid,f'(r/"native-data"/"paper"/{INSTANCE!r}/"receipt.key").read_bytes()')

    def test_refuse_modified_ledger_and_binding(self):
        self.sign()
        self.refusal(NATIVE,self.check_code('c.execute("UPDATE gate_events SET detail=\'{}\'");'))
        self.refusal(NATIVE,self.check_code('c.execute("UPDATE fin_events SET digest=?",("d"*64,));'))
        p=self.root/'native-data'/'paper'/INSTANCE/'receipt.key';p.write_bytes(os.urandom(32))
        self.refusal(NATIVE,self.check_code())
    def test_refuse_changed_stage_and_cold_export(self):
        p=self.root/'native-data'/'migration-candidate.json';raw=p.read_bytes();p.write_bytes(b'{}')
        with self.assertRaises(ValueError):self.sign()
        p.write_bytes(raw)
        source=self.root/'native-data'/'paper'/INSTANCE/'paper.sqlite';source.write_bytes(source.read_bytes()+b'x')
        with self.assertRaises(ValueError):self.sign()
    def test_refuse_busy_workspace(self):
        import fcntl
        p=self.root/'native-data'/'paper'/INSTANCE/'paper-worker.lock';p.touch(mode=0o600);os.chown(p,NATIVE,NATIVE)
        with p.open('rb+') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):self.sign()
    def test_refuse_signature_and_missing_receipt(self):
        self.refusal(NATIVE,self.check_code());self.sign()
        p=self.records/(INSTANCE+'.json');raw=json.loads(p.read_bytes());raw['signature']='0'*128
        from reserve_ipc import canonical
        p.write_bytes(canonical(raw));self.refusal(NATIVE,self.check_code())

# Inherited tests need a pristine un-staged setup and are executed in their original module.
for name in NativeMigrationTests.__dict__:
    if name.startswith('test_') and name not in RebindTests.__dict__:setattr(RebindTests,name,None)

if __name__=='__main__':
    import unittest
    unittest.main()
