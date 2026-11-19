"""Genuine supervised four-market collection and four measured storage samples.

Uses the real public collector, no fixtures, credentials or provider calls.
The fifteen-minute sample span is bounded qualification, not a 24-hour uptime test.
"""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_preparation import Preparation
from market_directory import MarketDirectory
from market_memory import MarketMemory
from market_stream import StreamJournal
from storage_telemetry import storage_growth
from recovery_budget import recovery_budget_status

def main():
    with tempfile.TemporaryDirectory(prefix='tb04f4-sustained-') as root:
        directory=Path(root)
        registry=MarketDirectory(directory)
        result=registry.refresh()
        registry.select('ADA/USD',True,result['version'])
        symbols=registry.active_symbols()
        assert len(symbols)==4
        controller=Preparation(directory)
        try:
            job=controller.start('collect');deadline=time.monotonic()+1140
            m=MarketMemory(directory)
            while time.monotonic()<deadline:
                current=controller.status()
                if current['state']!='running' or current['id']!=job['id']:raise RuntimeError('continuous collection did not remain owned')
                measured=storage_growth(directory,symbols)
                if measured['growth'] and measured['growth']['elapsed_seconds']>=900 and measured['sample_count']>=4:
                    rows=measured['latest']['markets']
                    if len(rows)==4 and all(r['published_candle_rows']>0 and r['latest_five_minute_observation'] is not None and r['latest_five_minute_observation']>=job['started'] for r in rows) and all(r['market_update_fresh'] for r in StreamJournal(m,symbols).status()) and measured['latest'].get('vintage_snapshots',0)>0:
                        break
                time.sleep(2)
            else:raise RuntimeError('sustained selected-market storage qualification timed out')
            controller.cancel()
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if controller.status()['state']=='cancelled':break
                time.sleep(1)
            else:raise RuntimeError('owned collection failed to stop')
            reopened=storage_growth(directory,symbols)
            assert reopened['latest']['observed_at']>=measured['latest']['observed_at'] and measured['latest']['logical_bytes']>0
            print('RECEIPT: four actual public markets, same owned collector, measured storage bytes and elapsed observation span',measured,flush=True)
            assert measured['windows'][0]['elapsed_seconds']>=900 and measured['latest']['vintage_payload_bytes']>0
            from vintage_memory import VintageMemory
            vintage=VintageMemory(directory).status()['coverage'];assert vintage['plan_created_at'] and vintage['saved']>0
            print('RECEIPT: genuine automatic vintage backfill alongside four markets',vintage,flush=True)
            print('RECEIPT: bounded historical recovery request admission',recovery_budget_status(m),flush=True)
        finally:controller.close()

if __name__=='__main__':main()
