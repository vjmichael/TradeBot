"""Staged PAPER authority IPC. No order, password, signing or desk-write endpoint."""
import argparse
import fcntl

import json
import os
import pwd
import re
import secrets
import socket
import stat
import struct
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from reserve_checkpoint import AuthorityStore, Checkpoint, CheckpointError, require, verify_prefix, reserve_events

MAX = 8192
DOMAIN = b'maj-vaj:PAPER:checkpoint-permit:v1\0'
TIMEOUT = 2


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def strict(raw):
    obj = json.loads(raw)
    require(type(obj) is dict and raw == canonical(obj), 'noncanonical_message')
    return obj


def checkpoint(obj):
    require(type(obj) is dict and set(obj) == {'seq', 'digest'}, 'checkpoint_schema')
    return Checkpoint(**obj)


def scope(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value), 'workspace_scope')
    return value


def peer(conn):
    return struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def receive(conn):
    def exact(n):
        out = bytearray()
        deadline = time.monotonic() + TIMEOUT
        while len(out) < n:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'message_timeout')
            conn.settimeout(remaining)
            part = conn.recv(n - len(out))
            require(part, 'truncated_message')
            out.extend(part)
        return bytes(out)
    size, = struct.unpack('!I', exact(4))
    require(0 < size <= MAX, 'message_size')
    return strict(exact(size))


def send(conn, value):
    raw = canonical(value)
    require(len(raw) <= MAX, 'message_size')
    conn.settimeout(TIMEOUT)
    conn.sendall(struct.pack('!I', len(raw)) + raw)


@contextmanager
def protected_dir(path, owner, mode):
    path = Path(path)
    require(path.is_absolute() and len(path.parts) > 1 and '..' not in path.parts, 'custody_path')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        root = os.fstat(fd)
        require(root.st_uid == 0 and not root.st_mode & 0o022, 'root_custody')
        for i, name in enumerate(path.parts[1:], 1):
            new = os.open(name, flags, dir_fd=fd)
            os.close(fd); fd = new
            s = os.fstat(fd)
            leaf = i == len(path.parts) - 1
            require(s.st_uid == (owner if leaf else 0) and
                    (stat.S_IMODE(s.st_mode) == mode if leaf else not s.st_mode & 0o022), 'directory_custody')
        yield fd
    finally:
        os.close(fd)


def pinned_key(path):
    path = Path(path)
    require(path.is_absolute(), 'pin_path')
    with protected_dir(path.parent, 0, 0o755) as fd:
        f = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        with os.fdopen(f, 'rb') as stream:
            s = os.fstat(stream.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and
                    stat.S_IMODE(s.st_mode) == 0o644 and s.st_size == 32, 'pin_custody')
            return stream.read(32)


def permit_body(workspace, previous, following, expires):
    require(following.seq > previous.seq, 'permit_sequence')
    require(type(expires) is int and expires > 0, 'permit_expiry')
    return {'workspace': scope(workspace), 'previous': asdict(previous), 'following': asdict(following), 'expires': expires}


def sign_permit(seed, workspace, previous, following, expires):
    """Trusted local signer only, after independent replay and exact human/evidence review.

    No RPC method exposes this function. Production review integration is separate.
    """
    body = permit_body(workspace, previous, following, expires)
    sig = Ed25519PrivateKey.from_private_bytes(seed).sign(DOMAIN + canonical(body))
    return {'body': body, 'signature': sig.hex()}


def verify_permit(public, workspace, permit, now):
    require(type(permit) is dict and set(permit) == {'body', 'signature'}, 'permit_schema')
    b = permit['body']; sig = permit['signature']
    require(type(b) is dict and set(b) == {'workspace', 'previous', 'following', 'expires'}, 'permit_body')
    prev, nxt = checkpoint(b['previous']), checkpoint(b['following'])
    require(b == permit_body(workspace, prev, nxt, b['expires']), 'permit_scope')
    require(now <= b['expires'] <= now + 300, 'permit_expired')
    require(type(sig) is str and re.fullmatch('[0-9a-f]{128}', sig), 'signature_format')
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(bytes.fromhex(sig), DOMAIN + canonical(b))
    except (ValueError, InvalidSignature) as exc:
        raise CheckpointError('permit_signature') from exc
    return prev, nxt


class AuthorityService:
    def __init__(self, store, socket_dir, *, authority_uid, desk_uid, workspace, pin):
        require(os.geteuid() == authority_uid and store.uid == authority_uid and authority_uid != desk_uid, 'service_identity')
        self.store, self.directory = store, Path(socket_dir)
        self.uid, self.desk, self.workspace, self.pin = authority_uid, desk_uid, scope(workspace), Path(pin)

    def dispatch(self, request):
        require(set(request) == {'v','nonce','op','workspace','permit'}, 'request_schema')
        nonce = request['nonce']
        require(type(request['v']) is int and request['v'] == 1 and type(nonce) is str and
                re.fullmatch('[0-9a-f]{32}', nonce) and request['workspace'] == self.workspace, 'request_identity')
        op = request['op']
        if op == 'checkpoint':
            require(request['permit'] is None, 'unexpected_permit')
            result = self.store.read()
        elif op == 'advance':
            # The authority never trusts the desk to choose a new checkpoint.
            # Only the independently signed exact digest and predecessor are accepted.
            with self.store._directory() as fd, self.store._lock(fd):
                prev, nxt = verify_permit(pinned_key(self.pin), self.workspace, request['permit'], int(time.time()))
                require(self.store._read(fd) == prev, 'checkpoint_compare_and_swap')
                self.store._publish(fd, nxt)
                result = nxt
        else:
            raise CheckpointError('unknown_operation')
        return {'v': 1, 'nonce': nonce, 'workspace': self.workspace, 'checkpoint': asdict(result)}

    def serve(self):
        # Prevent competing service instances from replacing each other's socket.
        with protected_dir(self.directory, self.uid, 0o755) as fd:
            lock = os.open('service.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                s = os.fstat(lock)
                require(stat.S_ISREG(s.st_mode) and s.st_uid == self.uid and s.st_nlink == 1 and stat.S_IMODE(s.st_mode) == 0o600, 'service_lock_custody')
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.store.read(); pinned_key(self.pin)
                name = 'authority.sock'
                try:
                    s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    require(stat.S_ISSOCK(s.st_mode) and s.st_uid == self.uid, 'socket_custody')
                    os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(self.directory / name))
                    os.chmod(name, 0o666, dir_fd=fd)
                    listener.listen(8)
                    try:
                        while True:
                            conn, _ = listener.accept()
                            with conn:
                                try:
                                    require(peer(conn) == self.desk, 'peer_refused')
                                    send(conn, self.dispatch(receive(conn)))
                                except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
                                    # Fixed failure reply; no raw diagnostics or secrets.
                                    try: send(conn, {'error': 'authority_refused'})
                                    except OSError: pass
                    finally:
                        os.unlink(name, dir_fd=fd)
            finally:
                os.close(lock)


class AuthorityClient:
    def __init__(self, directory, *, authority_uid, desk_uid, workspace):
        require(type(authority_uid) is int and authority_uid > 0 and desk_uid > 0 and authority_uid != desk_uid and os.geteuid() == desk_uid, 'client_identity')
        self.directory, self.uid, self.workspace = Path(directory), authority_uid, scope(workspace)

    def request(self, op, permit=None):
        nonce = secrets.token_hex(16)
        with protected_dir(self.directory, self.uid, 0o755) as fd:
            s = os.stat('authority.sock', dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISSOCK(s.st_mode) and s.st_uid == self.uid, 'socket_custody')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(TIMEOUT); conn.connect(str(self.directory/'authority.sock'))
                require(peer(conn) == self.uid, 'server_identity')
                send(conn, {'v':1, 'nonce':nonce, 'op':op, 'workspace':self.workspace, 'permit':permit})
                r = receive(conn)
        require(set(r) == {'v','nonce','workspace','checkpoint'} and type(r['v']) is int and r['v'] == 1 and
                r['nonce'] == nonce and r['workspace'] == self.workspace, 'authority_refused_or_bad_reply')
        return checkpoint(r['checkpoint'])

    def verify_transaction(self, db):
        """Call after full financial replay, while holding the serialized transaction."""
        cp = self.request('checkpoint'); verify_prefix(db, cp)
        require(not reserve_events(db, cp.seq), 'unwitnessed_reserve_event')
        return cp

    def advance_transaction(self, db, expected, permit):
        """After replay and independent exact permit, before committing the desk.

        If commit or acknowledgement fails after publication, refuse subsequent
        operations until explicit recovery. Never silently rewind either store.
        """
        verify_prefix(db, expected)
        events = reserve_events(db, expected.seq)
        require(len(events) == 1, 'exact_next_event_required')
        body = permit['body']
        require(checkpoint(body['previous']) == expected and checkpoint(body['following']) == events[0], 'permit_event_mismatch')
        actual = self.request('advance', permit)
        require(actual == events[0], 'checkpoint_response_mismatch')
        return actual


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket-dir', required=True); p.add_argument('--witness', required=True)
    p.add_argument('--authority-user', required=True); p.add_argument('--desk-user', required=True)
    p.add_argument('--workspace', required=True); p.add_argument('--pin', required=True)
    a = p.parse_args(); authority = pwd.getpwnam(a.authority_user).pw_uid; desk = pwd.getpwnam(a.desk_user).pw_uid
    store = AuthorityStore(a.witness, authority_uid=authority, desk_uid=desk)
    AuthorityService(store, a.socket_dir, authority_uid=authority, desk_uid=desk, workspace=a.workspace, pin=a.pin).serve()


if __name__ == '__main__':
    main()
