"""Linux kernel/logind attestation for a protected operator review endpoint.

SO_PEERCRED is read from the *accepted socket*, never from a JSON field,
browser session, bearer, argv or caller-provided UID. libsystemd ties that
exact peer PID to an actual logind PAM session. loginctl verifies that the
same session remains active, local, unlocked, on seat0 and X11.
No fallback to synthetic uid/session when systemd cannot attest.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import pwd
import re
import socket
import struct
import subprocess

from approval_ledger import Refused, Witness

SESSION_RE=re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
GROUPS=frozenset({'User','Name','Seat','Service','Type','Class','Active','Remote','LockedHint'})
BASE_ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C'}


@dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    gid: int


def peer_from_socket(connection):
    """Only the accepted AF_UNIX socket itself supplies identity."""
    if not isinstance(connection,socket.socket) or connection.family != socket.AF_UNIX:
        raise Refused('ipc_socket_refused')
    try:
        raw=connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
        pid,uid,gid=struct.unpack('3i',raw)
    except (OSError,struct.error):
        raise Refused('ipc_peer_unavailable') from None
    if pid<=1 or uid<=0 or gid<0:
        raise Refused('ipc_peer_unavailable')
    return Peer(pid,uid,gid)


def _systemd_pid_session(pid):
    if type(pid) is not int or pid<=1:
        raise Refused('operator_pid_refused')
    try:
        library=ctypes.CDLL('libsystemd.so.0',use_errno=True)
        func=library.sd_pid_get_session
        func.argtypes=(ctypes.c_int,ctypes.POINTER(ctypes.c_void_p))
        func.restype=ctypes.c_int
        p=ctypes.c_void_p()
        rc=func(pid,ctypes.byref(p))
        if rc<0 or not p.value:
            raise Refused('operator_pam_session_unavailable')
        try:
            value=ctypes.string_at(p).decode('ascii','strict')
        finally:
            libc=ctypes.CDLL(None)
            libc.free.argtypes=(ctypes.c_void_p,)
            libc.free(p)
        if SESSION_RE.fullmatch(value) is None:
            raise Refused('operator_pam_session_unavailable')
        return value
    except (OSError,AttributeError,UnicodeError,ValueError):
        raise Refused('operator_pam_session_unavailable') from None


def _login_properties(session):
    if type(session) is not str or SESSION_RE.fullmatch(session) is None:
        raise Refused('operator_pam_session_unavailable')
    try:
        r=subprocess.run(
            ['/usr/bin/loginctl','show-session',session,'--all',
             '--property='+','.join(sorted(GROUPS))],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=3, text=True,env=BASE_ENV)
    except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired):
        raise Refused('operator_pam_session_unavailable') from None
    props={}
    for line in r.stdout.splitlines():
        if line.count('=')!=1:
            raise Refused('operator_pam_session_unavailable')
        key,value=line.split('=',1)
        if key in props or key not in GROUPS:
            raise Refused('operator_pam_session_unavailable')
        props[key]=value
    if set(props)!=GROUPS:
        raise Refused('operator_pam_session_unavailable')
    return props


def check_live_session(witness: Witness):
    witness.validated()
    p=_login_properties(witness.session)
    if (p['User']!=str(witness.uid) or p['Name']!='maj-vaj-credential-operator'
            or p['Seat']!='seat0' or p['Service']!='lightdm'
            or p['Type']!='x11' or p['Class']!='user'
            or p['Active']!='yes' or p['Remote']!='no'
            or p['LockedHint']!='no'):
        return False
    return True


def attest_operator_peer(connection):
    """Requires the distinct dedicated operator uid from installed NSS."""
    peer=peer_from_socket(connection)
    try:
        operator=pwd.getpwnam('maj-vaj-credential-operator')
    except KeyError:
        raise Refused('operator_identity_unavailable') from None
    if operator.pw_uid<=0 or peer.uid!=operator.pw_uid:
        raise Refused('operator_peer_refused')
    sid=_systemd_pid_session(peer.pid)
    w=Witness(sid,peer.uid).validated()
    if not check_live_session(w):
        raise Refused('operator_session_inactive')
    return w


def require_vault_owner_peer(connection):
    peer=peer_from_socket(connection)
    try:
        owner=pwd.getpwnam('maj-vaj-vault').pw_uid
    except KeyError:
        raise Refused('vault_identity_unavailable') from None
    if owner<=0 or peer.uid!=owner:
        raise Refused('vault_owner_peer_required')
    return peer
