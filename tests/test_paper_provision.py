"""Actual disk/SQLite provisioning tests with an explicitly fixture native initializer."""
from contextlib import closing, contextmanager
from pathlib import Path
import os
import shutil
import sqlite3
import tempfile
import unittest

from financial_ledger import AccountingError, initialize, replay, SCALE
from paper_provision import provision, installed_workspace, scope
from paper_custody import workspace_key

I='a'*32


def fixture_native(path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript('CREATE TABLE trades(id INTEGER PRIMARY KEY); CREATE TABLE orders(id INTEGER PRIMARY KEY);')
        db.commit()


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.data=Path(tmp.name);self.path=self.data/'desk.sqlite3'
        with self.tx() as db:
            db.executescript('CREATE TABLE desk(id INTEGER,paused INTEGER,virtual_cap_cents INTEGER); INSERT INTO desk VALUES(1,1,10000); CREATE TABLE instances(id TEXT,pair TEXT,virtual_cents INTEGER,mode TEXT); CREATE TABLE gate_intents(id TEXT,instance_id TEXT); CREATE TABLE gate_events(seq INTEGER PRIMARY KEY,intent_id TEXT,action TEXT,at INTEGER,detail TEXT);')
            db.execute('INSERT INTO instances VALUES(?,?,10000,?)',(I,'BTC/USD','PAPER'));initialize(db)
        self.work=self.data/'paper'/I

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            try:yield db;db.commit()
            except BaseException:db.rollback();raise

    def provision(self,**kw):return provision(self.data,I,self.tx,initialize=fixture_native,**kw)

    def test_atomic_setup_and_duplicate_no_reset(self):
        self.assertEqual(self.provision()['state'],'provisioned');first=workspace_key(self.data,I)
        with self.tx() as db:
            self.assertEqual(replay(db)[0][I]['active'],100*SCALE)
            self.assertEqual(installed_workspace(self.data,I,db),self.work)
        with closing(sqlite3.connect(self.work/'paper.sqlite')) as db:db.execute('INSERT INTO trades VALUES(1)');db.commit()
        self.assertEqual(self.provision()['state'],'already_provisioned');self.assertEqual(workspace_key(self.data,I),first)
        with closing(sqlite3.connect(self.work/'paper.sqlite')) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM trades').fetchone()[0],1)
        with self.tx() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM fin_events WHERE kind='fund'").fetchone()[0],1)

    def test_resume_after_files_published_but_transaction_rolled_back(self):
        @contextmanager
        def rollback():
            with self.tx() as db:
                yield db
                raise RuntimeError('interrupted commit')
        with self.assertRaisesRegex(RuntimeError,'interrupted'):provision(self.data,I,rollback,initialize=fixture_native)
        first=workspace_key(self.data,I)
        with self.tx() as db:self.assertEqual(replay(db)[0],{})
        self.assertEqual(self.provision()['state'],'provisioned');self.assertEqual(first,workspace_key(self.data,I))

    def test_failure_before_publication_does_not_fund(self):
        def fail(path):fixture_native(path);raise RuntimeError('initialization interrupted')
        with self.assertRaises(RuntimeError):provision(self.data,I,self.tx,initialize=fail)
        self.assertFalse(self.work.exists())
        with self.tx() as db:self.assertEqual(replay(db)[0],{})
        self.provision()

    def test_lost_workspace_never_reinitialized(self):
        self.provision();shutil.rmtree(self.work)
        with self.assertRaises((AccountingError,OSError)):self.provision()
        self.assertFalse(self.work.exists())

    def test_tampered_identity_and_nonprivate_key_rejected(self):
        self.provision();p=self.work/'receipt.key';p.chmod(0o644)
        with self.assertRaisesRegex(AccountingError,'private_receipt_key'):self.provision()
        p.chmod(0o600);p.write_bytes(b'z'*32)
        with self.assertRaisesRegex(AccountingError,'authentication_failed'):self.provision()

    def test_unmanifested_workspace_refused_no_overwrite(self):
        self.work.mkdir(parents=True,mode=0o700);self.work.parent.chmod(0o700)
        marker=self.work/'legacy';marker.write_text('old')
        with self.assertRaises((OSError,AccountingError)):self.provision()
        self.assertEqual(marker.read_text(),'old')

    def test_scope_and_pause_gate(self):
        with self.assertRaisesRegex(AccountingError,'invalid_installed_instance_id'):provision(self.data,'..',self.tx,initialize=fixture_native)
        with self.tx() as db:expected=scope(db,I);db.execute('UPDATE instances SET virtual_cents=9900')
        with self.assertRaisesRegex(AccountingError,'provision_review_changed'):self.provision(reviewed=expected)
        with self.tx() as db:db.execute('UPDATE desk SET paused=0')
        with self.assertRaisesRegex(AccountingError,'provision_requires_pause'):self.provision()

    def test_history_blocks_fresh_native_workspace(self):
        with self.tx() as db:db.execute('INSERT INTO gate_intents VALUES(?,?)',('old',I))
        with self.assertRaisesRegex(AccountingError,'existing_execution_history'):self.provision()


if __name__=='__main__':unittest.main()
