"""R2B installed broker with guarded native-agent ingress.

This process has NO venue credentials, worker signing key, network,
provider dispatcher, financial authority, or desktop-session cookie.
Only the actual operator's fixed LightDM Tk process can positively
review exactly one owner-prepared read-only request. Only the dedicated
vault principal may prepare/cancel/consume; a latched logind event
revocation refuses consumption even after a seat becomes active again.
"""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import pwd
import stat
import sys
import threading

HERE=Path('/opt/maj-vaj/r2b-admission-r2')
sys.path[:0]=[str(HERE),'/opt/maj-vaj/r2b-admission-r1','/opt/maj-vaj']

from approval_ledger import Ledger,Refused
from broker_gateway import BrokerGateway
from broker_service import SOCKET,STATE,Server
from agent_broker_channel import AgentServer


class GuardedGateway(BrokerGateway):
    def __init__(self,ledger):
        super().__init__(ledger)
        self.agent=None

    def consume_from_owner(self,connection,request_id,context):
        if self.agent is None:
            raise Refused('native_agent_unavailable')
        result=super().consume_from_owner(connection,request_id,context)
        self.agent.clear_spent(request_id)
        return result

    def cancel_from_owner(self,connection,request_id):
        ok=super().cancel_from_owner(connection,request_id)
        if self.agent is not None:
            self.agent.clear_spent(request_id)
        return ok


def run():
    os.umask(0o077)
    user=pwd.getpwnam('maj-vaj-approval')
    if (os.getuid(),os.geteuid(),os.getgid())!=(user.pw_uid,user.pw_uid,user.pw_gid):
        raise SystemExit('broker_identity_refused')
    if ctypes.CDLL(None,use_errno=True).prctl(4,0,0,0,0)!=0:
        raise SystemExit('broker_memory_refused')
    st=SOCKET.parent.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=user.pw_uid
            or stat.S_IMODE(st.st_mode)!=0o711):
        raise SystemExit('broker_runtime_refused')
    ledger=Ledger(STATE)
    gateway=GuardedGateway(ledger)
    with AgentServer(gateway) as agent:
        gateway.agent=agent
        # Check the active session-event latch at consume. Event loss is
        # fail-closed; no fallback to just matching a UID or a cookie.
        gateway._live=agent.consume_guard
        agent_thread=threading.Thread(target=agent.serve_forever,
                           kwargs={'poll_interval':.1},daemon=True)
        agent_thread.start()
        try:
            with Server(SOCKET,gateway) as owner:
                owner.serve_forever(poll_interval=.1)
        finally:
            agent.shutdown()
            agent_thread.join(3)
            if agent_thread.is_alive():
                raise SystemExit('agent_thread_unavailable')


if __name__=='__main__':
    run()
