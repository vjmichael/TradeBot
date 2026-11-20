"""Construct a read-only reserve snapshot from an explicit column/row allowlist.

The reviewed database stays in root custody. Never copy pages, schema SQL,
free pages or unrelated tables (including the credential vault). Immutable
financial journal rows must retain their bytes for independent replay.
"""
import sqlite3
from contextlib import closing
from reserve_checkpoint import require
from reserve_native_migration import MAX_DB, check_sqlite

# Only columns used by reserve replay, policy evaluation, rebinding or wallet
# verification. In particular gate_intents.claim_token is not transferred.
TABLES = {
    'fin_events': ('seq INTEGER PRIMARY KEY, id TEXT, instance TEXT, kind TEXT, body TEXT, previous TEXT, digest TEXT', ''),
    'fin_head': ('id INTEGER PRIMARY KEY, count INTEGER, digest TEXT, boot INTEGER', ''),
    'fin_conflicts': ('id TEXT, attempted_hash TEXT', ''),
    'desk': ('id INTEGER PRIMARY KEY, paused INTEGER, human_pause INTEGER, virtual_cap_cents INTEGER', ''),
    'instances': ('id TEXT, pair TEXT, virtual_cents INTEGER, mode TEXT', ''),
    'gate_intents': ('id TEXT, instance_id TEXT, state TEXT, reserved INTEGER, entry INTEGER, stop INTEGER, target INTEGER, expires INTEGER', ''),
    'gate_events': ('seq INTEGER PRIMARY KEY, intent_id TEXT, action TEXT, at INTEGER, detail TEXT', "WHERE action IN ('workspace_provisioned','native_released','native_bound','native_attempt')"),
    'gate_current': ('kind TEXT, scope TEXT, receipt TEXT', "WHERE kind='policy' AND scope='desk'"),
    'gate_inputs': ('kind TEXT, scope TEXT, receipt TEXT, body TEXT, hash TEXT', "WHERE kind='policy' AND scope='desk' AND receipt IN (SELECT receipt FROM gate_current WHERE kind='policy' AND scope='desk')"),
    'reserve_drafts': ('instance TEXT, version INTEGER, id TEXT, at INTEGER, body TEXT, previous TEXT, digest TEXT', ''),
}
MAX_ROWS = 100_000


def columns(definition):
    return [part.split()[0] for part in definition.split(', ')]


def verify_shape(db):
    expected={('table', name, 'CREATE TABLE '+name+'('+sql+')') for name,(sql,_) in TABLES.items()}
    actual={tuple(row) for row in db.execute('SELECT type,name,sql FROM sqlite_master')}
    require(actual==expected,'snapshot_schema_not_allowlisted')
    for name,(_,where) in TABLES.items():
        if where:
            require(db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]==db.execute(f'SELECT COUNT(*) FROM {name} {where}').fetchone()[0],'snapshot_row_scope')


def project(source):
    """Source is already a reviewed, bounded, query-only SQLite transaction."""
    require(source.in_transaction,'snapshot_source_transaction')
    with closing(sqlite3.connect(':memory:')) as target:
        target.execute('PRAGMA trusted_schema=OFF')
        target.execute('PRAGMA page_size=4096')
        target.execute(f'PRAGMA max_page_count={MAX_DB//4096}')
        for name,(definition,where) in TABLES.items():
            row=source.execute('SELECT type FROM sqlite_master WHERE name=?',(name,)).fetchone()
            require(row is not None and row[0]=='table','snapshot_source_table_missing')
            cols=columns(definition)
            available={r[1] for r in source.execute(f'PRAGMA table_info({name})')}
            require(set(cols)<=available,'snapshot_source_columns_missing')
            target.execute(f'CREATE TABLE {name}({definition})')
            select=",".join(cols)
            if name=='gate_events':
                select="seq,intent_id,action,at,CASE WHEN action IN ('native_bound','native_attempt') THEN json_object('binding',json_extract(detail,'$.binding')) ELSE detail END"
            read=source.execute(f'SELECT {select} FROM {name} {where} LIMIT {MAX_ROWS+1}')
            count=0
            while batch:=read.fetchmany(256):
                count+=len(batch)
                require(count<=MAX_ROWS,'snapshot_row_limit')
                target.executemany(f'INSERT INTO {name} VALUES({",".join("?" for _ in cols)})',batch)
        target.commit()
        verify_shape(target)
        raw=target.serialize()
    require(0<len(raw)<=MAX_DB,'snapshot_size_limit')
    return raw


def open_projected(raw):
    db=check_sqlite(raw)
    try:
        verify_shape(db)
        return db
    except BaseException:
        db.close();raise
