import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
from data_scheduler import Schedule
from market_archive import RELEASE
from market_memory import MarketMemory,MemoryUnavailable
from release_seed import init
from seed_population import need_archive,run_epoch,population_status,_journal

@unittest.skipUnless(os.name=='posix','Linux worker custody')
class SeedPopulationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)/'seed'
        self.data=Path(init(self.root)['workspace'])
    def archive_done(self):
        with MarketMemory(self.data).tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS market_archive_status(pair TEXT PRIMARY KEY,checked INTEGER,state TEXT,release TEXT)')
            db.executemany('INSERT OR REPLACE INTO market_archive_status VALUES(?,?,?,?)',[(p,1,'imported',RELEASE) for p in ('BTC/USD','ETH/USD','SOL/USD')])
    def test_initial_archive_scope_and_reopen(self):
        self.assertTrue(need_archive(self.data));self.archive_done()
        self.assertFalse(need_archive(self.data));self.assertFalse(population_status(self.root)['archive_preparation_pending'])
        with MarketMemory(self.data).tx() as db:db.execute("DELETE FROM market_archive_status WHERE pair='ETH/USD'")
        self.assertTrue(need_archive(self.data))
    def test_operator_stop_reconciles_prior_epoch(self):
        with closing(_journal(self.root)) as db,db:db.execute("INSERT INTO epochs(version,started,state) VALUES('old',1,'running')")
        result=run_epoch(self.root,'new',60,stop=lambda:True)
        self.assertEqual(result['state'],'stopped')
        rows=population_status(self.root)['epochs']
        self.assertEqual({r['version']:r['state'] for r in rows},{'old':'interrupted','new':'stopped'})
        with self.assertRaises(MemoryUnavailable):run_epoch(self.root,'new',60,stop=lambda:True)
    def test_refuse_enabled_scheduler(self):
        s=Schedule(self.data);self.addCleanup(s.close);s.enable(True)
        with self.assertRaises(MemoryUnavailable):run_epoch(self.root,'new',60,stop=lambda:True)
        self.assertTrue(s.status()['enabled'])
    def test_owned_prepare_then_collect_and_checkpoint(self):
        modes=[]
        # Exercise real subprocess custody with a deterministic child, not
        # genuine exchange data; capture mocked separately in this unit test.
        script=self.root/'worker.py'
        script.write_text('import sys,time\ntime.sleep(.05)\n')
        def archive(workspace):
            modes.append('archive');return len(modes)==1
        receipt={'review':{'core_coverage_complete':False},'manifest_sha256':'a'*64}
        with patch('data_preparation.SCRIPT',script),patch('seed_population.need_archive',side_effect=archive),patch('seed_population.capture',return_value=receipt):
            out=run_epoch(self.root,'checkpoint',60)
        self.assertEqual(out['state'],'checkpoint_saved_incomplete')
        row=population_status(self.root)['epochs'][0]
        self.assertEqual((row['mode'],row['state'],row['manifest_sha256']),('collect','checkpoint_saved_incomplete','a'*64))
