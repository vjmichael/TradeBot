"""Genuine official public endpoint qualification; no fixture substitutes."""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from economic_memory import EconomicMemory,SOURCES,CALENDAR,MACRO,FED,SERIES,MACRO_EXT,EXT_SERIES


def main(source):
    if source not in SOURCES:raise ValueError('invalid source')
    with tempfile.TemporaryDirectory(prefix='tb04d-native-') as tmp:
        m=EconomicMemory(tmp);before=int(time.time())-1
        result=m.collect(source)
        assert result['state']=='observed'
        m=EconomicMemory(tmp);view=m.view(source);rows=view['rows']
        assert view['state']=='observed' and rows and view['snapshot_sha256']
        assert not m.view(source,as_of=before)['rows']
        assert all(r['observed_at']>=before+1 and r['evidence_id'] and r['reliability']=='official_primary' for r in rows)
        if source in (MACRO,MACRO_EXT):
            expected=SERIES if source==MACRO else EXT_SERIES
            assert set(r['series'] for r in rows)==set(expected)
            for series in expected:
                subset=[r for r in rows if r['series']==series and r['value'] is not None]
                assert len(subset)>=60 and all(r['published_at'] is None and r['vintage']=='current_at_observation' for r in subset)
                print(f'RECEIPT: {series}: {len(subset)} actual monthly values, {subset[0]["period"]} to {subset[-1]["period"]}; current vintage, not original release history',flush=True)
        if source==CALENDAR:
            assert all(r['forecast'] is None and r['actual'] is None for r in rows)
            upcoming=m.calendar();assert upcoming['total']>0
            print(f'RECEIPT: {upcoming["total"]} official calendar entries in bounded current window; no inferred forecast/actual',flush=True)
        if source==FED:assert all(r['published_at']<=r['observed_at'] for r in rows)
        saved=m.history(source)
        assert saved['total']==view['total'] and saved['distinct_records']==view['total'] and not saved['release_time_vintages_verified']
        assert not m.history(source,as_of=before)['rows']
        assert m.collect(source)['state']=='rate_limited'
        print(f'RECEIPT: {source}: {view["total"]} official observed records; digest {view["snapshot_sha256"]}; restart/as-of and request pacing verified',flush=True)

if __name__=='__main__':main(sys.argv[1])
