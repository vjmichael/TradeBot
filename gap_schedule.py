"""Bounded fair attempts across reported gaps; preserve original bridge validation."""
import time
from market_gap import GapBridge,MAX_SPAN
from market_memory import MemoryUnavailable

RETRY=300
MAX_TRACKED=100000

def repair_due(memory,reports,*,now=None,bridge=None,max_pages=64):
    now=int(time.time()) if now is None else now
    if type(max_pages) is not int or not 1<=max_pages<=64:raise ValueError("invalid recovery page budget")
    bridge=GapBridge(memory) if bridge is None else bridge
    with memory.tx() as db:
        db.execute('CREATE TABLE IF NOT EXISTS gap_attempts(pair TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,attempted INTEGER NOT NULL,outcome TEXT NOT NULL,PRIMARY KEY(pair,start,end))')
    result=[]
    for report in reports:
        pair=report['pair'];rs=[r for r in report['ranges'] if r['reason']=='outside_rest_window' and 0<r['end']-r['start']<=MAX_SPAN]
        with memory.tx() as db:
            known={(r['start'],r['end']):dict(r) for r in db.execute('SELECT * FROM gap_attempts WHERE pair=?',(pair,))}
            def rank(r):
                old=known.get((r['start'],r['end']));return (old['attempted'] if old else -1,-(r['end']-r['start']),r['start'])
            due=[r for r in rs if (r['start'],r['end']) not in known or (known[(r['start'],r['end'])]['outcome']!='complete' and now-known[(r['start'],r['end'])]['attempted']>=RETRY)]
            if not due:continue
            r=min(due,key=rank);start,end=r['start'],r['end']
            if (start,end) not in known and db.execute('SELECT count(*) FROM gap_attempts').fetchone()[0]>=MAX_TRACKED:raise MemoryUnavailable('gap attempt capacity reached')
            db.execute("INSERT INTO gap_attempts VALUES(?,?,?,?,'running') ON CONFLICT(pair,start,end) DO UPDATE SET attempted=excluded.attempted,outcome='running'",(pair,start,end,now))
        j=None
        try:
            j=bridge.run(pair,start,end,max_pages=max_pages)
            outcome='complete' if j['state']=='complete' else 'checkpointed'
        except (MemoryUnavailable,ValueError,OSError):outcome='unavailable'
        with memory.tx() as db:db.execute('UPDATE gap_attempts SET outcome=? WHERE pair=? AND start=? AND end=?',(outcome,pair,start,end))
        result.append({'pair':pair,'start':start,'end':end,'outcome':outcome,'pages':j.get('pages') if j else None,'cursor':j.get('cursor') if j else None})
    return result


def recovery_progress(memory):
    """Bounded status of attempts, not published coverage or guessed completion."""
    with memory.tx() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gap_attempts'").fetchone():return []
        attempts=db.execute('SELECT * FROM gap_attempts ORDER BY attempted DESC,pair,start LIMIT 30').fetchall()
        jobs=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gap_jobs'").fetchone()
        out=[]
        for a in attempts:
            row=db.execute('SELECT pages,state,last_stamp FROM gap_jobs WHERE pair=? AND start_ts=? AND end_ts=? ORDER BY rowid DESC LIMIT 1',(a['pair'],a['start'],a['end'])).fetchone() if jobs else None
            out.append({'pair':a['pair'],'start':a['start'],'end':a['end'],'outcome':a['outcome'],'attempted':a['attempted'],'pages':row['pages'] if row else 0,'checkpoint_time':int(row['last_stamp'])//10**9 if row and int(row['last_stamp']) else None,'bridge_state':row['state'] if row else None})
        return out
