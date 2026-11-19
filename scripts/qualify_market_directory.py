"""Native public directory check; no credentials, order endpoints or worker activation."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from market_directory import MarketDirectory, DEFAULTS
from market_memory import MarketMemory
from market_stream import exact_closed, run_stream, StreamJournal
from market_archive import RemoteParts, public_client, import_members
from market_lake import HistoricalLake
from market_gap import GapBridge
from data_lifecycle import backup,restore
from economic_memory import EconomicMemory
import asyncio
import time

with tempfile.TemporaryDirectory() as root:
    registry=MarketDirectory(root)
    result=registry.refresh()
    assert result["state"]=="fresh" and result["total"]>len(DEFAULTS)
    for pair in DEFAULTS:
        found=registry.browse(pair)
        assert any(x["pair"]==pair for x in found["markets"]), pair
    market=next(x for x in registry.browse("ADA/USD")["markets"] if x["eligible_for_research"] and x["pair"]=="ADA/USD")
    registry.select(market["pair"],True,result["version"])
    reopened=MarketDirectory(root).browse()
    assert reopened["sha256"]==result["sha256"]
    assert any(x["pair"]==market["pair"] and x["selection_valid"] and x["preparation"]=="selected_for_data" for x in reopened["selected"])
    pair=market["pair"]
    active=registry.active_symbols()
    scope={pair:active[pair]}
    memory=MarketMemory(root,symbols=scope)
    with public_client() as client:
        remote=RemoteParts(client)
        with remote.reader() as reader:
            receipt=import_members(memory,reader,symbols=scope)
    assert receipt[0]["rows"]>0,receipt
    recent=exact_closed(pair,60,symbols=scope)
    memory.record_recent(pair,60,recent)
    now=int(time.time());end=now//3600*3600
    lake=HistoricalLake(memory)
    lake.compact(pair,60,end-720*3600,end)
    assert lake.query(pair,60,end-720*3600,end,now)["coverage"]>700
    observed=asyncio.run(run_stream(memory,duration=25,symbols=scope))
    assert pair in observed["acknowledged"] and pair in observed["stream_observed"] and pair in observed["rest_repaired"],observed
    print("RECEIPT: genuine selected",pair,"archive rows",receipt[0]["rows"],"recent closed",len(recent["candles"]),"stream",observed,flush=True)
    # Real trades, independent closed anchors, bounded one-page attempt.
    # A checkpoint is not complete gap recovery.
    with tempfile.TemporaryDirectory() as gap_root:
        gap_memory=MarketMemory(gap_root,symbols=scope)
        tail=recent["candles"][-3:]
        assert tail[2][0]-tail[0][0]==7200000
        gap_memory.record_recent(pair,60,recent|{"candles":[tail[0],tail[2]]})
        start=tail[1][0]//1000
        end_gap=tail[2][0]//1000
        gap=GapBridge(gap_memory).run(pair,start,end_gap,max_pages=1)
        assert gap["pages"]==1 and gap["state"] in ("recovering","complete","ready"),gap
        print("RECEIPT: genuine selected gap trade page",pair,gap["state"],gap["pages"],flush=True)
    registry.select(market["pair"],False,result["version"])
    assert len(MarketDirectory(root).browse()["selected"])==len(DEFAULTS)
    assert pair in MarketDirectory(root).retained_symbols()
    print("PASS: genuine Kraken AssetPairs, USD markets:",result["total"],"selected/reopened/removed:",market["pair"],"snapshot SHA256:",result["sha256"])
