"""Additional markets: exact routing, retained identity, scope and recoverable storage."""
import tempfile
import time
import unittest
from pathlib import Path
from market_directory import MarketDirectory, DirectoryUnavailable, parse_directory, FRESH_SECONDS
from market_memory import MarketMemory
from market_stream import exact_closed, decode_message, StreamJournal
from market_gap import parse_page
from data_lifecycle import coverage, backup, restore
from economic_memory import EconomicMemory
from test_market_directory import payload

class DynamicMarkets(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.directory=self.root/'data'
        self.directory.mkdir(mode=0o700)
        self.registry=MarketDirectory(self.directory)
        self.now=int(time.time())
        result=self.registry.refresh(now=self.now,fetch=lambda:parse_directory(payload()))
        self.registry.select('ADA/USD',True,result['version'],now=self.now)

    def test_retained_read_and_active_write_scope(self):
        self.assertIn('ADA/USD',self.registry.active_symbols(now=self.now))
        memory=MarketMemory(self.directory)
        self.assertEqual(memory.symbols['ADA/USD'],('ADAUSD','ADAUSD'))
        end=self.now//3600*3600
        self.assertEqual(memory.query('ADA/USD',60,end-3600,end,self.now)['coverage'],0)
        self.assertIn('ADA/USD',[r['pair'] for r in coverage(memory,self.now)['markets']])
        with self.assertRaises(DirectoryUnavailable):
            self.registry.active_symbols(now=self.now+FRESH_SECONDS)
        self.registry.select('ADA/USD',False,None,now=self.now)
        self.assertIn('ADA/USD',memory.symbols)
        self.assertNotIn('ADA/USD',self.registry.active_symbols(now=self.now))
        self.assertNotIn('ADA/USD',[r['pair'] for r in coverage(memory,self.now)['markets']])

    def test_dynamic_rest_and_ws_identity(self):
        import httpx
        from unittest.mock import Mock
        active=self.registry.active_symbols(now=self.now)
        end=self.now//3600*3600
        body={'error':[],'result':{'ADAUSD':[[end-3600,'1','1.1','0.9','1','1','30',2],[end,'1','1.1','0.9','1','1','20',1]],'last':end}}
        client=Mock()
        client.get.return_value=httpx.Response(200,json=body)
        snap=exact_closed('ADA/USD',60,client=client,now=self.now,symbols=active)
        self.assertEqual(client.get.call_args.kwargs['params']['pair'],'ADAUSD')
        memory=MarketMemory(self.directory)
        self.assertEqual(memory.record_recent('ADA/USD',60,snap,now=self.now),1)
        self.assertEqual(memory.query('ADA/USD',60,end-3600,end,self.now)['coverage'],1)
        wrong={'error':[],'result':{'XETHZUSD':body['result']['ADAUSD'],'last':end}}
        client.get.return_value=httpx.Response(200,json=wrong)
        with self.assertRaises(ValueError):
            exact_closed('ADA/USD',60,client=client,now=self.now,symbols=active)
        ack='{"method":"subscribe","success":true,"result":{"channel":"ohlc","symbol":"ADA/USD","interval":60}}'
        self.assertEqual(decode_message(ack,self.now,active),('ack','ADA/USD'))
        with self.assertRaises(ValueError):
            decode_message(ack,self.now)
        journal=StreamJournal(memory,active)
        journal.start()
        journal.ack('ADA/USD',self.now)
        self.assertTrue(next(x for x in journal.status() if x['pair']=='ADA/USD')['acked'])
        journal.stop()

    def test_data_only_backup_retains_selected_identity(self):
        memory=MarketMemory(self.directory)
        EconomicMemory(self.directory)
        receipt=backup(self.directory,self.root/'snapshot')
        restore(self.root/'snapshot',self.root/'restored',receipt['manifest_sha256'])
        recovered=MarketDirectory(self.root/'restored')
        self.assertEqual(recovered.retained_symbols(),self.registry.retained_symbols())
        self.assertEqual(recovered.selected_symbols(),self.registry.selected_symbols())
        self.assertIn('ADA/USD',MarketMemory(self.root/'restored').symbols)
        self.assertFalse((self.root/'restored'/'desk.sqlite3').exists())

    def test_baseline_retirement_requires_fresh_version_and_keeps_identity(self):
        state=self.registry.browse(now=self.now)
        self.registry.select('ETH/USD',False,state['version'],now=self.now)
        self.assertNotIn('ETH/USD',self.registry.active_symbols(now=self.now))
        self.assertIn('ETH/USD',self.registry.retained_symbols())
        self.registry.select('ETH/USD',True,state['version'],now=self.now)
        self.assertIn('ETH/USD',self.registry.active_symbols(now=self.now))

    def test_selected_archive_missing_and_fixture_import(self):
        import io,zipfile
        from market_archive import import_members
        memory=MarketMemory(self.directory)
        end=self.now//3600*3600
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('ADAUSD_60.csv',f'{end-3600},1,1.1,0.9,1,30,2\n')
        buf.seek(0)
        receipt=import_members(memory,buf,fixture=True,now=self.now,symbols={'ADA/USD':memory.symbols['ADA/USD']})
        self.assertEqual(receipt[0]['rows'],1)
        self.assertEqual(memory.query('ADA/USD',60,end-3600,end,self.now,fixture=True)['coverage'],1)
        self.assertEqual(memory.query('ADA/USD',60,end-3600,end,self.now)['coverage'],0)
        empty=io.BytesIO()
        with zipfile.ZipFile(empty,'w'):pass
        empty.seek(0)
        receipt=import_members(memory,empty,fixture=True,now=self.now,symbols={'ADA/USD':memory.symbols['ADA/USD']})
        self.assertEqual(receipt[0]['state'],'not_in_pinned_archive')

    def test_trade_page_requires_selected_rest_response_key(self):
        from decimal import Decimal
        cursor=(self.now-1)*10**9
        stamp=self.now
        response={'error':[],'result':{'ADAUSD':[['1','2',Decimal(stamp),'b','m','',1]],'last':str(stamp*10**9+1)}}
        active=self.registry.active_symbols(now=self.now)
        self.assertEqual(parse_page('ADA/USD',cursor,response,active)[1][0][1],1)
        response['result']['ETHUSD']=response['result'].pop('ADAUSD')
        with self.assertRaises(ValueError):
            parse_page('ADA/USD',cursor,response,active)
