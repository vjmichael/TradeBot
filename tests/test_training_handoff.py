import json
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
from market_memory import MarketMemory,MemoryUnavailable
from economic_memory import EconomicMemory
from data_lifecycle import backup
from training_handoff import export,verify
T=1704067200
PAIR='BTC/USD'
class HandoffTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.data=self.root/'data';self.data.mkdir(mode=0o700)
        self.m=MarketMemory(self.data);EconomicMemory(self.data)
        self.snapshot=self.root/'snapshot';self.out=self.root/'input'
    def recent(self,stamps,price='100.125',count=4):
        observed=max(stamps)+3600
        self.m.record_recent(PAIR,60,{'exchange':'kraken','product':'spot','pair':PAIR,'interval_minutes':60,
            'observed_at':observed,'candles':[[s*1000,price,'101','99',price,'2.123456789123456789',count] for s in stamps]},now=observed)
    def freeze(self):
        return backup(self.data,self.snapshot)['manifest_sha256']
    def test_exact_numeric_json_and_lineage_from_restore(self):
        self.recent([T,T+3600]);digest=self.freeze()
        result=export(self.snapshot,self.out,digest,[PAIR],T,T+7200)
        self.assertEqual(verify(self.out,result['manifest_sha256']),result)
        file=self.out/'data/kraken/BTC_USD-1h.json'
        rows=json.loads(file.read_text(),parse_float=Decimal)
        self.assertEqual(rows[0],[T*1000,Decimal('100.125'),101,99,Decimal('100.125'),Decimal('2.123456789123456789')])
        line=json.loads((self.out/'data/kraken/BTC_USD-1h.provenance.jsonl').read_text().splitlines()[0])
        self.assertEqual(line['v'],'2.123456789123456789');self.assertTrue(line['source_ids'])
        self.assertFalse(result['training_ready'])
        with self.assertRaises(MemoryUnavailable):export(self.snapshot,self.out,digest,[PAIR],T,T+7200)
        file.write_text('[]')
        with self.assertRaises(MemoryUnavailable):verify(self.out,result['manifest_sha256'])
    def test_missing_scope_refused_atomically(self):
        self.recent([T]);digest=self.freeze()
        with self.assertRaises(MemoryUnavailable):export(self.snapshot,self.out,digest,[PAIR],T,T+7200)
        self.assertFalse(self.out.exists())
        self.assertFalse(list(self.root.glob('.training-input-*')))
        with self.assertRaises(MemoryUnavailable):export(self.snapshot,self.out,'0'*64,[PAIR],T,T+3600)
        self.assertFalse(self.out.exists())
    def test_conflicting_counts_refused(self):
        self.recent([T],count=4);self.recent([T],count=4);self.recent([T],count=5)
        with self.m.tx() as db:self.assertEqual(db.execute('SELECT count(*) FROM candles WHERE pair=? AND ts=?',(PAIR,T)).fetchone()[0],2)
        self.assertIsNone(self.m.query(PAIR,60,T,T+3600,T+7200)['rows'][0]['trades'])
        digest=self.freeze()
        with self.assertRaises(MemoryUnavailable):export(self.snapshot,self.out,digest,[PAIR],T,T+3600)
        self.assertFalse(self.out.exists())
