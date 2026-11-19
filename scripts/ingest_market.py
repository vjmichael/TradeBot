"""Developer-only TB-04 staged intake; no trading, GUI job control is deferred."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_memory import MarketMemory, export_parquet
from engine import SYMBOLS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    modes=parser.add_subparsers(dest='mode',required=True)
    archive=modes.add_parser('archive',help='verified, locally staged official Kraken CSV')
    archive.add_argument('path',type=Path);archive.add_argument('pair',choices=SYMBOLS);archive.add_argument('interval',type=int);archive.add_argument('sha256');archive.add_argument('--max-batches',type=int)
    poll=modes.add_parser('poll',help='public OHLC REST; closed recent candles only')
    poll.add_argument('--loop',action='store_true');poll.add_argument('--seconds',type=int,default=60)
    export=modes.add_parser('export');export.add_argument('pair',choices=SYMBOLS);export.add_argument('interval',type=int);export.add_argument('start',type=int);export.add_argument('end',type=int)
    a=parser.parse_args(); m=MarketMemory(a.data_dir)
    if a.mode=='archive': print(json.dumps(m.import_archive(a.path,a.pair,a.interval,a.sha256,max_batches=a.max_batches)))
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
