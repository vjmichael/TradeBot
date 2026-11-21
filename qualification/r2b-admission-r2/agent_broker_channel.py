"""R2B agent-side broker ingress with real kernel PID and PAM session.

This class is instantiated only inside the independent protected approval
service. It cannot sign a worker receipt, access credentials, or spawn
providers. The broker's trusted reviewer owns the native-message
exchange; no remote JSON 'approved' is ever converted into authority
without checking the original, pinned GUI process and session events.

Installed positive review is restricted to one owner-prepared
Kraken-read-only context and the original fixed LightDM Xsession.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import pwd
import re
import socket
import socketserver
import stat
import struct
import subprocess
import threading

from approval_ledger import Context,Refused
from broker_service import _reject_duplicate,MAX_MESSAGE
from operator_session import attest_operator_peer,peer_from_socket,check_live_session
from reserve_review_events import SessionEvents

AGENT_SOCKET=Path('/run/maj-vaj-r2b-approval/agent.sock')
COMMAND_FRAGMENT=b'from credential_graphical import main;main()'


def _one_line(conn):
    data=bytearray()
    while len(data)<=MAX_MESSAGE:
        part=conn.recv(1 if data.endswith(b'\n') else min(256,MAX_MESSAGE+1-len(data)))
        if not part:break
        data.extend(part)
        if data.endswith(b'\n'):break
    if (not data or len(data)>MAX_MESSAGE or data[-1:]!=b'\n'
            or data.count(b'\n')!=1 or b'\x00' in data):
        raise Refused('agent_frame_refused')
    try:
        obj=json.loads(bytes(data[:-1]).decode('ascii','strict'),
                       object_pairs_hook=_reject_duplicate)
    except (ValueError,UnicodeError,TypeError):
        raise Refused('agent_frame_refused') from None
    if type(obj) is not dict:
        raise Refused('agent_frame_refused')
    return obj


def _write(conn,record):
    frame=json.dumps(record,sort_keys=True,separators=(',',':')).encode('ascii')+b'\n'
    if len(frame)>MAX_MESSAGE:
        raise Refused('agent_frame_refused')
    conn.sendall(frame)


def _session_leader(session):
    try:
        r=subprocess.run(
            ['/usr/bin/loginctl','show-session',session,'--property=Leader','--value'],
            check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            timeout=3,text=True,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
        if not re.fullmatch(r'[0-9]+\n?',r.stdout):
            raise Refused('native_session_leader_refused')
        leader=int(r.stdout.strip())
        if leader<=1:
            raise Refused('native_session_leader_refused')
        return leader
    except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired):
        raise Refused('native_session_leader_refused') from None


def attest_exact_gui(conn):
    """Kernel PID must be this LightDM session leader, not just same UID.

    The installed fixed session wrapper EXECs credential_graphical's main
    process. A second same-UID process in the session is not accepted.
    A command-line string alone would NOT constitute authority.
    """
    w=attest_operator_peer(conn)
    peer=peer_from_socket(conn)
    if peer.pid!=_session_leader(w.session):
        raise Refused('not_native_session_leader')
    try:
        cmd=(Path('/proc')/str(peer.pid)/'cmdline').read_bytes()
    except OSError:
        raise Refused('native_session_process_unavailable') from None
    if (len(cmd)>4096 or cmd.count(COMMAND_FRAGMENT)!=1 or
            not cmd.startswith(b'/opt/maj-vaj/venv/bin/python\x00')):
        raise Refused('unexpected_native_process')
    return w


def next_unambiguous(ledger):
    """Single protected pending grant, never an operator-selected ID."""
    now,boot=ledger._time()
    with ledger._tx() as db:
        rows=db.execute("""SELECT request_id,state,boot_id,incarnation,
            created,expires,context FROM approvals
            WHERE state='pending' LIMIT 2""").fetchall()
        if len(rows)!=1:
            raise Refused('approval_request_not_unique')
        row=rows[0]
        ledger._pending(row[1:6],now,boot,'pending')
    data=json.loads(row[6])
    if type(data) is not dict or set(data)!={'operation','role','generation','policy','inventory','roles'}:
        raise Refused('approval_context_refused')
    context=Context(data['operation'],data['role'],data['generation'],
                    data['policy'],data['inventory'],tuple(data['roles'])).validated()
    if context.operation!='provider_test' or context.role!='kraken_read':
        raise Refused('provider_specific_review_not_ready')
    return row[0],context


class AgentHandler(socketserver.BaseRequestHandler):
    def handle(self):
        conn=self.request
        conn.settimeout(120)
        events=None
        try:
            w=attest_exact_gui(conn)
            opener=_one_line(conn)
            if opener!={'op':'review_next'}:
                raise Refused('agent_operation_refused')
            with self.server.review_lock:
                rid,context=next_unambiguous(self.server.gateway.ledger)
                if self.server.active:
                    raise Refused('review_in_flight')
                events=SessionEvents(w.session)
                events.check()
                self.server.active=True
                try:
                    def guarded():
                        events.check()
                        witness=attest_exact_gui(conn)
                        if witness!=w:
                            raise Refused('native_session_changed')
                        return witness
                    def native_dialog():
                        events.check()
                        _write(conn,{'kind':'preview','request_id':rid,
                                     'role':context.role,
                                     'generation':context.generation,
                                     'policy':context.policy})
                        response=_one_line(conn)
                        # A spoofed response from a different socket/process
                        # cannot reach this callback. A byte stream from this
                        # pinned GUI PID still must meet exact schema and ID.
                        if (set(response)!={'op','request_id','choice'}
                                or response['op']!='answer'
                                or response['request_id']!=rid
                                or response['choice'] not in ('approve','decline')):
                            raise Refused('native_response_refused')
                        trailing=conn.recv(1)
                        if trailing:
                            raise Refused('native_extra_frame_refused')
                        events.check()
                        return response['choice']=='approve'
                    try:
                        accepted=self.server.gateway.ledger.review_with_native_prompt(
                            rid,witness=guarded,prompt=native_dialog,reattest=guarded)
                    except BaseException:
                        # Lost transport or session events revoke this request.
                        self.server.gateway.ledger.cancel(rid)
                        raise
                    if accepted is True:
                        # Keep the irreversible logind session-event latch
                        # alive until OWNER consumption. Restart drops it
                        # and the persistent ledger independently revokes.
                        self.server.latches[rid]=(w,events)
                        events=None
                    _write(conn,{'ok':accepted is True})
                finally:
                    self.server.active=False
        except (Refused,ValueError,TypeError,OSError,TimeoutError,EOFError):
            try:_write(conn,{'ok':False})
            except OSError:pass
        except Exception:
            try:_write(conn,{'ok':False})
            except OSError:pass
        finally:
            if events is not None:events.close()


class AgentServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
    allow_reuse_address=False

    def __init__(self,gateway):
        self.gateway=gateway
        self.review_lock=threading.RLock()
        self.active=False
        self.latches={}
        if AGENT_SOCKET.exists() or AGENT_SOCKET.is_symlink():
            raise Refused('agent_socket_preexisting')
        super().__init__(str(AGENT_SOCKET),AgentHandler)
        try:
            import grp
            operator=grp.getgrnam('maj-vaj-credential-operator').gr_gid
            if operator not in os.getgroups():
                raise Refused('agent_group_unavailable')
            os.chown(AGENT_SOCKET,-1,operator)
            os.chmod(AGENT_SOCKET,0o660)
            st=AGENT_SOCKET.lstat()
            if (not stat.S_ISSOCK(st.st_mode) or st.st_uid!=os.geteuid()
                    or st.st_gid!=operator or stat.S_IMODE(st.st_mode)!=0o660):
                raise Refused('agent_socket_custody_refused')
            self._inode=(st.st_dev,st.st_ino)
        except BaseException:
            super().server_close()
            raise

    def consume_guard(self,witness):
        with self.review_lock:
            matching=[(rid,e) for rid,(w,e) in self.latches.items()
                      if w==witness]
            if len(matching)!=1:
                return False
            rid,events=matching[0]
            try:
                events.check()
                return check_live_session(witness) is True
            except (ValueError,OSError):
                return False

    def clear_spent(self,rid):
        with self.review_lock:
            data=self.latches.pop(rid,None)
            if data is not None:
                data[1].close()

    def server_close(self):
        for rid in list(self.latches):
            self.clear_spent(rid)
        try:
            st=AGENT_SOCKET.lstat()
            if (stat.S_ISSOCK(st.st_mode) and st.st_uid==os.geteuid()
                    and (st.st_dev,st.st_ino)==getattr(self,'_inode',None)):
                AGENT_SOCKET.unlink()
        except OSError:
            pass
        # Python's superclass may unlink server_address unconditionally.
        # We already made an inode-verified decision above.
        self._unlink_socket=False
        super().server_close()
