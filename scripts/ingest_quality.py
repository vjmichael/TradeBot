"""Explicit developer intake; bounded public observations, no autonomous worker."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import SYMBOLS
from market_memory import MarketMemory
from market_quality import QualityMemory
from market_stream import exact_closed


def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True,type=Path);p.add_argument('mode',choices=('book','candles','all'));a=p.parse_args()
    m=MarketMemory(a.data_dir);q=QualityMemory(m)
    for pair in SYMBOLS:
        if a.mode in ('candles','all'): print(pair,'five-minute candles inserted',m.record_recent(pair,5,exact_closed(pair,5)),flush=True)
        if a.mode in ('book','all'): print(pair,'book',q.sample(pair),flush=True)

if __name__=='__main__':main()
