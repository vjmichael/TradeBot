"""Local-first native scale qualification; generated lab history, never market evidence."""
import json
import tempfile
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import duckdb
from market_lake import HistoricalLake
from market_memory import MarketMemory, sha256_file, session_profile


def main():
    assert duckdb.__version__=='1.4.4'
    # Synthetic training-sized data in an isolated temporary directory. It is
    # deliberately exercise-ingested as non-fixture input, NEVER actual data.
    start=1609459200;count=5*365*24;end=start+count*3600;as_of=end+3600
    with tempfile.TemporaryDirectory(prefix='tb04c-synthetic-') as tmp:
        p=Path(tmp)/'XBTUSD_60.csv'
        with p.open('w') as f:
            for i in range(count):
                op=10000+i%200
                f.write(f'{start+i*3600},{op},{op+3},{op-2},{op+1},{1+i%30}.12345678,42\n')
        m=MarketMemory(tmp);lake=HistoricalLake(m)
        begin=time.monotonic();m.import_archive(p,'BTC/USD',60,sha256_file(p),now=as_of);ingest=time.monotonic()-begin
        begin=time.monotonic();baseline=m.query('BTC/USD',60,start,end,as_of);journal=time.monotonic()-begin
        begin=time.monotonic();manifest=lake.compact('BTC/USD',60,start,end);build=time.monotonic()-begin
        begin=time.monotonic();view=lake.query('BTC/USD',60,start,end,as_of);read=time.monotonic()-begin
        metrics=view.pop('storage');assert view==baseline and view['coverage']==count and not view['conflicts']
        assert metrics['parquet_partitions']==len(manifest) and metrics['sqlite_partitions']==0
        assert sum(x['samples'] for x in session_profile(view,'America/Phoenix')['bins'])==count
        restarted=HistoricalLake(MarketMemory(tmp));again=restarted.query('BTC/USD',60,start,end,as_of);again.pop('storage');assert again==view
        report={'fixture_only':True,'rows':count,'partitions':len(manifest),'parquet_bytes':lake.status()['lake_bytes'],'market_memory_bytes':lake.status()['market_memory_bytes'],'import_seconds':round(ingest,3),'materialize_seconds':round(build,3),'journal_query_seconds':round(journal,3),'lake_query_seconds':round(read,3),'row_equivalence':True,'restart_equivalence':True}
        print('RECEIPT: TB-04C native synthetic scale '+json.dumps(report),flush=True)
if __name__=='__main__':main()
