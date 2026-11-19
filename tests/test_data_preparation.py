"""Real subprocess custody with deterministic child work, no network or credentials."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from data_preparation import Preparation,_db
from market_memory import MemoryUnavailable
from tradebot import create_app

TOKEN='test-preparation-token-0123456789'
AUTH={'Authorization':'Bearer '+TOKEN}


@unittest.skipUnless(os.name=='posix','Linux process custody')
class PreparationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.p=Preparation(self.root);self.addCleanup(self.p.close)
        self.script=self.root/'worker.py';self.script.write_text('import os,sys,time\nassert "TRADEBOT_ADMIN_TOKEN" not in os.environ\nif sys.argv[2]=="prepare": time.sleep(.15)\nelse: time.sleep(30)\n')
    def wait(self,expected):
        for _ in range(100):
            row=self.p.status()
            if row['state']==expected:return row
            time.sleep(.03)
        self.fail(str(row))
    def test_real_process_completion_and_restart(self):
        with patch('data_preparation.SCRIPT',self.script),patch.dict(os.environ,{'TRADEBOT_ADMIN_TOKEN':'never-inherited'}):
            job=self.p.start('prepare');self.assertEqual(job['state'],'running')
            with self.assertRaises(MemoryUnavailable):Preparation(self.root).start('repair')
            row=self.wait('complete');self.assertEqual(row['id'],job['id']);self.assertEqual(Preparation(self.root).status(),row)
    def test_cancel_continuous_and_duplicate_refusal(self):
        with patch('data_preparation.SCRIPT',self.script):
            self.p.start('collect')
            with self.assertRaises(MemoryUnavailable):self.p.start('prepare')
            self.p.cancel();self.wait('cancelled');self.assertIsNone(self.p.child)
            self.p.start('repair');self.p.close();self.wait('cancelled')
    def test_interrupted_recovery_no_automatic_restart(self):
        with _db(self.p.root) as db:db.execute("INSERT INTO jobs VALUES('crashed','collect','running','starting',1,1)")
        self.assertEqual(Preparation(self.root).status()['state'],'interrupted');self.assertIsNone(self.p.child)
    def test_authenticated_api_controls(self):
        with patch('data_preparation.SCRIPT',self.script),TestClient(create_app(self.root,TOKEN),base_url='http://localhost') as c:
            self.assertEqual(c.get('/api/data-preparation').status_code,401)
            self.assertEqual(c.post('/api/data-preparation/collect').status_code,401)
            self.assertEqual(c.post('/api/data-preparation/collect',headers=AUTH|{'Origin':'https://evil.invalid'}).status_code,403)
            data=c.get('/api/data-preparation',headers=AUTH).json();self.assertFalse(data['training_ready']);self.assertEqual(len(data['markets']),3)
            self.assertEqual(c.post('/api/data-preparation/collect',headers=AUTH).status_code,200)
            self.assertEqual(c.post('/api/data-preparation/prepare',headers=AUTH).status_code,409)
            self.assertEqual(c.post('/api/data-preparation/cancel',headers=AUTH).status_code,200)
            self.assertEqual(c.get('/api/status',headers=AUTH).json()['mode'],'PAPER')
