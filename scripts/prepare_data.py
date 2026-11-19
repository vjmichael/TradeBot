"""Fixed public data worker; no credentials or trading commands."""
import asyncio
import os
import sqlite3
import sys
import time
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_preparation import parent_guard,progress,MODES
from market_directory import MarketDirectory
from market_memory import MarketMemory
from market_archive import RemoteParts,public_client,import_members,RELEASE
from market_stream import StreamJournal,run_stream,exact_closed
from market_quality import QualityMemory
from recovery_budget import recover_batch
from storage_telemetry import sample_storage
from market_lake import HistoricalLake
from economic_memory import EconomicMemory,SOURCES
from storage_management import run as run_maintenance


def run(directory,mode,job):
    root=directory/'preparation';registry=MarketDirectory(directory)
    maintenance=mode in ('backup','preview-retention','retire')
    symbols=registry.retained_symbols() if maintenance else registry.active_symbols(refresh_stale=True)
    m=MarketMemory(directory,symbols=symbols);e=EconomicMemory(directory);q=QualityMemory(m);journal=StreamJournal(m,symbols)
    scope_lock=threading.Lock()
    def check_scope():
        with scope_lock:
            if registry.active_symbols(refresh_stale=True)!=symbols:
                raise RuntimeError('market selection changed; restart data preparation')
    mark=lambda stage:progress(root,job,stage)
    def refresh():
        failed=False
        for pair in symbols:
            check_scope()
            try:m.record_recent(pair,5,exact_closed(pair,5,symbols=symbols));q.sample(pair)
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
        with m.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS market_archive_status(pair TEXT PRIMARY KEY,checked INTEGER NOT NULL,state TEXT NOT NULL,release TEXT NOT NULL)')
            done={r['pair'] for r in db.execute('SELECT pair,state FROM market_archive_status WHERE release=?',(RELEASE,)) if r['state'] in ('imported','not_in_pinned_archive')}
        pending={p:v for p,v in symbols.items() if p not in done}
        if pending:
            with public_client() as client:
                remote=RemoteParts(client)
                with remote.reader() as reader:
                    for pair,mapping in pending.items():
                        check_scope()
                        archives=import_members(m,reader,symbols={pair:mapping})
                        with m.tx() as db:
                            db.executemany('INSERT INTO market_archive_status VALUES(?,?,?,?) ON CONFLICT(pair) DO UPDATE SET checked=excluded.checked,state=excluded.state,release=excluded.release',[(r['pair'],int(time.time()),r.get('state','imported'),RELEASE) for r in archives])
        check_scope()
        mark('recent_history')
        failed=False
        for pair in symbols:
            check_scope()
            try:journal.repair(pair)
            except Exception:failed=True
        mark('economic_and_market_conditions')
        refreshed=refresh()
        mark('organizing_history')
        end=int(time.time())//3600*3600
        for pair in symbols:
            check_scope()
            HistoricalLake(m).compact(pair,60,end-1825*86400,end)
        mark('review_coverage')
        if failed or not refreshed:raise RuntimeError('some sources unavailable; saved observations retained')
    elif mode=='repair':
        check_scope()
        mark('checking_gaps')
        failed=False
        for pair in symbols:
            check_scope()
            try:journal.repair(pair)
            except Exception:failed=True
        mark('repairing_gaps')
        outcomes=recover_batch(m,journal.gap_reports(),scope_check=check_scope,max_pages=64,total_pages=192)
        for pair in symbols:
            check_scope()
            try:journal.repair(pair)
            except Exception:failed=True
        mark('review_coverage')
        if failed or any(r['outcome']!='complete' for r in outcomes):raise RuntimeError('gap recovery incomplete; checkpoint retained')
    elif mode=='collect':
        mark('continuous_public_data')
        async def collect():
            async def periodic():
                while True:
                    await asyncio.to_thread(check_scope)
                    ok=await asyncio.to_thread(refresh)
                    mark('continuous_public_data' if ok else 'some_sources_unavailable')
                    await asyncio.sleep(300)
            async def scope_guard():
                while True:
                    await asyncio.sleep(30)
                    await asyncio.to_thread(check_scope)
            async def telemetry():
                # Separate from refresh/recovery: a transient WAL or staging
                # file race must never stop collection or cost a whole sample.
                await asyncio.sleep(20)
                while True:
                    try:saved=await asyncio.to_thread(sample_storage,m,symbols)
                    except (OSError,RuntimeError,ValueError,sqlite3.Error):saved=False
                    await asyncio.sleep(300 if saved else 15)
            async def historical_recovery():
                # Let the initial stream reconciliation publish its gap report.
                await asyncio.sleep(90)
                while True:
                    await asyncio.to_thread(check_scope)
                    await asyncio.to_thread(recover_batch,m,journal.gap_reports(),scope_check=check_scope)
                    await asyncio.sleep(120)
            async with asyncio.TaskGroup() as group:
                group.create_task(run_stream(m,symbols=symbols))
                group.create_task(periodic())
                group.create_task(scope_guard())
                group.create_task(historical_recovery())
                group.create_task(telemetry())
        asyncio.run(collect())
    else:raise ValueError('unsupported mode')


if __name__=='__main__':
    directory,mode,job,parent,fd=sys.argv[1:]
    parent_guard(int(parent));os.fstat(int(fd))
    if mode not in MODES:raise SystemExit(2)
    run(Path(directory),mode,job)
