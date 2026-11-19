"""Native runner-only systemd service qualification; no external deployment.

Install the shipped unit temporarily outside the runner home, with only
identity and installation paths substituted. Exercise genuine public market
updates, service-main death, restart and durable opt-out without dashboard
custody. Never use trading credentials or a persistent VPS.
"""
import pwd
import grp
import os
import pathlib
import shutil
import subprocess
import sys
import time
from uuid import uuid4
from contextlib import closing

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from data_scheduler import Schedule
from data_preparation import _db
from market_memory import MarketMemory
from market_stream import StreamJournal
from tradebot import create_app

TOKEN='native-service-qualification-test-only-0123456789'
AUTH={'Authorization':'Bearer '+TOKEN}


def call(*args):return subprocess.run(args,check=True,text=True,capture_output=True,timeout=45).stdout.strip()


def wait(predicate,seconds,label):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        result=predicate()
        if result:return result
        time.sleep(1)
    raise RuntimeError('native service qualification timed out: '+label)


def main():
    if pathlib.Path('/proc/1/comm').read_text().strip()!='systemd' or os.getuid()==0:
        raise RuntimeError('requires non-root native Linux runner with systemd and scoped sudo')
    token=uuid4().hex;unit='maj-vaj-qualification-'+token+'.service';base=pathlib.Path('/srv')/unit.removesuffix('.service')
    user=pwd.getpwuid(os.getuid()).pw_name;group=grp.getgrgid(os.getgid()).gr_name
    call('sudo','-n','install','-d','-m','0700','-o',str(os.getuid()),'-g',str(os.getgid()),str(base))
    app=base/'app';state=base/'state';directory=state/'data';unit_path=pathlib.Path('/run/systemd/system')/unit
    installed=False;s=None
    try:
        app.mkdir(mode=0o700);state.mkdir(mode=0o700);directory.mkdir(mode=0o700)
        for p in ROOT.glob('*.py'):shutil.copy2(p,app/p.name)
        shutil.copytree(ROOT/'scripts',app/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        original=(ROOT/'deploy/maj-vaj-data.service').read_text()
        # Restrict staged code to read-only through the same unit hardening.
        text=original.replace('User=maj-vaj','User='+user).replace('Group=maj-vaj','Group='+group).replace('/opt/maj-vaj/venv/bin/python',sys.executable).replace('/opt/maj-vaj',str(app)).replace('/var/lib/maj-vaj/data',str(directory)).replace('/var/lib/maj-vaj',str(state))
        candidate=base/unit;candidate.write_text(text)
        s=Schedule(directory);m=MarketMemory(directory);journal=StreamJournal(m)
        assert not s.status()['enabled']
        call('sudo','-n','install','-m','0644',str(candidate),str(unit_path));installed=True
        call('sudo','-n','systemctl','daemon-reload');call('sudo','-n','systemctl','start',unit)
        def pid():return int(call('systemctl','show','--property=MainPID','--value',unit))
        wait(lambda:s.public_status()['service']=='running',30,'service heartbeat')
        assert s.controller.status()['state']=='idle' and not s.status()['enabled']
        with TestClient(create_app(directory,TOKEN),base_url='http://localhost') as c:
            r=c.post('/api/data-schedule',headers=AUTH,json={'enabled':True});assert r.status_code==200,r.text
        def observed(exclude=None):
            j=s.controller.status()
            if j['state']=='running' and j['mode']=='collect' and j['id']!=exclude and all(r['market_update_fresh'] and r['last_update']>=j['started'] for r in journal.status()):return j
        first=wait(observed,90,'public data without desk running');old=pid();assert old>0
        print('RECEIPT: installed qualification unit obeyed default OFF and explicit graphical schedule, actual all-market updates after dashboard shutdown',flush=True)
        call('sudo','-n','systemctl','kill','--kill-whom=main','--signal=SIGKILL',unit)
        wait(lambda:pid()>0 and pid()!=old and s.public_status()['service']=='running',40,'systemd restart')
        second=wait(lambda:observed(first['id']),150,'automatic collection resumption')
        with closing(_db(s.root)) as db:
            assert db.execute('SELECT state FROM jobs WHERE id=?',(first['id'],)).fetchone()[0]=='interrupted'
            assert db.execute("SELECT count(*) FROM jobs WHERE state='running'").fetchone()[0]==1
        assert s.status()['enabled'] and second['id']!=first['id']
        with TestClient(create_app(directory,TOKEN),base_url='http://localhost') as c:
            r=c.post('/api/data-schedule',headers=AUTH,json={'enabled':False});assert r.status_code==200,r.text
        wait(lambda:s.controller.status()['state']=='cancelled',30,'operator opt-out')
        assert not s.status()['enabled']
        print('RECEIPT: systemd killed/restarted scheduler main; old job interrupted, one new genuine collector automatically resumed, persisted opt-out stopped it',flush=True)
    finally:
        if s:s.close()
        if installed:
            subprocess.run(['sudo','-n','systemctl','stop',unit],check=False,timeout=45)
            call('sudo','-n','rm','-f','--',str(unit_path));call('sudo','-n','systemctl','daemon-reload')
            subprocess.run(['sudo','-n','systemctl','reset-failed',unit],check=False,timeout=30)
        for p in base.iterdir():
            if p.is_dir() and not p.is_symlink():shutil.rmtree(p)
            else:p.unlink()
        call('sudo','-n','rmdir','--',str(base))

if __name__=='__main__':main()
