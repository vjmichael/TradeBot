"""Local console -> independent credential login. Never carries authorization.

The URI is a constant UI intent, not a ticket. Only the dedicated, active
console principal may request its own session's termination; LightDM requires
fresh credential-operator authentication before enrollment can begin.
"""
import os
from pathlib import Path
import pwd
import socket
import select
import struct
import time
from contextlib import contextmanager
import stat
import subprocess
import sys
import tempfile

from paper_credential_vault import harden_process
from reserve_checkpoint import require
from reserve_ipc import protected_dir
from reserve_review_launcher import _login, _sessions, caller_session
from reserve_review_session import read_source, select_cookie
from reserve_review_events import SessionEvents

URI = 'majvaj-account:open'
CONSOLE = 'maj-vaj-console'
MARKER = Path('/etc/maj-vaj/account-handoff-enabled')
HANDLER = 'maj-vaj-account-handoff.desktop'
BROWSER = '/usr/bin/chromium'
INTENT_NAME = 'maj-vaj-account-handoff-intent.sock'
INTENT = b'OPEN\n'
ACK = b'QUEUED\n'
BASE_ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}


def validate_uri(args):
    require(tuple(args) == (URI,), 'account_handoff_request_refused')


def identity():
    user = pwd.getpwnam(CONSOLE)
    uid = user.pw_uid
    require(uid > 0 and os.getuid() == os.geteuid() == uid, 'account_console_required')
    require(os.getgid() == os.getegid() == user.pw_gid and set(os.getgroups()) <= {user.pw_gid}
            and set(os.getgrouplist(CONSOLE, user.pw_gid)) == {user.pw_gid}, 'account_console_groups')
    require(all(uid != pwd.getpwnam(name).pw_uid and user.pw_gid != pwd.getpwnam(name).pw_gid
                for name in ('maj-vaj-credential-operator', 'maj-vaj-vault', 'maj-vaj',
                             'maj-vaj-dashboard-auth', 'maj-vaj-approval', 'maj-vaj-native')), 'account_console_separation')
    with protected_dir(MARKER.parent, 0, 0o755) as fd:
        f = os.open(MARKER.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        try:
            st = os.fstat(f)
            require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1 and not st.st_mode & 0o022,
                    'account_handoff_disabled')
        finally:
            os.close(f)
    session, display = _sessions(uid)
    caller_session(session)
    require(_login('show-session', session, '--property=Seat', '--value').strip() == 'seat0', 'account_console_seat')
    home = Path(user.pw_dir)
    require(home.is_absolute() and home != Path('/'), 'account_console_home')
    auth = home / '.Xauthority'
    require(os.environ.get('DISPLAY') == display and
            os.environ.get('XAUTHORITY', str(auth)) == str(auth), 'account_console_display')
    cookie = select_cookie(read_source(home, uid), display, socket.gethostname().encode())
    result = subprocess.run(['/usr/bin/xhost'], check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, timeout=5,
                            env=BASE_ENV | {'DISPLAY': display, 'XAUTHORITY': str(auth)})
    require([line.strip() for line in result.stdout.splitlines() if line.strip()] ==
            ['access control enabled, only authorized clients can connect'], 'account_console_display_access')
    require(_sessions(uid) == (session, display) and
            select_cookie(read_source(home, uid), display, socket.gethostname().encode()) == cookie,
            'account_console_session_changed')
    caller_session(session)
    import hashlib
    return session, display, str(auth), hashlib.sha256(cookie).digest()


def confirm():
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        return messagebox.askokcancel('Maj Vaj — Open account connections',
                                      'End this local Maj Vaj dashboard session and return to sign-in?\n\n'
                                      'Choose Maj Vaj Account connections and authenticate with the credential-operator account. '
                                      'No credential or trading authorization will be transferred.', parent=root)
    finally:
        root.destroy()


def terminate(session):
    # No sudo, service activation, target UID, password, or caller-specified session.
    subprocess.run(['/usr/bin/loginctl', 'terminate-session', session], check=True,
                   timeout=8, env=BASE_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def handoff(args, *, snapshot=identity, events_factory=SessionEvents, ask=confirm, end=terminate):
    validate_uri(args)
    start = snapshot()
    with events_factory(start[0]) as events:
        def guard():
            events.check()
            require(snapshot() == start, 'account_console_session_changed')
            events.check()
        guard()
        if ask() is not True:
            return False
        guard()
        end(start[0])
        return True



@contextmanager
def intent_listener(runtime, uid):
    """Session-owned, non-authorizing fixed-intent socket; never a credential RPC."""
    with protected_dir(runtime, uid, 0o700):
        pass
    path = runtime / INTENT_NAME
    # Never unlink an unknown/stale socket; a fresh PAM session removes it.
    require(not path.exists() and not path.is_symlink(), 'account_intent_socket_busy')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        try:
            os.chmod(path, 0o600)
            st = path.lstat()
            require(stat.S_ISSOCK(st.st_mode) and st.st_uid == uid
                    and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1,
                    'account_intent_socket_refused')
            listener.listen(1)
            yield listener
        finally:
            # Remove only our exact owned socket inode, no symlink/future path.
            try:
                now = path.lstat()
                if (stat.S_ISSOCK(now.st_mode) and now.st_uid == uid
                        and (now.st_dev, now.st_ino) == (st.st_dev, st.st_ino)):
                    path.unlink()
            except (OSError, UnboundLocalError):
                pass


def receive_intent(listener, uid):
    """Only same-UID local peer, exact bounded frame and peer EOF."""
    conn, _ = listener.accept()
    with conn:
        conn.settimeout(1)
        try:
            pid, peer_uid, peer_gid = struct.unpack(
                '3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if pid <= 1 or peer_uid != uid:
                return False
            data = bytearray()
            while len(data) <= len(INTENT):
                chunk = conn.recv(len(INTENT) + 1 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if bytes(data) != INTENT:
                return False
            conn.sendall(ACK)
            return True
        except (OSError, ValueError, TimeoutError):
            return False


def relay_intent():
    """Desktop protocol is UI intent only, not a PAM session or authorization."""
    user = pwd.getpwnam(CONSOLE)
    uid = user.pw_uid
    require(uid > 0 and os.getuid() == os.geteuid() == uid
            and os.getgid() == os.getegid() == user.pw_gid,
            'account_console_required')
    runtime = Path(f'/run/user/{uid}')
    with protected_dir(runtime, uid, 0o700):
        pass
    path = runtime / INTENT_NAME
    st = path.lstat()
    require(stat.S_ISSOCK(st.st_mode) and st.st_uid == uid
            and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600,
            'account_intent_socket_refused')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(str(path))
        pid, peer_uid, peer_gid = struct.unpack(
            '3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(pid > 1 and peer_uid == uid, 'account_intent_owner_refused')
        sock.sendall(INTENT)
        sock.shutdown(socket.SHUT_WR)
        require(sock.recv(16) == ACK, 'account_intent_unavailable')


def console():
    # A separately authenticated, dedicated browser session; no admin-token
    # injection or profile reuse. Browser must retain its normal OS sandbox.
    before = identity()
    uid = os.getuid()
    runtime = Path(f'/run/user/{uid}')
    with protected_dir(runtime, uid, 0o700):
        pass
    require(os.environ.get('XDG_RUNTIME_DIR') == str(runtime), 'account_console_runtime')
    require(os.environ.get('HOME') == pwd.getpwnam(CONSOLE).pw_dir, 'account_console_home')
    require(Path(BROWSER).is_file() and os.access(BROWSER, os.X_OK), 'account_console_browser_missing')
    # Do not pass secrets through argv, the environment or a protocol URL.
    # Keep the PAM session bus environment supplied by the actual desktop.
    env = BASE_ENV | {k: os.environ[k] for k in ('HOME', 'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP') if k in os.environ}
    env['XAUTHORITY'] = before[2]
    subprocess.run(['/usr/bin/xdg-mime', 'default', HANDLER, 'x-scheme-handler/majvaj-account'],
                   check=True, timeout=8, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = subprocess.run(['/usr/bin/xdg-mime', 'query', 'default', 'x-scheme-handler/majvaj-account'],
                            check=True, timeout=8, env=env, capture_output=True, text=True)
    require(result.stdout.strip() == HANDLER, 'account_console_handler_missing')
    with intent_listener(runtime, uid) as intent, SessionEvents(before[0]) as events, tempfile.TemporaryDirectory(prefix='maj-vaj-browser-', dir=runtime) as profile:
        events.check()
        require(identity() == before, 'account_console_session_changed')
        child = subprocess.Popen([BROWSER, '--incognito', '--no-first-run', '--no-default-browser-check',
                                  '--disable-sync', '--disable-background-networking', '--disable-component-update',
                                  '--no-proxy-server', '--user-data-dir=' + profile, 'http://127.0.0.1:8765/'],
                                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            last_intent = 0.0
            while child.poll() is None:
                events.check()
                require(identity() == before, 'account_console_session_changed')
                ready, _, _ = select.select([intent], [], [], 0.5)
                if ready:
                    now = time.monotonic()
                    valid = receive_intent(intent, uid)
                    if not valid or now - last_intent < 2.0:
                        continue
                    last_intent = now
                    events.check()
                    require(identity() == before, 'account_console_session_changed')
                    # This executes in the original PAM/logind console owner,
                    # never inside Chromium's desktop-launcher subprocess.
                    if handoff((URI,)) is True:
                        break
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


def main():
    harden_process()
    os.umask(0o077)
    try:
        if sys.argv[1:] == ['console']:
            console()
        else:
            validate_uri(sys.argv[1:])
            relay_intent()
    except Exception:
        raise SystemExit('Local Maj Vaj account handoff unavailable. Sign out and choose Account connections at login.') from None


if __name__ == '__main__':
    main()
