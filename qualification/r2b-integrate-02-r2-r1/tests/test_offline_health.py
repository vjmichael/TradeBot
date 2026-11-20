import subprocess
import unittest
from types import SimpleNamespace
from offline_health import LocalHealth, probe_local, UNITS

class Tests(unittest.TestCase):
    def test_healthy_local_not_internet_claim(self):
        x=probe_local(lambda *a,**kw:SimpleNamespace(returncode=0,stdout='active\n'))
        self.assertEqual(x.summary(),'Local services running')
        self.assertEqual(x.internet,'unknown')
    def test_failing(self):
        x=probe_local(lambda args,**kw:SimpleNamespace(returncode=0,stdout='failed\n' if 'vault' in args[2] else 'active\n'))
        self.assertEqual(x.summary(),'Services need attention')
    def test_missing(self):
        x=probe_local(lambda *a,**kw:SimpleNamespace(returncode=1,stdout='secret PRIVATE\n'))
        self.assertEqual(x.summary(),'Checking services')
        self.assertNotIn('secret',repr(x))
    def test_timeout_no_crash(self):
        def timeout(*a,**kw):raise subprocess.TimeoutExpired(cmd='systemctl',timeout=1)
        self.assertEqual(probe_local(timeout).summary(),'Checking services')
    def test_no_network_commands(self):
        calls=[]
        def collect(args,**kw): calls.append((args,kw));return SimpleNamespace(returncode=0,stdout='active\n')
        probe_local(collect)
        self.assertEqual(len(calls),len(UNITS)+1)
        self.assertTrue(all(c[1]['timeout']==1 for c in calls))
        self.assertEqual(calls[-1][0],['/usr/bin/ip','route','get','1.1.1.1'])
    def test_kernel_no_route(self):
        def fake(args, **kw):
            return SimpleNamespace(returncode=1 if args[0]=='/usr/bin/ip' else 0, stdout='active\n')
        x=probe_local(fake)
        self.assertEqual(x.internet,'no_network_route')
        self.assertEqual(x.summary(),'Local services running')
    def test_invalid_stdout_never_green(self):
        self.assertEqual(probe_local(lambda *a,**kw:SimpleNamespace(returncode=0,stdout='READY\n')).summary(),'Checking services')
    def test_local_info_no_false_global(self):
        x=LocalHealth({'vault':'active','backend':'active'},internet='unknown')
        self.assertNotEqual(x.summary(),'Services available')

if __name__=='__main__': unittest.main()