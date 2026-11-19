"""Data-only backup, fault rejection and explicit five-year coverage."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from data_lifecycle import backup,restore,coverage
from market_memory import MarketMemory,MemoryUnavailable,sha256_file,export_parquet
from market_lake import HistoricalLake
from market_stream import StreamJournal
from economic_memory import EconomicMemory

T=1704067200
NOW=T+86400
PAIR='BTC/USD'


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.data=self.root/'data';self.data.mkdir(mode=0o700);self.m=MarketMemory(self.data);self.e=EconomicMemory(self.data)
        self.snap=self.root/'snapshot';self.target=self.root/'restored'
        self.m.record_recent(PAIR,60,{'exchange':'kraken','product':'spot','pair':PAIR,'interval_minutes':60,'observed_at':T+3600,'candles':[[T*1000,'100','101','99','100','2',4]]},now=NOW)
    def test_real_native_roundtrip(self):
        try:import duckdb
        except ImportError:self.skipTest('DuckDB native dependency absent')
        p=self.root/'XBTUSD_60.csv';p.write_text(f'{T+3600},100,101,99,100,2,4\n')
        self.m.import_archive(p,PAIR,60,sha256_file(p),now=NOW)
        lake=HistoricalLake(self.m);row=lake.compact(PAIR,60,T,T+7200)[0];lake.pin(row['id'],'historical-evaluation')
        export_parquet(self.m,PAIR,60,T,T+7200,NOW)
        (self.data/'secret.txt').write_text('must never be copied')
        stream=StreamJournal(self.m);stream.start();stream.ack(PAIR,NOW)
        evidence=b'<?xml version="1.0"?><rss><channel><item><title>Policy notice</title><link>https://www.federalreserve.gov/newsevents/pressreleases/test.htm</link><pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate></item></channel></rss>'
        self.e.ingest('fed_monetary',evidence,observed_at=NOW)
        receipt=backup(self.data,self.snap);restore(self.snap,self.target,receipt['manifest_sha256'])
        copy=MarketMemory(self.target);self.assertEqual(copy.query(PAIR,60,T,T+7200,NOW),self.m.query(PAIR,60,T,T+7200,NOW))
        self.assertEqual(HistoricalLake(copy).query(PAIR,60,T,T+7200,NOW),lake.query(PAIR,60,T,T+7200,NOW))
        self.assertEqual(EconomicMemory(self.target).view('fed_monetary',as_of=NOW),self.e.view('fed_monetary',as_of=NOW))
        self.assertFalse((self.target/'secret.txt').exists());self.assertFalse(any(r['connected'] for r in StreamJournal(copy).status()))
    def test_tamper_manifest_or_asset_no_partial_restore(self):
        receipt=backup(self.data,self.snap)
        with self.assertRaises(MemoryUnavailable):restore(self.snap,self.target,'0'*64)
        with (self.snap/'market-memory/catalog.sqlite3').open('ab') as f:f.write(b'bad')
        with self.assertRaises(MemoryUnavailable):restore(self.snap,self.target,receipt['manifest_sha256'])
        self.assertFalse(self.target.exists());self.assertFalse(list(self.root.glob('.restore-*')))
    def test_no_overwrite_and_disk_budget(self):
        self.snap.mkdir(mode=0o700);(self.snap/'keep').write_text('keep')
        with self.assertRaises(MemoryUnavailable):backup(self.data,self.snap)
        self.assertEqual((self.snap/'keep').read_text(),'keep')
        with patch('data_lifecycle.MAX_BYTES',1),self.assertRaises(MemoryUnavailable):backup(self.data,self.root/'other')
        self.assertFalse((self.root/'other').exists());self.assertFalse(list(self.root.glob('.snapshot-*')))
    def test_missing_catalog_asset_rejected(self):
        p=self.root/'XBTUSD_60.csv';p.write_text(f'{T+3600},100,101,99,100,2,4\n')
        entry=self.m.import_archive(p,PAIR,60,sha256_file(p),now=NOW)
        (self.m.root/(entry['id']+'.csv')).unlink()
        with self.assertRaises(MemoryUnavailable):backup(self.data,self.snap)
        self.assertFalse(self.snap.exists())
    def test_coverage_uses_fixed_window_not_earliest_observation(self):
        result=coverage(self.m,NOW)
        self.assertFalse(result['training_ready']);self.assertEqual(len(result['markets']),3)
        self.assertEqual(result['markets'][0]['expected'],43800)
        self.assertEqual(result['markets'][0]['coverage'],1);self.assertEqual(result['markets'][0]['missing'],43799)
        self.assertEqual(result['markets'][1]['coverage'],0)
