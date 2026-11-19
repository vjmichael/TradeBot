"""Worker orchestration regression tests; no Docker or network in unit suite."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import qualify_model_host as host

class ModelWorkerTests(unittest.TestCase):
    def test_identity_and_isolation(self):
        args=host.command('test')
        for key,value in (('--user','1000:1000'),('--network','none'),('--memory','3g')):
            self.assertEqual(args[args.index(key)+1],value)
        self.assertIn('--pull=never',args)
        self.assertIn('--read-only',args)
        self.assertNotIn('--privileged',args)
        self.assertIn('HOME=/home/ftuser',args)
    def test_probe_executes_before_collection(self):
        with patch.object(host,'invoke') as run:
            host.probe()
        args=run.call_args.args[0]
        self.assertIn('1000:1000',args)
        self.assertIn(host.IMAGE,args)
        self.assertIn('import httpx',args[-1])
        self.assertIn('from numerical_models import runtime',args[-1])
    def test_staged_copy_and_cleanup_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'dataset';out.mkdir(mode=0o700)
            (out/'manifest.json').write_text('receipt')
            seen=[]
            def own(path,uid,gid):seen.append((path,uid,gid))
            def fail(args,name,timeout):
                self.assertIn('1000:1000',args)
                mounts=[args[i+1] for i,v in enumerate(args) if v=='--mount']
                self.assertFalse(any(f'source={out},' in m for m in mounts))
                self.assertTrue(any(m.endswith('target=/input,readonly') for m in mounts))
                self.assertTrue(any(m.endswith('target=/output') for m in mounts))
                raise RuntimeError('native failed')
            with patch.object(host,'verify',return_value={'test':'receipt'}),patch.object(host.os,'getuid',return_value=1001),patch.object(host.os,'getgid',return_value=1001),patch.object(host,'ownership',side_effect=own),patch.object(host,'invoke',side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,'native failed'):host.worker(out,'a'*64)
            self.assertEqual([(p.name,u,g) for p,u,g in seen],[('input',1000,1000),('output',1000,1000),('input',1001,1001),('output',1001,1001)])
            self.assertTrue(all(p!=out for p,_,_ in seen))
            self.assertEqual((out/'manifest.json').read_text(),'receipt')
            self.assertEqual(list(Path(tmp).iterdir()),[out])
