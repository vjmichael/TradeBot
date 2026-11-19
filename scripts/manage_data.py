"""Developer-only data snapshot maintenance; no account credentials or trading state."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_lifecycle import backup,restore,coverage
from market_memory import MarketMemory
from data_retention import retire_books
from training_handoff import export as export_training_input,verify as verify_training_input


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='mode',required=True)
    b=sub.add_parser('backup');b.add_argument('directory',type=Path);b.add_argument('destination',type=Path)
    r=sub.add_parser('restore');r.add_argument('snapshot',type=Path);r.add_argument('destination',type=Path);r.add_argument('manifest_sha256')
    c=sub.add_parser('coverage');c.add_argument('directory',type=Path)
    q=sub.add_parser('retire-books');q.add_argument('directory',type=Path);q.add_argument('snapshot',type=Path);q.add_argument('manifest_sha256');q.add_argument('--apply',action='store_true')
    x=sub.add_parser('export-training-input');x.add_argument('snapshot',type=Path);x.add_argument('destination',type=Path);x.add_argument('manifest_sha256');x.add_argument('start',type=int);x.add_argument('end',type=int);x.add_argument('pairs',nargs='+')
    x=sub.add_parser('verify-training-input');x.add_argument('directory',type=Path);x.add_argument('manifest_sha256')
    a=p.parse_args()
    if a.mode=='backup':result=backup(a.directory,a.destination)
    elif a.mode=='restore':result=restore(a.snapshot,a.destination,a.manifest_sha256)
    elif a.mode=='retire-books':result=retire_books(a.directory,a.snapshot,a.manifest_sha256,apply=a.apply)
    elif a.mode=='export-training-input':result=export_training_input(a.snapshot,a.destination,a.manifest_sha256,a.pairs,a.start,a.end)
    elif a.mode=='verify-training-input':result=verify_training_input(a.directory,a.manifest_sha256)
    else:result=coverage(MarketMemory(a.directory))
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
