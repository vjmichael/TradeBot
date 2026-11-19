"""Native parquet storage, late revisions, publication and retention invariants."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import market_memory as mm
import market_lake as ml
from tradebot import create_app

T=ml.partition_start(60,1704067200)+ml.span(60)-3600
NOW=T+10*86400
PAIR='BTC/USD'
TOKEN='test-lake-token-12345678900000000'
AUTH={'Authorization':'Bearer '+TOKEN}
try:
    import duckdb
except ImportError:
    duckdb=None

class LakeTests(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);self.root=Path(d.name)
        self.m=mm.MarketMemory(self.root);self.l=ml.HistoricalLake(self.m)
    def recent(self,ts=T,close='101',published=NOW,trades=7):
        payload={'exchange':'kraken','product':'spot','pair':PAIR,'interval_minutes':60,'observed_at':ts+3600,'candles':[[ts*1000,'100','103','99',close,'2',trades]]}
        self.m.record_recent(PAIR,60,payload,now=published)
    def view(self,asof=NOW+100):
        return self.l.query(PAIR,60,T,T+3*3600,asof)
    def assert_equal(self,asof=NOW+100):
        lake=self.view(asof);lake.pop('storage');self.assertEqual(lake,self.m.query(PAIR,60,T,T+3*3600,asof))
    def native(self):
        if duckdb is None:self.skipTest('DuckDB native dependency absent')
    def test_empty_and_bounds(self):
        self.assertEqual(self.view()['coverage'],0)
        self.assertEqual(self.view()['storage']['sqlite_partitions'],2)
        self.assertEqual(self.l.compact(PAIR,60,T,T+3600)[0]['state'],'empty')
        self.assertEqual(self.l.query(PAIR,60,T,T+3600,T)['expected'],0)
        for args in [('BTC/EUR',60,T,T+3600,NOW),(PAIR,1,T,T+60*(mm.MAX_QUERY_ROWS+1),NOW),(PAIR,60,T+1,T+3600,NOW)]:
            with self.assertRaises(ValueError):self.l.query(*args)
    def test_mixed_and_point_in_time(self):
        self.native();self.recent();self.recent(T+3600,published=NOW+10)
        old=self.l.compact(PAIR,60,T,T+3600)[0]
        self.assertEqual(old['state'],'published');self.assertEqual(self.l.compact(PAIR,60,T,T+3600)[0]['id'],old['id'])
        self.assertEqual(self.view()['storage']['parquet_partitions'],1)
        self.assertEqual(self.view()['storage']['sqlite_partitions'],1)
        self.assert_equal(NOW-1);self.assert_equal(NOW);self.assert_equal(NOW+100)
        self.l=ml.HistoricalLake(mm.MarketMemory(self.root));self.assert_equal()
        self.assertEqual(self.l.compact(PAIR,60,T,T+3*3600)[1]['state'],'published')
        self.assertEqual(self.view()['storage']['parquet_partitions'],2);self.assert_equal()
    def test_late_revision_and_retention_pin(self):
        self.native();self.recent();old=self.l.compact(PAIR,60,T,T+3600)[0]
        self.l.pin(old['id'],'experiment-1');self.recent(close='102',published=NOW+20)
        self.assertEqual(self.l.status()['dirty'],1);self.assertEqual(self.view()['conflicts'],1)
        self.assert_equal(NOW);self.assert_equal(NOW+20)
        new=self.l.compact(PAIR,60,T,T+3600)[0];self.assertNotEqual(old['id'],new['id']);self.assertEqual(new['rows'],2)
        self.assert_equal(NOW);self.assert_equal(NOW+100)
        self.assertEqual(self.l.prune(before=int(time.time()),dry_run=False)['generations'],[])
        self.l.pin(old['id'],'experiment-1',release=True)
        self.assertEqual(self.l.prune(before=int(time.time()))['generations'],[old['id']]);self.assertTrue(self.l._file(old['id']).exists())
        self.l.prune(before=int(time.time()),dry_run=False);self.assertFalse(self.l._file(old['id']).exists());self.assertTrue(self.l._file(new['id']).exists())
        self.assert_equal(NOW);self.assert_equal(NOW+100)
    def test_staged_archive_publish_and_fixture_isolation(self):
        self.native();self.recent();self.l.compact(PAIR,60,T,T+3600);p=self.root/'XBTUSD_60.csv'
        p.write_text(f'{T},100,103,99,102,2,7\n{T+3600},100,103,99,101,2,7\n')
        with patch.object(mm,'BATCH',1):
            self.m.import_archive(p,PAIR,60,mm.sha256_file(p),now=NOW+10,max_batches=1)
            self.assertEqual(self.l.status()['dirty'],0);self.assertEqual(self.view()['coverage'],1)
            self.m.import_archive(p,PAIR,60,mm.sha256_file(p),now=NOW+20)
        self.assertEqual(self.l.status()['dirty'],1);self.assert_equal(NOW+19);self.assert_equal(NOW+20)
        self.l.compact(PAIR,60,T,T+3*3600);self.assert_equal()
        self.m.import_archive(p,PAIR,60,mm.sha256_file(p),now=NOW+30,fixture=True)
        self.assertEqual(self.l.status()['dirty'],0);self.assert_equal()
    def test_quota_and_corruption_fail_closed(self):
        self.native();self.recent();self.l.policy(1024*1024,0)
        with self.assertRaisesRegex(mm.MemoryUnavailable,'admission'):self.l.compact(PAIR,60,T,T+3600)
        self.assertEqual(self.l.status()['active'],0);self.assertEqual(self.view()['coverage'],1)
        self.l.policy(ml.DEFAULT_QUOTA,ml.DEFAULT_RESERVE)
        f=self.l.compact(PAIR,60,T,T+3600)[0];self.l._file(f['id']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(mm.MemoryUnavailable,'integrity'):self.view()
        with self.assertRaisesRegex(mm.MemoryUnavailable,'integrity'):self.l.compact(PAIR,60,T,T+3600)
    def test_crash_orphan_recovery(self):
        self.native();self.recent()
        # Failure after atomic rename but before SQLite manifest publication.
        with patch.object(ml,'syncdir',side_effect=OSError('simulated interrupted publication')):
            with self.assertRaises(OSError):self.l.compact(PAIR,60,T,T+3600)
        self.assertEqual(self.l.status()['active'],0);self.assert_equal()
        listing=self.l.recover_orphans();self.assertEqual(len(listing['files']),1)
        self.l.recover_orphans(dry_run=False);self.assertEqual(self.l.status()['lake_files'],0)
        f=self.l.compact(PAIR,60,T,T+3600)[0];self.assertEqual(self.l.recover_orphans()['files'],[]);self.assert_equal()
        self.assertTrue(self.l._file(f['id']).exists())
    def test_storage_endpoint_and_private_policy(self):
        with TestClient(create_app(self.root,TOKEN),base_url='http://localhost') as c:
            self.assertEqual(c.get('/api/market-storage').status_code,401)
            r=c.get('/api/market-storage',headers=AUTH);self.assertEqual(r.status_code,200);self.assertTrue(r.json()['staging_retained']);self.assertNotIn('path',r.json())
            self.assertEqual(c.post('/api/market-storage',headers=AUTH,json={'quota':0}).status_code,405)
    def test_scan_limit_and_revision_bound(self):
        self.native();self.recent();self.l.compact(PAIR,60,T,T+3600);self.recent(T+3600)
        with patch.object(ml,'MAX_RAW',1):
            with self.assertRaisesRegex(mm.MemoryUnavailable,'revision limit'):self.view()
        self.assert_equal()
    def test_reserved_free_space_and_symlinks(self):
        self.native();self.recent();self.l.policy(ml.DEFAULT_QUOTA,2**50)
        self.assertEqual(self.l.status()['state'],'storage_limited')
        with self.assertRaisesRegex(mm.MemoryUnavailable,'admission'):self.l.compact(PAIR,60,T,T+3600)
        (self.l.root/('a'*64+'.partial')).symlink_to(self.root/'other')
        with self.assertRaises(mm.MemoryUnavailable):self.l.recover_orphans(dry_run=False)
        with self.assertRaises(mm.MemoryUnavailable):self.l.status()
