"""Use real SQLite and real OS identities, never an actual trading desk."""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from reserve_checkpoint import AuthorityStore, Checkpoint, CheckpointError, GENESIS

AUTHORITY = 65533
DESK = 65534
ROOT = Path(__file__).resolve().parents[1]


def db():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE fin_events(seq INTEGER PRIMARY KEY,kind TEXT,digest TEXT)')
    conn.execute('BEGIN IMMEDIATE')
    return conn


def event(conn, seq, kind='reserve_adopt'):
    digest = hashlib.sha256(f'{seq}:{kind}'.encode()).hexdigest()
    conn.execute('INSERT INTO fin_events VALUES(?,?,?)', (seq, kind, digest))
    return Checkpoint(seq, digest)


@unittest.skipUnless(os.geteuid() == 0 and shutil.which('setpriv'), 'root and setpriv required for genuine independent UIDs')
class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='reserve-checkpoint-', dir='/run'))
        self.root.chmod(0o755)
        self.addCleanup(shutil.rmtree, self.root)
        self.store = self.root / 'witness'; self.store.mkdir(mode=0o755)
        os.chown(self.store, AUTHORITY, AUTHORITY)
        self.creds = self.root / 'approval'; self.creds.mkdir(mode=0o700)
        os.chown(self.creds, AUTHORITY, AUTHORITY)
        secret = self.creds / 'fixture'; secret.write_bytes(b'opaque fixture only'); secret.chmod(0o600)
        os.chown(secret, AUTHORITY, AUTHORITY)
        shutil.copyfile(ROOT/'reserve_checkpoint.py', self.root/'reserve_checkpoint.py')
        shutil.copyfile(__file__, self.root/'fixture.py')
        self.run_as(AUTHORITY, 's.initialize(c)')

    def run_as(self, uid, script):
        prefix = (f'import sys;sys.path.insert(0,{str(self.root)!r});'
                  'from fixture import *; c=db();'
                  f's=AuthorityStore({str(self.store)!r},authority_uid={AUTHORITY},desk_uid={DESK});')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run(['setpriv', f'--reuid={uid}', f'--regid={uid}', '--clear-groups',
                                 sys.executable, '-c', prefix+script], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_separate_principal_read_only_and_private_credentials(self):
        self.assertEqual(self.run_as(DESK, 'print(s.verify(c)==GENESIS)'), 'True')
        self.assertEqual(self.run_as(AUTHORITY, f'print(Path({str(self.creds/"fixture")!r}).read_bytes()==b"opaque fixture only")'), 'True')
        paths = [self.creds/'fixture', self.store/'checkpoint.json', self.store/'writer.lock']
        for path in paths:
            flag = 'os.O_RDONLY' if path.parent == self.creds else 'os.O_WRONLY'
            code = f'try:\n os.open({str(path)!r},{flag})\nexcept PermissionError:\n print("denied")\nelse:\n raise AssertionError("unexpected access")'
            self.assertEqual(self.run_as(DESK, 'exec('+repr(code)+')'), 'denied')
        code = f'try:\n os.unlink({str(self.store/"checkpoint.json")!r})\nexcept PermissionError:\n print("denied")\nelse:\n raise AssertionError("unexpected unlink")'
        self.assertEqual(self.run_as(DESK, 'exec('+repr(code)+')'), 'denied')

    def test_rollback_adopt_revoke_and_tick(self):
        self.run_as(AUTHORITY, 'a=event(c,1);s.advance(c,GENESIS,a.digest)')
        self.assertEqual(self.run_as(DESK, 'a=event(c,1);print(s.verify(c)==a)'), 'True')
        self.run_as(AUTHORITY, 'a=event(c,1);b=event(c,2,"reserve_tick");s.advance(c,a,b.digest)')
        self.run_as(AUTHORITY, 'a=event(c,1);b=event(c,2,"reserve_tick");d=event(c,3,"reserve_revoke");s.advance(c,b,d.digest)')
        for code in ['s.verify(c)', 'event(c,1);s.verify(c)', 'event(c,1);event(c,2,"reserve_tick");s.verify(c)']:
            self.assertEqual(self.run_as(DESK, 'exec('+repr('try:\n '+code+'\nexcept CheckpointError:\n print("refused")\nelse:\n raise AssertionError("rollback accepted")')+')'), 'refused')
        self.assertEqual(self.run_as(DESK, 'event(c,1);event(c,2,"reserve_tick");d=event(c,3,"reserve_revoke");event(c,4,"mark");print(s.verify(c)==d)'), 'True')

    def test_unwitnessed_and_wrong_event(self):
        for code in ['event(c,1);s.verify(c)', 'a=event(c,1);s.advance(c,GENESIS,"f"*64)',
                     'a=event(c,1);b=event(c,2,"reserve_tick");s.advance(c,GENESIS,b.digest)']:
            self.assertEqual(self.run_as(AUTHORITY, 'exec('+repr('try:\n '+code+'\nexcept CheckpointError:\n print("refused")\nelse:\n raise AssertionError("unwitnessed advance")')+')'), 'refused')
        self.assertEqual(self.run_as(DESK, 'print(s.verify(c)==GENESIS)'), 'True')

    def test_failed_desk_commit_fails_closed_and_cas(self):
        self.run_as(AUTHORITY, 'a=event(c,1);s.advance(c,GENESIS,a.digest);c.rollback()')
        for code in ['s.verify(c)', 'a=event(c,1);s.advance(c,GENESIS,a.digest)']:
            self.assertEqual(self.run_as(AUTHORITY, 'exec('+repr('try:\n '+code+'\nexcept CheckpointError:\n print("refused")\nelse:\n raise AssertionError("unexpected acceptance")')+')'), 'refused')

    def test_missing_or_symlink_never_defaults_to_genesis(self):
        path = self.store/'checkpoint.json'; path.unlink()
        for symlink in (False, True):
            if symlink: path.symlink_to('/dev/null')
            self.assertEqual(self.run_as(DESK, 'exec('+repr('try:\n s.verify(c)\nexcept (OSError,CheckpointError):\n print("refused")\nelse:\n raise AssertionError("unexpected acceptance")')+')'), 'refused')

    def test_principal_and_root_ancestry(self):
        with self.assertRaises(CheckpointError):
            AuthorityStore(self.store, authority_uid=DESK, desk_uid=DESK)
        self.root.chmod(0o777)
        self.assertEqual(self.run_as(DESK, 'exec('+repr('try:\n s.verify(c)\nexcept CheckpointError:\n print("refused")\nelse:\n raise AssertionError("unexpected acceptance")')+')'), 'refused')


if __name__ == '__main__':
    unittest.main()
