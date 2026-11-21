"""Protected installed R2B approval-broker socket — NEGATIVE-ONLY R1 deployment.

The installed service prepares/cancels owner-bound requests but DOES NOT
admit a positive native approval, provider attempt, worker, or migration.
An operator-origin 'review' request is always refused until a distinct
trusted native GUI agent and authenticated response transport are added.

Security-critical boundaries:
  * AF_UNIX SO_PEERCRED, never client-declared UID/PID/session.
  * owner-only prepare/consume/cancel; same-UID non-GUI review refused.
  * independent approval UID, 0700 persistent dir, 0600 SQLite.
  * reject unknown socket inode; fixed bounded one-frame EOF protocol.
  * restart revokes unconsumed approvals; no auto retry or provider egress.

No outside URL, arbitrary command, credential export, signing API,
password/secret input, real provider request or financial operation.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, '/opt/maj-vaj')

from approval_ledger import Context, Ledger, Refused
from broker_gateway import BrokerGateway
from operator_session import attest_operator_peer, require_vault_owner_peer, check_live_session

SOCKET=Path('/run/maj-vaj-r2b-approval/approval.sock')
STATE=Path('/var/lib/maj-vaj-r2b-approval/ledger.sqlite3')
MAX_MESSAGE=1024
FAIL={'ok':False,'reason':'approval_refused'}


def _reject_duplicate(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise Refused('duplicate_request_field')
        result[key]=value
    return result


def parse_frame(raw):
    if (type(raw) is not bytes or not raw or len(raw)>MAX_MESSAGE
            or raw[-1:]!=b'\n' or raw.count(b'\n')!=1 or b'\x00' in raw):
        raise Refused('approval_frame_refused')
    try:
        value=json.loads(raw[:-1].decode('ascii','strict'),
                         object_pairs_hook=_reject_duplicate,
                         parse_constant=lambda _: (_ for _ in ()).throw(Refused('invalid_json')))
    except (UnicodeError,ValueError,TypeError,OverflowError):
        raise Refused('approval_frame_refused') from None
    if type(value) is not dict or type(value.get('op')) is not str:
        raise Refused('approval_request_refused')
    return value


def context_from_request(value):
    if type(value) is not dict or set(value)!={'operation','role','generation','policy','inventory','roles'}:
        raise Refused('approval_context_refused')
    if type(value['roles']) is not list:
        raise Refused('approval_context_refused')
    return Context(value['operation'],value['role'],
                   value['generation'],value['policy'],
                   value['inventory'],tuple(value['roles'])).validated()


def dispatch(gateway, conn, request):
    """Exact allowed operations; never accept an 'approved' client field."""
    op=request['op']
    if op=='prepare' and set(request)=={'op','context'}:
        return {'ok':True, 'request_id':gateway.prepare_from_owner(
            conn,context_from_request(request['context']))}
    if op=='cancel' and set(request)=={'op','request_id'}:
        if gateway.cancel_from_owner(conn,request['request_id']):
            return {'ok':True}
        return FAIL.copy()
    if op=='consume' and set(request)=={'op','request_id','context'}:
        # A positive approval cannot exist on this installed candidate:
        # no trusted GUI response path is provisioned. Keep the owner-only
        # consumer checked so replay/probing is refused by the real ledger.
        gateway.consume_from_owner(conn,request['request_id'],
                                   context_from_request(request['context']))
        return {'ok':True}
    if op=='review' and set(request)=={'op','request_id'}:
        # Enforce kernel session identity even for the refused path.
        attest_operator_peer(conn)
        raise Refused('trusted_native_agent_not_installed')
    raise Refused('approval_operation_refused')


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        conn=self.request
        conn.settimeout(2)
        try:
            data=bytearray()
            while len(data)<=MAX_MESSAGE:
                chunk=conn.recv(min(256,MAX_MESSAGE+1-len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            request=parse_frame(bytes(data))
            reply=dispatch(self.server.gateway,conn,request)
        except (Refused,ValueError,TypeError,OverflowError,OSError,TimeoutError):
            reply=FAIL.copy()
        except Exception:
            # Never log untrusted contents or runtime exception details.
            reply=FAIL.copy()
        try:
            conn.sendall(json.dumps(reply,separators=(',',':')).encode('ascii')+b'\n')
        except (OSError,TimeoutError):
            pass


class Server(socketserver.UnixStreamServer):
    def __init__(self,path,gateway):
        self.gateway=gateway
        if path.is_symlink() or path.exists():
            raise Refused('approval_socket_preexisting')
        super().__init__(str(path),Handler)
        try:
            import grp
            shared=grp.getgrnam('maj-vaj-r2b-approval-rpc').gr_gid
            if shared not in os.getgroups():
                raise Refused('approval_socket_group_refused')
            os.chown(path,-1,shared)
            os.chmod(path,0o660)
            st=path.lstat()
            if (not stat.S_ISSOCK(st.st_mode) or st.st_uid!=os.geteuid()
                    or stat.S_IMODE(st.st_mode)!=0o660 or st.st_nlink!=1):
                raise Refused('approval_socket_custody_refused')
            self._inode=(st.st_dev,st.st_ino)
        except BaseException:
            self.server_close()
            raise

    def server_close(self):
        try:
            st=Path(self.server_address).lstat()
            if (stat.S_ISSOCK(st.st_mode) and st.st_uid==os.geteuid()
                    and (st.st_dev,st.st_ino)==getattr(self,'_inode',None)):
                Path(self.server_address).unlink()
        except OSError:
            pass
        super().server_close()


def main():
    os.umask(0o077)
    approval=pwd.getpwnam('maj-vaj-approval')
    if (os.getuid()!=approval.pw_uid or os.geteuid()!=approval.pw_uid
            or os.getgid()!=approval.pw_gid):
        raise SystemExit('approval_identity_refused')
    if ctypes.CDLL(None,use_errno=True).prctl(4,0,0,0,0)!=0:
        raise SystemExit('approval_memory_custody_unavailable')
    runtime=SOCKET.parent
    st=runtime.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid()
            or stat.S_IMODE(st.st_mode)!=0o711):
        raise SystemExit('approval_runtime_refused')
    ledger=Ledger(STATE)
    # The installed service cannot show a genuine trusted operator Tk UI:
    # the operator socket 'review' remains explicitly disabled.
    gateway=BrokerGateway(ledger)
    with Server(SOCKET,gateway) as server:
        server.serve_forever(poll_interval=.2)


if __name__=='__main__':
    main()
