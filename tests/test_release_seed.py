import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import time
from market_memory import MarketMemory,MemoryUnavailable,sha256_file
from economic_memory import EconomicMemory
from release_seed import init,capture,verify,materialize,status,restore_bundle
from training_handoff import export

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
