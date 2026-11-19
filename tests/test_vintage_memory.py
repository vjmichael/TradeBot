import tempfile
import time
import unittest
from pathlib import Path
import httpx
from vintage_memory import VintageMemory,parse,cutoff,URL
from market_memory import MemoryUnavailable
V='2020-05-01'
RAW=b'observation_date,UNRATE_20200501\n2020-02-01,3.5\n2020-03-01,4.4\n'
class VintageTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.m=VintageMemory(self.root)
    def test_qualified_identity_and_no_lookahead(self):
        calls=[]
        def fetch(req):
            calls.append(req);self.assertEqual(req.url,httpx.URL(URL,params={'id':'UNRATE','vintage_date':V}))
            return httpx.Response(200,stream=httpx.ByteStream(RAW))
        with httpx.Client(transport=httpx.MockTransport(fetch)) as client:
            self.assertEqual(self.m.collect('UNRATE',V,client=client)['state'],'observed')
            self.assertEqual(self.m.collect('UNRATE',V,client=client)['state'],'already_observed')
        self.assertEqual(len(calls),1)
        v=VintageMemory(self.root).view('UNRATE',V,replay_at=cutoff(V))
        self.assertEqual(len(v['rows']),2)
        self.assertFalse(self.m.view('UNRATE',V,replay_at=cutoff(V)-1)['rows'])
        self.assertFalse(self.m.view('UNRATE',V,retrieved_by=v['retrieved_at']-1)['rows'])
        with self.m.memory.tx(True) as db:db.execute("UPDATE alfred_vintages SET body=X'31'")
        with self.assertRaises(MemoryUnavailable):self.m.view('UNRATE',V)
    def test_wrong_header_future_row_duplicate_and_invalid_value(self):
        for raw in (RAW.replace(b'20200501',b'20200601'),RAW.replace(b'2020-03-01',b'2020-06-01'),RAW.replace(b'2020-03-01',b'2020-02-01'),RAW.replace(b'4.4',b'101')):
            with self.assertRaises(ValueError):parse('UNRATE',V,raw)
    def test_failed_request_paced_across_reopen(self):
        now=int(time.time())
        with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(503))) as client:
            with self.assertRaises(MemoryUnavailable):self.m.collect('UNRATE',V,client=client,clock=lambda:now)
            self.assertEqual(VintageMemory(self.root).collect('PAYEMS',V,client=client,clock=lambda:now+1)['state'],'paced')
        self.assertEqual(self.m.status()['snapshots'],0)
