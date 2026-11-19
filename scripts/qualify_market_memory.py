"""Isolated public-data/Parquet native qualification; no keys, paid API, or trading."""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import duckdb
from market_memory import MarketMemory, export_parquet, session_profile, sha256_file
from engine import SYMBOLS
from market_lake import HistoricalLake


def main():
    if duckdb.__version__ != '1.4.4':
        raise RuntimeError('unexpected DuckDB version')
    with tempfile.TemporaryDirectory(prefix='tb04-public-') as tmp:
        for pair in SYMBOLS:
            m = MarketMemory(Path(tmp))
            count = m.poll(pair)
            if count < 2:
                raise RuntimeError('no native intake evidence')
            # Reopen the durable catalog; do not rely on transient responses.
            m = MarketMemory(Path(tmp))
            as_of = int(time.time())
            end = as_of // 3600 * 3600
            view = m.query(pair,60,end-31*86400,end,as_of)
            if view['coverage'] != count or view['conflicts'] or not all(r['source_ids'] for r in view['rows']):
                raise RuntimeError('persisted observation coverage mismatch')
            profile = session_profile(view, 'Asia/Tokyo')
            if sum(b['samples'] for b in profile['bins']) != count or len(profile['bins']) != 168:
                raise RuntimeError('session profile count mismatch')
            export = export_parquet(m,pair,60,view['start'],view['end'],as_of)
            if sha256_file(m.root/export['path']) != export['sha256'] or export['row_count'] != count:
                raise RuntimeError('native Parquet export mismatch')
            lake=HistoricalLake(m)
            manifest=lake.compact(pair,60,view['start'],view['end'])
            compacted=lake.query(pair,60,view['start'],view['end'],as_of)
            storage=compacted.pop('storage')
            if compacted!=view or not storage['parquet_partitions'] or not any(x['state']=='published' for x in manifest):
                raise RuntimeError('partitioned native observation mismatch')
            print(f'RECEIPT: {pair} verified {storage["parquet_partitions"]} active Parquet partitions against exact journal observations',flush=True)
            print(f'RECEIPT: {pair} preserved {count} actual public closed candles; 168 session bins; checksum-verified Parquet round-trip',flush=True)
            print('Coverage limited to recent observation; not historical readiness.',flush=True)

if __name__=='__main__': main()
