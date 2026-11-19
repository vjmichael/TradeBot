"""Developer-only data snapshot maintenance; no account credentials or trading state."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_lifecycle import backup,restore,coverage
from market_memory import MarketMemory


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='mode',required=True)
    b=sub.add_parser('backup');b.add_argument('directory',type=Path);b.add_argument('destination',type=Path)
    r=sub.add_parser('restore');r.add_argument('snapshot',type=Path);r.add_argument('destination',type=Path);r.add_argument('manifest_sha256')
    c=sub.add_parser('coverage');c.add_argument('directory',type=Path)
    a=p.parse_args()
    if a.mode=='backup':result=backup(a.directory,a.destination)
    elif a.mode=='restore':result=restore(a.snapshot,a.destination,a.manifest_sha256)
    else:result=coverage(MarketMemory(a.directory))
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
