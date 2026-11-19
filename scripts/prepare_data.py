"""Fixed public data worker; no credentials or trading commands."""
import asyncio
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_preparation import parent_guard,progress,MODES
from engine import SYMBOLS
from market_memory import MarketMemory
from market_archive import RemoteParts,public_client,import_members
from market_stream import StreamJournal,run_stream,exact_closed
from market_quality import QualityMemory
from gap_schedule import repair_due
from market_lake import HistoricalLake
from economic_memory import EconomicMemory,SOURCES
from storage_management import run as run_maintenance


def run(directory,mode,job):
    root=directory/'preparation';m=MarketMemory(directory);e=EconomicMemory(directory);q=QualityMemory(m);journal=StreamJournal(m)
    mark=lambda stage:progress(root,job,stage)
    def refresh():
        failed=False
        for pair in SYMBOLS:
            try:m.record_recent(pair,5,exact_closed(pair,5));q.sample(pair)
            except Exception:failed=True
        for source in SOURCES:
            try:e.collect(source)
            except Exception:failed=True
        return not failed
    if mode in ('backup','preview-retention','retire'):
        mark({'backup':'creating_recovery_copy','preview-retention':'reviewing_old_samples','retire':'retiring_reviewed_samples'}[mode])
        run_maintenance(directory,mode,job)
    elif mode=='prepare':
        mark('historical_archive')
        with public_client() as client:
            remote=RemoteParts(client)
            with remote.reader() as reader:import_members(m,reader)
        mark('recent_history')
        for pair in SYMBOLS:journal.repair(pair)
        mark('economic_and_market_conditions')
        if not refresh():raise RuntimeError('some sources unavailable')
        mark('organizing_history')
        end=int(time.time())//3600*3600
        for pair in SYMBOLS:HistoricalLake(m).compact(pair,60,end-1825*86400,end)
        mark('review_coverage')
    elif mode=='repair':
        mark('checking_gaps')
        for pair in SYMBOLS:journal.repair(pair)
        mark('repairing_gaps')
        outcomes=repair_due(m,journal.gap_reports())
        for pair in SYMBOLS:journal.repair(pair)
        mark('review_coverage')
        if any(r['outcome']!='complete' for r in outcomes):raise RuntimeError('gap recovery incomplete; checkpoint retained')
    elif mode=='collect':
        mark('continuous_public_data')
        async def collect():
            async def periodic():
                while True:
                    ok=await asyncio.to_thread(refresh)
                    mark('continuous_public_data' if ok else 'some_sources_unavailable')
                    await asyncio.sleep(300)
            await asyncio.gather(run_stream(m),periodic())
        asyncio.run(collect())
    else:raise ValueError('unsupported mode')


if __name__=='__main__':
    directory,mode,job,parent,fd=sys.argv[1:]
    parent_guard(int(parent));os.fstat(int(fd))
    if mode not in MODES:raise SystemExit(2)
    run(Path(directory),mode,job)
