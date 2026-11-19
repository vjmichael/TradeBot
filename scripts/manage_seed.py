"""Developer/operator release-seed workspace; no customer terminal requirement.

Initialize on a durable private disk, collect using the existing opt-in service
against the returned workspace, and capture immutable versions. No implicit
network acquisition, release publication, or live trading.
"""
import argparse
import json
import signal
import threading
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from release_seed import init,capture,status,verify,materialize,restore_bundle
from seed_population import run_epoch,population_status

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    sub=p.add_subparsers(dest='action',required=True)
    i=sub.add_parser('init');i.add_argument('--snapshot',type=Path);i.add_argument('--snapshot-sha256');sub.add_parser('status')
    c=sub.add_parser('capture');c.add_argument('version')
    c=sub.add_parser('populate');c.add_argument('version');c.add_argument('--seconds',type=int,required=True)
    sub.add_parser('population-status')
    for mode in ('verify','snapshot','restore','bundle'):
        q=sub.add_parser(mode);q.add_argument('version');q.add_argument('manifest_sha256')
        if mode!='verify':q.add_argument('destination',type=Path)
    b=sub.add_parser('restore-bundle');b.add_argument('bundle',type=Path);b.add_argument('manifest_sha256');b.add_argument('destination',type=Path)
    a=p.parse_args()
    if a.action=='init':result=init(a.root,snapshot=a.snapshot,snapshot_sha256=a.snapshot_sha256)
    elif a.action=='status':result=status(a.root)
    elif a.action=='capture':result=capture(a.root,a.version)
    elif a.action=='population-status':result=population_status(a.root)
    elif a.action=='populate':
        stop=threading.Event()
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
        result=run_epoch(a.root,a.version,a.seconds,stop=stop.is_set)
    elif a.action=='verify':result=verify(a.root,a.version,a.manifest_sha256)
    elif a.action=='restore-bundle':result=restore_bundle(a.bundle,a.manifest_sha256,a.destination)
    else:result=materialize(a.root,a.version,a.manifest_sha256,a.destination,restore_data=a.action=='restore',bundle=a.action=='bundle')
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
