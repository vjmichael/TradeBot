"""Durable R2B one-use approval ledger — protected owner-side candidate.

This library deliberately has NO browser/AI RPC, GUI admission socket,
provider dispatcher, worker signer, or authority to enable trading.
Production must bind each method to a *different* OS-verified caller and
execute the native confirmation inside an authenticated operator session.
A test-injected UI callback is never a deployable authorization.

State is exclusively local to the dedicated approval service UID, outside
the credential operator's writable home. SQLite BEGIN IMMEDIATE with FULL
sync consumes approval BEFORE any external provider operation. Reboot,
process restart, session loss, mismatched credential context, expiry and
clock regression all fail closed; an interrupted action is not retried.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

from vault_owner_hooks import ROLES

HEX32 = re.compile(r'[0-9a-f]{32}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
SID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
SCHEMA = 1
MAX_TTL = 120
STATES = ('pending','approved','consumed','cancelled','revoked')


class Refused(ValueError):
    """Fixed non-secret refusal. Do not print request or exception context."""


@dataclass(frozen=True)
class Context:
    operation: str
    role: str
    generation: str = ''
    policy: str = ''
    inventory: str = ''
    roles: tuple[str,...] = ()

    def validated(self):
        if (type(self) is not Context or type(self.operation) is not str
                or type(self.role) is not str):
            raise Refused('context_refused')
        if self.operation == 'provider_test':
            if (self.role not in ROLES or type(self.generation) is not str
                    or HEX32.fullmatch(self.generation) is None
                    or type(self.policy) is not str or HEX64.fullmatch(self.policy) is None
                    or self.inventory != '' or self.roles != ()):
                raise Refused('context_refused')
        elif self.operation == 'legacy_migration':
            if (self.role != 'legacy' or self.generation != '' or self.policy != ''
                    or type(self.inventory) is not str or HEX64.fullmatch(self.inventory) is None
                    or type(self.roles) is not tuple or not self.roles
                    or len(self.roles) > len(ROLES) or any(type(r) is not str for r in self.roles)
                    or tuple(sorted(set(self.roles))) != self.roles
                    or not set(self.roles) <= set(ROLES)):
                raise Refused('context_refused')
        else:
            raise Refused('context_refused')
        return self

    def canonical(self):
        self.validated()
        return json.dumps({'operation':self.operation,'role':self.role,
            'generation':self.generation,'policy':self.policy,
            'inventory':self.inventory,'roles':list(self.roles)},
            sort_keys=True, separators=(',',':'))


@dataclass(frozen=True)
class Witness:
    session: str
    uid: int

    def validated(self):
        if (type(self) is not Witness or type(self.session) is not str
                or SID.fullmatch(self.session) is None or type(self.uid) is not int
                or self.uid <= 0):
            raise Refused('operator_session_refused')
        return self


def _boot_id():
    value=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if re.fullmatch(r'[0-9a-f-]{36}',value) is None:
        raise Refused('boot_identity_unavailable')
    return value


class Ledger:
    """Private approval service state; the operator UID must not own its DB.

    Production orchestration MUST authenticate owner peer with SO_PEERCRED,
    authenticate operator process against sd_pid_get_session + logind,
    and implement a fixed native UI agent. The callback parameters below
    are internal seam points, NOT serialized RPC options.
    """
    def __init__(self, path, *, clock=time.monotonic_ns, boot=_boot_id):
        self.path=Path(path)
        self.clock=clock
        self.boot=boot
        self.incarnation=secrets.token_hex(16)
        self._secure_path()
        with self._tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS ledger_schema (version INTEGER NOT NULL)')
            schema=db.execute('SELECT version FROM ledger_schema').fetchall()
            if schema==[]:
                db.execute('INSERT INTO ledger_schema VALUES (?)',(SCHEMA,))
            elif schema != [(SCHEMA,)]:
                raise Refused('approval_schema_refused')
            db.execute("""CREATE TABLE IF NOT EXISTS approvals (
                request_id TEXT PRIMARY KEY,
                context TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','approved','consumed','cancelled','revoked')),
                boot_id TEXT NOT NULL,
                incarnation TEXT NOT NULL,
                created INTEGER NOT NULL,
                expires INTEGER NOT NULL,
                session TEXT NOT NULL,
                operator_uid INTEGER NOT NULL
            )""")
            # No approved request survives crash/restart or system boot.
            db.execute("UPDATE approvals SET state='revoked' WHERE state IN ('pending','approved')")

    def _secure_path(self):
        parent=self.path.parent
        st=parent.lstat()
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_mode & 0o077):
            raise Refused('approval_directory_refused')
        try:
            fd=os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        st=self.path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink != 1):
            raise Refused('approval_file_refused')

    @contextmanager
    def _tx(self):
        self._secure_path()
        db=sqlite3.connect(self.path, timeout=3.0, isolation_level=None)
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _time(self):
        now=self.clock()
        boot=self.boot()
        if type(now) is not int or now<=0 or type(boot) is not str or len(boot)!=36:
            raise Refused('trusted_time_unavailable')
        return now,boot

    @staticmethod
    def _id(request_id):
        if type(request_id) is not str or HEX32.fullmatch(request_id) is None:
            raise Refused('approval_id_refused')
        return request_id

    def prepare(self, context: Context, *, ttl_seconds=MAX_TTL):
        """Owner-only internal operation. No approval is created here."""
        canonical=context.canonical()
        if type(ttl_seconds) is not int or not 1<=ttl_seconds<=MAX_TTL:
            raise Refused('approval_ttl_refused')
        now,boot=self._time()
        rid=secrets.token_hex(16)
        with self._tx() as db:
            db.execute("""INSERT INTO approvals
                (request_id,context,state,boot_id,incarnation,created,expires,session,operator_uid)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (rid,canonical,'pending',boot,self.incarnation,now,
                 now+ttl_seconds*1_000_000_000,'',0))
        return rid

    def _pending(self, row, now,boot, state):
        if (row is None or row[0]!=state or row[1]!=boot
                or row[2]!=self.incarnation or now<row[3] or now>=row[4]):
            raise Refused('approval_unavailable')

    def review_with_native_prompt(self, request_id, *, witness, prompt, reattest):
        """INTERNAL trusted native UI operation, never a caller-supplied bool.

        The *installed* broker must own prompt implementation and peer session
        observation. A forged boolean over IPC must not reach this method.
        Do not hold a SQLite transaction while the user considers the prompt.
        """
        self._id(request_id)
        if not callable(witness) or not callable(prompt) or not callable(reattest):
            raise Refused('native_review_unavailable')
        initial=witness().validated()
        now,boot=self._time()
        with self._tx() as db:
            row=db.execute("""SELECT state,boot_id,incarnation,created,expires
                FROM approvals WHERE request_id=?""",(request_id,)).fetchone()
            self._pending(row,now,boot,'pending')
        accepted=prompt()
        later=reattest().validated()
        # Fail closed on a lost/changed native PAM/logind session even when
        # the prompt says yes, and never accept truthy strings or integers.
        if accepted is not True or later!=initial:
            self.cancel(request_id)
            return False
        now,boot=self._time()
        with self._tx() as db:
            row=db.execute("""SELECT state,boot_id,incarnation,created,expires
                FROM approvals WHERE request_id=?""",(request_id,)).fetchone()
            self._pending(row,now,boot,'pending')
            n=db.execute("""UPDATE approvals SET state='approved',session=?,operator_uid=?
                WHERE request_id=? AND state='pending'""",
                (initial.session,initial.uid,request_id)).rowcount
            if n!=1:
                raise Refused('approval_raced')
        return True

    def consume(self, request_id, context: Context, *, live_session):
        """Owner-only: durably consume BEFORE any provider attempt or migration.

        The returned context contains no credential. If a subsequent action
        fails, the approval is spent. The production caller must bind this
        call to authenticated maj-vaj-vault SO_PEERCRED and authoritative
        owner-held current credential generation/policy or ciphertext inventory.
        """
        self._id(request_id)
        expected=context.canonical()
        if not callable(live_session):
            raise Refused('operator_session_guard_required')
        now,boot=self._time()
        with self._tx() as db:
            row=db.execute("""SELECT state,boot_id,incarnation,created,expires,
                context,session,operator_uid FROM approvals WHERE request_id=?""",
                (request_id,)).fetchone()
            if row is None:
                raise Refused('approval_unavailable')
            self._pending(row[:5],now,boot,'approved')
            if not hmac.compare_digest(row[5],expected):
                raise Refused('approval_context_changed')
            witness=Witness(row[6],row[7]).validated()
            if live_session(witness) is not True:
                raise Refused('operator_session_revoked')
            if db.execute("""UPDATE approvals SET state='consumed'
                WHERE request_id=? AND state='approved'""",(request_id,)).rowcount!=1:
                raise Refused('approval_raced')
        return context

    def cancel(self, request_id):
        self._id(request_id)
        with self._tx() as db:
            n=db.execute("""UPDATE approvals SET state='cancelled'
                WHERE request_id=? AND state IN ('pending','approved')""",
                (request_id,)).rowcount
        return n==1

    def state_for_test(self, request_id):
        """Read-only diagnostic; never expose to status RPC or browser."""
        self._id(request_id)
        with self._tx() as db:
            row=db.execute('SELECT state FROM approvals WHERE request_id=?',(request_id,)).fetchone()
        return row[0] if row else None
