"""Installed fixed no-network *synthetic-denial* worker.

NEVER contacts Kraken, Telegram, Grok or an external endpoint. This
is NOT the actual provider adapter and cannot generate positive
permission receipts. It demonstrates separate OS identity, systemd
LoadCredential key custody, kernel-authenticated vault request and
bound single-purpose HMAC receipt in a disposable Debian guest.
"""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import pwd
import socket
import socketserver
import stat
import sys
import time

# -I deliberately drops the script directory and cwd from sys.path.
# Register only the fixed root-owned installed modules, never a client path.
sys.path[:0]=['/opt/maj-vaj/r2b-admission-r2','/opt/maj-vaj']

from operator_session import peer_from_socket
from synthetic_worker_protocol import one_frame,sign_denial
from vault_owner_hooks import Attempt,Refused

UNIT='maj-vaj-r2b-synthetic-worker.service'
SOCKET=Path('/run/maj-vaj-r2b-synthetic-worker/result.sock')
MAX=1600
KEYNAME='r2b-worker-signing-key'


def read_key():
    base=Path('/run/credentials')/UNIT
    if os.environ.get('CREDENTIALS_DIRECTORY')!=str(base):
        raise Refused('synthetic_worker_service_credential_unavailable')
    st=base.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0,os.geteuid())
            or stat.S_IMODE(st.st_mode) not in (0o500,0o550)):
        raise Refused('synthetic_worker_service_credential_unavailable')
    fd=os.open(base/KEYNAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid not in (0,os.geteuid())
                or stat.S_IMODE(st.st_mode) not in (0o400,0o440)
                or st.st_nlink!=1 or st.st_size!=32):
            raise Refused('synthetic_worker_signing_key_unavailable')
        key=f.read(33)
    if len(key)!=32:
        raise Refused('synthetic_worker_signing_key_unavailable')
    return key


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        conn=self.request
        try:
            peer=peer_from_socket(conn)
            if peer.uid!=self.server.vault_uid:
                raise Refused('vault_peer_required')
            obj=one_frame(conn)
            if (set(obj)!={'op','attempt'} or obj['op']!='fixed_synthetic_denial'
                    or type(obj['attempt']) is not dict
                    or set(obj['attempt'])!=
                      {'role','generation','attempt_id','policy','started','nonce'}):
                raise Refused('synthetic_command_refused')
            a=obj['attempt']
            attempt=Attempt(a['role'],a['generation'],a['attempt_id'],
                            a['policy'],a['started'],a['nonce'])
            record=sign_denial(attempt,self.server.signing_key,int(time.time()))
            payload=json.dumps(record,sort_keys=True,
                               separators=(',',':')).encode('ascii')+b'\n'
        except (OSError,ValueError,TypeError,OverflowError,KeyError):
            payload=b'{"ok":false}\n'
        try:
            conn.sendall(payload)
        except (OSError,TimeoutError):
            pass


class Server(socketserver.UnixStreamServer):
    allow_reuse_address=False
    def __init__(self,vault_uid,signing_key):
        self.vault_uid=vault_uid
        self.signing_key=signing_key
        if SOCKET.exists() or SOCKET.is_symlink():
            raise Refused('synthetic_socket_preexisting')
        super().__init__(str(SOCKET),Handler)
        try:
            import grp
            group=grp.getgrnam('maj-vaj-vault').gr_gid
            if group not in os.getgroups():
                raise Refused('vault_group_unavailable')
            os.chown(SOCKET,-1,group)
            os.chmod(SOCKET,0o660)
            st=SOCKET.lstat()
            if (not stat.S_ISSOCK(st.st_mode) or st.st_uid!=os.geteuid()
                    or st.st_gid!=group or stat.S_IMODE(st.st_mode)!=0o660):
                raise Refused('synthetic_socket_custody_refused')
            self._inode=(st.st_dev,st.st_ino)
        except BaseException:
            super().server_close()
            raise
    def server_close(self):
        try:
            st=SOCKET.lstat()
            if (stat.S_ISSOCK(st.st_mode) and st.st_uid==os.geteuid()
                    and (st.st_dev,st.st_ino)==getattr(self,'_inode',None)):
                SOCKET.unlink()
        except OSError:pass
        self._unlink_socket=False
        super().server_close()


def main():
    os.umask(0o077)
    worker=pwd.getpwnam('maj-vaj-r2b-worker')
    vault=pwd.getpwnam('maj-vaj-vault')
    if (os.getuid(),os.geteuid(),os.getgid())!=(
          worker.pw_uid,worker.pw_uid,worker.pw_gid):
        raise SystemExit('synthetic_worker_uid_refused')
    if ctypes.CDLL(None,use_errno=True).prctl(4,0,0,0,0)!=0:
        raise SystemExit('synthetic_worker_memory_refused')
    st=SOCKET.parent.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=worker.pw_uid
            or stat.S_IMODE(st.st_mode)!=0o711):
        raise SystemExit('synthetic_worker_runtime_refused')
    key=read_key()
    with Server(vault.pw_uid,key) as server:
        server.serve_forever(poll_interval=.1)


if __name__=='__main__':
    main()
