"""Developer-only TB-04 intake; no trading or user-facing CLI requirement."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_memory import MarketMemory, export_parquet, ARCHIVE_INTERVALS
from engine import SYMBOLS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    modes=parser.add_subparsers(dest='mode',required=True)
    archive=modes.add_parser('archive',help='verified, locally staged official Kraken CSV')
    archive.add_argument('path',type=Path);archive.add_argument('pair',choices=SYMBOLS);archive.add_argument('interval',type=int);archive.add_argument('sha256');archive.add_argument('--max-batches',type=int)
    bootstrap=modes.add_parser('bootstrap',help='official split archive; select three supported pairs')
    bootstrap.add_argument('--interval',type=int,choices=ARCHIVE_INTERVALS,action='append');bootstrap.add_argument('--parts-dir',type=Path);bootstrap.add_argument('--download-full',action='store_true',help='explicit multi-GB download; requires private parts-dir')
    poll=modes.add_parser('poll',help='public OHLC REST; closed recent candles only')
    poll.add_argument('--loop',action='store_true');poll.add_argument('--seconds',type=int,default=60)
    modes.add_parser('stream',help='public WebSocket with REST reconciliation, no trading')
    modes.add_parser('stream-status',help='inspect current stream freshness')
    gap=modes.add_parser('bridge-gap',help='bounded, resumable public trade-history recovery between known candles');gap.add_argument('pair',choices=SYMBOLS);gap.add_argument('start',type=int);gap.add_argument('end',type=int);gap.add_argument('--max-pages',type=int,default=64)
    export=modes.add_parser('export');export.add_argument('pair',choices=SYMBOLS);export.add_argument('interval',type=int);export.add_argument('start',type=int);export.add_argument('end',type=int)
    a=parser.parse_args(); m=MarketMemory(a.data_dir)
    if a.mode=='archive': print(json.dumps(m.import_archive(a.path,a.pair,a.interval,a.sha256,max_batches=a.max_batches)))
    elif a.mode=='bootstrap':
        from market_archive import import_members, verified_reader, RemoteParts, download_full_parts, public_client, exclusive_job
        if a.download_full and a.parts_dir is None:parser.error('--download-full requires --parts-dir')
        with exclusive_job(m.root,'archive-bootstrap'):
            with public_client() as c:
                if a.parts_dir:
                    if a.download_full:download_full_parts(a.parts_dir,c)
                    with verified_reader(a.parts_dir) as reader:print(json.dumps(import_members(m,reader,intervals=a.interval or (60,),verified=True)))
                else:
                    remote=RemoteParts(c)
                    with remote.reader() as reader:print(json.dumps({'receipts':import_members(m,reader,intervals=a.interval or (60,)),'downloaded_bytes':remote.received}))
    elif a.mode=='stream':
        from market_stream import run_stream
        asyncio.run(run_stream(m))
    elif a.mode=='stream-status':
        from market_stream import StreamJournal
        j=StreamJournal(m);print(json.dumps({'health':j.status(),'gaps':j.gap_reports()}))
    elif a.mode=='bridge-gap':
        from market_gap import GapBridge
        print(json.dumps(GapBridge(m).run(a.pair,a.start,a.end,max_pages=a.max_pages)))
    elif a.mode=='export': print(json.dumps(export_parquet(m,a.pair,a.interval,a.start,a.end,int(time.time()))))
    else:
        if a.seconds<60 or a.seconds>3600: parser.error('poll interval must be 60–3600 seconds')
        while True:
            bad=False
            for pair in SYMBOLS:
                try: print(json.dumps({'pair':pair,'new_rows':m.poll(pair)}),flush=True)
                except Exception as exc:
                    bad=True;print(json.dumps({'pair':pair,'status':'unavailable','error_type':type(exc).__name__}),flush=True)
            if not a.loop:
                if bad: raise SystemExit(1)
                return
            time.sleep(a.seconds)

if __name__=='__main__': main()
