"""Explicit, crash-recoverable installation of a virtual PAPER workspace."""
from contextlib import closing, nullcontext
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import tempfile
import time

from financial_ledger import CENT, FinancialLedger, canon, replay, require, sha
from paper_custody import authenticate, packet, private_key, workspace_key
from paper_supervisor import custody
from paper_execution import append_event, event_detail


def private_directory(path):
    p=Path(path);st=p.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and not st.st_mode&0o077,'private_directory_required')
    return p


def private_open(path,flags):
    fd=os.open(path,flags|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        st=os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and not st.st_mode&0o077,'private_file_required')
        return fd
    except BaseException:os.close(fd);raise


def instance_id(value):
    require(isinstance(value,str) and re.fullmatch(r'[0-9a-f]{32}',value),'invalid_installed_instance_id')
    return value


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def write_new(path,data):
    with os.fdopen(private_open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL),'wb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())


def scope(db,instance):
    row=db.execute('SELECT id,pair,virtual_cents,mode FROM instances WHERE id=?',(instance,)).fetchone()
    require(row is not None and row[3]=='PAPER','paper_instance_required')
    require(row[1] in ('BTC/USD','ETH/USD','SOL/USD') and type(row[2]) is int and row[2]>0,'invalid_paper_scope')
    return {'instance':instance,'pair':row[1],'principal_cents':row[2],'mode':'PAPER'}


def native_check(path,*,empty=False):
    os.close(private_open(path,os.O_RDONLY))
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        require(db.execute('PRAGMA quick_check').fetchone()[0]=='ok','native_database_integrity')
        for table in ('trades','orders'):
            exists=db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone()
            require(exists is not None,'native_schema_missing')
            if empty:require(not db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone(),'nonempty_native_workspace')


def native_init(path):
    # This is native persistence initialization only; no bot, exchange, or RPC.
    from freqtrade.persistence import init_db, Trade
    init_db('sqlite:///'+str(Path(path).resolve()))
    engine=Trade.session.get_bind()
    Trade.session.remove()
    engine.dispose()
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('PRAGMA journal_mode=DELETE')
    os.chmod(path,0o600)
    native_check(path,empty=True)


def verified_workspace(data,instance,expected):
    instance_id(instance); key,binding=workspace_key(data,instance)
    work=Path(data)/'paper'/instance
    with os.fdopen(private_open(work/'provision.json',os.O_RDONLY),'rb') as handle:
        raw=handle.read(8193)
    require(len(raw)<=8192,'invalid_provision_manifest')
    body=authenticate(key,'workspace-provision-v1',json.loads(raw))
    require(body==expected|{'binding':binding,'version':1},'workspace_scope_or_identity_changed')
    native_check(work/'paper.sqlite')
    return work


def installed_workspace(data,instance,db):
    work=verified_workspace(data,instance,scope(db,instance))
    key,binding=workspace_key(data,instance)
    marker=event_detail(db,instance,'workspace_provisioned')
    require(marker=={'binding':binding,'key_sha':sha(key.hex())},'workspace_provision_receipt_missing_or_changed')
    return work


def provision(data,instance,transaction,*,initialize=native_init,reviewed=None):
    """Install once, resume only exact pristine incomplete provisioning.

    Files are published under a private parent lease before the journal funding
    commit. A crash between them can resume without reallocating or replacing
    a workspace. Existing unmanifested data must be reviewed, never adopted.
    """
    instance_id(instance); data=private_directory(data)
    parent=data/'paper';parent.mkdir(mode=0o700,exist_ok=True);private_directory(parent)
    work=parent/instance
    with custody(parent),transaction() as db:
        expected=scope(db,instance)
        require(reviewed is None or reviewed==expected,'provision_review_changed')
        require(db.execute('SELECT paused FROM desk WHERE id=1').fetchone()[0]==1,'provision_requires_pause')
        states,_,_,_=replay(db);s=states.get(instance)
        marker=event_detail(db,instance,'workspace_provisioned')
        if marker is not None:
            installed_workspace(data,instance,db)
            require(s is not None and -s['capital']==expected['principal_cents']*CENT,'workspace_principal_mismatch')
            return {'instance':instance,'state':'already_provisioned','mode':'PAPER'}
        require(not db.execute('SELECT 1 FROM gate_intents WHERE instance_id=?',(instance,)).fetchone(),'existing_execution_history')
        if s is not None:
            require(-s['capital']==expected['principal_cents']*CENT and s['active']==-s['capital'] and len(s['history'])==1 and s['history'][0][0]=='fund' and s['reserve']['last_time']==0,'nonpristine_financial_history')
        if work.exists() or work.is_symlink():
            verified_workspace(data,instance,expected)
            native_check(work/'paper.sqlite',empty=True)
        else:
            with tempfile.TemporaryDirectory(prefix='.provision-',dir=parent) as tmp:
                stage=Path(tmp)/'workspace';stage.mkdir(mode=0o700)
                path=stage/'paper.sqlite';write_new(path,b'');initialize(path);native_check(path,empty=True)
                with path.open('rb') as f:os.fsync(f.fileno())
                st=path.stat();key=secrets.token_bytes(32)
                binding=sha([instance,str((work/'paper.sqlite').resolve()),st.st_dev,st.st_ino])
                write_new(stage/'receipt.key',key)
                write_new(stage/'provision.json',canon(packet(key,'workspace-provision-v1',expected|{'binding':binding,'version':1})).encode())
                sync_dir(stage);os.rename(stage,work);sync_dir(parent)
            verified_workspace(data,instance,expected)
        if s is None:
            require(not db.execute('SELECT 1 FROM gate_intents WHERE instance_id=?',(instance,)).fetchone(),'existing_execution_history')
            FinancialLedger(lambda:nullcontext(db)).fund('provision:'+instance,instance)
        key,binding=workspace_key(data,instance)
        append_event(db,instance,'workspace_provisioned',{'binding':binding,'key_sha':sha(key.hex())},int(time.time()))
        return {'instance':instance,'state':'provisioned','mode':'PAPER'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data',type=Path);parser.add_argument('instance');args=parser.parse_args()
    require(sys.stdin.isatty() and sys.stdout.isatty(),'interactive_operator_required')
    from reserve_runtime import desk_transaction
    tx=desk_transaction(args.data)
    with tx() as db:expected=scope(db,instance_id(args.instance))
    print(canon(expected));require(input('Type PROVISION PAPER '+args.instance+' to confirm: ')=='PROVISION PAPER '+args.instance,'provision_cancelled')
    print(canon(provision(args.data.resolve(),args.instance,tx,reviewed=expected)))


if __name__=='__main__':main()
