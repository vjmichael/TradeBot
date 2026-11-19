"""TB-04C: immutable revision partitions and bounded historical reads.

SQLite remains the authoritative ingestion/provenance journal. Parquet contains
all published revisions, not just the winning candle at export time. A catalog
watermark invalidates affected partitions in the *same* transaction as intake.
Only current, verified generations participate in mixed Parquet/SQLite reads.
No source data deletion, arbitrary SQL, user filenames or network extensions.
"""
import hashlib
import json
import os
import re
import shutil
import time
from contextlib import closing
from pathlib import Path

from market_memory import MAX_QUERY_ROWS, MAX_TIME, MemoryUnavailable, identity, sha256_file

MAX_RAW = MAX_QUERY_ROWS * 4
MAX_PARTITION_RAW = 12000
MAX_PARTITIONS = 128
MAX_FILE = 64 * 1024**2
DEFAULT_QUOTA = 2 * 1024**3
DEFAULT_RESERVE = 256 * 1024**2
FIELDS = ('ts', 'digest', 'o', 'h', 'l', 'c', 'v', 'trades', 'available_at', 'source_id')
SCHEMA = 'ts BIGINT,digest VARCHAR,o VARCHAR,h VARCHAR,l VARCHAR,c VARCHAR,v VARCHAR,trades BIGINT,available_at BIGINT,source_id VARCHAR'
RAW_SQL = '''SELECT c.ts,c.digest,c.o,c.h,c.l,c.c,c.v,c.trades,
 MAX(c.available_at,s.published_at) AS available_at,c.source_id
 FROM candles c JOIN sources s ON s.id=c.source_id
 WHERE c.pair=? AND c.interval=? AND c.ts>=? AND c.ts<?
 AND s.kind!='fixture' AND s.state='complete' AND s.published_at IS NOT NULL'''


def span(interval):
    # UTC epoch-aligned daily fine resolution / 30-day coarse blocks.
    return 86400 if interval < 60 else 30 * 86400


def partition_start(interval, timestamp):
    return timestamp // span(interval) * span(interval)


def blocks(interval, start, end):
    if end <= start:
        return []
    values = range(partition_start(interval, start), end, span(interval))
    if len(values) > MAX_PARTITIONS:
        raise ValueError('too many partitions in one request')
    return values


def window(pair, interval, start, end, as_of):
    step = identity(pair, interval)
    if any(type(v) is not int for v in (start, end, as_of)) or not 0 <= start < end <= MAX_TIME or not 0 < as_of < MAX_TIME or start % step or end % step or (end-start)//step > MAX_QUERY_ROWS:
        raise ValueError('invalid or unbounded lake window')
    return max(start, min(end, as_of//step*step)), step


def duck():
    try:
        import duckdb
    except ImportError as exc:
        raise MemoryUnavailable('native historical query engine unavailable') from exc
    return duckdb.connect(':memory:', config={'threads': 2, 'memory_limit': '256MB', 'max_temp_directory_size': '0B', 'autoload_known_extensions': False, 'autoinstall_known_extensions': False, 'enable_external_access': True})


def syncdir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class HistoricalLake:
    def __init__(self, memory):
        self.memory = memory
        self.root = memory.root / 'lake-v1'
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir() or (os.name == 'posix' and self.root.stat().st_mode & 0o077):
            raise MemoryUnavailable('unsafe lake directory')
        # Triggers cover both new revisions and publication of staged CSV rows.
        # Existing data starts at generation zero, never implicitly archived.
        with memory.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS lake_epoch(pair TEXT,interval INTEGER,block INTEGER,epoch INTEGER NOT NULL,PRIMARY KEY(pair,interval,block))')
            db.execute('''CREATE TABLE IF NOT EXISTS lake_files(id TEXT PRIMARY KEY,
                pair TEXT NOT NULL,interval INTEGER NOT NULL,block INTEGER NOT NULL,epoch INTEGER NOT NULL,
                sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,rows INTEGER NOT NULL,created INTEGER NOT NULL,
                retired INTEGER, UNIQUE(pair,interval,block,epoch,id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS lake_active(pair TEXT,interval INTEGER,block INTEGER,
                id TEXT NOT NULL REFERENCES lake_files(id),PRIMARY KEY(pair,interval,block))''')
            db.execute('''CREATE TABLE IF NOT EXISTS lake_pins(id TEXT REFERENCES lake_files(id),label TEXT,
                PRIMARY KEY(id,label))''')
            db.execute('CREATE TABLE IF NOT EXISTS lake_policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),quota INTEGER NOT NULL,reserve INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO lake_policy VALUES(1,?,?)',(DEFAULT_QUOTA,DEFAULT_RESERVE))
            for action, alias in [('INSERT','NEW'),('DELETE','OLD'),('UPDATE','OLD'),('UPDATE','NEW')]:
                p = f'{alias}.interval'; b = f'{alias}.ts / (CASE WHEN {p}<60 THEN 86400 ELSE 2592000 END) * (CASE WHEN {p}<60 THEN 86400 ELSE 2592000 END)'
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS lake_candle_{action.lower()}_{alias.lower()} AFTER {action} ON candles
                    WHEN EXISTS(SELECT 1 FROM sources WHERE id={alias}.source_id AND kind!='fixture' AND state='complete')
                    BEGIN INSERT INTO lake_epoch VALUES({alias}.pair,{p},{b},1)
                    ON CONFLICT(pair,interval,block) DO UPDATE SET epoch=epoch+1; END''')
            # Source visibility/kind/availability changes affect all its blocks.
            db.execute('''CREATE TRIGGER IF NOT EXISTS lake_source_publish AFTER UPDATE OF state,published_at,kind ON sources
                WHEN OLD.state IS NOT NEW.state OR OLD.published_at IS NOT NEW.published_at OR OLD.kind IS NOT NEW.kind
                BEGIN INSERT INTO lake_epoch SELECT DISTINCT c.pair,c.interval,
                c.ts/(CASE WHEN c.interval<60 THEN 86400 ELSE 2592000 END)*(CASE WHEN c.interval<60 THEN 86400 ELSE 2592000 END),1
                FROM candles c WHERE c.source_id=NEW.id AND (OLD.kind!='fixture' OR NEW.kind!='fixture')
                ON CONFLICT(pair,interval,block) DO UPDATE SET epoch=epoch+1; END''')
            db.execute('CREATE INDEX IF NOT EXISTS lake_source_rows ON candles(source_id,ts)')

    @staticmethod
    def _epoch(db, pair, interval, block):
        r = db.execute('SELECT epoch FROM lake_epoch WHERE pair=? AND interval=? AND block=?',(pair,interval,block)).fetchone()
        return r[0] if r else 0

    def _file(self, ident):
        if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{64}',ident):
            raise MemoryUnavailable('invalid partition identity')
        return self.root / (ident + '.parquet')

    def _verified(self, row):
        path = self._file(row['id'])
        if path.is_symlink() or not path.is_file() or not 0 < row['bytes'] <= MAX_FILE or path.stat().st_size != row['bytes'] or sha256_file(path) != row['sha256']:
            raise MemoryUnavailable('partition integrity failure')
        return path

    def _usage(self, db):
        policy = db.execute('SELECT quota,reserve FROM lake_policy WHERE singleton=1').fetchone()
        total = 0; files = 0
        for path in self.root.iterdir():
            if files>=100000: raise MemoryUnavailable('lake inventory exceeds bound')
            if path.is_symlink() or not path.is_file() or not re.fullmatch(r'[0-9a-f]{64}\.(?:parquet|partial)',path.name):
                raise MemoryUnavailable('unexpected lake entry')
            total += path.stat().st_size; files += 1
        free = shutil.disk_usage(self.root).free
        active = db.execute('SELECT count(*) FROM lake_active').fetchone()[0]
        dirty = db.execute('''SELECT count(*) FROM lake_active a JOIN lake_files f ON a.id=f.id
             LEFT JOIN lake_epoch e ON e.pair=a.pair AND e.interval=a.interval AND e.block=a.block
             WHERE f.epoch!=COALESCE(e.epoch,0)''').fetchone()[0]
        retired = db.execute('SELECT count(*) FROM lake_files WHERE retired IS NOT NULL').fetchone()[0]
        pinned = db.execute('SELECT count(DISTINCT id) FROM lake_pins').fetchone()[0]
        return {'lake_bytes':total,'lake_files':files,'quota_bytes':policy[0],'reserve_bytes':policy[1],
                'free_bytes':free,'active':active,'dirty':dirty,'retired':retired,'pinned':pinned,
                'state':'storage_limited' if total>=policy[0] or free<=policy[1] else 'available'}

    def status(self):
        with self.memory.tx() as db:
            result = self._usage(db)
            # Bounded traversal of managed catalog root, includes WAL/CSV/export
            # files, not arbitrary external locations. No following symlinks.
            size=0; count=0
            for directory, dirs, files in os.walk(self.memory.root, followlinks=False):
                for name in dirs+files:
                    count+=1
                    if count>100000: raise MemoryUnavailable('storage inventory exceeds bound')
                    p=Path(directory)/name
                    if p.is_symlink(): raise MemoryUnavailable('unexpected storage symlink')
                    if p.is_file(): size+=p.stat().st_size
            return result | {'market_memory_bytes':size,'staging_retained':True}

    def policy(self, quota, reserve):
        if any(type(x) is not int or not 0<=x<=2**50 for x in (quota,reserve)) or quota<1024*1024:
            raise ValueError('invalid storage budget')
        with self.memory.tx() as db:
            db.execute('UPDATE lake_policy SET quota=?,reserve=? WHERE singleton=1',(quota,reserve))
            return self._usage(db)

    def compact(self, pair, interval, start, end):
        """One immutable generation per block. Retry reuses current generation.

        SQLite write lock serializes publication, pruning and mixed reads. Raw
        source rows remain authoritative; old manifests are retired, never
        implicitly destroyed. Each block commits independently for restart.
        """
        end,_ = window(pair,interval,start,end,MAX_TIME-1)
        output=[]
        for block in blocks(interval,start,end):
            output.append(self._compact_block(pair,interval,block))
        return output

    def _compact_block(self,pair,interval,block):
        end=block+span(interval)
        with self.memory.tx() as db:
            epoch=self._epoch(db,pair,interval,block)
            old=db.execute('''SELECT f.* FROM lake_active a JOIN lake_files f ON f.id=a.id
                WHERE a.pair=? AND a.interval=? AND a.block=?''',(pair,interval,block)).fetchone()
            if old and old['epoch']==epoch:
                self._verified(old);return dict(old)|{'state':'reused'}
            rows=db.execute(RAW_SQL+' ORDER BY c.ts,c.digest,c.source_id LIMIT ?', (pair,interval,block,end,MAX_PARTITION_RAW+1)).fetchall()
            if len(rows)>MAX_PARTITION_RAW: raise MemoryUnavailable('partition revision limit exceeded')
            if not rows:
                if old:
                    db.execute('DELETE FROM lake_active WHERE pair=? AND interval=? AND block=?',(pair,interval,block))
                    db.execute('UPDATE lake_files SET retired=? WHERE id=?',(int(time.time()),old['id']))
                return {'pair':pair,'interval':interval,'block':block,'state':'empty'}
            data=[tuple(r) for r in rows]
            # Immutable content/version key, includes publication time and exact revisions.
            ident=hashlib.sha256(json.dumps([pair,interval,block,epoch,data],separators=(',',':')).encode()).hexdigest()
            path=self._file(ident);temp=path.with_suffix('.partial')
            usage=self._usage(db);estimate=min(MAX_FILE,1024*1024+len(data)*1024)
            if usage['lake_bytes']+estimate>usage['quota_bytes'] or usage['free_bytes']-estimate<usage['reserve_bytes']:
                raise MemoryUnavailable('lake storage admission denied')
            if path.is_symlink() or temp.is_symlink(): raise MemoryUnavailable('unsafe publication path')
            # A crash after rename but before commit leaves an unreferenced file.
            # Rebuild it, never trust a file without a manifest.
            if db.execute('SELECT 1 FROM lake_files WHERE id=?',(ident,)).fetchone():
                raise MemoryUnavailable('unexpected generation identity reuse')
            temp.unlink(missing_ok=True)
            try:
                with closing(duck()) as con:
                    con.execute('CREATE TABLE partition_data('+SCHEMA+')')
                    # Native COPY uses bounded batches; exact decimals are strings.
                    for i in range(0,len(data),2048):
                        con.executemany('INSERT INTO partition_data VALUES (?,?,?,?,?,?,?,?,?,?)',data[i:i+2048])
                    con.execute("COPY partition_data TO '"+str(temp).replace("'","''")+"' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 2048)")
                    if con.execute('SELECT '+','.join(FIELDS)+' FROM read_parquet(?) ORDER BY ts,digest,source_id',[str(temp)]).fetchall()!=data:
                        raise MemoryUnavailable('partition roundtrip mismatch')
                size=temp.stat().st_size;usage=self._usage(db)
                if not 0<size<=MAX_FILE or usage['lake_bytes']>usage['quota_bytes'] or usage['free_bytes']<usage['reserve_bytes']:
                    raise MemoryUnavailable('lake publication exceeds storage budget')
                os.chmod(temp,0o600)
                with temp.open('rb') as fd: os.fsync(fd.fileno())
                digest=sha256_file(temp);os.replace(temp,path);syncdir(self.root)
                now=int(time.time())
                db.execute('INSERT INTO lake_files VALUES(?,?,?,?,?,?,?,?,?,NULL)',(ident,pair,interval,block,epoch,digest,size,len(data),now))
                if old:db.execute('UPDATE lake_files SET retired=? WHERE id=?',(now,old['id']))
                db.execute('INSERT INTO lake_active VALUES(?,?,?,?) ON CONFLICT(pair,interval,block) DO UPDATE SET id=excluded.id',(pair,interval,block,ident))
                return dict(db.execute('SELECT * FROM lake_files WHERE id=?',(ident,)).fetchone())|{'state':'published'}
            finally:
                temp.unlink(missing_ok=True)

    def query(self,pair,interval,start,end,as_of):
        """Bounded mixed read; dirty/unmaterialized blocks use authoritative journal.

        Read from one catalog transaction. Do not silently fallback for corrupt
        active files. Late conflicts and as-of visibility match SQLite rules.
        """
        end,step=window(pair,interval,start,end,as_of)
        raw=[]; used=0; tail=0; checked_bytes=0
        with self.memory.tx() as db:
            con=None
            try:
                for block in blocks(interval,start,end):
                    left=max(start,block);right=min(end,block+span(interval));remaining=MAX_RAW-len(raw)
                    row=db.execute('''SELECT f.* FROM lake_active a JOIN lake_files f ON f.id=a.id
                        WHERE a.pair=? AND a.interval=? AND a.block=?''',(pair,interval,block)).fetchone()
                    if row and row['epoch']==self._epoch(db,pair,interval,block):
                        checked_bytes+=row['bytes']
                        if checked_bytes>512*1024**2: raise MemoryUnavailable('partition scan byte bound exceeded')
                        p=self._verified(row)
                        try:
                            if con is None: con=duck()
                            data=con.execute('SELECT '+','.join(FIELDS)+' FROM read_parquet(?) WHERE ts>=? AND ts<? AND available_at<=? LIMIT ?',[str(p),left,right,as_of,remaining+1]).fetchall()
                        except MemoryUnavailable:
                            raise
                        except Exception as exc:
                            # Native failures are unavailable observations, not fallback successes.
                            raise MemoryUnavailable('native partition query unavailable') from exc
                        used+=1
                    else:
                        data=db.execute(RAW_SQL+' AND c.available_at<=? AND s.published_at<=? LIMIT ?', (pair,interval,left,right,as_of,as_of,remaining+1)).fetchall();tail+=1
                    if len(data)>remaining: raise MemoryUnavailable('bounded query revision limit exceeded')
                    raw.extend(tuple(r) for r in data)
            finally:
                if con is not None:con.close()
            collector=db.execute('SELECT last_attempt,last_success,state FROM collector WHERE pair=? AND interval=?',(pair,interval)).fetchone()
        grouped={}
        for ts,digest,o,h,l,c,v,trades,available,source_id in raw:
            r=grouped.setdefault(ts,{}).setdefault(digest,{'ts':ts,'digest':digest,'o':o,'h':h,'l':l,'c':c,'v':v,'trades_set':set(),'available_at':available,'source_ids':set()})
            if trades is not None:r['trades_set'].add(trades)
            r['available_at']=min(r['available_at'],available);r['source_ids'].add(source_id)
        rows=[];conflicts=0
        for _,revisions in sorted(grouped.items()):
            if len(revisions)!=1:conflicts+=1;continue
            r=next(iter(revisions.values()));counts=r.pop('trades_set');r['trades']=next(iter(counts)) if len(counts)==1 else None;r['source_ids']=sorted(r['source_ids']);rows.append(r)
        expected=(end-start)//step
        return {'exchange':'kraken','product':'spot','pair':pair,'interval_minutes':interval,'as_of':as_of,'start':start,'end':end,'expected':expected,'coverage':len(rows),'missing':expected-len(rows),'conflicts':conflicts,'fixture':False,'collector':dict(collector) if collector and collector['last_attempt']<=as_of else None,'rows':rows,'storage':{'parquet_partitions':used,'sqlite_partitions':tail,'verified_bytes':checked_bytes}}

    def pin(self, ident, label, *, release=False):
        self._file(ident)
        if not isinstance(label,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',label): raise ValueError('invalid evaluation label')
        with self.memory.tx() as db:
            row=db.execute('SELECT * FROM lake_files WHERE id=?',(ident,)).fetchone()
            if not row:raise MemoryUnavailable('unknown partition generation')
            if release:db.execute('DELETE FROM lake_pins WHERE id=? AND label=?',(ident,label))
            else:self._verified(row);db.execute('INSERT OR IGNORE INTO lake_pins VALUES(?,?)',(ident,label))

    def prune(self, *, before, dry_run=True, limit=32):
        """Remove only retired, unpinned generations; recover rename orphans separately."""
        if type(before) is not int or not 0<=before<=int(time.time()) or type(limit) is not int or not 1<=limit<=128 or type(dry_run) is not bool:raise ValueError('invalid retention bounds')
        with self.memory.tx() as db:
            rows=db.execute('''SELECT f.* FROM lake_files f WHERE f.retired IS NOT NULL AND f.retired<=?
                AND NOT EXISTS(SELECT 1 FROM lake_pins p WHERE p.id=f.id)
                AND NOT EXISTS(SELECT 1 FROM lake_active a WHERE a.id=f.id) ORDER BY f.retired,f.id LIMIT ?''',(before,limit)).fetchall()
            if not dry_run:
                for row in rows:
                    p=self._file(row['id'])
                    if p.is_symlink():raise MemoryUnavailable('unsafe retired partition')
                    p.unlink(missing_ok=True);syncdir(self.root)
                    db.execute('DELETE FROM lake_files WHERE id=?',(row['id'],))
            return {'dry_run':dry_run,'generations':[r['id'] for r in rows],'bytes':sum(r['bytes'] for r in rows)}

    def recover_orphans(self, *, dry_run=True, limit=32):
        if type(limit) is not int or not 1<=limit<=128 or type(dry_run) is not bool:raise ValueError('invalid recovery bounds')
        with self.memory.tx() as db:
            files=[]
            for p in self.root.iterdir():
                if p.is_symlink() or not p.is_file() or not re.fullmatch(r'[0-9a-f]{64}\.(?:parquet|partial)',p.name):raise MemoryUnavailable('unexpected lake file')
                if p.suffix=='.partial' or not db.execute('SELECT 1 FROM lake_files WHERE id=?',(p.stem,)).fetchone():
                    files.append(p)
                    if len(files)>=limit:break
            if not dry_run:
                for p in files:p.unlink()
                syncdir(self.root)
            return {'dry_run':dry_run,'files':[p.name for p in files]}
