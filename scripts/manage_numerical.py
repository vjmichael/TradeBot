"""Developer-only immutable numerical research dataset builder and verifier."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from numerical_features import Plan,MODES
from numerical_dataset import build,verify,reproduce

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    b=sub.add_parser('build')
    b.add_argument('snapshot',type=Path);b.add_argument('destination',type=Path);b.add_argument('snapshot_sha256')
    b.add_argument('--pairs',nargs='+',required=True)
    for name in ('start','end','train-end','validation-end','horizon-hours','gap-hours','fee-bps','spread-bps','slippage-bps'):
        b.add_argument('--'+name,type=int,required=True)
    b.add_argument('--availability-mode',choices=MODES,required=True)
    v=sub.add_parser('verify');v.add_argument('directory',type=Path);v.add_argument('manifest_sha256')
    v=sub.add_parser('reproduce');v.add_argument('directory',type=Path);v.add_argument('manifest_sha256');v.add_argument('snapshot',type=Path)
    a=p.parse_args()
    if a.action=='reproduce':receipt=reproduce(a.directory,a.manifest_sha256,a.snapshot)
    elif a.action=='verify':receipt=verify(a.directory,a.manifest_sha256)
    else:
        plan=Plan(a.start,a.end,a.train_end,a.validation_end,a.horizon_hours,a.gap_hours,a.fee_bps,a.spread_bps,a.slippage_bps,a.availability_mode)
        receipt=build(a.snapshot,a.destination,a.snapshot_sha256,a.pairs,plan)
    print(json.dumps(receipt,sort_keys=True))
if __name__=='__main__':main()
