"""Independent PAPER reserve rollback witness, stage-one primitive.

This module does not grant approval, execute a release, or open the desk.
Call verify/advance only in the same serialized desk transaction in which
full financial replay and approval/settlement validation have succeeded.
The authority principal is trusted; the scheduler/desk principal is not.
"""
import fcntl
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class CheckpointError(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise CheckpointError(reason)


@dataclass(frozen=True)
class Checkpoint:
    seq: int
    digest: str

    def __post_init__(self):
        require(type(self.seq) is int and 0 <= self.seq <= 2**63 - 1,
                'checkpoint_sequence')
        require(type(self.digest) is str and re.fullmatch('[0-9a-f]{64}', self.digest),
                'checkpoint_digest')
        require((self.seq == 0) == (self.digest == '0' * 64), 'checkpoint_genesis')


GENESIS = Checkpoint(0, '0' * 64)


def reserve_events(db, after=0, limit=2):
    require(db.in_transaction, 'serialized_desk_transaction_required')
    return [Checkpoint(*row) for row in db.execute(
        "SELECT seq,digest FROM fin_events WHERE substr(kind,1,8)='reserve_' "
        "AND seq>? ORDER BY seq LIMIT ?", (after, limit))]


def verify_prefix(db, checkpoint):
    require(db.in_transaction, 'serialized_desk_transaction_required')
    if checkpoint.seq:
        row = db.execute("SELECT digest,kind FROM fin_events WHERE seq=?",
                         (checkpoint.seq,)).fetchone()
        require(row is not None and row[0] == checkpoint.digest and row[1].startswith('reserve_'),
                'checkpoint_missing_or_rollback')


class AuthorityStore:
    """Single trusted writer, separate read-only desk/scheduler identity.

    All enclosing directories must be root-owned and not writable by group
    or other. The leaf is owned by the independent authority (0755); only
    the leaf owner can update the witness. No caller-provided fallback.
    """
    def __init__(self, directory, *, authority_uid, desk_uid):
        self.path = Path(directory)
        require(self.path.is_absolute() and len(self.path.parts) > 1 and
                '..' not in self.path.parts, 'checkpoint_path')
        require(type(authority_uid) is int and type(desk_uid) is int and
                authority_uid > 0 and desk_uid > 0 and authority_uid != desk_uid,
                'independent_principals_required')
        self.uid = authority_uid

    @contextmanager
    def _directory(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open('/', flags)
        try:
            for i, name in enumerate(self.path.parts):
                if i:
                    child = os.open(name, flags, dir_fd=fd)
                    os.close(fd)
                    fd = child
                s = os.fstat(fd)
                leaf = i == len(self.path.parts) - 1
                require(s.st_uid == (self.uid if leaf else 0) and
                        (stat.S_IMODE(s.st_mode) == 0o755 if leaf else not s.st_mode & 0o022),
                        'checkpoint_directory_custody')
            yield fd
        finally:
            os.close(fd)

    def _read(self, fd):
        f = os.open('checkpoint.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                    dir_fd=fd)
        with os.fdopen(f, 'rb') as stream:
            s = os.fstat(stream.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_uid == self.uid and s.st_nlink == 1 and
                    stat.S_IMODE(s.st_mode) == 0o644 and 0 < s.st_size <= 256,
                    'checkpoint_file_custody')
            raw = stream.read(257)
            obj = json.loads(raw)
            require(type(obj) is dict and set(obj) == {'seq', 'digest'}, 'checkpoint_schema')
            cp = Checkpoint(obj['seq'], obj['digest'])
            require(raw == self._encode(cp), 'checkpoint_encoding')
            return cp

    @staticmethod
    def _encode(cp):
        return (json.dumps({'seq': cp.seq, 'digest': cp.digest}, sort_keys=True,
                           separators=(',', ':')) + '\n').encode('ascii')

    def read(self):
        with self._directory() as fd:
            return self._read(fd)

    def verify(self, db):
        cp = self.read()
        verify_prefix(db, cp)
        require(not reserve_events(db, cp.seq), 'unwitnessed_reserve_event')
        return cp

    def _writer(self):
        require(os.geteuid() == self.uid, 'checkpoint_writer_refused')

    @contextmanager
    def _lock(self, fd, *, create=False):
        self._writer()
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        lock = os.open('writer.lock', flags, 0o600, dir_fd=fd)
        try:
            s = os.fstat(lock)
            require(stat.S_ISREG(s.st_mode) and s.st_uid == self.uid and s.st_nlink == 1 and
                    stat.S_IMODE(s.st_mode) == 0o600, 'checkpoint_lock_custody')
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
        finally:
            os.close(lock)

    def _publish(self, fd, cp, *, initial=False):
        name = '.checkpoint-' + secrets.token_hex(16)
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                      0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb') as stream:
                stream.write(self._encode(cp))
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
            if initial:
                os.link(name, 'checkpoint.json', src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                os.unlink(name, dir_fd=fd)
            else:
                os.replace(name, 'checkpoint.json', src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass

    def initialize(self, db):
        """New desk ONLY: never adopt an existing possibly rolled-back journal."""
        self._writer()
        require(not reserve_events(db), 'existing_reserve_history_refused')
        with self._directory() as fd, self._lock(fd, create=True):
            self._publish(fd, GENESIS, initial=True)
        return GENESIS

    def advance(self, db, expected, authorized_event_digest):
        """Persist exactly one externally authorized reserve event PRE-COMMIT.

        The trusted writer must validate its own approval or a bounded release
        independently; this primitive only enforces monotonicity and exact
        event identity. Failed desk commit after publication requires explicit
        recovery; never silently rewind the witness to make the desk work.
        """
        self._writer()
        with self._directory() as fd, self._lock(fd):
            current = self._read(fd)
            require(current == expected, 'checkpoint_compare_and_swap')
            verify_prefix(db, current)
            newer = reserve_events(db, current.seq)
            require(len(newer) == 1 and newer[0].digest == authorized_event_digest,
                    'checkpoint_exact_next_event_required')
            self._publish(fd, newer[0])
            return newer[0]
