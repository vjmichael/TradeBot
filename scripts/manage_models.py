"""Offline operator research model fit/verify/evaluate; not a trading launcher."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from numerical_models import COHORTS,fit,verify_model,evaluate,verify_evaluation
def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    q=sub.add_parser('fit');q.add_argument('dataset',type=Path);q.add_argument('dataset_sha256');q.add_argument('destination',type=Path);q.add_argument('pair');q.add_argument('cohort',choices=COHORTS)
    q=sub.add_parser('verify');q.add_argument('model',type=Path);q.add_argument('model_sha256')
    q=sub.add_parser('evaluate');q.add_argument('dataset',type=Path);q.add_argument('dataset_sha256');q.add_argument('model',type=Path);q.add_argument('model_sha256');q.add_argument('destination',type=Path)
    q=sub.add_parser('verify-evaluation');q.add_argument('evaluation',type=Path);q.add_argument('evaluation_sha256');q.add_argument('model',type=Path);q.add_argument('model_sha256');q.add_argument('dataset',type=Path);q.add_argument('dataset_sha256')
    a=p.parse_args()
    if a.action=='fit':r=fit(a.dataset,a.dataset_sha256,a.destination,a.pair,a.cohort)
    elif a.action=='verify':r=verify_model(a.model,a.model_sha256)
    elif a.action=='verify-evaluation':r=verify_evaluation(a.evaluation,a.evaluation_sha256,a.model,a.model_sha256,a.dataset,a.dataset_sha256)
    else:r=evaluate(a.dataset,a.dataset_sha256,a.model,a.model_sha256,a.destination)
    print(json.dumps(r,sort_keys=True))
if __name__=='__main__':main()
