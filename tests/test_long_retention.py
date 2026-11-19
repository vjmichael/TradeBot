"""Time-shifted durability/retention regression, NOT elapsed production proof."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from market_memory import MarketMemory,MemoryUnavailable
from market_quality import QualityMemory,SAMPLES_PER_MARKET,MAX_SAMPLES
from data_lifecycle import backup,restore
from data_retention import retire_books,MIN_AGE
from economic_memory import EconomicMemory
from engine import SYMBOLS
from market_directory import MarketDirectory,parse_directory
from test_market_directory import payload

T=1795000200
class LongRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.data=self.root/'data';self.data.mkdir(mode=0o700)
        self.m=MarketMemory(self.data);self.e=EconomicMemory(self.data);self.q=QualityMemory(self.m)
    def test_multi_market_margin_and_preview_exact_copy_across_reopen(self):
        # Bound arithmetic is a design limit, not a measured 35-day run.
        self.assertEqual(SAMPLES_PER_MARKET,35*288)
        self.assertEqual(MAX_SAMPLES,32*SAMPLES_PER_MARKET)
        now=T+36*86400
        registry=MarketDirectory(self.data)
        snapshot=registry.refresh(now=T,fetch=lambda:parse_directory(payload()))
        registry.select('ADA/USD',True,snapshot['version'],now=T)
        scope=registry.selected_symbols();self.assertEqual(len(scope),4)
        with self.m.tx() as db:
            for pair in scope:
                for slot in (T,T+300,now-300):
                    db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',(pair,slot,slot,slot,'unavailable'))
        receipt=backup(self.data,self.root/'copy')
        preview=retire_books(self.data,self.root/'copy',receipt['manifest_sha256'],now=now,limit=2)
        self.assertEqual(preview['attempts'],2)
        applied=retire_books(self.data,self.root/'copy',receipt['manifest_sha256'],now=now,limit=2,apply=True,expected_selection=preview['selection_sha256'])
        self.assertEqual(applied['attempts'],2)
        reopened=MarketMemory(self.data)
        with reopened.tx() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0],10)
            self.assertEqual(db.execute('SELECT count(*) FROM quality_retired').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts WHERE slot=?',(now-300,)).fetchone()[0],4)
        restore(self.root/'copy',self.root/'restored',receipt['manifest_sha256'])
        with MarketMemory(self.root/'restored').tx() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0],12)
    def test_per_market_capacity_isolated_and_interrupted_attempt_reconciled(self):
        with self.m.tx() as db:
            db.execute('INSERT INTO quality_attempts VALUES(?,?,?,?,?)',('BTC/USD',T,T,None,'pending'))
        with patch('market_quality.SAMPLES_PER_MARKET',1):
            with self.assertRaises(MemoryUnavailable):
                self.q.sample('BTC/USD',clock=lambda:T+600,fetch=lambda *_:self.fail('capacity'))
            with self.m.tx() as db:
                # Reconciliation persists even on refused admission.
                self.assertEqual(db.execute("SELECT state FROM quality_attempts WHERE pair='BTC/USD'").fetchone()[0],'unavailable')
            def unavailable(*args):raise ValueError('provider unavailable')
            with self.assertRaises(ValueError):
                self.q.sample('ETH/USD',clock=lambda:T+600,fetch=unavailable)
            with self.m.tx() as db:
                self.assertEqual(db.execute("SELECT state FROM quality_attempts WHERE pair='BTC/USD'").fetchone()[0],'unavailable')
                self.assertEqual(db.execute("SELECT state FROM quality_attempts WHERE pair='ETH/USD'").fetchone()[0],'unavailable')
    def test_catalog_watermark_blocks_without_network(self):
        with patch('market_quality.MAX_CATALOG_BYTES',0),self.assertRaises(MemoryUnavailable):
            self.q.sample('BTC/USD',clock=lambda:T,fetch=lambda *_:self.fail('capacity'))
