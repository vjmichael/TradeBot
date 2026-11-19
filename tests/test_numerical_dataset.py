"""Deterministic test observations only; not evidence of market performance."""
import tempfile
import unittest
import time
from pathlib import Path
from market_memory import MarketMemory,MemoryUnavailable
from economic_memory import EconomicMemory
from data_lifecycle import backup
from numerical_features import Plan,STEP
from numerical_dataset import build,verify,reproduce,model_rows

class NumericalDatasetTests(unittest.TestCase):
    def test_verified_snapshot_reproducible_and_tamper_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';data.mkdir(mode=0o700)
            m=MarketMemory(data);EconomicMemory(data)
            end=int(time.time())//STEP*STEP-STEP;start=end-360*STEP
            # Marked as simulated provider response in the test, no public I/O.
            candles=[[t*1000,'100','101','99','100',str(10+(t//STEP)%5),4] for t in range(start,end,STEP)]
            m.record_recent('BTC/USD',60,{'exchange':'kraken','product':'spot','pair':'BTC/USD','interval_minutes':60,
                'observed_at':end,'candles':candles},now=end)
            receipt=backup(data,root/'snapshot');digest=receipt['manifest_sha256']
            p=Plan(start,end,start+180*STEP,start+270*STEP,6,2,40,10,5,'provider-retrospective')
            a=build(root/'snapshot',root/'one',digest,['BTC/USD'],p)
            b=build(root/'snapshot',root/'two',digest,['BTC/USD'],p)
            self.assertEqual(a,b)
            self.assertEqual(verify(root/'one',a['manifest_sha256']),a)
            self.assertTrue(reproduce(root/'one',a['manifest_sha256'],root/'snapshot')['reproduced'])
            x=model_rows(root/'one',a['manifest_sha256'],'BTC/USD','train','numerical_only','&-net_return')
            ab=model_rows(root/'one',a['manifest_sha256'],'BTC/USD','train','no_sessions','&-net_return')
            self.assertEqual(x['decisions'],ab['decisions']);self.assertEqual(x['y'],ab['y'])
            self.assertTrue(all(k.startswith('%-') for k in x['columns']))
            self.assertTrue(all(len(row)==len(x['columns']) for row in x['X']))
            self.assertLess(len(ab['columns']),len(x['columns']))
            self.assertFalse(a['training_ready'])
            self.assertGreater(a['markets'][0]['counts']['test'],24)
            with self.assertRaises(MemoryUnavailable):
                build(root/'snapshot',root/'strict',digest,['BTC/USD'],Plan(**(p.as_dict()|{'availability_mode':'local-observed'})))
            self.assertFalse((root/'strict').exists())
            with self.assertRaises(MemoryUnavailable):build(root/'snapshot',root/'one',digest,['BTC/USD'],p)
            path=root/'one'/'BTC_USD-train.jsonl'
            path.write_bytes(path.read_bytes()+b'{}\n')
            with self.assertRaises(MemoryUnavailable):verify(root/'one',a['manifest_sha256'])

    def test_later_identical_price_count_not_available_earlier(self):
        from numerical_dataset import checked_rows
        from market_memory import MarketMemory
        from numerical_features import technical
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);m=MarketMemory(root)
            end=int(time.time())//STEP*STEP-STEP;start=end-360*STEP
            for i,t in enumerate(range(start,end,STEP)):
                payload={'exchange':'kraken','product':'spot','pair':'BTC/USD','interval_minutes':60,
                         'observed_at':t+STEP,'candles':[[t*1000,'100','101','99','100','10',None if i==0 else 4]]}
                m.record_recent('BTC/USD',60,payload,now=t+STEP)
                if i==0:
                    payload['candles'][0][-1]=4
                    m.record_recent('BTC/USD',60,payload,now=end)
            p=Plan(start,end,start+180*STEP,start+270*STEP,6,2,40,10,5,'local-observed')
            rows=checked_rows(m,'BTC/USD',p,int(time.time()))
            self.assertEqual(rows[0]['available_at'],start+STEP)
            self.assertEqual(rows[0]['contributing_available_at'],end)
            self.assertIsNone(technical(rows[:25],start+25*STEP,'local-observed'))
