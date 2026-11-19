import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from engine import IMAGE, PLATFORM, MarketUnavailable, candles, paper_config, write_config
from tradebot import InstanceSpec, create_app

NOW = 18000
TOKEN = "test-admin-token-" * 3
AUTH = {"Authorization": "Bearer " + TOKEN}


def payload(key="XXBTZUSD"):
    return {"error": [], "result": {key: [[ts, "100", "102", "99", "101", "100", "12", 10] for ts in (10800, 14400, 18000)], "last": 14400}}


def client(value, status=200):
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "api.kraken.com"
        assert request.url.path == "/0/public/OHLC"
        assert "API-Key" not in request.headers and "API-Sign" not in request.headers
        return httpx.Response(status, json=value)
    return httpx.Client(transport=httpx.MockTransport(handler))


class EngineTests(unittest.TestCase):
    def test_public_closed_candles_only(self):
        for pair, key in [("BTC/USD", "XXBTZUSD"), ("ETH/USD", "XETHZUSD"), ("SOL/USD", "SOLUSD")]:
            with client(payload(key)) as c:
                result = candles(pair, client=c, now=NOW)
            self.assertEqual(result["pair"], pair)
            self.assertEqual(result["gap_count"], 0)
            self.assertEqual(result["coverage"], "recent_only")
            self.assertEqual([row[0] for row in result["candles"]], [10800000, 14400000])

    def test_fail_closed(self):
        variants = [payload("wrong"), {"error": ["EGeneral:bad"]}, payload(), payload(), payload(), payload()]
        variants[2]["result"]["XXBTZUSD"][0][1] = "nan"
        variants[3]["result"]["XXBTZUSD"][1][0] = 10800
        variants[4]["result"]["XXBTZUSD"][1][2] = "0"
        variants[5]["result"]["XXBTZUSD"][2][0] = 21600
        for value in variants:
            with self.subTest(value=value), client(value) as c, self.assertRaises(MarketUnavailable):
                candles("BTC/USD", client=c, now=NOW)
        with client(payload()) as c, self.assertRaises(MarketUnavailable):
            candles("BTC/USD", client=c, now=NOW + 7200)
        with client(payload(), 429) as c, patch("engine.time.sleep"), self.assertRaises(MarketUnavailable):
            candles("BTC/USD", client=c, now=NOW)
        for pair, interval in [("BTC/USDT", 60), ("BTC/USD", 7), ("BTC/USD", True)]:
            with self.assertRaises(ValueError):
                candles(pair, interval)

    def test_transient_retry_and_request_identity(self):
        statuses = iter([429, 503, 200])
        seen = []
        def handler(request):
            seen.append(request)
            status = next(statuses)
            return httpx.Response(status, json=payload(), headers={"Retry-After": "2"} if status == 429 else {})
        with httpx.Client(transport=httpx.MockTransport(handler)) as c, patch("engine.time.sleep") as sleep:
            self.assertEqual(len(candles("BTC/USD", client=c, now=NOW)["candles"]), 2)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 2])
        self.assertEqual(len(seen), 3)
        for request in seen:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.params["pair"], "XBTUSD")
            self.assertIn("TradeBot/", request.headers["User-Agent"])
            self.assertNotIn("API-Key", request.headers)

    def test_terminal_diagnostics_and_no_retry(self):
        for status, value, expected in [(403, {"private": "untrusted response"}, "http_403"), (200, payload("wrong"), "unexpected_market_response"), (200, {"error": ["untrusted response"]}, "exchange_error")]:
            with client(value, status) as c, patch("engine.time.sleep") as sleep, self.assertRaisesRegex(MarketUnavailable, "^" + expected + "$"):
                try:
                    candles("BTC/USD", client=c, now=NOW)
                finally:
                    sleep.assert_not_called()
        with client(payload(), 503) as c, patch("engine.time.sleep") as sleep, self.assertRaisesRegex(MarketUnavailable, "^http_503$"):
            try:
                candles("BTC/USD", client=c, now=NOW)
            finally:
                self.assertEqual(sleep.call_count, 2)

    def test_long_retry_after_is_not_ignored(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": "120"}))) as c, patch("engine.time.sleep") as sleep:
            with self.assertRaisesRegex(MarketUnavailable, "^http_429$"):
                candles("BTC/USD", client=c, now=NOW)
            sleep.assert_not_called()

    def test_transport_retry_and_invalid_json(self):
        def handler(request):
            raise httpx.ConnectError("untrusted internal details", request=request)
        with httpx.Client(transport=httpx.MockTransport(handler)) as c, patch("engine.time.sleep") as sleep:
            with self.assertRaisesRegex(MarketUnavailable, "^transport_ConnectError$"):
                candles("BTC/USD", client=c, now=NOW)
            self.assertEqual(sleep.call_count, 2)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not JSON"))) as c:
            with self.assertRaisesRegex(MarketUnavailable, "^invalid_json$"):
                candles("BTC/USD", client=c, now=NOW)

    def test_acceptance_continues_but_fails(self):
        from scripts.qualify_engine import qualify_stages
        seen = []
        def bad():
            raise MarketUnavailable("http_403")
        with patch("builtins.print"), patch("scripts.qualify_engine.traceback.print_exc"):
            with self.assertRaisesRegex(RuntimeError, "Required acceptance stages failed: bad"):
                qualify_stages([("bad", bad), ("later", lambda: seen.append("later"))])
        self.assertEqual(seen, ["later"])

    def test_gaps_visible(self):
        p = payload()
        p["result"]["XXBTZUSD"][0][0] = 7200
        with client(p) as c:
            self.assertEqual(candles("BTC/USD", client=c, now=NOW)["gap_count"], 1)

    def test_runtime_image_pin(self):
        self.assertEqual(PLATFORM, "linux/amd64")
        self.assertEqual(IMAGE, "docker.io/freqtradeorg/freqtrade@sha256:deb1853f454761058b75251d4b5d6f6ce441bb89d248c6578d7c0bfee1a6f6c8")
        from scripts.qualify_engine import docker
        args = docker(Path("/tmp/tb-test"), "tb-test", ["--version"])
        self.assertEqual(args[args.index("--platform") + 1], PLATFORM)
        self.assertIn(IMAGE, args)

    def test_safe_config(self):
        spec = InstanceSpec(name="Test", pair="SOL/USD", virtual_cents=12500)
        for qualify in (False, True):
            c = paper_config(spec, qualification=qualify)
            self.assertIs(c["dry_run"], True)
            self.assertEqual(c["trading_mode"], "spot")
            self.assertEqual(c["dry_run_wallet"], 125)
            self.assertEqual(c["exchange"]["pair_whitelist"], ["SOL/USD"])
            self.assertEqual(c["exchange"]["key"], "")
            self.assertEqual(c["exchange"]["secret"], "")
            self.assertEqual(c["initial_state"], "stopped")
            self.assertFalse(c["force_entry_enable"] or c["telegram"]["enabled"] or c["api_server"]["enabled"])
            self.assertEqual(c["freqai"]["enabled"], qualify)
            self.assertEqual(c["strategy"], "FreqAIQualification" if qualify else "NoEntry")
        with self.assertRaises(Exception):
            paper_config(spec.model_copy(update={"mode": "LIVE"}))
        self.assertIn("@sha256:", IMAGE)
        with tempfile.TemporaryDirectory() as tmp:
            write_config(Path(tmp), spec)
            with self.assertRaises(FileExistsError):
                write_config(Path(tmp), spec)

    def test_authenticated_market_endpoint_and_cache(self):
        snapshot = {"exchange": "kraken", "candles": []}
        with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(Path(tmp), TOKEN), base_url="http://localhost") as c, patch("tradebot.candles", return_value=snapshot) as fetch:
            route = "/api/markets/BTC/candles"
            self.assertEqual(c.get(route).status_code, 401)
            self.assertEqual(c.get("/api/markets/DOGE/candles", headers=AUTH).status_code, 422)
            self.assertEqual(c.get(route + "?minutes=7", headers=AUTH).status_code, 422)
            self.assertEqual(c.get(route, headers=AUTH).json(), snapshot)
            self.assertEqual(c.get(route, headers=AUTH).json(), snapshot)
            fetch.assert_called_once_with("BTC/USD", 60)
            fetch.side_effect = MarketUnavailable("no market")
            self.assertEqual(c.get("/api/markets/ETH/candles", headers=AUTH).status_code, 503)
            self.assertFalse(c.get("/api/status", headers=AUTH).json()["engine_connected"])
