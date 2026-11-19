"""Genuine two provider-dated observations from ALFRED."""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vintage_memory import VintageMemory,cutoff,PACE
def main():
    with tempfile.TemporaryDirectory(prefix='tb04-vintage-') as root:
        directory=Path(root)/'data';directory.mkdir(mode=0o700)
        memory=VintageMemory(directory)
        for vintage in ('2020-05-01','2020-06-01'):
            result=memory.collect('UNRATE',vintage)
            assert result['state']=='observed',result
            row=VintageMemory(directory).view('UNRATE',vintage,replay_at=cutoff(vintage))
            assert row['state']=='observed' and row['rows'] and row['vintage']==vintage
            assert not memory.view('UNRATE',vintage,replay_at=cutoff(vintage)-1)['rows']
            assert not memory.view('UNRATE',vintage,retrieved_by=row['retrieved_at']-1)['rows']
            print('RECEIPT: genuine ALFRED vintage',vintage,'rows',len(row['rows']),'sha256',row['sha256'],flush=True)
            time.sleep(PACE)
        assert memory.status()['snapshots']==2
        from vintage_memory import ensure_plan,coverage
        ensure_plan(memory.memory,int(time.time()))
        prior=coverage(memory.memory);previous={s['series']:s['saved'] for s in prior['series']}
        batch=memory.collect_batch();assert batch['observed']==6,batch
        saved=memory.status()['coverage'];assert saved['saved']==prior['saved']+6 and saved['missing']==prior['missing']-6
        assert all(s['saved']>=previous[s['series']]+1 for s in saved['series'])
        print('RECEIPT: six genuine frozen monthly targets across three ALFRED series',saved,flush=True)
        # Reopen between bursts and verify no duplication of real snapshots.
        for _ in range(2):
            time.sleep(PACE)
            memory=VintageMemory(directory);prior=coverage(memory.memory)
            batch=memory.collect_batch();assert batch['observed']==6,batch
            assert coverage(memory.memory)['saved']==prior['saved']+6
        from vintage_memory import supplemental_targets
        time.sleep(PACE)
        rate=supplemental_targets(saved['anchor_month'])[0]
        result=memory.collect(*rate);assert result['state']=='observed',result
        assert memory.view(*rate)['state']=='observed'
        saved=coverage(memory.memory);assert saved['supplemental']['saved']==1
        print('RECEIPT: three genuine core bursts across reopen, additional historical effective federal funds rate vintage',saved,flush=True)
        from market_memory import MarketMemory
        from data_lifecycle import backup,restore
        MarketMemory(directory)
        before=int(time.time());original=coverage(memory.memory,as_of=before,detail=True)
        receipt=backup(directory,Path(root)/'copy')
        restore(Path(root)/'copy',Path(root)/'restored',receipt['manifest_sha256'])
        restored=VintageMemory(Path(root)/'restored')
        assert coverage(restored.memory,as_of=before,detail=True)==original
        assert restored.view('UNRATE','2020-05-01')['sha256']==memory.view('UNRATE','2020-05-01')['sha256']
        print('RECEIPT: verified data-only restore of frozen vintage plan, request ledger and actual raw evidence',receipt,flush=True)
if __name__=='__main__':main()
