"""Integration gate for the real ledger and Freqtrade schema. PAPER fixtures only.

Run explicitly in the pinned native environment. Skips do not qualify it.
"""
from contextlib import closing, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

INSTANCE='a'*32
SCOPE={'instance':INSTANCE,'pair':'BTC/USD','principal_cents':10000,'mode':'PAPER'}


@unittest.skipUnless(importlib.util.find_spec('financial_ledger') is not None,'exact financial ledger checkout required')
class RealLedgerMigrationTests(unittest.TestCase):
    def setUp(self):
        from financial_ledger import FinancialLedger, initialize
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'desk.sqlite3'
        with self.tx() as db:
            db.execute('CREATE TABLE desk(id INTEGER PRIMARY KEY,virtual_cap_cents INTEGER,paused INTEGER,human_pause INTEGER)')
            db.execute('INSERT INTO desk VALUES(1,10000,1,1)')
            db.execute('CREATE TABLE instances(id TEXT PRIMARY KEY,pair TEXT,virtual_cents INTEGER,mode TEXT)')
            db.execute('INSERT INTO instances VALUES(?,?,10000,?)',(INSTANCE,'BTC/USD','PAPER'))
            db.execute('CREATE TABLE gate_intents(id TEXT PRIMARY KEY,instance_id TEXT,pair TEXT,notional INTEGER,reserved INTEGER,state TEXT)')
            db.execute('CREATE TABLE gate_events(seq INTEGER PRIMARY KEY,intent_id TEXT,action TEXT,at INTEGER,detail TEXT)')
            db.execute('INSERT INTO gate_events(intent_id,action,at,detail) VALUES(?,?,?,?)',(INSTANCE,'workspace_provisioned',1,json.dumps({'binding':'a'*64,'key_sha':'b'*64})))
            initialize(db)
        self.ledger=FinancialLedger(self.tx,clock=lambda:1800000000)
        self.ledger.fund('fund:'+INSTANCE,INSTANCE)

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:yield db;db.commit()
            except BaseException:db.rollback();raise

    def test_real_pristine_replay_and_history_guard(self):
        from reserve_native_migration import pristine_ledger
        from financial_ledger import replay, SCALE
        with self.tx() as db:
            receipt=pristine_ledger(db,SCOPE)
            self.assertEqual(receipt['ledger_head'],replay(db)[3])
            self.assertEqual(receipt['old_provision']['binding'],'a'*64)
            db.execute('UPDATE desk SET human_pause=0')
        with self.tx() as db:
            with self.assertRaises(ValueError):pristine_ledger(db,SCOPE)
            db.execute('UPDATE desk SET human_pause=1')
            db.execute('INSERT INTO gate_intents VALUES(?,?,?,?,?,?)',('i',INSTANCE,'BTC/USD',9900,10000,'CLAIMED'))
        self.ledger.buy('buy',INSTANCE,'i',SCALE,98*SCALE,2*SCALE)
        self.ledger.sell('sell',INSTANCE,'exit',SCALE,105*SCALE,0)
        self.ledger.settle('settle',INSTANCE,'sell',105*SCALE)
        with self.tx() as db:
            db.execute('DELETE FROM gate_intents')
            with self.assertRaisesRegex(ValueError,'nonpristine_financial_history'):pristine_ledger(db,SCOPE)


@unittest.skipUnless(importlib.util.find_spec('freqtrade') is not None,'pinned Freqtrade runtime required')
class NativeSchemaMigrationTests(unittest.TestCase):
    def test_actual_native_initialization_cold_export(self):
        from paper_provision import native_init
        from reserve_native_migration import pristine_native, check_sqlite
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'paper.sqlite';path.touch(mode=0o600)
            native_init(path)
            raw=path.read_bytes();pristine_native(raw)
            with closing(check_sqlite(raw)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM trades').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],0)

if __name__=='__main__':unittest.main()
