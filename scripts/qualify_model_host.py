"""Prepare real dataset on host, then run pinned offline FreqAI fit worker."""
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import IMAGE,PLATFORM
from qualify_numerical import main as qualify
from uuid import uuid4

def worker(out,sha):
    name='tb06-'+uuid4().hex
    root=out.parent;dest=root/'models';dest.mkdir(mode=0o700)
    source=Path(__file__).resolve().parents[1]
    args=['docker','run','--rm','--name',name,'--pull=never','--platform',PLATFORM,
          '--network','none','--cap-drop','ALL','--security-opt','no-new-privileges',
          '--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=512m','--cpus','2','--memory','3g','--pids-limit','256',
          '--user',f'{os.getuid()}:{os.getgid()}','--env','HOME=/home/ftuser','--env','PYTHONDONTWRITEBYTECODE=1',
          '--env','OMP_NUM_THREADS=2','--env','OPENBLAS_NUM_THREADS=1',
          '--mount',f'type=bind,source={source},target=/source,readonly',
          '--mount',f'type=bind,source={out},target=/input,readonly',
          '--mount',f'type=bind,source={dest},target=/output',
          '--entrypoint','/usr/local/bin/python',IMAGE,'/source/scripts/qualify_numerical_models.py','/input',sha,'/output']
    try:subprocess.run(args,check=True,timeout=600)
    finally:subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=False)
if __name__=='__main__':qualify(after=worker)
