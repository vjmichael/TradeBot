import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from tradebot import create_app, Store, Vault

TOKEN = 'synthetic-test-access-' * 3
AUTH = {'Authorization': 'Bearer ' + TOKEN}

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.key = Fernet.generate_key()

    def app(self, key=True):
        return TestClient(create_app(self.directory,TOKEN,vault_key=self.key if key else None),base_url='http://localhost')

    def test_auth_headers_and_origin(self):
        with self.app() as c:
            response=c.get('/')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertIn("frame-ancestors 'none'",response.headers['content-security-policy'])
            for path in ('/api/setup','/api/calendar','/api/status'):
                self.assertEqual(c.get(path).status_code,401)
            for path,body in [('/api/setup/credentials',{'grok':'synthetic'}),('/api/setup/paper-capital',{'virtual_cap_cents':100})]:
                self.assertEqual(c.post(path,json=body).status_code,401)
                self.assertEqual(c.post(path,json=body,headers=AUTH|{'Origin':'https://evil.invalid'}).status_code,403)
            self.assertEqual(c.get('/api/setup',headers=AUTH|{'Host':'evil.invalid'}).status_code,400)
            calendar=c.get('/api/calendar',headers=AUTH).json()
            self.assertEqual((calendar['state'],calendar['events']),('not_connected',[]))

    def test_credentials_write_only_atomic_and_wrong_key(self):
        values={'grok':'synthetic-grok','kraken_read_key':'synthetic-key','kraken_read_secret':'synthetic-secret'}
        with self.app() as c:
            self.assertEqual(c.post('/api/setup/credentials',json={'kraken_read_key':'partial'},headers=AUTH).status_code,422)
            self.assertEqual(c.get('/api/setup',headers=AUTH).json()['credentials']['kraken_read_key'],'not_configured')
            self.assertEqual(c.post('/api/setup/credentials',json={'kraken_trade_key':'DO-NOT-ECHO'},headers=AUTH).status_code,422)
            response=c.post('/api/setup/credentials',json=values,headers=AUTH)
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.json()['connections_verified'])
            for slot,value in values.items():
                self.assertEqual(response.json()['credentials'][slot],'stored_unverified')
                self.assertNotIn(value,response.text)
        with closing(sqlite3.connect(self.directory/'desk.sqlite3')) as db:
            for slot,blob in db.execute('SELECT slot,ciphertext FROM secrets'):
                self.assertNotIn(values[slot].encode(),blob)
        with self.app() as c:
            self.assertEqual(c.get('/api/setup',headers=AUTH).json()['credentials']['grok'],'stored_unverified')
        with TestClient(create_app(self.directory,TOKEN,vault_key=Fernet.generate_key()),base_url='http://localhost') as c:
            self.assertEqual(c.get('/api/setup',headers=AUTH).json()['credentials']['grok'],'unavailable')
            self.assertEqual(c.post('/api/setup/credentials',json={'grok':'replacement'},headers=AUTH).status_code,503)
        self.assertEqual(Vault(Store(self.directory,0),self.key).get('grok'),values['grok'])

    def test_no_vault_no_secret_writes(self):
        with self.app(False) as c:
            self.assertFalse(c.get('/api/setup',headers=AUTH).json()['vault_available'])
            self.assertEqual(c.post('/api/setup/credentials',headers=AUTH,json={'grok':'synthetic'}).status_code,503)

    def test_paper_cap_wizard_metadata_and_restart(self):
        spec={'name':'SOL research','pair':'SOL/USD','virtual_cents':12000,'model':'grok-test','history_years':5,'monthly_budget_cents':2000,'strategy_family':'numerical-baseline'}
        with self.app() as c:
            self.assertEqual(c.post('/api/instances',headers=AUTH,json=spec).status_code,409)
            self.assertEqual(c.post('/api/setup/paper-capital',headers=AUTH,json={'virtual_cap_cents':15000}).status_code,200)
            r=c.post('/api/instances',headers=AUTH,json=spec)
            self.assertEqual(r.status_code,201)
            self.assertEqual(r.json()['model'],'grok-test')
            self.assertEqual(r.json()['state'],'PAUSED')
            self.assertEqual(c.post('/api/setup/paper-capital',headers=AUTH,json={'virtual_cap_cents':11999}).status_code,409)
            self.assertEqual(c.post('/api/instances',headers=AUTH,json=spec|{'name':'ETH','mode':'LIVE'}).status_code,422)
            self.assertEqual(c.post('/api/pause',headers=AUTH).status_code,200)
        with self.app() as c:
            self.assertEqual(c.get('/api/status',headers=AUTH).json()['virtual_cap_cents'],15000)
            self.assertTrue(c.get('/api/status',headers=AUTH).json()['human_pause'])
            self.assertEqual(c.get('/api/instances',headers=AUTH).json()[0]['monthly_budget_cents'],2000)

    def test_migrate_actual_v1_schema(self):
        with closing(sqlite3.connect(self.directory/'desk.sqlite3')) as db:
            db.executescript("CREATE TABLE desk (id INTEGER PRIMARY KEY,paused INTEGER,human_pause INTEGER);INSERT INTO desk VALUES(1,1,1);CREATE TABLE instances(id TEXT PRIMARY KEY,name TEXT UNIQUE,pair TEXT,virtual_cents INTEGER,mode TEXT,state TEXT);INSERT INTO instances VALUES('old','Old','BTC/USD',100,'PAPER','PAUSED');PRAGMA user_version=1;")
        with self.app() as c:
            r=c.get('/api/instances',headers=AUTH).json()[0]
            self.assertEqual((r['id'],r['provider'],r['history_years']),('old','grok',5))
            self.assertTrue(c.get('/api/status',headers=AUTH).json()['human_pause'])
        with closing(sqlite3.connect(self.directory/'desk.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
