"""Developer-only matched research replay; no execution or model fitting."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from strategy_comparison import compare,verify_comparison,read_pinned,MAX_MANIFEST

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshot',type=Path);p.add_argument('dataset',type=Path);p.add_argument('dataset_sha256')
    p.add_argument('models',type=Path);p.add_argument('receipt_index',type=Path);p.add_argument('receipt_index_sha256')
    p.add_argument('destination',type=Path);p.add_argument('--capital-cents',type=int,required=True)
    p.add_argument('--verify-sha256')
    a=p.parse_args();index=json.loads(read_pinned(a.receipt_index,a.receipt_index_sha256,MAX_MANIFEST))
    if index.get('dataset_sha256')!=a.dataset_sha256:raise ValueError('receipt index dataset mismatch')
    args=(a.snapshot,a.dataset,a.dataset_sha256,a.models,index['evaluations'])
    if a.verify_sha256:result=verify_comparison(a.destination,a.verify_sha256,*args,a.capital_cents)
    else:result=compare(*args,a.destination,a.capital_cents)
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
