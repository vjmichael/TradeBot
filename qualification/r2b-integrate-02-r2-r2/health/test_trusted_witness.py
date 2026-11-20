import json, multiprocessing, os, pwd, socket, tempfile, time, unittest
from pathlib import Path
from trusted_witness import Source, inspect_one, collect

UID=pwd.getpwnam('oai').pw_uid
GID=pwd.getpwnam('oai').pw_gid

def serve(path, data, ready):
    try:
        os.setgroups([]);os.setgid(GID);os.setuid(UID)
        s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        s.bind(path);os.chmod(path,0o600);s.listen(1);ready.send('ready')
        c,_=s.accept();c.settimeout(2)
        c.recv(128);c.sendall(data);c.close();s.close()
    except BaseException as e:
        try:ready.send('error:'+type(e).__name__)
        except Exception:pass

class TestTrusted(unittest.TestCase):
    def setUp(self):
        self.path=Path(tempfile.mkdtemp(prefix='majvaj-witness-',dir='/run'))
        os.chown(self.path,UID,GID);os.chmod(self.path,0o700)
        self.src=Source('vault',str(self.path/'vault.sock'),UID,UID)
        self.now=time.monotonic()
    def tearDown(self):
        for f in self.path.iterdir():f.unlink()
        self.path.rmdir()
    def response(self, **replace):
        o={'version':1,'service':'vault','state':'ready','observed_monotonic':self.now}
        o.update(replace)
        return (json.dumps(o,separators=(',',':'))+'\n').encode()
    def call(self, data=None,src=None):
        parent,child=multiprocessing.Pipe(duplex=False)
        p=multiprocessing.Process(target=serve,args=(str(self.path/'vault.sock'),data if data is not None else self.response(),child))
        p.start(); self.assertTrue(parent.poll(5),'server startup timed out');self.assertEqual(parent.recv(),'ready')
        result=inspect_one(src or self.src,lambda:self.now)
        p.join(0.15)
        if p.is_alive():p.terminate();p.join(2)
        self.assertFalse(p.is_alive());return result
    def test_ready(self):self.assertEqual(self.call().state,'ready')
    def test_failed(self):self.assertEqual(self.call(self.response(state='failed')).state,'failed')
    def test_unknown(self):self.assertEqual(self.call(self.response(state='unknown')).state,'unknown')
    def test_wrong_service(self):self.assertIsNone(self.call(self.response(service='data')))
    def test_wrong_uid(self):self.assertIsNone(self.call(src=Source('vault',self.src.socket_path,UID+3,UID)))
    def test_wrong_dir_owner(self):self.assertIsNone(self.call(src=Source('vault',self.src.socket_path,UID,0)))
    def test_stale(self):self.assertIsNone(self.call(self.response(observed_monotonic=self.now-55)))
    def test_future(self):self.assertIsNone(self.call(self.response(observed_monotonic=self.now+2)))
    def test_bad_state(self):self.assertIsNone(self.call(self.response(state='tradable')))
    def test_duplicate_json(self):self.assertIsNone(self.call(b'{"version":1,"version":1}\n'))
    def test_oversize(self):self.assertIsNone(self.call(b'x'*513+b'\n'))
    def test_missing(self):self.assertIsNone(inspect_one(self.src))
    def test_expected_directory_mode(self):
        os.chmod(self.path,0o777)
        self.assertIsNone(inspect_one(self.src))
    def test_malformed(self):self.assertIsNone(self.call(b'not-json\n'))
    def test_invalid_spec(self):self.assertIsNone(inspect_one(Source('vault','/tmp/test',UID)))
    def test_collect_unavailable(self):self.assertEqual(collect({'vault':self.src}),{})

if __name__=='__main__':unittest.main()