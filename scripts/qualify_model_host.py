"""Host qualification of a pinned native FreqAI offline worker.

Use the image's supported UID/GID, not the unrelated CI host account.
Only privately staged, independently verified data and output are mounted.
Ownership changes are limited to those disposable staging trees.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import IMAGE,PLATFORM
from qualify_numerical import main as qualify
from numerical_dataset import verify

ENGINE_UID=1000
ENGINE_GID=1000
ENTRYPOINT='/usr/local/bin/python'
RUNTIME_PROBE="""import os, site, sys
from pathlib import Path
assert (os.getuid(),os.getgid())==(1000,1000)
assert Path.home()==Path('/home/ftuser')
import httpx, freqtrade, lightgbm, numpy, pandas, sklearn
from freqtrade.freqai.prediction_models.LightGBMClassifier import LightGBMClassifier
from freqtrade.freqai.prediction_models.LightGBMRegressor import LightGBMRegressor
from freqtrade.freqai.data_kitchen import FreqaiDataKitchen
assert freqtrade.__version__=='2026.10'
print('RECEIPT: native ftuser offline runtime imports',os.getuid(),os.getgid(),sys.version.split()[0],httpx.__version__,freqtrade.__version__,lightgbm.__version__,site.getusersitepackages(),flush=True)
"""

def command(name):
    return ['docker','run','--rm','--name',name,'--pull=never','--platform',PLATFORM,
            '--network','none','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=512m','--cpus','2','--memory','3g','--pids-limit','256',
            '--user',f'{ENGINE_UID}:{ENGINE_GID}','--env','HOME=/home/ftuser','--env','PYTHONDONTWRITEBYTECODE=1',
            '--env','OMP_NUM_THREADS=2','--env','OPENBLAS_NUM_THREADS=1']

def invoke(args,name,timeout):
    try:subprocess.run(args,check=True,timeout=timeout)
    finally:subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=False)

def probe():
    name='tb06-probe-'+uuid4().hex
    source=Path(__file__).resolve().parents[1]
    code=RUNTIME_PROBE+"\nsys.path.insert(0,'/source')\nfrom numerical_models import runtime\nprint('RECEIPT: repository numerical runtime',runtime(),flush=True)\n"
    invoke(command(name)+['--mount',f'type=bind,source={source},target=/source,readonly','--entrypoint',ENTRYPOINT,IMAGE,'-c',code],name,120)

def ownership(root,uid,gid):
    # The caller only passes the dedicated mkdtemp staging root, never source
    # data or the checkout. Non-root CI uses non-interactive scoped chown.
    prefix=[] if os.getuid()==0 else ['sudo','-n']
    subprocess.run(prefix+['chown','-hR','--',f'{uid}:{gid}',str(root)],check=True,timeout=60)

def worker(out,sha):
    source=Path(__file__).resolve().parents[1]
    manifest=verify(out,sha)
    host_uid,host_gid=os.getuid(),os.getgid()
    # Keep the outer root under the calling user. Bind-mount only children;
    # the container never sees host parents, original dataset or snapshot.
    with tempfile.TemporaryDirectory(prefix='tb06-worker-',dir=out.parent) as tmp:
        stage=Path(tmp)
        data=stage/'input';dest=stage/'output'
        shutil.copytree(out,data)
        dest.mkdir(mode=0o700)
        if verify(data,sha)!=manifest:raise RuntimeError('staged dataset differs')
        name='tb06-'+uuid4().hex
        try:
            if (host_uid,host_gid)!=(ENGINE_UID,ENGINE_GID):
                ownership(data,ENGINE_UID,ENGINE_GID)
                ownership(dest,ENGINE_UID,ENGINE_GID)
            args=command(name)+['--mount',f'type=bind,source={source},target=/source,readonly',
                 '--mount',f'type=bind,source={data},target=/input,readonly',
                 '--mount',f'type=bind,source={dest},target=/output',
                 '--entrypoint',ENTRYPOINT,IMAGE,'/source/scripts/qualify_numerical_models.py','/input',sha,'/output']
            invoke(args,name,600)
        finally:
            if (host_uid,host_gid)!=(ENGINE_UID,ENGINE_GID):
                ownership(data,host_uid,host_gid)
                ownership(dest,host_uid,host_gid)
        if verify(data,sha)!=manifest or verify(out,sha)!=manifest:raise RuntimeError('dataset changed during native qualification')
        print('RECEIPT: original and staged datasets unchanged after offline ftuser qualification',sha,flush=True)
if __name__=='__main__':
    probe()
    if sys.argv[1:]==['--probe-only']:sys.exit(0)
    if len(sys.argv)!=1:raise SystemExit('unsupported qualification arguments')
    qualify(after=worker)
