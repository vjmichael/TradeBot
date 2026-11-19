"""Local deterministic fault drills; fixture records never leave private temp dirs."""
import asyncio
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import httpx
import market_archive as ar
from market_memory import MarketMemory,MemoryUnavailable,sha256_file
from market_stream import StreamJournal,decode_message,exact_closed,session
from market_gap import GapBridge,parse_page,NS
from engine import SYMBOLS

T=1704067200
NOW=T+8*3600


def fixture_zip(duplicate=False):
    f=io.BytesIO()
    with zipfile.ZipFile(f,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for b,_ in SYMBOLS.values():z.writestr(f'Kraken/{b}_60.csv',f'{T},100,103,99,101,2,4\n{T+3600},101,103,99,100,3,5\n')
        z.writestr('../../not-selected.txt','ignore')
        if duplicate:z.writestr('another/XBTUSD_60.csv','wrong')
    return f.getvalue()


def split(data):
    sizes=[len(data)*i//5 for i in range(6)]
    return [data[a:b] for a,b in zip(sizes,sizes[1:])]


def rest(pair,stamps,observed=NOW):
    return {'exchange':'kraken','product':'spot','pair':pair,'interval_minutes':60,'observed_at':observed,'candles':[[s*1000,'100','100','100','100','2',1] for s in stamps]}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.mem=MarketMemory(self.root)
    def transport(self,parts,ignore=False,cut=False):
        def mock(req):
            self.assertEqual(req.url.host,'assets.kraken.com');self.assertNotIn('authorization',req.headers)
            idx=ar.PARTS.index(req.url.path.split('/')[-1]);data=parts[idx];headers={'ETag':f'"{idx}"','Content-Length':str(len(data))}
            if req.method=='HEAD':return httpx.Response(200,headers=headers)
            r=req.headers.get('Range');self.assertEqual(req.method,'GET')
            if not r or ignore:return httpx.Response(200,headers=headers,stream=httpx.ByteStream(data))
            start,end=r.removeprefix('bytes=').split('-');start=int(start);end=int(end) if end else len(data)-1
            body=data[start:end+1];headers['Content-Range']=f'bytes {start}-{end}/{len(data)}';headers['Content-Length']=str(len(body))
            return httpx.Response(206,headers=headers,stream=httpx.ByteStream(body[:-1] if cut else body))
        return httpx.Client(transport=httpx.MockTransport(mock),follow_redirects=False)
    def test_split_selective_crc_and_fixture_isolation(self):
        data=fixture_zip();parts=split(data)
        with self.transport(parts) as c:
            r=ar.RemoteParts(c);receipt=ar.import_members(self.mem,r.reader(),fixture=True,now=NOW)
        self.assertEqual(len(receipt),3);self.assertTrue(all(x['rows']==2 and x['validation']=='fixture' for x in receipt))
        self.assertFalse((self.root/'not-selected.txt').exists())
        self.assertEqual(self.mem.query('BTC/USD',60,T,T+7200,NOW,fixture=True)['coverage'],2)
        self.assertEqual(self.mem.query('BTC/USD',60,T,T+7200,NOW)['coverage'],0)
    def test_range_refusal_and_truncation(self):
        for mode in ({'ignore':True},{'cut':True}):
            with self.transport(split(fixture_zip()),**mode) as c,self.assertRaises(ar.ArchiveError):ar.RemoteParts(c).reader().read(1)
    def test_ambiguous_selected_members_fail(self):
        with self.assertRaises(ar.ArchiveError):ar.import_members(self.mem,io.BytesIO(fixture_zip(True)),fixture=True,now=NOW)
        self.assertEqual(self.mem.query('BTC/USD',60,T,T+7200,NOW,fixture=True)['coverage'],0)
    def test_full_resume_verify_and_no_reassembly(self):
        data=fixture_zip();parts=split(data);folder=self.root/'parts';folder.mkdir(mode=0o700)
        partial=folder/(ar.PARTS[0]+'.partial');partial.write_bytes(parts[0][:8])
        expected=tuple(hashlib.sha256(p).hexdigest() for p in parts)
        with patch.object(ar,'HASHES',expected),patch.object(ar,'FULL_SHA',hashlib.sha256(data).hexdigest()),self.transport(parts) as c:
            ar.download_full_parts(folder,c)
            with ar.verified_reader(folder) as reader:
                self.assertEqual(reader.read(),data);reader.seek(0)
                self.assertEqual(len(ar.import_members(self.mem,reader,verified=True,fixture=True,now=NOW)),3)
            (folder/ar.PARTS[1]).write_bytes(b'bad')
            with self.assertRaises(ar.ArchiveError):ar.verified_reader(folder)
        self.assertFalse(partial.exists())
    def test_single_writer(self):
        with ar.exclusive_job(self.mem.root,'test'),self.assertRaises(ar.ArchiveError):
            with ar.exclusive_job(self.mem.root,'test'):pass


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.mem=MarketMemory(Path(self.tmp.name));self.journal=StreamJournal(self.mem)
    def message(self,pair='BTC/USD',stamp=T,close=100,trades=1):
        return json.dumps({'channel':'ohlc','type':'update','data':[{'symbol':pair,'interval':60,'interval_begin':datetime.fromtimestamp(stamp,timezone.utc).isoformat().replace('+00:00','Z'),'open':100,'high':103,'low':99,'close':close,'volume':2,'trades':trades}]})
    def test_stream_indicative_never_historical(self):
        self.journal.start();self.journal.ack('BTC/USD',NOW)
        typ,rows=decode_message(self.message(stamp=NOW),NOW);self.assertEqual(typ,'updates');self.journal.updates(rows,NOW)
        self.assertEqual(self.mem.query('BTC/USD',60,T,NOW,NOW)['coverage'],0)
        self.journal.updates(decode_message(self.message(stamp=NOW-3600),NOW)[1],NOW)
        with self.assertRaises(ValueError):self.journal.updates(decode_message(self.message(stamp=NOW,close=102),NOW)[1],NOW)
        self.journal.stop();self.assertTrue(all(not x['transport_fresh'] for x in self.journal.status()))
    def test_rest_format_exact_and_forming_excluded(self):
        rows=[[NOW-3600,'100.000000000001','101','99','100','100','2.000000000000001',2],[NOW,'100','101','99','100','100','1',1]]
        c=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'error':[],'result':{'XXBTZUSD':rows,'last':NOW}})))
        with c: s=exact_closed('BTC/USD',client=c,now=NOW)
        self.assertEqual(s['candles'][0][1],'100.000000000001');self.assertEqual(len(s['candles']),1)
        self.mem.record_recent('BTC/USD',60,s,now=NOW)
        view=self.mem.query('BTC/USD',60,NOW-3600,NOW,NOW);self.assertEqual(view['rows'][0]['trades'],2)
    def test_gap_repair_preserves_unresolved_history(self):
        # One old anchor, missed 1h, later gap repaired using REST replay.
        self.mem.record_recent('BTC/USD',60,rest('BTC/USD',[T],T+3600),now=T+3600)
        v=self.journal.repair('BTC/USD',fetch=lambda *_:rest('BTC/USD',[T+2*3600,T+4*3600,T+5*3600,T+6*3600,T+7*3600]),now=NOW)
        self.assertEqual((v['coverage'],v['missing'],v['outside_rest_window']),(6,2,1))
        self.journal=StreamJournal(self.mem)
        v=self.journal.repair('BTC/USD',fetch=lambda *_:rest('BTC/USD',[T+2*3600,T+3*3600,T+4*3600,T+5*3600,T+6*3600,T+7*3600]),now=NOW)
        self.assertEqual((v['inserted'],v['missing'],v['outside_rest_window']),(1,1,1))
    def test_decode_rejects_foreign_future_and_wrong_interval(self):
        for raw in [self.message('BTC/EUR'),self.message(stamp=NOW+3600),self.message().replace('"interval": 60','"interval": 1')]:
            with self.assertRaises(ValueError):decode_message(raw,NOW)
    def test_session_subscribes_all_and_repairs_on_reconnect(self):
        class WS:
            def __init__(s):s.n=0;s.sent=[]
            async def send(s,m):s.sent.append(json.loads(m))
            async def recv(s):
                n=s.n;s.n+=1
                # Explicitly reproduce a valid last market arriving later than
                # the old 180 ms observation window; acknowledgement alone
                # must not satisfy qualification.
                await asyncio.sleep(.25 if n==5 else .01)
                if n<3:return json.dumps({'method':'subscribe','success':True,'result':{'channel':'ohlc','symbol':list(SYMBOLS)[n],'interval':60}})
                if n<6:return self.message(list(SYMBOLS)[n-3],stamp=NOW)
                return '{"channel":"heartbeat"}'
        calls=[]
        def fetch(p,i):calls.append(p);return rest(p,[NOW-3600],NOW)
        # Current real clock is after fixture, so repair publishing is legal.
        for _ in range(2):
            ws=WS();r=asyncio.run(session(self.journal,ws,duration=10,fetch=fetch,until_observed=True))
            self.assertEqual(set(r['acknowledged']),set(SYMBOLS));self.assertEqual(set(r['stream_observed']),set(SYMBOLS));self.assertEqual(set(r['rest_repaired']),set(SYMBOLS));self.assertEqual(ws.sent[0]['params']['symbol'],list(SYMBOLS))
        self.assertEqual(calls,list(SYMBOLS)*2)
        self.assertFalse(any(x['connected'] for x in self.journal.status()))


    def test_native_loopback_websocket_and_disconnect(self):
        try:
            from websockets.asyncio.server import serve
            from websockets.asyncio.client import connect
        except ImportError:self.skipTest('native websockets dependency not installed')
        async def exercise():
            async def sender(ws):
                request=json.loads(await ws.recv());self.assertEqual(request['params']['symbol'],list(SYMBOLS))
                for pair in SYMBOLS:
                    await ws.send(json.dumps({'method':'subscribe','success':True,'result':{'channel':'ohlc','symbol':pair,'interval':60}}))
                    await ws.send(self.message(pair,stamp=NOW))
                try:
                    while True:
                        await ws.send('{"channel":"heartbeat"}');await asyncio.sleep(.02)
                except Exception:pass
            async with serve(sender,'127.0.0.1',0) as server:
                port=server.sockets[0].getsockname()[1]
                async with connect(f'ws://127.0.0.1:{port}',proxy=None) as ws:
                    receipt=await session(self.journal,ws,duration=10,fetch=lambda p,i:rest(p,[NOW-3600],NOW),until_observed=True)
                self.assertEqual(set(receipt['stream_observed']),set(SYMBOLS));self.assertEqual(set(receipt['rest_repaired']),set(SYMBOLS))
                self.assertFalse(any(r['connected'] for r in self.journal.status()))
        asyncio.run(exercise())

    def test_qualification_deadline_never_fabricates_missing_observation(self):
        pair='BTC/USD'
        journal=StreamJournal(self.mem,{pair:SYMBOLS[pair]})
        class WS:
            async def send(s,m):s.sent=json.loads(m)
            async def recv(s):
                if not getattr(s,'sent_ack',False):
                    s.sent_ack=True
                    return json.dumps({'method':'subscribe','success':True,'result':{'channel':'ohlc','symbol':pair,'interval':60}})
                # Transport remains quiet: no qualifying market update ever arrives.
                await asyncio.Future()
        async def exercise():
            with self.assertRaises(ValueError):
                await session(journal,WS(),until_observed=True)
            ws=WS()
            calls=[]
            def fetch(p,i):
                calls.append(p);return rest(p,[NOW-3600],NOW)
            receipt=await asyncio.wait_for(session(journal,ws,duration=1,fetch=fetch,until_observed=True),timeout=5)
            self.assertEqual(ws.sent['params']['symbol'],[pair])
            self.assertEqual(receipt,{'acknowledged':[pair],'stream_observed':[],'rest_repaired':[pair]})
            self.assertEqual(calls,[pair])
            self.assertFalse(any(r['connected'] for r in journal.status()))
        asyncio.run(exercise())

    def test_stale_snapshot_does_not_report_market_fresh(self):
        self.journal.start();self.journal.ack('BTC/USD',NOW)
        self.journal.updates(decode_message(self.message(stamp=NOW-3600),NOW)[1],NOW)
        with patch('market_stream.time.time',return_value=NOW):
            row=next(r for r in self.journal.status() if r['pair']=='BTC/USD')
            self.assertTrue(row['transport_fresh']);self.assertFalse(row['market_update_fresh'])
        with patch('market_stream.time.time',return_value=NOW+60):
            row=next(r for r in self.journal.status() if r['pair']=='BTC/USD');self.assertFalse(row['transport_fresh'])


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.mem=MarketMemory(Path(self.tmp.name));self.b=GapBridge(self.mem)
        self.start=T+3600;self.end=T+2*3600
        self.mem.record_recent('BTC/USD',60,rest('BTC/USD',[T],T+3600),now=T+3600)
        self.mem.record_recent('BTC/USD',60,rest('BTC/USD',[self.end],T+3*3600),now=T+3*3600)
    def page(self,stamps,ids,prices=None):
        prices=prices or ['100']*len(stamps)
        rows=[[p,'2',s,'b','m','',i] for p,s,i in zip(prices,stamps,ids)]
        return {'error':[],'result':{'XXBTZUSD':rows,'last':str(stamps[-1]*NS)}}
    def test_checkpoint_resume_and_exact_anchor_validation(self):
        a=self.page([T,self.start],[11,12]);b=self.page([self.end,T+3*3600],[13,14])
        j=self.b.run('BTC/USD',self.start,self.end,max_pages=1,fetch=lambda *_:a,pace=lambda _:None,clock=lambda:NOW)
        self.assertEqual((j['state'],j['pages']),('recovering',1));self.assertEqual(self.mem.query('BTC/USD',60,T,T+3*3600,NOW)['coverage'],2)
        def second(pair,cursor):self.assertEqual(cursor,self.start*NS);return b
        j=GapBridge(self.mem).run('BTC/USD',self.start,self.end,fetch=second,pace=lambda _:None,clock=lambda:NOW)
        self.assertEqual(j['state'],'complete');v=self.mem.query('BTC/USD',60,T,T+3*3600,NOW);self.assertEqual(v['coverage'],3);self.assertEqual(v['rows'][1]['trades'],1)
        self.assertEqual(GapBridge(self.mem).run('BTC/USD',self.start,self.end,fetch=lambda *_:self.fail('already completed'),clock=lambda:NOW)['source_id'],j['source_id'])
    def test_admission_deferral_before_network_preserves_exact_frontier(self):
        from market_gap import RecoveryDeferred
        first=self.run_page(self.page([T,self.start],[11,12]))
        def denied(pair):raise RecoveryDeferred('budget')
        with self.assertRaises(RecoveryDeferred):
            self.b.run('BTC/USD',self.start,self.end,max_pages=1,fetch=lambda *_:self.fail('request escaped budget'),clock=lambda:NOW,before_page=denied)
        with self.mem.tx() as db:
            state=db.execute('SELECT cursor,pages,state FROM gap_jobs WHERE id=?',(first['id'],)).fetchone()
            self.assertEqual(tuple(state),(first['cursor'],1,'recovering'))
        self.assertEqual(self.mem.query('BTC/USD',60,self.start,self.end,NOW)['coverage'],0)
    def test_bad_anchor_never_publishes(self):
        p=self.page([T,self.start,self.end,T+3*3600],[11,12,13,14],['99','100','100','100'])
        with self.assertRaises(MemoryUnavailable):self.b.run('BTC/USD',self.start,self.end,fetch=lambda *_:p,clock=lambda:NOW)
        self.assertEqual(self.mem.query('BTC/USD',60,T,T+3*3600,NOW)['coverage'],2)
    def test_nonadvancing_and_duplicate_ids(self):
        with self.assertRaises(ValueError):parse_page('BTC/USD',T*NS,self.page([T],[1]))
        with self.assertRaises(ValueError):parse_page('BTC/USD',T*NS-1,self.page([T,T+3600],[1,1]))

    def run_page(self,page,max_pages=1):
        return GapBridge(self.mem).run('BTC/USD',self.start,self.end,max_pages=max_pages,fetch=lambda *_:page,pace=lambda _:None,clock=lambda:NOW)

    def test_exact_overlap_across_restart_counts_once(self):
        first=self.run_page(self.page([T,self.start],[11,12]))
        result=self.run_page(self.page([T,self.start,self.end,T+3*3600],[11,12,13,14]))
        self.assertEqual(result['state'],'complete')
        view=self.mem.query('BTC/USD',60,self.start,self.end,NOW)
        self.assertEqual((view['coverage'],view['rows'][0]['v'],view['rows'][0]['trades']),(1,'2',1))
        with self.mem.tx() as db:
            rows=db.execute('SELECT accepted_count,overlap_count FROM gap_overlap_receipts WHERE job=? ORDER BY page',(first['id'],)).fetchall()
            self.assertEqual([tuple(r) for r in rows],[(2,0),(2,2)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gap_trade_tail WHERE job=?',(first['id'],)).fetchone()[0],4)

    def test_overlap_spans_multiple_pages_and_same_timestamp_new_trade(self):
        # Two distinct trade IDs at the same timestamp must both count.
        self.run_page(self.page([T,self.start],[11,12]))
        p=self.page([self.start,self.start],[12,13]);p['result']['last']=str(self.start*NS+1)
        self.run_page(p)
        j=self.run_page(self.page([T,self.start,self.start,self.end,T+3*3600],[11,12,13,14,15]))
        self.assertEqual(j['state'],'complete')
        row=self.mem.query('BTC/USD',60,self.start,self.end,NOW)['rows'][0]
        self.assertEqual((row['v'],row['trades']),('4',2))

    def test_bad_overlap_does_not_advance_or_publish(self):
        j=self.run_page(self.page([T,self.start],[11,12]))
        bad=[self.page([self.start,self.end,T+3*3600],[12,13,14],['101','100','100']),
             self.page([T,self.end,T+3*3600],[11,13,14]),
             self.page([T,self.start,self.end,T+3*3600],[10,12,13,14]),
             self.page([T,self.start],[11,12])]
        bad[-1]['result']['last']=str(self.start*NS+1)
        for p in bad:
            with self.subTest(p=p),self.assertRaises(ValueError):self.run_page(p)
            with self.mem.tx() as db:
                row=db.execute('SELECT cursor,pages,last_trade FROM gap_jobs WHERE id=?',(j['id'],)).fetchone()
                self.assertEqual(tuple(row),(j['cursor'],1,12))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM gap_pages WHERE job=?',(j['id'],)).fetchone()[0],1)
            self.assertEqual(self.mem.query('BTC/USD',60,self.start,self.end,NOW)['coverage'],0)
        self.assertEqual(self.run_page(self.page([self.start,self.end,T+3*3600],[12,13,14]))['state'],'complete')

    def test_missing_durable_tail_fails_closed(self):
        j=self.run_page(self.page([T,self.start],[11,12]))
        with self.mem.tx() as db:db.execute('DELETE FROM gap_trade_tail WHERE job=?',(j['id'],))
        with self.assertRaises(MemoryUnavailable):self.run_page(self.page([self.end,T+3*3600],[13,14]))
        self.assertEqual(self.mem.query('BTC/USD',60,self.start,self.end,NOW)['coverage'],0)

    def test_legacy_partial_checkpoint_kept_separate(self):
        first,last=self.b.anchors('BTC/USD',self.start,self.end,NOW)
        legacy=hashlib.sha256(f'BTC/USD:{self.start}:{self.end}:{first}:{last}'.encode()).hexdigest()
        with self.mem.tx() as db:
            db.execute('INSERT INTO gap_jobs(id,pair,start_ts,end_ts,first_digest,last_digest,cursor,last_trade,last_stamp,pages,state) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(legacy,'BTC/USD',self.start,self.end,first,last,str(self.start*NS),12,str(self.start*NS),1,'recovering'))
        j=self.run_page(self.page([T,self.start,self.end,T+3*3600],[11,12,13,14]))
        self.assertNotEqual(j['id'],legacy);self.assertEqual(j['state'],'complete')
        with self.mem.tx() as db:
            self.assertEqual(tuple(db.execute('SELECT pages,state FROM gap_jobs WHERE id=?',(legacy,)).fetchone()),(1,'recovering'))

    def test_overlap_tail_is_bounded(self):
        from market_gap import TAIL_LIMIT
        # Unique trades with one second between each, entirely inside the gap.
        stamps=[self.start+i for i in range(TAIL_LIMIT)]
        self.run_page(self.page([T],[1]))
        j=self.run_page(self.page(stamps,list(range(2,TAIL_LIMIT+2))))
        with self.mem.tx() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gap_trade_tail WHERE job=?',(j['id'],)).fetchone()[0],TAIL_LIMIT)
        with self.assertRaises(ValueError):self.run_page(self.page([T,stamps[-1],self.end],[1,TAIL_LIMIT+1,TAIL_LIMIT+2]))
        self.assertEqual(self.run_page(self.page([stamps[-1],self.end,T+3*3600],[TAIL_LIMIT+1,TAIL_LIMIT+2,TAIL_LIMIT+3]))['state'],'complete')
