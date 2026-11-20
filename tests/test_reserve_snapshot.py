import sqlite3
import unittest
from contextlib import closing
from reserve_snapshot import TABLES, project, open_projected, verify_shape

CANARY='synthetic-secret-canary-not-a-real-credential'

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        for name,(definition,_) in TABLES.items():
            self.db.execute(f'CREATE TABLE {name}({definition})')
        self.db.execute('ALTER TABLE gate_intents ADD COLUMN claim_token TEXT')
        self.db.execute('CREATE TABLE secrets(slot TEXT,ciphertext BLOB)')
        self.db.execute('INSERT INTO secrets VALUES(?,?)',('grok',CANARY))
        self.db.execute('CREATE TABLE unrelated(value TEXT)')
        self.db.execute('INSERT INTO unrelated VALUES(?)',(CANARY,))
        self.db.execute('INSERT INTO gate_intents VALUES(?,?,?,?,?,?,?,?,?)',('i','a','VOID',5,100,90,110,1000,CANARY))
        self.db.execute("INSERT INTO gate_events VALUES(1,'i','native_released',1,'{}')")
        self.db.execute("INSERT INTO gate_events VALUES(2,'i','error',2,?)",(CANARY,))
        import json
        self.db.execute("INSERT INTO gate_events VALUES(3,'i','native_bound',3,?)",(json.dumps({'binding':'a'*64,'extra':CANARY}),))
        self.db.execute("INSERT INTO gate_current VALUES('policy','desk','cur')")
        self.db.execute("INSERT INTO gate_current VALUES('evidence','desk',?)",(CANARY,))
        self.db.execute("INSERT INTO gate_inputs VALUES('policy','desk','cur','{}','hash')")
        self.db.execute("INSERT INTO gate_inputs VALUES('policy','desk','old',?,'hash')",(CANARY,))
        self.db.execute("INSERT INTO gate_inputs VALUES('evidence','desk',?,'{}','hash')",(CANARY,))
        self.db.execute("INSERT INTO fin_events VALUES(1,'fund:a','a','fund','{}','prev','digest')")
        self.db.commit();self.db.execute('BEGIN')

    def test_minimized_snapshot_excludes_unrelated_records_columns_and_bytes(self):
        raw=project(self.db)
        self.assertNotIn(CANARY.encode(),raw)
        with closing(open_projected(raw)) as result:
            self.assertEqual(result.execute('SELECT * FROM gate_intents').fetchall(),[('i','a','VOID',5,100,90,110,1000)])
            self.assertEqual(result.execute('SELECT action FROM gate_events').fetchall(),[('native_released',),('native_bound',)])
            self.assertEqual(result.execute("SELECT detail FROM gate_events WHERE action='native_bound'").fetchone()[0],'{"binding":"'+'a'*64+'"}')
            self.assertEqual(result.execute('SELECT receipt FROM gate_inputs').fetchall(),[('cur',)])
            self.assertEqual(result.execute('SELECT * FROM fin_events').fetchall(),self.db.execute('SELECT * FROM fin_events').fetchall())
            with self.assertRaises(sqlite3.OperationalError):result.execute('SELECT * FROM secrets')
            with self.assertRaises(sqlite3.OperationalError):result.execute('DELETE FROM fin_events')
        self.assertEqual(self.db.execute('SELECT ciphertext FROM secrets').fetchone()[0],CANARY)

    def test_reader_refuses_full_database(self):
        raw=self.db.serialize()
        with self.assertRaises(ValueError):open_projected(raw)

    def test_missing_source_table_and_column(self):
        self.db.execute('DROP TABLE reserve_drafts')
        with self.assertRaises(ValueError):project(self.db)
        self.db.execute('CREATE TABLE reserve_drafts(instance TEXT)')
        with self.assertRaises(ValueError):project(self.db)

    def test_receiver_refuses_extra_schema_and_rows(self):
        with closing(sqlite3.connect(':memory:')) as mod:
            mod.deserialize(project(self.db))
            mod.execute("INSERT INTO gate_inputs VALUES('evidence','desk','x','{}','hash')")
            with self.assertRaises(ValueError):verify_shape(mod)
            mod.execute("DELETE FROM gate_inputs WHERE kind='evidence'")
            verify_shape(mod)
            mod.execute('CREATE TABLE unexpected(value TEXT)')
            with self.assertRaises(ValueError):verify_shape(mod)

    def test_requires_transaction(self):
        self.db.rollback()
        with self.assertRaises(ValueError):project(self.db)
