"""TB-04A: bounded, public-only candle intake and point-in-time memory.

SQLite is the durable ingress/checkpoint and revision index. A separately
qualified DuckDB exporter publishes immutable Parquet batches (no deletion of
staging data yet). No model, account credentials or execution dependency.
"""
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import SYMBOLS, INTERVALS, candles

ARCHIVE_DOC = 'https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data'
REST_DOC = 'https://api.kraken.com/0/public/OHLC'
ARCHIVE_INTERVALS = (1, 5, 15, 60, 240, 720, 1440)
BATCH = 2048
MAX_CSV_BYTES = 4 * 1024**3
MAX_QUERY_ROWS = 45000  # ~5 years of hourly bins; paginate longer/finer requests
MAX_TIME = 4102444800  # 2100-01-01 UTC
ZONES = ('UTC', 'America/Phoenix', 'America/New_York', 'Europe/London', 'Asia/Tokyo', 'Asia/Hong_Kong')


class MemoryUnavailable(RuntimeError):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def identity(pair, interval, symbols=SYMBOLS):
    if pair not in symbols or type(interval) is not int or interval not in ARCHIVE_INTERVALS:
        raise ValueError('unsupported Kraken spot pair or interval')
    return interval * 60


def decimal_text(value):
    # Preserve normalized decimal source values, without unbounded exponents.
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('invalid number')
    raw = str(value)
    if len(raw) > 64 or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?', raw):
        raise ValueError('invalid number')
    try:
        d = Decimal(raw)
    except InvalidOperation:
        raise ValueError('invalid number') from None
    if not d.is_finite() or abs(d) > Decimal('1e18') or (d and abs(d) < Decimal('1e-24')):
        raise ValueError('out of bounds number')
    s = format(d, 'f')
    return ('0' if d == 0 else s.rstrip('0').rstrip('.') if '.' in s else s)


def normalize(row, interval, observed_at):
    """UTC-second candle start, exact decimal strings, optional trade count."""
    step = interval * 60
    if len(row) != 7 or type(row[0]) not in (str, int) or not re.fullmatch(r'\d{1,10}', str(row[0])):
        raise ValueError('invalid candle shape or timestamp')
    stamp = int(row[0])
    if not 0 < stamp < MAX_TIME or stamp % step or stamp + step > observed_at:
        raise ValueError('unaligned, future or forming candle')
    values = tuple(decimal_text(v) for v in row[1:6])
    o, h, l, c, v = map(Decimal, values)
    if not (0 < l <= min(o, c) <= max(o, c) <= h and v >= 0):
        raise ValueError('invalid OHLCV')
    trades = row[6]
    if trades is not None:
        if type(trades) not in (int, str) or not re.fullmatch(r'\d{1,10}', str(trades)):
            raise ValueError('invalid trades count')
        trades = int(trades)
    digest = hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()
    return (stamp, *values, trades, digest)


def archive_identity(path, pair, interval, symbols=SYMBOLS):
    identity(pair, interval, symbols)
    base, alias = symbols[pair]
    if Path(path).name not in {f'{code}_{interval}.csv' for code in (base, alias, pair.replace('/', ''))}:
        raise ValueError('archive filename does not match pair and timeframe')


def csv_batches(path, interval, observed_at, *, offset=0, previous=0):
    """Bounded validated batches; reject reordered or malformed source rows."""
    with Path(path).open('r', encoding='ascii', newline='') as f:
        batch = []
        # Kraken OHLCVT extracts are headerless. CSV rows are physical lines.
        for n, line in enumerate(iter(lambda: f.readline(513), ''), 1):
            if len(line) > 512:
                raise ValueError('oversize CSV row')
            if n <= offset:
                continue
            row = normalize(next(csv.reader([line], strict=True)), interval, observed_at)
            if row[0] <= previous:
                raise ValueError('non-increasing source time')
            previous = row[0]
            batch.append(row)
            if len(batch) == BATCH:
                yield n, batch
                batch = []
        if batch:
            yield n, batch


class MarketMemory:
    def __init__(self, directory, *, symbols=None):
        self.directory = Path(directory)
        self._symbols = dict(symbols) if symbols is not None else None
        self.root = Path(directory) / 'market-memory'
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or (os.name == 'posix' and self.root.stat().st_mode & 0o077):
            raise PermissionError('market memory directory must be private')
        self.db = self.root / 'catalog.sqlite3'
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('PRAGMA journal_mode=WAL')
        with self.tx() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise MemoryUnavailable('unsupported market memory schema')
            schema='''CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('archive','rest','fixture')), pair TEXT NOT NULL, interval INTEGER NOT NULL, locator TEXT NOT NULL, sha256 TEXT, observed_at INTEGER NOT NULL, cursor INTEGER NOT NULL DEFAULT 0, last_ts INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,published_at INTEGER);
CREATE TABLE IF NOT EXISTS candles(pair TEXT NOT NULL, interval INTEGER NOT NULL, ts INTEGER NOT NULL, digest TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id), available_at INTEGER NOT NULL, o TEXT NOT NULL,h TEXT NOT NULL,l TEXT NOT NULL,c TEXT NOT NULL,v TEXT NOT NULL,trades INTEGER, PRIMARY KEY(pair,interval,ts,digest,source_id));
CREATE INDEX IF NOT EXISTS candle_window ON candles(pair,interval,ts,available_at);
CREATE TABLE IF NOT EXISTS collector(pair TEXT NOT NULL, interval INTEGER NOT NULL, last_attempt INTEGER NOT NULL, last_success INTEGER, state TEXT NOT NULL, PRIMARY KEY(pair,interval));
CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,sha256 TEXT NOT NULL,row_count INTEGER NOT NULL,created_at INTEGER NOT NULL,pair TEXT NOT NULL,interval INTEGER NOT NULL,start_ts INTEGER NOT NULL,end_ts INTEGER NOT NULL,as_of INTEGER NOT NULL);
PRAGMA user_version=1;'''
            for statement in schema.split(';'):
                if statement.strip(): db.execute(statement)

    @property
    def symbols(self):
        if self._symbols is not None:
            return self._symbols
        from market_directory import MarketDirectory
        path = self.directory / 'market-directory'
        return MarketDirectory(self.directory).retained_symbols() if path.exists() else dict(SYMBOLS)

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.db, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _insert(self, db, source, pair, interval, observed_at, rows):
        # Distinguish fixture evidence even when timestamps/prices happen to match.
        kind = db.execute('SELECT kind FROM sources WHERE id=?', (source,)).fetchone()[0]
        inserted = 0
        for stamp, o, h, l, c, v, trades, digest in rows:
            # Same price/volume with a different trade count is evidence of
            # disagreement, not a duplicate. Keep it for explicit review.
            if kind == 'rest' and db.execute('''SELECT 1 FROM candles c JOIN sources s ON s.id=c.source_id
                WHERE c.pair=? AND c.interval=? AND c.ts=? AND c.digest=? AND c.trades IS ? AND s.kind='rest' LIMIT 1''', (pair, interval, stamp, digest, trades)).fetchone():
                continue
            inserted += db.execute('INSERT OR IGNORE INTO candles VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (pair, interval, stamp, digest, source, observed_at, o,h,l,c,v,trades)).rowcount
        return inserted

    def import_archive(self, path, pair, interval, sha256, *, fixture=False, now=None, max_batches=None, provenance=None):
        """Developer/operator-staged official CSV, not an automated downloader.

        Verify checksum; bind filename + source identity; copy into private
        immutable staging before ingestion. Partial source never appears in normal
        queries. Resume committed batches only. Fixture data never enters real views.
        """
        path = Path(path)
        archive_identity(path, pair, interval, self.symbols)
        if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
            raise ValueError('invalid batch limit')
        if not re.fullmatch(r'[0-9a-f]{64}', sha256) or not path.is_file() or path.stat().st_size > MAX_CSV_BYTES:
            raise ValueError('invalid archive or expected checksum')
        if sha256_file(path) != sha256:
            raise ValueError('archive checksum mismatch')
        kind = 'fixture' if fixture else 'archive'
        if provenance is not None:
            if not isinstance(provenance,dict) or provenance.get('validation') not in ({'fixture'} if fixture else {'publisher_full_sha256','https_range_member_crc32'}) or provenance.get('csv_sha256')!=sha256 or provenance.get('pair')!=pair or provenance.get('interval')!=interval or len(json.dumps(provenance))>32768:
                raise ValueError('invalid archive provenance')
        def publish_receipt(db,entry):
            if provenance is None:return
            receipt=provenance|{'source_id':entry['id'],'rows':entry['cursor'],'end':entry['last_ts']}
            db.execute('CREATE TABLE IF NOT EXISTS archive_receipts(source_id TEXT,validation TEXT,metadata TEXT NOT NULL,PRIMARY KEY(source_id,validation))')
            db.execute('INSERT OR IGNORE INTO archive_receipts VALUES(?,?,?)',(entry['id'],receipt['validation'],json.dumps(receipt,sort_keys=True)))
        source = hashlib.sha256(f'{kind}:{pair}:{interval}:{sha256}'.encode()).hexdigest()
        staging = self.root / f'{source}.csv'
        clock_override = now
        now = int(time.time()) if now is None else now
        if type(now) is not int or not 0 < now < MAX_TIME: raise ValueError('invalid observed time')
        with self.tx() as db:
            entry = db.execute('SELECT * FROM sources WHERE id=?', (source,)).fetchone()
            if entry and entry['state'] == 'complete':
                publish_receipt(db,entry)
                return dict(entry)
            if entry and entry['state'] != 'importing':
                raise MemoryUnavailable('archive rejected; use independently verified replacement')
            if not staging.exists():
                temp = staging.with_suffix('.partial')
                temp.unlink(missing_ok=True)
                with path.open('rb') as src, temp.open('xb') as dst:
                    total = 0
                    for block in iter(lambda: src.read(1024*1024), b''):
                        total += len(block)
                        if total > MAX_CSV_BYTES:
                            raise ValueError('archive exceeds staging limit')
                        dst.write(block)
                    dst.flush(); os.fsync(dst.fileno())
                if sha256_file(temp) != sha256:
                    temp.unlink(missing_ok=True)
                    raise ValueError('archive changed during staging')
                os.chmod(temp, 0o600); os.replace(temp, staging)
                fd = os.open(self.root, os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
            elif sha256_file(staging) != sha256:
                raise MemoryUnavailable('staged archive integrity failure')
            if not entry:
                db.execute('INSERT INTO sources(id,kind,pair,interval,locator,sha256,observed_at,state) VALUES(?,?,?,?,?,?,?,?)', (source,kind,pair,interval,ARCHIVE_DOC if not fixture else 'synthetic_fixture',sha256,now,'importing'))
                entry = db.execute('SELECT * FROM sources WHERE id=?', (source,)).fetchone()
        offset = entry['cursor']; previous = entry['last_ts']; observed = entry['observed_at']
        try:
            for index, (end, rows) in enumerate(csv_batches(staging, interval, observed, offset=offset, previous=previous), 1):
                with self.tx() as db:
                    current = db.execute('SELECT cursor,state FROM sources WHERE id=?', (source,)).fetchone()
                    if current['cursor'] != offset or current['state'] != 'importing':
                        raise MemoryUnavailable('concurrent import; retry from checkpoint')
                    self._insert(db, source, pair, interval, observed, rows)
                    db.execute('UPDATE sources SET cursor=?,last_ts=? WHERE id=?', (end, rows[-1][0], source))
                offset = end
                if max_batches is not None and index >= max_batches:
                    break
            else:
                if not offset: raise ValueError('empty archive')
                if sha256_file(staging) != sha256: raise ValueError('archive integrity failure')
                with self.tx() as db:
                    db.execute('UPDATE sources SET state=\'complete\',published_at=? WHERE id=? AND cursor=? AND state=\'importing\'', (max(int(time.time()) if clock_override is None else clock_override, observed),source, offset))
                    entry=db.execute('SELECT * FROM sources WHERE id=?',(source,)).fetchone()
                    if entry['state']!='complete':raise MemoryUnavailable('source publication failed')
                    publish_receipt(db,entry)
        except (ValueError, UnicodeError, csv.Error):
            with self.tx() as db:
                db.execute('UPDATE sources SET state=\'invalid\' WHERE id=? AND state=\'importing\' AND cursor=?', (source, offset))
            raise
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute('SELECT * FROM sources WHERE id=?', (source,)).fetchone())

    def record_recent(self, pair, interval, snapshot, *, now=None):
        step = identity(pair, interval, self.symbols)
        observed = snapshot.get('observed_at')
        if (snapshot.get('exchange'), snapshot.get('product'), snapshot.get('pair'),snapshot.get('interval_minutes')) != ('kraken','spot',pair,interval) or type(observed) is not int or not 0 < observed < MAX_TIME:
            raise ValueError('invalid market identity')
        published = int(time.time()) if now is None else now
        if type(published) is not int or not observed <= published < MAX_TIME:
            raise ValueError('observation cannot be published before acquisition')
        raw = snapshot.get('candles')
        if not isinstance(raw,list) or not 1 <= len(raw) <= 720:
            raise ValueError('invalid observed candle count')
        rows = []
        for row in raw:
            if not isinstance(row,list) or len(row) not in (6, 7) or type(row[0]) is not int or row[0] % 1000:
                raise ValueError('invalid observed candle')
            rows.append(normalize([row[0]//1000,*row[1:6],row[6] if len(row)==7 else None],interval,observed))
        if any(a[0]>=b[0] for a,b in zip(rows,rows[1:])) or observed-rows[-1][0]>step*2:
            raise ValueError('stale or reordered observation')
        key = hashlib.sha256(json.dumps([pair,interval,observed,rows],separators=(',',':')).encode()).hexdigest()
        with self.tx() as db:
            db.execute('INSERT OR IGNORE INTO sources(id,kind,pair,interval,locator,observed_at,cursor,last_ts,state,published_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(key,'rest',pair,interval,REST_DOC,observed,len(rows),rows[-1][0],'complete',published))
            count=self._insert(db,key,pair,interval,observed,rows)
            db.execute('INSERT INTO collector VALUES(?,?,?,?,?) ON CONFLICT(pair,interval) DO UPDATE SET last_attempt=MAX(last_attempt,excluded.last_attempt),last_success=MAX(COALESCE(last_success,0),excluded.last_success),state=CASE WHEN excluded.last_attempt>=last_attempt THEN excluded.state ELSE state END',(pair,interval,observed,observed,'observed'))
        return count

    def poll(self, pair, interval=60, *, fetch=candles):
        identity(pair, interval, self.symbols)
        if interval not in INTERVALS: raise ValueError('unsupported REST interval')
        try:
            result = candles(pair, interval, symbols=self.symbols) if fetch is candles else fetch(pair, interval)
            return self.record_recent(pair,interval,result)
        except Exception:
            with self.tx() as db:
                db.execute('INSERT INTO collector VALUES(?,?,?,NULL,?) ON CONFLICT(pair,interval) DO UPDATE SET last_attempt=excluded.last_attempt,state=excluded.state',(pair,interval,int(time.time()),'unavailable'))
            raise

    def query(self, pair, interval, start, end, as_of, *, fixture=False):
        step = identity(pair, interval, self.symbols)
        if any(type(x) is not int for x in (start,end,as_of)) or not 0 <= start < end <= MAX_TIME or not 0 < as_of < MAX_TIME or start % step or end % step or (end-start)//step > MAX_QUERY_ROWS:
            raise ValueError('invalid or unbounded query window')
        end = max(start, min(end, as_of//step*step))
        mode = "s.kind='fixture'" if fixture else "s.kind!='fixture'"
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory = sqlite3.Row
            # Conflicting values are withheld, never silently overwritten.
            rows=db.execute('''SELECT c.ts, c.digest, c.o,c.h,c.l,c.c,c.v, CASE WHEN COUNT(DISTINCT c.trades)<=1 THEN MAX(c.trades) ELSE NULL END AS trades, MIN(MAX(c.available_at,s.published_at)) AS available_at, GROUP_CONCAT(c.source_id) AS source_ids
              FROM candles c JOIN sources s ON s.id=c.source_id WHERE c.pair=? AND c.interval=? AND c.ts>=? AND c.ts<? AND c.available_at<=? AND s.state='complete' AND s.published_at<=? AND '''+mode+''' GROUP BY c.ts,c.digest ORDER BY c.ts,c.digest''',(pair,interval,start,end,as_of,as_of))
            output=[]; conflicts=0; pending=None; conflicting=False
            for index,row in enumerate(rows):
                if index>=MAX_QUERY_ROWS*4: raise MemoryUnavailable('revision query exceeds bound')
                row=dict(row)
                row['source_ids']=sorted(set(row['source_ids'].split(',')))
                if pending is not None and row['ts']==pending['ts']:
                    conflicting=True; continue
                if pending is not None:
                    if conflicting: conflicts+=1
                    else: output.append(pending)
                pending=row; conflicting=False
            if pending is not None:
                if conflicting: conflicts+=1
                else: output.append(pending)
            collector=db.execute('SELECT last_attempt,last_success,state FROM collector WHERE pair=? AND interval=?',(pair,interval)).fetchone()
        expected=max(0,(end-start)//step)
        return {'exchange':'kraken','product':'spot','pair':pair,'interval_minutes':interval,'as_of':as_of,'start':start,'end':end,'expected':expected,'coverage':len(output),'missing':expected-len(output),'conflicts':conflicts,'fixture':fixture,'collector':dict(collector) if collector and not fixture and collector['last_attempt']<=as_of else None,'rows':output}


def session_profile(snapshot, zone='UTC'):
    """Full 168-bin weekday/hour profile; no interpolation or future labels."""
    if zone not in ZONES or snapshot['interval_minutes'] != 60:
        raise ValueError('profile requires supported zone and hourly candles')
    tz=ZoneInfo(zone)
    bins=[{'weekday':d,'hour':h,'samples':0,'expected':0,'range_sum':0.,'abs_return_sum':0.,'volume_sum':0.} for d in range(7) for h in range(24)]
    def cell(ts):
        dt=datetime.fromtimestamp(ts,timezone.utc).astimezone(tz)
        return bins[dt.weekday()*24+dt.hour]
    for ts in range(snapshot['start'],snapshot['end'],3600): cell(ts)['expected']+=1
    for row in snapshot['rows']:
        o,h,l,c,v=(float(row[x]) for x in ('o','h','l','c','v'))
        out=cell(row['ts']);out['samples']+=1;out['range_sum']+=(h-l)/o*100;out['abs_return_sum']+=abs(math.log(c/o))*100;out['volume_sum']+=v
    for out in bins:
        n=out['samples']
        for field, name in [('range_sum','mean_range_pct'),('abs_return_sum','mean_abs_log_return_pct'),('volume_sum','mean_base_volume')]:
            out[name]=out.pop(field)/n if n else None
    hourly=[]
    for h in range(24):
        parts=[b for b in bins if b['hour']==h]; n=sum(b['samples'] for b in parts)
        hourly.append({'hour':h,'samples':n,'expected':sum(b['expected'] for b in parts),**{k:sum((b[k] or 0)*b['samples'] for b in parts)/n if n else None for k in ('mean_range_pct','mean_abs_log_return_pct','mean_base_volume')}})
    return {k:v for k,v in snapshot.items() if k!='rows'} | {'zone':zone,'metric':'hourly_high_low_range_percent_of_open','bins':bins,'hourly':hourly,'spread':None,'depth':None,'slippage':None,'realized_subhour_volatility':None}


def export_parquet(memory, pair, interval, start, end, as_of):
    """Native DuckDB qualification candidate; immutable selected window export.

    Excludes all fixtures by default. Retains staging as recovery source until
    manifest/query/restore acceptance; no arbitrary SQL or URL supplied by caller.
    """
    import duckdb  # deliberately no silent fallback if native dependency absent
    snapshot=memory.query(pair,interval,start,end,as_of)
    if not snapshot['rows'] or snapshot['conflicts']:
        raise MemoryUnavailable('no clean observations to export')
    ident=hashlib.sha256(json.dumps([pair,interval,start,end,snapshot['rows']],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    directory=memory.root/'parquet'/pair.replace('/','_')/str(interval)
    directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    target=directory/f'{ident}.parquet'; temp=directory/f'{ident}.partial'
    with memory.tx() as db:
        existing=db.execute('SELECT * FROM exports WHERE id=?',(ident,)).fetchone()
        if existing:
            if not target.is_file() or sha256_file(target)!=existing['sha256']: raise MemoryUnavailable('export integrity failure')
            return dict(existing)
        temp.unlink(missing_ok=True)
        with closing(duckdb.connect(':memory:',config={'threads':2,'memory_limit':'256MB','autoload_known_extensions':False,'autoinstall_known_extensions':False})) as con:
            con.execute('CREATE TABLE window_data(ts BIGINT,digest VARCHAR,o VARCHAR,h VARCHAR,l VARCHAR,c VARCHAR,v VARCHAR,trades BIGINT,available_at BIGINT,source_ids VARCHAR)')
            con.executemany('INSERT INTO window_data VALUES (?,?,?,?,?,?,?,?,?,?)',[tuple(r[k] for k in ('ts','digest','o','h','l','c','v','trades','available_at'))+(json.dumps(r['source_ids'],separators=(',',':')),) for r in snapshot['rows']])
            # COPY path is internally derived, SQL quoted; not supplied by API.
            con.execute("COPY window_data TO '"+str(temp).replace("'","''")+"' (FORMAT PARQUET, COMPRESSION ZSTD)")
            roundtrip=con.execute('SELECT * FROM read_parquet(?) ORDER BY ts',[str(temp)]).fetchall()
            expected=[tuple(r[k] for k in ('ts','digest','o','h','l','c','v','trades','available_at'))+(json.dumps(r['source_ids'],separators=(',',':')),) for r in snapshot['rows']]
            if roundtrip!=expected: raise MemoryUnavailable('export round-trip data mismatch')
            count=len(roundtrip)
        with temp.open('rb') as f: os.fsync(f.fileno())
        os.chmod(temp,0o600);os.replace(temp,target)
        fd=os.open(directory,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        digest=sha256_file(target)
        db.execute('INSERT INTO exports VALUES(?,?,?,?,?,?,?,?,?,?)',(ident,str(target.relative_to(memory.root)),digest,count,int(time.time()),pair,interval,snapshot['start'],snapshot['end'],as_of))
        return dict(db.execute('SELECT * FROM exports WHERE id=?',(ident,)).fetchone())
