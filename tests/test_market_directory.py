import tempfile
import unittest
from pathlib import Path

from market_directory import MarketDirectory, DirectoryUnavailable, parse_directory, FRESH_SECONDS


def row(base, alt, status="online"):
    return dict(wsname=base+"/USD", altname=alt, base=base, quote="ZUSD",
                aclass_base="currency", aclass_quote="currency", status=status,
                pair_decimals=5, lot_decimals=8, cost_decimals=5,
                ordermin="0.001", costmin="0.5", tick_size="0.00001")


def payload():
    return {"error": [], "result": {"XXBTZUSD": row("XBT","XBTUSD"), "XETHZUSD": row("ETH","ETHUSD"),
            "SOLUSD": row("SOL","SOLUSD"), "ADAUSD": row("ADA","ADAUSD"),
            "DOTUSD": row("DOT","DOTUSD","cancel_only")}}


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.registry = MarketDirectory(self.path)
        self.now = 200000

    def refresh(self, delta=0, source=payload):
        return self.registry.refresh(now=self.now+delta, fetch=lambda: parse_directory(source()))

    def test_mapping_search_and_restart(self):
        self.assertEqual(self.registry.browse()["state"], "unavailable")
        first = self.refresh()
        result = self.registry.browse("XBT",now=self.now)
        self.assertEqual(result["markets"][0]["pair"], "BTC/USD")
        self.assertEqual(result["markets"][0]["rest_key"], "XXBTZUSD")
        self.registry.select("ADA/USD",True,first["version"],now=self.now)
        reopened = MarketDirectory(self.path).browse(now=self.now)
        self.assertEqual(reopened["version"], first["version"])
        self.assertEqual(len(reopened["sha256"]),64)
        self.assertEqual(next(s for s in reopened["selected"] if s["pair"]=="ADA/USD")["preparation"],"selected_for_data")
        self.registry.select("ADA/USD",False,None,now=self.now)
        self.assertEqual(len(self.registry.browse(now=self.now)["selected"]),3)

    def test_stale_version_and_status(self):
        first = self.refresh()
        with self.assertRaises(ValueError):
            self.registry.select("DOT/USD",True,first["version"],now=self.now)
        with self.assertRaises(ValueError):
            self.registry.select("ADA/USD",True,first["version"],now=self.now+FRESH_SECONDS)
        second = self.refresh(61)
        with self.assertRaises(ValueError):
            self.registry.select("ADA/USD",True,first["version"],now=self.now+61)
        self.registry.select("ADA/USD",True,second["version"],now=self.now+61)
        with self.assertRaises(ValueError):
            self.registry.select("BTC/USD",False,None,now=self.now+61)

    def test_failed_refresh_preserves_snapshot_and_pacing(self):
        first = self.refresh()
        with self.assertRaises(DirectoryUnavailable):
            self.refresh(1)
        with self.assertRaises(DirectoryUnavailable):
            self.refresh(61,lambda: {"error":["failure"],"result":{}})
        self.assertEqual(self.registry.browse(now=self.now+61)["version"],first["version"])

    def test_ambiguous_identity_and_non_usd(self):
        data = payload()
        data["result"]["ADAOTHER"] = row("ADA","ADAOTHER")
        data["result"]["ETHBTC"] = dict(row("ETH","ETHBTC"),wsname="ETH/BTC")
        markets = parse_directory(data)
        self.assertNotIn("ADA/USD",[m["pair"] for m in markets])
        self.assertNotIn("ETH/BTC",[m["pair"] for m in markets])

    def test_status_change_preserves_intent_but_invalidates_selection(self):
        start = self.refresh()
        self.registry.select("ADA/USD", True, start["version"], now=self.now)
        def changed():
            data=payload()
            data["result"]["ADAUSD"]["status"]="maintenance"
            return data
        later=self.refresh(61,changed)
        ada=next(s for s in later["selected"] if s["pair"]=="ADA/USD")
        self.assertFalse(ada["selection_valid"])
        self.assertEqual(ada["directory_status"],"maintenance")

    def test_mapping_change_requires_manual_review(self):
        first = self.refresh()
        self.registry.select("ADA/USD", True, first["version"], now=self.now)
        def changed():
            data = payload()
            data["result"]["ADAUSD"]["base"] = "ADA2"
            return data
        later = self.refresh(61, changed)
        ada = next(s for s in later["selected"] if s["pair"] == "ADA/USD")
        self.assertTrue(ada["mapping_changed"])
        self.assertFalse(ada["selection_valid"])
        with self.assertRaises(ValueError):
            self.registry.select("ADA/USD", True, later["version"], now=self.now+61)
