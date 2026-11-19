"""Developer-only history materialization, bounded query and storage maintenance."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import SYMBOLS
from market_memory import MarketMemory
from market_lake import HistoricalLake

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    modes=parser.add_subparsers(dest='mode',required=True)
    compact=modes.add_parser('compact',help='publish bounded immutable history partitions')
    query=modes.add_parser('query-history',help='bounded point-in-time read (no arbitrary SQL)')
    for cmd in (compact,query):
        cmd.add_argument('pair',choices=SYMBOLS);cmd.add_argument('interval',type=int);cmd.add_argument('start',type=int);cmd.add_argument('end',type=int)
    query.add_argument('--as-of',type=int,default=None)
    modes.add_parser('storage-status')
    policy=modes.add_parser('storage-policy');policy.add_argument('quota_bytes',type=int);policy.add_argument('reserve_bytes',type=int)
    pin=modes.add_parser('pin-partition');pin.add_argument('id');pin.add_argument('label');pin.add_argument('--release',action='store_true')
    prune=modes.add_parser('prune-retired');prune.add_argument('before',type=int);prune.add_argument('--apply',action='store_true');prune.add_argument('--limit',type=int,default=32)
    orphan=modes.add_parser('recover-orphans');orphan.add_argument('--apply',action='store_true');orphan.add_argument('--limit',type=int,default=32)
    a=parser.parse_args(); m=MarketMemory(a.data_dir)
    lake=HistoricalLake(m)
    if a.mode=='compact': result=lake.compact(a.pair,a.interval,a.start,a.end)
    elif a.mode=='query-history': result=lake.query(a.pair,a.interval,a.start,a.end,int(time.time()) if a.as_of is None else a.as_of)
    elif a.mode=='storage-status': result=lake.status()
    elif a.mode=='storage-policy': result=lake.policy(a.quota_bytes,a.reserve_bytes)
    elif a.mode=='pin-partition': lake.pin(a.id,a.label,release=a.release);result={'id':a.id,'released':a.release}
    elif a.mode=='prune-retired':result=lake.prune(before=a.before,dry_run=not a.apply,limit=a.limit)
    else:result=lake.recover_orphans(dry_run=not a.apply,limit=a.limit)
    print(json.dumps(result));return

if __name__=='__main__':main()
