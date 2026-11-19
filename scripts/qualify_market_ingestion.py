"""Native bounded public-only historical and streaming acceptance; no orders or paid API."""
import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import SYMBOLS
from market_archive import RemoteParts, public_client, import_members, RELEASE_END, MAX_RANGE
from market_memory import MarketMemory, session_profile
from market_stream import exact_closed, run_stream, StreamJournal
from market_gap import GapBridge


def archive(memory):
    with public_client() as c:
        remote=RemoteParts(c)
        with remote.reader() as reader: receipts=import_members(memory,reader)
    assert len(receipts)==3 and remote.received <= MAX_RANGE
    for r in receipts:
        assert r['validation']=='https_range_member_crc32' and r['publisher_full_sha256'] is None
        assert r['rows']>1000 and r['end']<=RELEASE_END-3600
        as_of=int(time.time());start=RELEASE_END-1825*86400
        view=memory.query(r['pair'],60,start,RELEASE_END,as_of)
        if not view['coverage'] or view['conflicts']: raise RuntimeError('archive coverage not qualified')
        profile=session_profile(view,'UTC')
        assert len(profile['bins'])==168 and sum(x['samples'] for x in profile['bins'])==view['coverage']
        print(f"RECEIPT: {r['pair']} historical archive {r['rows']} hourly rows; last {r['end']}; five-year requested window {view['coverage']}/{view['expected']} observed; {view['missing']} missing; member CRC and CSV SHA-256; not full publisher SHA",flush=True)
    print(f'RECEIPT: fixed-origin selective archive transfer {remote.received} bytes',flush=True)
    journal=StreamJournal(memory)
    for pair in SYMBOLS:
        report=journal.repair(pair)
        print(f"RECEIPT: {pair} archive-to-recent reconciliation: {report['coverage']} observed hours; {report['missing']} missing; {report['outside_rest_window']} outside recent REST; {report['conflicts']} conflicts",flush=True)
    assert len(journal.gap_reports())==3


async def live(memory):
    for attempt in range(2):
        receipt=await run_stream(memory,duration=25)
        for key in ('acknowledged','stream_observed','rest_repaired'):
            if set(receipt[key])!=set(SYMBOLS):raise RuntimeError(f'incomplete native stream proof: {key}')
        print(f'RECEIPT: public Kraken WS v2 connection {attempt+1}: all three subscriptions, actual indicative updates, closed REST reconciliation',flush=True)
    j=StreamJournal(memory)
    assert all(not r['connected'] and r['last_repair'] for r in j.status())


def bridge(memory):
    # Deliberately omit one recent, actual SOL candle; do not substitute a
    # synthetic sample. Independently recover it and compare exact values.
    pair='SOL/USD';snapshot=exact_closed(pair);source=snapshot['candles'];end_ts=snapshot['observed_at']//3600*3600
    candidates=[r for r in source if end_ts-5*3600<=r[0]//1000<end_ts-3600];target=next((b for a,b,c in zip(candidates,candidates[1:],candidates[2:]) if b[0]-a[0]==c[0]-b[0]==3600*1000),None)
    if target is None:raise RuntimeError('no contiguous real bridge anchors')
    start=target[0]//1000;end=start+3600
    memory.record_recent(pair,60,snapshot|{'candles':[r for r in source if r[0]!=target[0]]})
    assert not memory.query(pair,60,start,end,int(time.time()))['coverage']
    j=GapBridge(memory).run(pair,start,end,max_pages=256)
    if j['state']!='complete':raise RuntimeError('actual bridge page budget insufficient')
    result=memory.query(pair,60,start,end,int(time.time()))
    if result['coverage']!=1 or result['conflicts']:raise RuntimeError('actual trade bridge missing/conflicting result')
    row=result['rows'][0]
    if [row[k] for k in ('o','h','l','c','v','trades')]!=target[1:]:raise RuntimeError('trade reconstruction differs from independent reference candle')
    print(f'RECEIPT: actual SOL/USD missing hourly candle reconstructed from {j["pages"]} cursor pages and matches independent REST OHLCVT exactly',flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('archive','stream','bridge'));mode=p.parse_args().mode
    with tempfile.TemporaryDirectory(prefix='tb04b-native-') as t:
        m=MarketMemory(Path(t))
        if mode=='archive':archive(m)
        elif mode=='stream':asyncio.run(live(m))
        else:bridge(m)

if __name__=='__main__':main()
