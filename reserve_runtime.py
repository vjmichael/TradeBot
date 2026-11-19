"""Local human approval and keyless PAPER scheduler; no network or order interface."""
import argparse
import copy
import fcntl
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from financial_ledger import AccountingError, canon, ident, replay, require, sha
from reserve_policy import DAY, ReserveManager, validate

ACTIONS = {'save','activate','revoke','earmark','once'}


def private_directory(path):
    p=Path(path)
    s=p.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==os.getuid() and not s.st_mode&0o077,'private_directory_required')
    return p


def private_open(path, flags):
    fd=os.open(path,flags|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and not s.st_mode&0o077,'private_file_required')
        return fd
    except BaseException:
        os.close(fd)
        raise


def password_hash(password,salt):
    require(type(password) is str and 16<=len(password)<=1024,'invalid_passphrase')
    return hashlib.scrypt(password.encode(),salt=salt,n=32768,r=8,p=1,maxmem=64*1024*1024,dklen=32)


def provision(directory,password):
    path=private_directory(directory)/'reserve-approval.json'
    salt=secrets.token_bytes(32)
    nonce=secrets.token_bytes(12)
    encrypted=AESGCM(password_hash(password,salt)).encrypt(nonce,secrets.token_bytes(32),b'maj-vaj:PAPER:approval-key:v1')
    record={'version':1,'salt':salt.hex(),'nonce':nonce.hex(),'ciphertext':encrypted.hex()}
    with os.fdopen(private_open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL),'w') as out:
        out.write(canon(record)); out.flush(); os.fsync(out.fileno())
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def unlock(directory,password):
    with os.fdopen(private_open(private_directory(directory)/'reserve-approval.json',os.O_RDONLY)) as f:
        data=json.loads(f.read(2048))
    require(set(data)=={'version','salt','nonce','ciphertext'} and data['version']==1,'invalid_approval_store')
    salt,nonce,ciphertext=(bytes.fromhex(data[k]) for k in ('salt','nonce','ciphertext'))
    require(len(salt)==32 and len(nonce)==12 and len(ciphertext)==48,'invalid_approval_store')
    try:
        return AESGCM(password_hash(password,salt)).decrypt(nonce,ciphertext,b'maj-vaj:PAPER:approval-key:v1')
    except InvalidTag as exc:
        raise AccountingError('authentication_failed') from exc


def desk_transaction(directory):
    path=private_directory(directory)/'desk.sqlite3'
    def transaction():
        return _transaction(path)
    return transaction


@contextmanager
def _transaction(path):
    # Never boot or create a desk from the worker; startup would change pause state.
    with os.fdopen(private_open(path,os.O_RDONLY),'rb'):
        pass
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=10,isolation_level=None)
    db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        require(db.execute('PRAGMA user_version').fetchone()[0]==4,'unsupported_desk_schema')
        yield db
        db.commit()
    except BaseException:
        db.rollback(); raise
    finally:db.close()


def status(transaction, *, clock=time.time):
    with transaction() as db:
        states,_,_,head=replay(db); now=int(clock()); boot=db.execute('SELECT boot FROM fin_head WHERE id=1').fetchone()[0]
        paused,human=db.execute('SELECT paused,human_pause FROM desk WHERE id=1').fetchone()
        result={}
        for key,s in states.items():
            r=copy.deepcopy(s['reserve']); p=r['active']['policy'] if r['active'] else None
            r['authorized']=bool(p and p['start']<=now<min(p['end'],p['expiry']))
            r['effective_rate_bps']=p['rate_bps'] if r['authorized'] else 0
            r['blocked']=bool(paused or human or s['losses'] or s['correction_locked'])
            r['active_cash']=s['active']; r['protected_cash']=s['protected']
            result[key]=r
        return {'mode':'PAPER','journal_head':head,'boot':boot,'instances':result}


class ApprovalBroker:
    """Short-lived in-process exact review. Passphrase + local OS custody, no remote API."""
    def __init__(self,transaction,credentials, *, clock=time.time):
        self.tx=transaction; self.credentials=credentials; self.clock=clock; self.pending={}

    def review(self,instance,action,payload):
        require(action in ACTIONS and type(payload) is dict,'invalid_human_action'); ident(instance)
        snapshot=status(self.tx,clock=self.clock)
        require(instance in snapshot['instances'],'unfunded_instance')
        if action in ('save','activate'):
            require(set(payload)=={'version','policy'},'invalid_policy_request'); ident(payload['version']); validate(payload['policy'])
        current=snapshot['instances'][instance]
        preview=None
        if action=='activate':
            require(current['saved']==payload,'unsaved_policy')
            preview=ReserveManager(self.tx,clock=self.clock).preview(instance,payload['policy'])
            require(preview['journal_head']==snapshot['journal_head'],'review_changed')
        now=int(self.clock()); receipt=secrets.token_hex(16)
        review=dict(receipt=receipt,instance=instance,action=action,payload=copy.deepcopy(payload),
                    head=snapshot['journal_head'],boot=snapshot['boot'],expires=now+300,
                    current=current,preview=preview,confirmation='APPROVE '+receipt)
        self.pending={receipt:copy.deepcopy(review)}
        return review

    def confirm(self,receipt,confirmation,password):
        review=self.pending.pop(receipt,None); now=int(self.clock())
        require(review is not None and now<=review['expires'],'review_expired_or_consumed')
        require(hmac.compare_digest(confirmation,review['confirmation']),'confirmation_mismatch')
        key=unlock(self.credentials,password)
        snap=status(self.tx,clock=self.clock)
        require(review['action']=='revoke' or (snap['boot']==review['boot'] and snap['journal_head']==review['head']),'review_changed')
        expiry=review['expires']; instance=review['instance']; action=review['action']; payload=review['payload']
        message=canon(['maj-vaj:PAPER:reserve:v1',receipt,instance,action,payload,review['head'],expiry]).encode()
        grant={'head':review['head'],'expiry':expiry,'signature':hmac.new(key,message,hashlib.sha256).hexdigest()}
        return ReserveManager(self.tx,key,clock=self.clock).submit(receipt,instance,action,payload,grant)


def tick_all(transaction, *, clock=time.time):
    """Keyless worker; immutable per-policy/cycle receipt, no activation path."""
    snapshot=status(transaction,clock=clock); result={}
    for instance,r in sorted(snapshot['instances'].items()):
        if not r['active']:continue
        version=r['active']['version']; p=r['active']['policy']; cycle=(int(clock())-p['anchor'])//DAY
        if cycle<=r['last_cycle'] or r['last_cycle']>=(min(p['end'],p['expiry'])-p['anchor'])//DAY+1:continue
        receipt='cycle:'+sha([instance,version,cycle])
        result[instance]=ReserveManager(transaction,clock=clock).submit(receipt,instance,'tick',{})
    return result


def run_worker(directory):
    p=private_directory(directory)
    with os.fdopen(private_open(p/'reserve-scheduler.lock',os.O_CREAT|os.O_RDWR),'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return tick_all(desk_transaction(p))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data_dir',type=Path); parser.add_argument('command',choices=['init','review','status','tick'])
    parser.add_argument('--credentials',type=Path); parser.add_argument('--instance'); parser.add_argument('--action',choices=sorted(ACTIONS)); parser.add_argument('--payload',type=Path)
    args=parser.parse_args()
    if args.command=='tick':
        require(args.credentials is None,'worker_must_not_have_credentials'); result=run_worker(args.data_dir)
    elif args.command=='status':result=status(desk_transaction(args.data_dir))
    else:
        require(sys.stdin.isatty() and sys.stdout.isatty(),'interactive_operator_terminal_required')
        require(args.credentials is not None,'private_approval_directory_required')
        require(args.credentials.resolve()!=args.data_dir.resolve(),'separate_approval_directory_required')
        if args.command=='init':
            p=getpass.getpass('New approval passphrase (at least 16 characters): ')
            require(p==getpass.getpass('Repeat approval passphrase: '),'passphrase_mismatch')
            provision(args.credentials,p); result={'initialized':True}
        else:
            require(args.instance and args.action and args.payload,'exact_review_required')
            with args.payload.open() as f: payload=json.loads(f.read(32768))
            broker=ApprovalBroker(desk_transaction(args.data_dir),args.credentials)
            review=broker.review(args.instance,args.action,payload)
            print(json.dumps(review,indent=2)); confirmation=input('Type the exact confirmation shown (anything else cancels): ')
            result=broker.confirm(review['receipt'],confirmation,getpass.getpass('Approval passphrase: '))
    print(canon(result))


if __name__=='__main__':
    try:main()
    except (AccountingError,OSError,ValueError,KeyError,sqlite3.Error) as exc:
        print('Reserve operation refused: '+type(exc).__name__+': '+str(exc),file=sys.stderr); sys.exit(1)
