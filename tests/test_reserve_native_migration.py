"""Real Linux UID + SQLite boundary tests; mocked ledger replay, not Freqtrade."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from reserve_checkpoint import AuthorityStore, Checkpoint, GENESIS
from reserve_native_migration import stage_pristine, digest
from reserve_native_handoff import issue_reviewed

ROOT=Path(__file__).resolve().parents[1]
AUTH,DESK,NATIVE=65533,65534,65532
INSTANCE='a'*32
SCOPE={'instance':INSTANCE,'pair':'BTC/USD','principal_cents':10000,'mode':'PAPER'}


def identity(db):
    # Explicit fixture; real journal replay is a separate native gate.
    count,head,boot=db.execute('SELECT count,digest,boot FROM fin_head WHERE id=1').fetchone()
    return {'count':count,'head':head,'boot':boot,'anchor':'f'*64}


def sql(path, commands):
    with closing(sqlite3.connect(path)) as c:
        with c:c.executescript(commands)
    os.chmod(path,0o600)


@unittest.skipUnless(os.geteuid()==0 and shutil.which('setpriv'),'actual separate UIDs required')
class NativeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='native-migration-',dir='/run'));self.root.chmod(0o755)
        self.addCleanup(shutil.rmtree,self.root)
        self.export=self.root/'export';self.export.mkdir(mode=0o700)
        self.inbox=self.root/'inbox';self.inbox.mkdir();os.chown(self.inbox,0,NATIVE);self.inbox.chmod(0o750)
        self.witness=self.root/'witness';self.witness.mkdir();self.witness.chmod(0o755);os.chown(self.witness,AUTH,AUTH)
        sql(self.export/'paper.sqlite','CREATE TABLE trades(id INTEGER);CREATE TABLE orders(id INTEGER);')
        sql(self.export/'desk.sqlite3',"CREATE TABLE fin_events(seq INTEGER PRIMARY KEY,kind TEXT,digest TEXT);CREATE TABLE fin_head(id INTEGER PRIMARY KEY,count INTEGER,digest TEXT,boot INTEGER);INSERT INTO fin_head VALUES(1,0,'"+'0'*64+"',1);CREATE TABLE desk(id INTEGER,paused INTEGER,human_pause INTEGER);INSERT INTO desk VALUES(1,1,1);")
        self.seed=os.urandom(32);self.pin=self.root/'relay.pub';self.pin.write_bytes(Ed25519PrivateKey.from_private_bytes(self.seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw));self.pin.chmod(0o644)
        for name in ('reserve_checkpoint.py','reserve_ipc.py','reserve_native_migration.py','reserve_native_handoff.py'):
            shutil.copyfile(ROOT/name,self.root/name)
        shutil.copyfile(__file__,self.root/'fixture.py')
        self.prefix=(f'import sys;sys.path.insert(0,{str(self.root)!r});from fixture import *;'
                     'from reserve_native_handoff import open_reviewed;'
                     f'r=Path({str(self.root)!r});s=AuthorityStore(r/"witness",authority_uid={AUTH},desk_uid={DESK});')
        self.run_as(AUTH,'c=sqlite3.connect(":memory:");c.execute("CREATE TABLE fin_events(seq INTEGER,kind TEXT,digest TEXT)");c.execute("BEGIN");s.initialize(c)')
        self.store=AuthorityStore(self.witness,authority_uid=AUTH,desk_uid=DESK)

    def review(self):return {'scope':SCOPE,'native_sha256':digest((self.export/'paper.sqlite').read_bytes()),'desk_sha256':digest((self.export/'desk.sqlite3').read_bytes())}
    def run_as(self,uid,code):
        r=subprocess.run(['setpriv',f'--reuid={uid}',f'--regid={uid}','--clear-groups',sys.executable,'-B','-c',self.prefix+code],capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);return r.stdout.strip()
    def refusal(self,uid,code):
        text='try:\n '+code+'\nexcept (OSError,ValueError,sqlite3.Error):\n print("refused")\nelse:\n raise AssertionError("unexpected success")'
        self.assertEqual(self.run_as(uid,'exec('+repr(text)+')'),'refused')
    def stage(self,review=None):
        with patch('reserve_native_migration.pristine_ledger',lambda db,scope:{'fixture_only':True}):
            return stage_pristine(self.export,self.root/'native-data',review or self.review(),native_uid=NATIVE,desk_uid=DESK,authority_uid=AUTH)
    def publish(self):
        with patch('reserve_native_handoff.state_identity',identity):
            return issue_reviewed(self.export,self.review()['desk_sha256'],self.inbox,'fixture',self.store,self.seed,native_uid=NATIVE,native_gid=NATIVE,clock=lambda:100)
    def open_code(self,workspace='fixture',at=100,body='print(db.execute("SELECT boot FROM fin_head").fetchone()[0])'):
        code=f'with patch("reserve_native_handoff.state_identity",identity),open_reviewed(r/"inbox",{workspace!r},s,r/"relay.pub",native_uid={NATIVE},native_gid={NATIVE},desk_uid={DESK},authority_uid={AUTH},clock=lambda:{at}) as (db,cp):\n {body}'
        return 'exec('+repr(code)+')'
    def test_stage_pristine_rotates_key_and_preserves_export(self):
        before=self.review();result=self.stage();self.assertEqual(result['state'],'staged_not_activated');self.assertEqual(before,self.review())
        work=f'r/"native-data"/"paper"/{INSTANCE!r}'
        self.assertEqual(self.run_as(NATIVE,f'print(len(({work}/"receipt.key").read_bytes()))'),'32')
        self.refusal(DESK,f'({work}/"receipt.key").read_bytes()');self.refusal(AUTH,f'({work}/"paper.sqlite").read_bytes()')
        self.assertEqual(digest((self.root/'native-data'/'paper'/INSTANCE/'paper.sqlite').read_bytes()),before['native_sha256'])
        with self.assertRaises(ValueError):self.stage()
    def test_refuses_nonpristine_or_changed_export(self):
        before=self.review();sql(self.export/'paper.sqlite','INSERT INTO trades VALUES(1);')
        for review in (before,self.review()):
            with self.assertRaises(ValueError):self.stage(review)
        self.assertFalse((self.root/'native-data').exists())
    def test_signed_snapshot_native_only_and_query_only(self):
        self.publish();self.assertEqual(self.run_as(NATIVE,self.open_code()),'1')
        for uid in (DESK,AUTH):self.refusal(uid,'(r/"inbox"/"snapshot.bin").read_bytes()')
        self.refusal(NATIVE,'(r/"inbox"/"snapshot.bin").write_bytes(b"x")')
        self.refusal(NATIVE,self.open_code(body='db.execute("UPDATE fin_head SET boot=2")'))
    def test_snapshot_wrong_scope_expiry_tamper(self):
        self.publish();self.refusal(NATIVE,self.open_code(workspace='other'));self.refusal(NATIVE,self.open_code(at=131))
        p=self.inbox/'snapshot.bin';raw=bytearray(p.read_bytes());raw[-1]^=1;p.write_bytes(raw)
        self.refusal(NATIVE,self.open_code())
    def test_snapshot_checkpoint_change(self):
        self.publish();self.run_as(AUTH,'c=sqlite3.connect(":memory:");c.execute("CREATE TABLE fin_events(seq INTEGER,kind TEXT,digest TEXT)");c.execute("BEGIN");c.execute("INSERT INTO fin_events VALUES(1,\'reserve_adopt\',?)",("a"*64,));s.advance(c,GENESIS,"a"*64)')
        self.refusal(NATIVE,self.open_code())

if __name__=='__main__':unittest.main()
