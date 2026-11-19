"""Native public-data research-dataset qualification, no model or live intent."""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_lifecycle import backup
from market_memory import MarketMemory,MemoryUnavailable
from market_lake import HistoricalLake
from market_stream import exact_closed
from economic_memory import EconomicMemory
from vintage_memory import VintageMemory,current_anchor,targets,PACE
from numerical_features import Plan,STEP
from numerical_dataset import build,verify,reproduce,model_rows
from market_directory import DEFAULTS

def main(after=None):
    with tempfile.TemporaryDirectory(prefix='tb06-numerical-') as tmp:
        root=Path(tmp);data=root/'data';data.mkdir(mode=0o700)
        m=MarketMemory(data);EconomicMemory(data);lake=HistoricalLake(m)
        end=int(time.time())//STEP*STEP-2*STEP;start=end-360*STEP
        for pair in DEFAULTS:
            m.record_recent(pair,60,exact_closed(pair))
            lake.compact(pair,60,start,end)
            assert lake.query(pair,60,start,end,int(time.time()))['coverage']==360
        # Predetermined previous-month vintages; never substitute latest revisions.
        v=VintageMemory(data)
        for pair in targets(current_anchor(int(time.time())))[:3]:
            receipt=v.collect(*pair)
            assert receipt['state']=='observed',receipt
            time.sleep(PACE)
        snap=root/'snapshot';receipt=backup(data,snap)
        p=Plan(start,end,start+180*STEP,start+270*STEP,6,2,40,10,5,'provider-retrospective')
        out=root/'research';manifest=build(snap,out,receipt['manifest_sha256'],list(DEFAULTS),p);sha=manifest['manifest_sha256']
        assert verify(out,sha)==manifest and reproduce(out,sha,snap)['reproduced']
        for pair in DEFAULTS:
            record=next(a for a in manifest['markets'] if a['pair']==pair)
            assert record['macro_missing']['UNRATE']<sum(record['counts'].values()),record['macro_missing']
            train=model_rows(out,sha,pair,'train','numerical_only','&-net_return')
            ablation=model_rows(out,sha,pair,'train','no_sessions','&-net_return')
            assert train['decisions']==ablation['decisions'] and train['y']==ablation['y']
            print('RECEIPT: genuine public numerical input, frozen split and matched session ablation',pair,record['counts'],record['macro_missing'],flush=True)
        try:
            build(snap,root/'strict',receipt['manifest_sha256'],list(DEFAULTS),Plan(**(p.as_dict()|{'availability_mode':'local-observed'})))
        except MemoryUnavailable:assert not (root/'strict').exists()
        else:raise RuntimeError('retrospective candles incorrectly accepted as locally known')
        assert not manifest['training_ready'] and not manifest['paper_ready']
        print('RECEIPT: exact genuine retrospective research dataset reproduces; no training or readiness authorization',sha,flush=True)
        if after is not None:after(out,sha)
if __name__=='__main__':main()
