"""Staged local PAPER credential custody. No venue or plaintext-read interface."""
import argparse
from contextlib import contextmanager
import getpass
import json
import os
from pathlib import Path
import pwd
import grp
import resource
import socket
import socketserver
import sqlite3
import stat
import struct
import sys
import threading
import warnings
import ctypes

from cryptography.fernet import Fernet
from credential_input import CredentialSetup

SLOTS = frozenset(CredentialSetup.model_fields)
PAIR = frozenset(('kraken_read_key', 'kraken_read_secret'))
STATE = Path('/var/lib/maj-vaj-paper-vault')
SOCKET = Path('/run/maj-vaj-paper-vault/enroll.sock')
STATUS_SOCKET = Path('/run/maj-vaj-paper-vault-status/status.sock')
MAX_MESSAGE = 65536
DOMAIN = b'maj-vaj:PAPER:credential-vault:v1\0'
UNAVAILABLE = {'ok': False, 'reason': 'credential_storage_unavailable'}
REFUSED = {'ok': False, 'reason': 'credential_request_refused'}


def _private_dir(path):
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise PermissionError('private vault directory required')


def _private_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077 or s.st_nlink != 1:
            raise PermissionError('private vault file required')
    finally:
        os.close(fd)


def load_service_credential(name):
    """Read only from systemd's private credential mount, never secret environment values."""
    units = {'venue-vault-key': 'maj-vaj-paper-vault.service', 'dashboard-token': 'maj-vaj-paper-dashboard.service', 'provider-tag-key': 'maj-vaj-paper-vault.service'}
    if name not in units:
        raise PermissionError('systemd credential required')
    base = Path('/run/credentials') / units[name]
    if os.environ.get('CREDENTIALS_DIRECTORY') != str(base):
        raise PermissionError('systemd credential required')
    directory = base.lstat()
    # systemd 257 uses root:root 0550/0440 with a per-UID ACL; older
    # installations may use owner-only modes. Never accept writable or public files.
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid not in (0, os.getuid())
            or stat.S_IMODE(directory.st_mode) not in (0o500, 0o550)
            or (directory.st_mode & 0o050 and (directory.st_uid, directory.st_gid) != (0, 0))):
        raise PermissionError('systemd credential directory refused')
    fd = os.open(base / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        if (not stat.S_ISREG(s.st_mode) or s.st_uid not in (0, os.getuid())
                or stat.S_IMODE(s.st_mode) not in (0o400, 0o440)
                or (s.st_mode & 0o040 and (s.st_uid, s.st_gid) != (0, 0))
                or not 1 <= s.st_size <= 4096):
            raise PermissionError('systemd credential refused')
        return f.read(4097).rstrip(b'\n')



def _validate_slots(slots):
    if not slots or not slots <= SLOTS or (slots & PAIR and not PAIR <= slots):
        raise ValueError('credential selection refused')


class PaperVault:
    def __init__(self, directory, key):
        self.directory = Path(directory)
        _private_dir(self.directory)
        self.path = self.directory / 'vault.sqlite3'
        self.cipher = Fernet(key)
        # Dedicated service umask is 0077. Exclusive creation avoids following links.
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            created = False
        else:
            os.close(fd)
            created = True
        with self.transaction() as db:
            if created:
                db.execute('CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK(id=1), verifier BLOB NOT NULL)')
                db.execute('CREATE TABLE secrets (slot TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)')
                db.execute('INSERT INTO meta VALUES(1,?)', (self.cipher.encrypt(DOMAIN+b'verifier'),))
            self._verify(db)

    @contextmanager
    def transaction(self):
        _private_dir(self.directory)
        _private_file(self.path)
        # No workspace DB, no plaintext credential row, no general backup route.
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA secure_delete=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _verify(self, db):
        row = db.execute('SELECT verifier FROM meta WHERE id=1').fetchone()
        if row is None or self.cipher.decrypt(row[0]) != DOMAIN+b'verifier':
            raise ValueError('vault verification refused')
        slots = set()
        for slot, blob in db.execute('SELECT slot,ciphertext FROM secrets'):
            if slot not in SLOTS or not self.cipher.decrypt(blob).startswith(DOMAIN+slot.encode()+b'\0'):
                raise ValueError('vault verification refused')
            slots.add(slot)
        if slots & PAIR and not PAIR <= slots:
            raise ValueError('incomplete credential pair')
        return slots

    def _status(self, db):
        slots = self._verify(db)
        return {'ok': True, 'credentials': {slot: 'stored_unverified' if slot in slots else 'not_configured' for slot in sorted(SLOTS)}, 'connections_verified': False, 'deployment_ready': False}

    def status(self):
        with self.transaction() as db:
            return self._status(db)

    def enroll(self, values):
        _validate_slots(set(values))
        if any(type(v) is not str or not 0 < len(v) <= 8192 or any(c.isspace() or ord(c)<33 or ord(c)==127 for c in v) for v in values.values()):
            raise ValueError('credential value refused')
        sealed = [(slot, self.cipher.encrypt(DOMAIN+slot.encode()+b'\0'+v.encode())) for slot,v in values.items()]
        with self.transaction() as db:
            self._verify(db)
            db.executemany('INSERT INTO secrets VALUES (?,?) ON CONFLICT(slot) DO UPDATE SET ciphertext=excluded.ciphertext', sealed)
            return self._status(db)

    def remove(self, slots):
        _validate_slots(slots)
        with self.transaction() as db:
            self._verify(db)
            db.executemany('DELETE FROM secrets WHERE slot=?', [(slot,) for slot in slots])
            return self._status(db)


def dispatch(vault, request):
    if type(request) is not dict:
        return REFUSED.copy()
    op = request.get('op')
    try:
        if op == 'status' and set(request) == {'op'}:
            return vault.status()
        if op == 'enroll' and set(request) == {'op', 'values'}:
            model = CredentialSetup.model_validate(request['values'])
            values = model.vault_values()
            _validate_slots(set(values))
            return vault.enroll(values)
        if op == 'remove' and set(request) == {'op', 'slots'} and type(request['slots']) is list and all(type(s) is str for s in request['slots']):
            return vault.remove(set(request['slots']))
        return REFUSED.copy()
    except (ValueError, TypeError):
        return REFUSED.copy()
    except Exception:
        return UNAVAILABLE.copy()


def _peer_uid(sock):
    return struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            if _peer_uid(self.connection) != self.server.operator_uid:
                result = REFUSED
            else:
                raw = self.rfile.readline(MAX_MESSAGE+1)
                if not raw.endswith(b'\n') or len(raw) > MAX_MESSAGE:
                    result = REFUSED
                else:
                    try:
                        request = json.loads(raw)
                    except (ValueError, RecursionError):
                        result = REFUSED
                    else:
                        result = (REFUSED.copy() if self.server.read_only and request != {'op': 'status'} else dispatch(self.server.vault, request))
            self.wfile.write(json.dumps(result, separators=(',', ':')).encode()+b'\n')
        except (OSError, ValueError, RecursionError):
            pass


class Server(socketserver.UnixStreamServer):
    def __init__(self, path, vault, operator_uid, operator_gid=None, *, read_only=False):
        self.vault, self.operator_uid, self.read_only = vault, operator_uid, read_only
        if not read_only and operator_gid is None:
            raise PermissionError('enrollment group required')
        # Service-owned runtime directory; absent socket required (no unlink of unknown files).
        super().__init__(str(path), Handler)
        try:
            if read_only:
                # Only the exact desk UID may query this status-only endpoint.
                # No group membership in the desk or enrollment role is granted.
                os.chmod(path, 0o666)
            else:
                os.chown(path, -1, operator_gid)
                os.chmod(path, 0o660)
        except BaseException:
            self.server_close()
            raise

    def handle_error(self, request, address):
        # Never print untrusted request contents or exception text.
        pass


def send(request, path=SOCKET, service_uid=None):
    if service_uid is None:
        service_uid = pwd.getpwnam('maj-vaj-vault').pw_uid
    raw = json.dumps(request, separators=(',', ':')).encode()+b'\n'
    if len(raw) > MAX_MESSAGE:
        return REFUSED.copy()
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(5)
        s.connect(str(path))
        if _peer_uid(s) != service_uid:
            raise PermissionError('vault peer refused')
        s.sendall(raw)
        with s.makefile('rb') as f:
            response = f.readline(MAX_MESSAGE+1)
        if len(response) > MAX_MESSAGE:
            raise ValueError('vault response refused')
        return json.loads(response)


def checked_status(result):
    """Only fixed presence labels cross the status boundary, never arbitrary text."""
    if (type(result) is not dict or set(result) != {'ok', 'credentials', 'connections_verified', 'deployment_ready'}
            or result['ok'] is not True or result['connections_verified'] is not False
            or result['deployment_ready'] is not False):
        raise ValueError('credential status unavailable')
    presence = result['credentials']
    if (type(presence) is not dict or set(presence) != SLOTS
            or any(type(v) is not str or v not in ('stored_unverified', 'not_configured') for v in presence.values())
            or len({presence[s] for s in PAIR}) != 1):
        raise ValueError('credential status unavailable')
    return dict(presence)


def dashboard_status():
    return checked_status(send({'op': 'status'}, STATUS_SOCKET))


def harden_process():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Linux PR_SET_DUMPABLE=0: deny ordinary same-UID process memory access.
    # This does not isolate systemd credential files from another process with the same UID.
    if ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) != 0:
        raise PermissionError('process dump protection unavailable')


def serve():
    harden_process()
    os.umask(0o077)
    if os.getuid() != pwd.getpwnam('maj-vaj-vault').pw_uid:
        raise PermissionError('vault identity refused')
    operator = pwd.getpwnam('maj-vaj-credential-operator')
    vault = PaperVault(STATE, load_service_credential('venue-vault-key'))
    desk = pwd.getpwnam('maj-vaj')
    if len({os.getuid(), operator.pw_uid, desk.pw_uid}) != 3 or min(operator.pw_uid, desk.pw_uid) <= 0:
        raise PermissionError('distinct credential identities required')
    # R2B: one authoritative writer. No legacy enrollment socket remains.
    # Provider tag-key is a second protected systemd credential. A missing
    # key or a populated pre-migration vault fails closed without adoption.
    import hashlib
    import time
    from managed_paper_vault import ManagedPaperVault
    from managed_rpc import ManagedVaultCommands
    from vault_owner_hooks import ROLES
    tag_key = load_service_credential('provider-tag-key')
    policies = {role: hashlib.sha256(
        ('maj-vaj:r2b:stored-unverified:v1:' + role).encode('ascii')).hexdigest()
        for role in ROLES}
    managed = ManagedPaperVault(vault, tag_key, policies)
    with vault.transaction() as db:
        migrated = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_metadata'").fetchone() is not None
    if not migrated:
        managed.migrate_empty(int(time.time()))
    managed.startup_invalidate(int(time.time()))
    commands = ManagedVaultCommands(managed)
    with Server(SOCKET, commands, operator.pw_uid, grp.getgrnam('maj-vaj-credential-operator').gr_gid) as server:
        with Server(STATUS_SOCKET, commands, desk.pw_uid, read_only=True) as status:
            worker = threading.Thread(target=status.serve_forever, kwargs={'poll_interval': .2}, daemon=True)
            worker.start()
            try:
                server.serve_forever(poll_interval=.2)
            finally:
                status.shutdown()
                worker.join()


def main():
    p = argparse.ArgumentParser(description='Staged local PAPER test credentials only')
    p.add_argument('command', choices=['serve','status','enroll','remove'])
    p.add_argument('slots', nargs='*')
    args = p.parse_args()
    try:
        if args.command == 'serve':
            serve(); return 0
        harden_process()
        if os.getuid() != pwd.getpwnam('maj-vaj-credential-operator').pw_uid:
            raise PermissionError('operator identity refused')
        if args.command == 'status':
            if args.slots: raise ValueError('unexpected slots')
            request = {'op':'status'}
        else:
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise PermissionError('local interactive terminal required')
            _validate_slots(set(args.slots))
            if input('Type PAPER to confirm local test-credential change: ') != 'PAPER':
                return 1
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                request = {'op':'enroll','values':{slot:getpass.getpass(slot+': ') for slot in args.slots}} if args.command == 'enroll' else {'op':'remove','slots':args.slots}
        result = send(request)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get('ok') else 1
    except Exception:
        print('Credential operation unavailable', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
