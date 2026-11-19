"""Genuine two provider-dated observations from ALFRED."""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vintage_memory import VintageMemory,cutoff,PACE
def main():
    with tempfile.TemporaryDirectory(prefix='tb04-vintage-') as root:
        memory=VintageMemory(root)
        for vintage in ('2020-05-01','2020-06-01'):
            result=memory.collect('UNRATE',vintage)
            assert result['state']=='observed',result
            row=VintageMemory(root).view('UNRATE',vintage,replay_at=cutoff(vintage))
            assert row['state']=='observed' and row['rows'] and row['vintage']==vintage
            assert not memory.view('UNRATE',vintage,replay_at=cutoff(vintage)-1)['rows']
            assert not memory.view('UNRATE',vintage,retrieved_by=row['retrieved_at']-1)['rows']
            print('RECEIPT: genuine ALFRED vintage',vintage,'rows',len(row['rows']),'sha256',row['sha256'],flush=True)
            time.sleep(PACE)
        assert memory.status()['snapshots']==2
if __name__=='__main__':main()
