"""Offline, non-activating staging of pristine PAPER native workspace custody.

Accepts only an administrator-reviewed cold export. Never transfers the old
receipt key, changes the desk, starts a worker, or migrates traded history.
The returned binding is a candidate; old provision markers are not rewritten.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from reserve_checkpoint import require
from reserve_ipc import protected_dir, canonical

MAX_DB = 32 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_export(directory, name, limit=MAX_DB):
    # Root-reviewed cold export, not a file chosen by the desk daemon.
    require(re.fullmatch(r'[a-zA-Z0-9_.-]+',name) and name not in ('.','..'),'export_name')
    with protected_dir(directory,0,0o700) as fd:
        raw=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
        with os.fdopen(raw,'rb') as f:
            s=os.fstat(f.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=limit,'export_custody')
            data=f.read(limit+1)
            require(len(data)==s.st_size,'export_size')
            return data


def check_sqlite(raw):
    require(raw[:16]==b'SQLite format 3\x00' and raw[18:20]==b'\x01\x01','cold_sqlite_required')
    db=sqlite3.connect(':memory:')
    try:
        db.deserialize(raw);db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA query_only=ON')
        require(db.execute('PRAGMA quick_check').fetchall()==[('ok',)],'sqlite_integrity')
        return db
    except BaseException:
        db.close();raise


def pristine_native(raw):
    with closing(check_sqlite(raw)) as db:
        for table in ('trades','orders'):
            require(db.execute('SELECT type FROM sqlite_master WHERE name=?',(table,)).fetchone()==('table',),'native_schema_missing')
            require(db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() is None,'nonpristine_native_history')


def pristine_ledger(db, expected):
    """Actual production replay, no caller-supplied account totals."""
    from financial_ledger import replay, CENT
    require(db.execute('SELECT paused,human_pause FROM desk WHERE id=1').fetchone()==(1,1),'migration_requires_human_pause')
    instance=expected['instance']
    require(db.execute('SELECT pair,virtual_cents,mode FROM instances WHERE id=?',(instance,)).fetchone()==(expected['pair'],expected['principal_cents'],'PAPER'),'migration_scope')
    states,_,count,head=replay(db);s=states.get(instance)
    require(s is not None and s['pair']==expected['pair'].replace('/','-') and -s['capital']==expected['principal_cents']*CENT and s['active']==-s['capital'] and not s['protected'] and not s['pending'] and not s['lots'] and not s['sales'] and not s['correction_locked'] and len(s['history'])==1 and s['history'][0][0]=='fund' and s['reserve']['last_time']==0 and s['reserve']['active'] is None,'nonpristine_financial_history')
    require(not db.execute('SELECT 1 FROM fin_conflicts LIMIT 1').fetchone(),'migration_financial_conflict')
    require(not db.execute('SELECT 1 FROM gate_intents WHERE instance_id=?',(instance,)).fetchone(),'existing_execution_history')
    require(not db.execute("SELECT 1 FROM gate_intents WHERE state IN ('AUTHORIZED','CLAIMED','UNKNOWN','FILLED_HELD') LIMIT 1").fetchone(),'desk_execution_in_flight')
    rows=db.execute("SELECT detail FROM gate_events WHERE intent_id=? AND action='workspace_provisioned'",(instance,)).fetchall()
    require(len(rows)==1,'legacy_provision_receipt_missing')
    marker=json.loads(rows[0][0]);require(type(marker) is dict and set(marker)=={'binding','key_sha'} and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in marker.values()),'legacy_provision_receipt_invalid')
    return {'old_provision':marker,'ledger_count':count,'ledger_head':head}


def stage_pristine(export_dir, destination, review, *, native_uid, desk_uid, authority_uid):
    """Root-only stage into a NEW directory; closed exports remain unchanged.

    Review must bind both cold-export hashes and exact portfolio scope. This
    does not certify legacy history or authorize replacing a live workspace.
    """
    require(os.geteuid()==0 and len({native_uid,desk_uid,authority_uid})==3 and all(type(x) is int and x>0 for x in (native_uid,desk_uid,authority_uid)),'migration_principals')
    require(type(review) is dict and set(review)=={'scope','native_sha256','desk_sha256'},'migration_review')
    expected=review['scope'];require(type(expected) is dict and set(expected)=={'instance','pair','principal_cents','mode'} and re.fullmatch('[0-9a-f]{32}',expected['instance']) and expected['mode']=='PAPER' and expected['pair'] in ('BTC/USD','ETH/USD','SOL/USD') and type(expected['principal_cents']) is int and expected['principal_cents']>0,'migration_scope')
    native=read_export(export_dir,'paper.sqlite');desk=read_export(export_dir,'desk.sqlite3')
    require(digest(native)==review['native_sha256'] and digest(desk)==review['desk_sha256'],'migration_export_changed')
    pristine_native(native)
    with closing(check_sqlite(desk)) as db:
        db.execute("BEGIN");prior=pristine_ledger(db,expected)
    return _stage(destination,review,native,native_uid,prior)


def _stage(destination,review,native,native_uid,prior):
    # Internal staging only after validation. Root-owned parent prevents any
    # of the application identities replacing the final custody root.
    destination=Path(destination)
    require(os.geteuid()==0 and native_uid>0 and destination.is_absolute(),'migration_destination')
    instance=review['scope']['instance']
    with protected_dir(destination.parent,0,0o755) as parent:
        require(not destination.exists() and not destination.is_symlink(),'migration_destination_exists')
        temp='.native-stage-'+secrets.token_hex(16);os.mkdir(temp,0o700,dir_fd=parent)
        root=destination.parent/temp
        # Do not remove incomplete staging automatically: preserve for review.
        paper=root/'paper';work=paper/instance;paper.mkdir(mode=0o700);work.mkdir(mode=0o700)
        def write(path,raw):
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fchown(f.fileno(),native_uid,-1);os.fsync(f.fileno())
        path=work/'paper.sqlite';write(path,native)
        st=path.stat();key=secrets.token_bytes(32)
        # Exact existing financial_ledger.sha and paper_custody sign domains.
        binding=digest(canonical([instance,str(destination/'paper'/instance/'paper.sqlite'),st.st_dev,st.st_ino]))
        write(work/'receipt.key',key)
        body=review['scope']|{'binding':binding,'version':1}
        mac=hmac.new(key,canonical(['maj-vaj:PAPER:R2','workspace-provision-v1',body]),hashlib.sha256).hexdigest()
        write(work/'provision.json',canonical({'body':body,'mac':mac}))
        result={'state':'staged_not_activated','scope':review['scope'],'binding':binding,'key_sha':digest(canonical(key.hex())),'review':review,'prior':prior}
        write(root/'migration-candidate.json',canonical(result))
        for d in (work,paper,root):
            fd=os.open(d,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fchown(fd,native_uid,-1);os.fsync(fd)
            finally:os.close(fd)
        os.rename(temp,destination.name,src_dir_fd=parent,dst_dir_fd=parent);os.fsync(parent)
        return result
