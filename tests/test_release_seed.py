import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import time
from market_memory import MarketMemory,MemoryUnavailable,sha256_file
from economic_memory import EconomicMemory
from release_seed import init,capture,verify,materialize,status,restore_bundle
from training_handoff import export
from market_lake import HistoricalLake,lake_trigger_definitions
from data_lifecycle import backup

class ReleaseSeedTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.repo=self.root/'seed'
        self.workspace=Path(init(self.repo)['workspace'])
        self.m=MarketMemory(self.workspace)
        self.ts=int(time.time())//3600*3600-7200
    def append(self,ts):
        self.m.record_recent('BTC/USD',60,{'exchange':'kraken','product':'spot','pair':'BTC/USD','interval_minutes':60,
            'observed_at':ts+3600,'candles':[[ts*1000,'100','101','99','100','2',4]]},now=ts+3600)
    def test_versioned_resume_dedup_and_portability(self):
        self.append(self.ts)
        a=capture(self.repo,'v1')
        self.assertFalse(a['review']['core_coverage_complete'])
        self.assertFalse(a['distribution_approved'])
        self.assertEqual(verify(self.repo,'v1',a['manifest_sha256'])['review'],a['review'])
        self.assertEqual(Path(init(self.repo)['workspace']),self.workspace)
        self.append(self.ts+3600)
        b=capture(self.repo,'v2')
        self.assertGreater(b['reused_object_bytes'],0)
        self.assertEqual(status(self.repo)['version_count'],2)
        self.assertEqual(verify(self.repo,'v1',a['manifest_sha256'])['review'],a['review'])
        copied=self.root/'fresh'
        materialize(self.repo,'v2',b['manifest_sha256'],copied,restore_data=True)
        self.assertEqual(MarketMemory(copied).query('BTC/USD',60,self.ts,self.ts+7200,b['cutoff'])['coverage'],2)
        portable=self.root/'portable'
        materialize(self.repo,'v2',b['manifest_sha256'],portable)
        self.assertEqual(sha256_file(portable/'manifest.json'),b['snapshot_sha256'])
        result=export(portable,self.root/'input',b['snapshot_sha256'],['BTC/USD'],self.ts,self.ts+7200)
        self.assertEqual(result['markets'][0]['rows'],2)
        self.assertFalse(result['training_ready'])
        package=self.root/'bundle';materialize(self.repo,'v2',b['manifest_sha256'],package,bundle=True)
        from shutil import copytree
        moved=self.root/'moved';copytree(package,moved)
        imported=self.root/'imported';restore_bundle(moved,b['manifest_sha256'],imported)
        self.assertEqual(MarketMemory(imported).query('BTC/USD',60,self.ts,self.ts+7200,b['cutoff'])['coverage'],2)
        new_seed=self.root/'new-seed';init(new_seed,snapshot=portable,snapshot_sha256=b['snapshot_sha256'])
        self.assertEqual(MarketMemory(new_seed/'workspace').query('BTC/USD',60,self.ts,self.ts+7200,b['cutoff'])['coverage'],2)
        with self.assertRaises(MemoryUnavailable):init(new_seed,snapshot=portable,snapshot_sha256=b['snapshot_sha256'])
        with self.assertRaises(MemoryUnavailable):capture(self.repo,'v2')
        with self.assertRaises(MemoryUnavailable):materialize(self.repo,'v2',b['manifest_sha256'],copied,restore_data=True)
    def test_tamper_and_quota_fail_closed(self):
        self.append(self.ts);a=capture(self.repo,'v1');target=self.root/'fresh'
        with self.assertRaises(MemoryUnavailable):materialize(self.repo,'v1','0'*64,target)
        self.assertFalse(target.exists())
        obj=next((self.repo/'objects').iterdir());obj.write_bytes(b'tampered')
        with self.assertRaises(MemoryUnavailable):materialize(self.repo,'v1',a['manifest_sha256'],target)
        self.assertFalse(target.exists())
        self.assertEqual(status(self.repo)['version_count'],1)
        with patch('release_seed.MAX_STORE_BYTES',1),self.assertRaises(MemoryUnavailable):capture(self.repo,'v2')
        self.assertFalse((self.repo/'versions/v2').exists())
    def test_fixture_and_unknown_schema_refused(self):
        p=self.root/'XBTUSD_60.csv';p.write_text(f'{self.ts},100,101,99,100,2,4\n')
        self.m.import_archive(p,'BTC/USD',60,sha256_file(p),fixture=True)
        with self.assertRaises(MemoryUnavailable):capture(self.repo,'fixture')
        with self.m.tx() as db:db.execute('CREATE TABLE private_account(secret TEXT)')
        with self.assertRaises(MemoryUnavailable):capture(self.repo,'unsafe')
        self.assertEqual(status(self.repo)['version_count'],0)
    def test_path_and_private_destination(self):
        self.append(self.ts);a=capture(self.repo,'v1')
        for version in ('../bad','/absolute',''):
            with self.assertRaises(ValueError):capture(self.repo,version)
        with self.assertRaises(MemoryUnavailable):materialize(self.repo,'v1',a['manifest_sha256'],self.workspace/'bad')

    def test_portable_bundle_extra_file_refused(self):
        self.append(self.ts);a=capture(self.repo,'v1')
        package=self.root/'bundle';materialize(self.repo,'v1',a['manifest_sha256'],package,bundle=True)
        (package/'snapshot/secret.txt').write_text('must not be accepted')
        target=self.root/'restored'
        with self.assertRaises(MemoryUnavailable):restore_bundle(package,a['manifest_sha256'],target)
        self.assertFalse(target.exists())

    def test_hidden_directory_refused(self):
        self.append(self.ts);a=capture(self.repo,'v1')
        package=self.root/'bundle';materialize(self.repo,'v1',a['manifest_sha256'],package,bundle=True)
        (package/'snapshot/market-memory/unexpected').mkdir()
        with self.assertRaises(MemoryUnavailable):restore_bundle(package,a['manifest_sha256'],self.root/'fresh')
        self.assertFalse((self.root/'fresh').exists())

    def test_lake_triggers_survive_seed_roundtrip(self):
        lake=HistoricalLake(self.m);self.append(self.ts)
        try:
            import duckdb
        except ImportError:
            duckdb=None
        if duckdb is not None:
            self.assertEqual(lake.compact('BTC/USD',60,self.ts,self.ts+3600)[0]['state'],'published')
        as_of=int(time.time());before=lake.query('BTC/USD',60,self.ts,self.ts+3600,as_of)
        with self.m.tx() as db:
            expected=[tuple(row) for row in db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")]
        self.assertEqual(len(expected),5)
        receipt=backup(self.workspace,self.root/'native-shape')
        seeded=self.root/'imported-seed';init(seeded,snapshot=self.root/'native-shape',snapshot_sha256=receipt['manifest_sha256'])
        a=capture(seeded,'lake-v1')
        verified=verify(seeded,'lake-v1',a['manifest_sha256'])
        self.assertEqual(verified['review'],a['review'])
        bundle=self.root/'lake-bundle';materialize(seeded,'lake-v1',a['manifest_sha256'],bundle,bundle=True)
        fresh=self.root/'lake-fresh';restore_bundle(bundle,a['manifest_sha256'],fresh)
        restored=MarketMemory(fresh)
        # Inspect actual restored DDL BEFORE HistoricalLake can repair it.
        with restored.tx() as db:
            self.assertEqual(expected,[tuple(row) for row in db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")])
            epoch_before=db.execute("SELECT coalesce(sum(epoch),0) FROM lake_epoch").fetchone()[0]
        self.assertEqual(HistoricalLake(restored).query('BTC/USD',60,self.ts,self.ts+3600,as_of),before)
        ts=self.ts+3600
        restored.record_recent('BTC/USD',60,{'exchange':'kraken','product':'spot','pair':'BTC/USD','interval_minutes':60,
            'observed_at':ts+3600,'candles':[[ts*1000,'100','101','99','100','2',4]]},now=ts+3600)
        with restored.tx() as db:
            self.assertGreater(db.execute("SELECT coalesce(sum(epoch),0) FROM lake_epoch").fetchone()[0],epoch_before)
        self.assertEqual(restored.query('BTC/USD',60,self.ts,ts+3600,int(time.time()))['coverage'],2)

    def test_missing_replaced_and_extra_lake_triggers_refused(self):
        HistoricalLake(self.m);self.append(self.ts)
        name='lake_candle_insert_new'
        with self.m.tx() as db:db.execute('DROP TRIGGER '+name)
        with self.assertRaisesRegex(MemoryUnavailable,'missing or altered'):capture(self.repo,'missing')
        with self.m.tx() as db:db.execute('CREATE TRIGGER '+name+' AFTER INSERT ON candles BEGIN SELECT 1; END')
        with self.assertRaisesRegex(MemoryUnavailable,'missing or altered'):capture(self.repo,'replaced')
        with self.m.tx() as db:
            db.execute('DROP TRIGGER '+name)
            db.execute(lake_trigger_definitions()[name][1])
            db.execute('CREATE TRIGGER unexpected AFTER INSERT ON candles BEGIN SELECT 1; END')
        with self.assertRaisesRegex(MemoryUnavailable,'object type trigger'):capture(self.repo,'extra')
        self.assertEqual(status(self.repo)['version_count'],0)

    def test_unknown_schema_refused_without_fixture(self):
        self.append(self.ts)
        with self.m.tx() as db:db.execute('CREATE TABLE private_account(secret TEXT)')
        with self.assertRaisesRegex(MemoryUnavailable,'table inventory'):capture(self.repo,'unknown')
        self.assertEqual(status(self.repo)['version_count'],0)
