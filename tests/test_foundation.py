import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tradebot import InstanceSpec, Store, Vault, create_app, dry_run_contract

TOKEN = "test-admin-token-" * 3
AUTH = {"Authorization": "Bearer " + TOKEN}


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.store = Store(self.directory, 10000)
        self.store.boot()

    def spec(self, name="BTC", cents=6000):
        return InstanceSpec(name=name, pair="BTC/USD", virtual_cents=cents)

    def test_auth_and_no_order_routes(self):
        with TestClient(create_app(self.directory, TOKEN, 10000), base_url="http://localhost") as c:
            self.assertFalse(c.get("/health").json()["ready_to_trade"])
            self.assertEqual(c.get("/api/status").status_code, 401)
            self.assertEqual(c.get("/api/instances").status_code, 401)
            self.assertEqual(c.post("/api/pause").status_code, 401)
            self.assertEqual(c.post("/api/instances", json=self.spec().model_dump()).status_code, 401)
            for route in ("/api/order", "/api/resume", "/api/live", "/api/secrets"):
                self.assertEqual(c.post(route, headers=AUTH).status_code, 404)
            self.assertEqual(c.get("/api/status", headers=AUTH).json()["mode"], "PAPER")

    def test_admission_and_restart(self):
        with TestClient(create_app(self.directory, TOKEN, 10000), base_url="http://localhost") as c:
            self.assertEqual(c.post("/api/instances", headers=AUTH, json=self.spec().model_dump()).status_code, 201)
            self.assertEqual(c.post("/api/instances", headers=AUTH, json=self.spec("ETH").model_dump()).status_code, 409)
            self.assertEqual(c.post("/api/pause", headers=AUTH).status_code, 200)
        with TestClient(create_app(self.directory, TOKEN, 10000), base_url="http://localhost") as c:
            s = c.get("/api/status", headers=AUTH).json()
            self.assertTrue(s["human_pause"] and s["paused"])
            self.assertEqual(s["allocated_cents"], 6000)
            self.assertEqual(len(c.get("/api/instances", headers=AUTH).json()), 1)
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM journal WHERE event='startup_paused'").fetchone()[0], 3)

    def test_invalid_input(self):
        for changes in ({"mode": "LIVE"}, {"exchange": "binance"}, {"pair": "BTC/USDT"}, {"virtual_cents": -1}, {"virtual_cents": True}, {"secret": "fake"}):
            with self.assertRaises(ValidationError):
                InstanceSpec(**(self.spec().model_dump() | changes))
        with self.assertRaises(ValueError):
            create_app(self.directory, "short")

    def test_invalid_http_redacts_input(self):
        with TestClient(create_app(self.directory, TOKEN, 10000), base_url="http://localhost") as c:
            payload = self.spec().model_dump() | {"secret": "not-to-be-echoed"}
            response = c.post("/api/instances", headers=AUTH, json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("not-to-be-echoed", response.text)
            self.assertEqual(c.get("/health", headers={"Host": "attacker.invalid"}).status_code, 400)

    def test_concurrent_cap(self):
        def attempt(i):
            try:
                self.store.add(self.spec(str(i)))
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(attempt, range(4))), 1)
        self.assertEqual(self.store.status()["allocated_cents"], 6000)

    def test_zero_default_cap(self):
        with TestClient(create_app(self.directory, TOKEN), base_url="http://localhost") as c:
            self.assertEqual(c.post("/api/instances", headers=AUTH, json=self.spec().model_dump()).status_code, 409)

    def test_vault(self):
        key = Fernet.generate_key()
        vault = Vault(self.store, key)
        value = "synthetic-test-value"
        vault.put("grok", value)
        self.assertEqual(Vault(self.store, key).get("grok"), value)
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertNotIn(value.encode(), db.execute("SELECT ciphertext FROM secrets").fetchone()[0])
        with self.assertRaises(InvalidToken):
            Vault(self.store, Fernet.generate_key()).get("grok")
        with self.assertRaises(ValueError):
            vault.put("kraken_trade_key", value)

    def test_dry_run_contract(self):
        c = dry_run_contract(self.spec())
        self.assertTrue(c["dry_run"])
        self.assertEqual(c["initial_state"], "stopped")
        self.assertEqual(c["exchange"]["key"], "")
        self.assertEqual(c["exchange"]["pair_whitelist"], ["BTC/USD"])
        self.assertFalse(c["force_entry_enable"] or c["api_server"]["enabled"])
