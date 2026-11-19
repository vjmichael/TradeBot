"""Native upstream FreqAI fit qualification on genuine frozen research data.

Run in the pinned image, with a readonly source tree and network disabled.
No strategy, order, exchange credentials or production model artifacts.
"""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from numerical_models import fit,verify_model,evaluate,verify_evaluation,COHORTS,MemoryUnavailable
from numerical_dataset import verify
from market_memory import sha256_file
from training_handoff import canonical

def main():
    assert (os.getuid(),os.getgid())==(1000,1000), 'unexpected native worker identity'
    directory=Path(sys.argv[1]);sha=sys.argv[2];dest=Path(sys.argv[3])
    data=verify(directory,sha)
    evaluations={}
    for asset in data['markets']:
        pair=asset['pair'];base=pair.replace('/','_')
        evaluations[pair]={}
        for cohort in COHORTS:
            model=dest/(base+'-'+cohort)
            receipt=fit(directory,sha,model,pair,cohort);digest=receipt['manifest_sha256']
            assert verify_model(model,digest)==receipt
            result=evaluate(directory,sha,model,digest,dest/(base+'-'+cohort+'-test'))
            assert result['metrics']['rows']==asset['counts']['test'] and not result['training_ready']
            assert verify_evaluation(dest/(base+'-'+cohort+'-test'),result['manifest_sha256'],model,digest,directory,sha)==result
            try:evaluate(directory,sha,model,digest,dest/(base+'-'+cohort+'-test'))
            except MemoryUnavailable:pass
            else:raise RuntimeError('evaluation overwrite unexpectedly accepted')
            evaluations[pair][cohort]={'model_sha256':digest,'evaluation_sha256':result['manifest_sha256']}
            print('RECEIPT: upstream FreqAI fit, validation-only calibration, frozen native text reload and test evaluation',pair,cohort,digest,result['manifest_sha256'],result['metrics'],flush=True)
    with (dest/'evaluation-index.json').open('x') as h:h.write(canonical({'dataset_sha256':sha,'evaluations':evaluations}))
    print('RECEIPT: all native research models evaluated; no production promotion',sha256_file(dest/'evaluation-index.json'),flush=True)
if __name__=='__main__':main()
