"""TB-04F4: bounded, read-only evidence and missing-hour reconciliation.

The 1825-day target is not a listing-date assertion. Missing observations,
conflicting prices, and known recovery attempts are distinct. No synthetic
candles, unproved no-trade claims, readiness promotion, or execution authority.
"""
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from market_memory import MAX_TIME, MemoryUnavailable, sha256_file
from market_gap import MAX_SPAN
from economic_memory import SOURCES
from market_quality import parse_depth, measure, SLOT

STEP=3600
HOURS=43800
PAGE=50
MAX_ARCHIVES=64
MAX_VERIFY_BYTES=256*1024**2
MAX_ATTEMPTS=10000
HEX=re.compile(r'[0-9a-f]{64}')


def _has(db,table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone())


def _archive_assets(db,memory,pair,as_of):
    sources=db.execute("""SELECT id,sha256,state,observed_at,published_at
        FROM sources WHERE pair=? AND interval=60 AND kind='archive'
        AND observed_at<=? AND (published_at IS NULL OR published_at<=?)
        ORDER BY id LIMIT ?""",(pair,as_of,as_of,MAX_ARCHIVES+1)).fetchall()
    if len(sources)>MAX_ARCHIVES:raise MemoryUnavailable('archive verification inventory exceeds bound')
    states=Counter(); modes=Counter();checked=0;size=0
    for source in sources:
        ident=source['id'];digest=source['sha256']
        if not isinstance(ident,str) or not HEX.fullmatch(ident) or not isinstance(digest,str) or not HEX.fullmatch(digest):
            raise MemoryUnavailable('invalid retained archive identity')
        path=memory.root/(ident+'.csv')
        if path.is_symlink() or not path.is_file():
            states['missing_or_unsafe']+=1
        else:
            n=path.stat().st_size;size+=n
            if checked+n>MAX_VERIFY_BYTES:
                states['verification_budget_reached']+=1
            else:
                checked+=n
                states['verified' if sha256_file(path)==digest else 'checksum_mismatch']+=1
        if source['state']!='complete':
            modes['not_published']+=1;continue
        modes['published']+=1
        if _has(db,'archive_receipts'):
            rows=db.execute("SELECT validation FROM archive_receipts WHERE source_id=? ORDER BY validation",(ident,)).fetchall()
            for row in rows:modes[row[0]]+=1
            if not rows:modes['no_publisher_receipt']+=1
        else:modes['no_publisher_receipt']+=1
    return {'assets':len(sources),'bytes':size,'verified_bytes_scanned':checked,
            'integrity':dict(states),'provenance':dict(modes),
            'scope':'retained hourly archive CSV assets; database and partition integrity qualified separately'}


def historical_report(memory,economic,pair,*,as_of=None,offset=0,report_id=None,clock=time.time):
    now=int(clock())
    as_of=now if as_of is None else as_of
    if (type(as_of) is not int or not HOURS*STEP+STEP<as_of<=now<MAX_TIME
            or type(offset) is not int or not 0<=offset<=HOURS
            or (offset and (not isinstance(report_id,str) or not HEX.fullmatch(report_id)))
            or (report_id is not None and (not isinstance(report_id,str) or not HEX.fullmatch(report_id)))):
        raise ValueError('invalid report window or page')
    symbols=memory.symbols
    if pair not in symbols:raise ValueError('unknown market')
    end=as_of//STEP*STEP;start=end-HOURS*STEP
    with closing(sqlite3.connect(memory.db,timeout=10)) as db:
        db.row_factory=sqlite3.Row
        # One SQLite read cut for all market counts, source and recovery evidence.
        db.execute('BEGIN')
        base="""FROM candles c JOIN sources s ON s.id=c.source_id
            WHERE c.pair=? AND c.interval=60 AND c.ts>=? AND c.ts<?
            AND c.available_at<=? AND s.published_at<=?
            AND s.kind!='fixture' AND s.state='complete'"""
        args=(pair,start,end,as_of,as_of)
        rows=db.execute("SELECT c.ts,COUNT(DISTINCT c.digest) AS versions,COUNT(DISTINCT c.trades) AS counts "+base+" GROUP BY c.ts ORDER BY c.ts LIMIT ?",args+(HOURS+1,)).fetchall()
        if len(rows)>HOURS or any(r['ts']%STEP for r in rows):raise MemoryUnavailable('unexpected historical observation identity')
        by_time={r['ts']:r for r in rows}
        clean={t for t,r in by_time.items() if r['versions']==1}
        anchors={t for t in clean if by_time[t]['counts']<=1}
        bounds=db.execute("SELECT MIN(CASE WHEN s.kind='archive' THEN c.ts END),MAX(CASE WHEN s.kind='archive' THEN c.ts END),MIN(c.ts),MAX(c.ts) "+base,args).fetchone()
        archive_first,archive_last,first,last=tuple(bounds)
        # Historical recovery is read-only; an attempt is not an observation.
        attempts={}
        if _has(db,'gap_attempts'):
            found=db.execute("""SELECT start,end,attempted,outcome FROM gap_attempts
                WHERE pair=? AND start>=? AND end<=? AND attempted<=?
                ORDER BY start,end LIMIT ?""",(pair,start,end,as_of,MAX_ATTEMPTS+1)).fetchall()
            if len(found)>MAX_ATTEMPTS:raise MemoryUnavailable('gap attempt inventory exceeds report bound')
            attempts={(r['start'],r['end']):dict(r) for r in found}
        jobs={}
        if _has(db,'gap_jobs'):
            found=db.execute("""SELECT id,start_ts,end_ts,state,pages,observed_at
                FROM gap_jobs WHERE pair=? AND start_ts>=? AND end_ts<=?
                AND observed_at<=? ORDER BY rowid LIMIT ?""",(pair,start,end,as_of,MAX_ATTEMPTS+1)).fetchall()
            if len(found)>MAX_ATTEMPTS:raise MemoryUnavailable('gap job inventory exceeds report bound')
            for j in found:jobs[(j['start_ts'],j['end_ts'])]=dict(j)
        collector=db.execute('SELECT last_success,state FROM collector WHERE pair=? AND interval=60 AND last_attempt<=?',(pair,as_of)).fetchone()
        source_counts=dict(db.execute("""SELECT kind,COUNT(*) FROM sources WHERE pair=? AND interval=60
            AND kind!='fixture' AND state='complete' AND published_at<=? GROUP BY kind""",(pair,as_of)))
        assets=_archive_assets(db,memory,pair,as_of)
        # Latest saved cost evidence is explicitly a sample, not historical fills.
        book=None;cost=None
        if _has(db,'quality_books'):
            book=db.execute("SELECT observed,digest,body,source_digest FROM quality_books WHERE pair=? AND published<=? ORDER BY observed DESC LIMIT 1",(pair,as_of)).fetchone()
            if book:
                if hashlib.sha256(book['body'].encode()).hexdigest()!=book['digest']:
                    raise MemoryUnavailable('saved order book integrity failure')
                cost=measure(parse_depth(pair,{'error':[],'result':{symbols[pair][1]:json.loads(book['body'])}},book['observed'],symbols))
                cost.update(observed_at=book['observed'],source_digest=book['source_digest'],
                            state='recent_sample' if 0<=as_of-book['observed']<=2*SLOT else 'old_sample',
                            fee_bps_per_side=None)
        db.rollback()
    def reason(t):
        row=by_time.get(t)
        if row is not None:
            if row['versions']>1:return 'conflicting_observations'
            return 'conflicting_trade_counts' if row['counts']>1 else None
        if first is None:return 'no_observed_history'
        if t<first:return 'before_first_observation'
        if t>last:return 'after_last_observation'
        if archive_first is not None and archive_first<=t<=archive_last:return 'inside_observed_archive_span'
        # This is a target window (720 hours), not a claim about an actual REST response.
        if t>=end-720*STEP:return 'recent_target_gap'
        return 'historical_gap'
    ranges=[];counts=Counter()
    for t in range(start,end,STEP):
        label=reason(t)
        if label is None:continue
        counts[label]+=1
        if ranges and ranges[-1]['reason']==label and ranges[-1]['end']==t:ranges[-1]['end']=t+STEP
        else:ranges.append({'start':t,'end':t+STEP,'reason':label})
    # Keep a contiguous missing run intact for recovery even if the
    # reporting categories split at archive/recent boundaries.
    recovery_bounds={};i=0
    while i<len(ranges):
        if ranges[i]['reason'].startswith('conflicting_'):
            i+=1;continue
        j=i+1
        while j<len(ranges) and not ranges[j]['reason'].startswith('conflicting_') and ranges[j-1]['end']==ranges[j]['start']:
            j+=1
        bound=(ranges[i]['start'],ranges[j-1]['end'])
        for k in range(i,j):recovery_bounds[k]=bound
        i=j
    for i,item in enumerate(ranges):
        label=item['reason'];a,b=recovery_bounds.get(i,(item['start'],item['end']))
        job=jobs.get((a,b));attempt=attempts.get((a,b))
        item['hours']=(item['end']-item['start'])//STEP
        if label.startswith('conflicting_'):
            recovery='independent_source_review'
        elif job and job['state']=='complete':
            # A completed bridge can still leave a missing hour. Never assert
            # verified no-trade absence merely from a missing candle.
            recovery='completed_attempt_still_missing'
        elif job and job['state']=='rejected':
            recovery='rejected_requires_review'
        elif job and job['state'] in ('recovering','ready'):
            recovery='checkpoint_saved'
        elif a-STEP not in anchors or b not in anchors:
            recovery='independent_anchors_missing'
        elif b-a>MAX_SPAN:
            recovery='exceeds_current_recovery_span'
        elif attempt and attempt['outcome']=='unavailable':
            recovery='attempt_unavailable'
        else:
            recovery='bounded_recovery_possible'
        item.update(recovery=recovery,recovery_start=a,recovery_end=b,pages=job['pages'] if job else 0,
                    last_attempt=attempt['attempted'] if attempt else None)
    evidence=[]
    for source in SOURCES:
        view=economic.view(source,as_of=as_of,limit=1)
        saved=economic.history(source,as_of=as_of,limit=1)
        evidence.append({k:view[k] for k in ('source','source_url','state','last_success','snapshot_sha256','total')}|{'retained_revisions':saved['total'],'retained_distinct_records':saved['distinct_records'],'first_observed':saved['first_observed'],'release_time_vintages_verified':False})
    conflicts=counts['conflicting_observations'];trade_conflicts=counts['conflicting_trade_counts']
    coverage=len(clean);missing=HOURS-coverage
    if sum(n for label,n in counts.items() if label!='conflicting_trade_counts')!=missing:
        raise MemoryUnavailable('historical classification does not reconcile')
    from vintage_memory import coverage as vintage_coverage
    vintages=vintage_coverage(economic,as_of=as_of)
    report={'pair':pair,'as_of':as_of,'start':start,'end':end,'expected':HOURS,'coverage':coverage,
            'missing':missing,'conflicts':conflicts,'trade_count_conflicts':trade_conflicts,
            'first_observed':first,'last_observed':last,'archive_first':archive_first,'archive_last':archive_last,
            'last_recent':collector['last_success'] if collector else None,
            'collector_state':collector['state'] if collector else 'not_observed',
            'classification_hours':dict(sorted(counts.items())),'source_counts':source_counts,'archive_retention':assets,
            'latest_book_observed':book['observed'] if book else None,'cost_sample':cost,'economic_sources':evidence,'vintage_coverage':vintages,
            'historical_state':'observations_incomplete' if missing or trade_conflicts else 'integrity_review_required',
            'training_ready':False,'paper_ready':False,
            'range_count':len(ranges),'no_trade_hours_verified':0}
    # A changed report must restart pagination rather than splice different cuts.
    fingerprint=hashlib.sha256(json.dumps([report,ranges],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    if report_id is not None and fingerprint!=report_id:raise ValueError('report changed; refresh the first page')
    if offset>len(ranges):raise ValueError('report page is out of range')
    return report|{'report_id':fingerprint,'ranges':ranges[offset:offset+PAGE],
                   'next_offset':offset+PAGE if offset+PAGE<len(ranges) else None}
