"""Native end-to-end public-data preparation, collector custody and fresh restore."""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_preparation import Preparation
from data_lifecycle import backup,restore,coverage
from market_memory import MarketMemory
from market_lake import HistoricalLake
from market_stream import StreamJournal
from economic_memory import EconomicMemory,SOURCES
from engine import SYMBOLS
from market_gap import GapBridge,MAX_SPAN
from gap_schedule import repair_due,recovery_progress


def wait(controller, timeout, predicate):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=controller.status()
        if predicate(state):return state
        time.sleep(1)
    raise RuntimeError('native data job did not reach required state')


def main():
    with tempfile.TemporaryDirectory(prefix='tb04f-native-') as tmp:
        root=Path(tmp);directory=root/'data';directory.mkdir(mode=0o700)
        controller=Preparation(directory)
        try:
            controller.start('prepare');j=wait(controller,480,lambda r:r['state']!='running')
            if j['state']!='complete':raise RuntimeError('genuine preparation failed at '+str(j['stage']))
            m=MarketMemory(directory);lake=HistoricalLake(m);e=EconomicMemory(directory)
            v=coverage(m);assert not v['training_ready'] and all(r['coverage']>40000 and r['conflicts']==0 for r in v['markets'])
            print('RECEIPT: actual graphical preparation worker; '+str(v['markets']),flush=True)
            controller.start('collect');deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                if controller.status()['state']!='running':raise RuntimeError('continuous worker exited')
                if all(r['market_update_fresh'] for r in StreamJournal(m).status()):break
                time.sleep(1)
            else:raise RuntimeError('no genuine streaming updates for all markets')
            controller.cancel();wait(controller,20,lambda r:r['state']=='cancelled')
            print('RECEIPT: genuine continuous worker collected all three market updates and terminated under owner control',flush=True)
            # Exercise the actual archive-to-REST gap, not a deliberately
            # omitted recent candle or a fixture. Bound this native run to two
            # four-page batches: progress is evidence, not full completion.
            pair='SOL/USD';report=next(r for r in StreamJournal(m).gap_reports() if r['pair']==pair)
            candidates=[r for r in report['ranges'] if r['reason']=='outside_rest_window' and 48*3600<=r['end']-r['start']<=MAX_SPAN]
            if not candidates:raise RuntimeError('no eligible actual multi-day gap; review pinned archive and scope')
            target=max(candidates,key=lambda r:r['end']-r['start']);start,end=target['start'],target['end']
            initial=m.query(pair,60,start,end,int(time.time()));assert initial['coverage']==0 and initial['conflicts']==0
            first=repair_due(m,[{'pair':pair,'ranges':[target]}],max_pages=4)[0]
            assert first['outcome']=='checkpointed' and first['pages']==4,first
            reopened=MarketMemory(directory);bridge=GapBridge(reopened)
            second=bridge.run(pair,start,end,max_pages=4)
            assert second['pages']==8 and int(second['cursor'])>int(first['cursor']) and second['state']=='recovering',second
            with reopened.tx() as db:
                pages=db.execute('SELECT old_cursor,new_cursor FROM gap_pages WHERE job=? ORDER BY page',(second['id'],)).fetchall()
                assert len(pages)==8 and pages[4]['old_cursor']==first['cursor'] and all(a['new_cursor']==b['old_cursor'] for a,b in zip(pages,pages[1:]))
            assert recovery_progress(reopened)[0]['pages']==8 and reopened.query(pair,60,start,end,int(time.time()))['coverage']==0
            print(f'RECEIPT: actual SOL archive-to-REST gap {((end-start)//3600)} hours; eight durable public trade pages across two reopened batches, cursor advanced; no premature publication; remaining recovery unfinished',flush=True)
            receipt=backup(directory,root/'snapshot');restore(root/'snapshot',root/'restored',receipt['manifest_sha256'])
            restored=MarketMemory(root/'restored');restored_lake=HistoricalLake(restored);restored_e=EconomicMemory(root/'restored')
            now=int(time.time());end=now//3600*3600
            for pair in SYMBOLS:
                a=lake.query(pair,60,end-720*3600,end,now);b=restored_lake.query(pair,60,end-720*3600,end,now)
                assert a==b and a['coverage']>700
            for source in SOURCES:
                a=e.view(source,as_of=now);b=restored_e.view(source,as_of=now)
                assert a==b and a['rows']
            assert all(not r['connected'] for r in StreamJournal(restored).status())
            print('RECEIPT: verified data-only backup and fresh restore; actual observations, Parquet and economic evidence preserved; '+str(receipt),flush=True)
        finally:controller.close()


if __name__=='__main__':main()
