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

    def test_goal_api_is_authenticated_versioned_and_reporting_only(self):
        from tradebot import InstanceSpec
        from financial_goals import DEFAULT
        with self.app() as c:
            self.assertEqual(c.post('/api/setup/paper-capital',json={'virtual_cap_cents':20000},headers=AUTH).status_code,200)
            store=Store(self.directory,0)
            instance=store.add(InstanceSpec(name='Goal fixture',pair='BTC/USD',virtual_cents=10000,provider='none',strategy_family='numerical-baseline'))['id']
            other=store.add(InstanceSpec(name='Other paper fixture',pair='ETH/USD',virtual_cents=10000,provider='none',strategy_family='numerical-baseline'))['id']
            path='/api/financial-goals/'+instance
            other_path='/api/financial-goals/'+other
            p=DEFAULT|dict(expenses_cents=250000,income_cents=150000,savings_cents=50000,tax_bps=2000,target_kind='usd',target_value=100,pause_on_goal=True,expected_version=0)
            before=c.get('/api/status',headers=AUTH).json()
            self.assertEqual(c.get(path).status_code,401)
            self.assertEqual(c.post(path,json=p).status_code,401)
            self.assertEqual(c.post(path,json=p|{'mode':'LIVE'},headers=AUTH).status_code,422)
            self.assertEqual(c.get(path,headers=AUTH).json()['settings']['version'],0)
            self.assertEqual(c.post(path,json=p,headers=AUTH|{'Origin':'https://evil.invalid'}).status_code,403)
            self.assertEqual(c.get('/api/financial-goals/'+'f'*32,headers=AUTH).status_code,409)
            self.assertEqual(c.post(path,json=p,headers=AUTH).status_code,200)
            self.assertEqual(c.post(path,json=p,headers=AUTH).status_code,409)
            other_report=c.get(other_path,headers=AUTH).json()
            self.assertEqual(other_report['settings']['version'],0)
            self.assertFalse(other_report['goal_pause_enabled'])
            self.assertEqual(other_report['conservative_bps'],0)
            self.assertEqual(c.post(other_path,json=p|{'conservative_bps':5000},headers=AUTH).status_code,409)
            self.assertEqual(c.post(other_path,json=p|{'pause_on_goal':False,'conservative_bps':10000},headers=AUTH).status_code,422)
            self.assertEqual(c.post(other_path,json=p|{'pause_on_goal':False,'conservative_bps':5000},headers=AUTH).status_code,200)
            self.assertEqual(c.get(other_path,headers=AUTH).json()['conservative_bps'],5000)
            self.assertFalse(other_report['real_income'])
            r=c.get(path,headers=AUTH).json()
            self.assertEqual(r['plan']['monthly_pre_tax_usd'],'1875.00000000')
            self.assertIsNone(r['daily_realized_usd'])
            self.assertIsNone(r['execution_day_original_pl_usd'])
            self.assertEqual(r['baseline_state'],'unavailable')
            self.assertFalse(r['real_income'])
            self.assertTrue(r['goal_pause_enabled'])
            self.assertFalse(r['goal_pause_latched'])
            self.assertIsNone(r['goal_notification'])
            self.assertEqual(c.get('/api/status',headers=AUTH).json(),before)
        with self.app() as c:
            stored=c.get(path,headers=AUTH).json()
            self.assertEqual(stored['settings']['version'],1)
            self.assertTrue(stored['goal_pause_enabled'])
            self.assertEqual(c.get(other_path,headers=AUTH).json()['settings']['version'],1)
            self.assertEqual(c.get(other_path,headers=AUTH).json()['conservative_bps'],5000)

    def test_history_api_read_only_and_portfolio_scoped(self):
        from tradebot import InstanceSpec
        from financial_ledger import FinancialLedger
        with self.app() as c:
            self.assertEqual(c.post('/api/setup/paper-capital',json={'virtual_cap_cents':20000},headers=AUTH).status_code,200)
            store=Store(self.directory,0)
            a=store.add(InstanceSpec(name='History A',pair='BTC/USD',virtual_cents=10000,provider='none',strategy_family='numerical-baseline'))['id']
            b=store.add(InstanceSpec(name='History B',pair='ETH/USD',virtual_cents=10000,provider='none',strategy_family='numerical-baseline'))['id']
            path='/api/financial-history/'+a
            self.assertEqual(c.get(path).status_code,401)
            self.assertEqual(c.post(path,json={},headers=AUTH).status_code,405)
            for query in ('?days=32','?days=0','?end_day=bad'):
                self.assertEqual(c.get(path+query,headers=AUTH).status_code,422)
            self.assertEqual(c.get(path+'?end_day=2099-01-01',headers=AUTH).status_code,409)
            self.assertEqual(c.get('/api/financial-history/'+'f'*32,headers=AUTH).status_code,409)
            initial=c.get(path+'?days=7',headers=AUTH).json()
            self.assertEqual(initial['reported_days'],0)
            self.assertIsNone(initial['totals']['realized_usd'])
            # Direct internal ledger fixture, not funding through a reporting API.
            FinancialLedger(store.transaction).fund('history-fund',a)
            before=c.get('/api/status',headers=AUTH).json()
            with store.transaction() as db:head=tuple(db.execute('SELECT * FROM fin_head').fetchone())
            r=c.get(path+'?days=7',headers=AUTH).json()
            self.assertEqual((r['instance'],r['mode'],r['real_income'],r['reported_days']),(a,'PAPER',False,1))
            self.assertEqual(r['days'][-1]['active_usd'],'100.00000000')
            self.assertEqual(r['days'][-1]['status'],'partial')
            self.assertEqual(c.get('/api/financial-history/'+b,headers=AUTH).json()['reported_days'],0)
            self.assertEqual(c.get('/api/status',headers=AUTH).json(),before)
            with store.transaction() as db:self.assertEqual(tuple(db.execute('SELECT * FROM fin_head').fetchone()),head)

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
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(db.execute("SELECT id,pair,virtual_cents,mode,state FROM instances").fetchall(),
                             [('old','BTC/USD',100,'PAPER','PAUSED')])
            self.assertEqual(db.execute("SELECT paused,human_pause FROM desk WHERE id=1").fetchone(),(1,1))
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'gate_clock','gate_inputs','gate_current','gate_decisions','gate_intents','gate_events'} <= tables)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gate_clock").fetchone()[0],1)
            self.assertTrue({'fin_events','fin_head','fin_conflicts'} <= tables)
            self.assertEqual(db.execute("SELECT count FROM fin_head WHERE id=1").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM fin_events").fetchone()[0],0)
            for table in ('gate_inputs','gate_current','gate_decisions','gate_intents','gate_events'):
                self.assertEqual(db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0],0)
        with self.app() as c:
            self.assertEqual(c.get('/api/instances',headers=AUTH).json()[0]['id'],'old')
            status=c.get('/api/status',headers=AUTH).json()
            self.assertTrue(status['paused'] and status['human_pause'])
            self.assertFalse(status['live_available'])
            self.assertEqual(status['allocated_cents'],100)
        with closing(sqlite3.connect(self.directory/'desk.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gate_intents").fetchone()[0],0)

    def test_market_directory_auth_and_selection(self):
        from market_directory import MarketDirectory, parse_directory
        import time
        def item(base, alt, status="online"):
            return dict(wsname=base+"/USD", altname=alt, base=base, quote="ZUSD",
                        aclass_base="currency", aclass_quote="currency", status=status,
                        pair_decimals=5, lot_decimals=8, cost_decimals=5,
                        ordermin="0.001", costmin="0.5", tick_size="0.00001")
        markets = parse_directory({"error":[], "result":{
            "XXBTZUSD":item("XBT","XBTUSD"),"XETHZUSD":item("ETH","ETHUSD"),
            "SOLUSD":item("SOL","SOLUSD"),"ADAUSD":item("ADA","ADAUSD")}})
        with self.app() as c:
            path = "/api/market-directory"
            self.assertEqual(c.get(path).status_code,401)
            self.assertEqual(c.post(path+"/refresh",json={}).status_code,401)
            self.assertEqual(c.post(path+"/selection",json={}).status_code,401)
            start = MarketDirectory(self.directory).refresh(fetch=lambda:markets,now=int(time.time()))
            body={"pair":"ADA/USD","enabled":True,"version":start["version"]}
            self.assertEqual(c.post(path+"/selection",headers=AUTH,json=body|{"enabled":"true"}).status_code,422)
            self.assertEqual(c.post(path+"/selection",headers=AUTH,json=body).status_code,200)
            self.assertEqual(c.get(path+"?query=ADA",headers=AUTH).json()["markets"][0]["pair"],"ADA/USD")
            self.assertEqual(c.get("/api/instances",headers=AUTH).json(),[])
            self.assertEqual(c.get("/api/status",headers=AUTH).json()["mode"],"PAPER")
        with self.app() as c:
            result=c.get("/api/market-directory",headers=AUTH).json()
            self.assertIn("ADA/USD",[x["pair"] for x in result["selected"]])

    def test_selected_market_routing_and_schedule_admission(self):
        from market_directory import MarketDirectory, parse_directory
        from test_market_directory import payload
        from unittest.mock import patch
        import time
        now=int(time.time())
        directory=MarketDirectory(self.directory)
        result=directory.refresh(now=now,fetch=lambda:parse_directory(payload()))
        selection={"pair":"ADA/USD","enabled":True,"version":result["version"]}
        with self.app() as c, patch("tradebot.candles",return_value={"pair":"ADA/USD","candles":[]}) as fetch:
            self.assertEqual(c.get("/api/markets/ADA/candles",headers=AUTH).status_code,422)
            self.assertEqual(c.post("/api/market-directory/selection",json=selection,headers=AUTH).status_code,200)
            self.assertEqual(c.get("/api/markets/ADA/candles",headers=AUTH).status_code,200)
            self.assertEqual(fetch.call_args.kwargs["symbols"]["ADA/USD"],("ADAUSD","ADAUSD"))
            self.assertEqual(c.post("/api/data-schedule",json={"enabled":True},headers=AUTH).status_code,200)
            self.assertEqual(c.post("/api/market-directory/selection",json=selection|{"enabled":False},headers=AUTH).status_code,409)
            self.assertEqual(c.post("/api/data-schedule",json={"enabled":False},headers=AUTH).status_code,200)
            self.assertEqual(c.post("/api/market-directory/selection",json=selection|{"enabled":False},headers=AUTH).status_code,200)
            self.assertEqual(c.get("/api/markets/ADA/candles",headers=AUTH).status_code,422)
            self.assertIn("ADA/USD",directory.retained_symbols())
            self.assertEqual(fetch.call_count,1)
