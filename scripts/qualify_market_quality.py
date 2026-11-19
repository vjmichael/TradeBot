"""Actual public-only Kraken observations; not an execution experiment."""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import SYMBOLS
from market_memory import MarketMemory
from market_stream import exact_closed
from market_quality import QualityMemory


def main():
    with tempfile.TemporaryDirectory(prefix='tb-quality-') as d:
        m=MarketMemory(Path(d));q=QualityMemory(m)
        for pair in SYMBOLS:
            n=m.record_recent(pair,5,exact_closed(pair,5));assert n>24
            # Avoid crossing slot end during request, without changing timestamps.
            if int(time.time())%300>275:time.sleep(301-int(time.time())%300)
            assert q.sample(pair)=='observed'
            q=QualityMemory(MarketMemory(Path(d)));now=int(time.time());end=now//3600*3600
            a=q.profile(pair,end-3*86400,end,now,'Asia/Tokyo',40)
            assert a['rv_hours']>0 and a['latest'] and a['latest']['spread_bps']>0 and a['latest']['state']=='recent'
            assert q.sample(pair)=='paced'
            assert sum(b['rv_hours'] for b in a['bins'])==a['rv_hours']
            assert sum(b['books'] for b in a['bins'])==a['book_samples']
            print(f"RECEIPT: {pair} {n} genuine closed five-minute candles; {a['rv_hours']}/{a['expected_hours']} complete volatility hours; book spread {a['latest']['spread_bps']:.4f} bps; persisted checked book, pacing and restart verified; 40 bps per-side illustrative fee scenario only",flush=True)

if __name__=='__main__':main()
