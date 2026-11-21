"""DISPOSABLE QEMU-ONLY operator SO_PEERCRED / logind attestation fixture.

This is a TEST socket, not an admission broker or provider executor.
No approval grant is created, signed, or consumed. No secrets flow.
"""
import os
from pathlib import Path
import socket
import sys
import time

sys.path[:0]=['/opt/maj-vaj/r2b-admission','/opt/maj-vaj']
from approval_ledger import Refused
from operator_session import attest_operator_peer,check_live_session,peer_from_socket

PATH='/run/maj-vaj-r2b-operator-probe.sock'
RECEIPT=Path('/run/maj-vaj-r2b-operator-probe.txt')


def record(message):
    with RECEIPT.open('a') as f:
        f.write(message+'\n')
        f.flush()
        os.fsync(f.fileno())


def main():
    if os.getuid()!=0:
        raise RuntimeError('fixture_root_required')
    RECEIPT.touch(mode=0o600,exist_ok=False)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
        server.bind(PATH)
        os.chmod(PATH,0o666)  # test-only: kernel UID enforcement, no secrets
        server.listen(4)
        server.settimeout(120)
        record('SERVER_READY')
        refused_root=False
        witness=None
        while witness is None:
            conn,_=server.accept()
            with conn:
                conn.settimeout(4)
                try:
                    peer=peer_from_socket(conn)
                    observed=attest_operator_peer(conn)
                except Refused:
                    if not refused_root:
                        refused_root=True
                        record('R2B_NATIVE_ROOT_PEER_REFUSED')
                    continue
                if not refused_root:
                    raise RuntimeError('root_negative_probe_not_exercised')
                if peer.pid<=1 or observed.uid!=peer.uid:
                    raise RuntimeError('kernel_peer_mismatch')
                witness=observed
                conn.sendall(b'ATTESTED\n')
                record('R2B_NATIVE_OPERATOR_PAM_PEER_PASS')
        # Owner fixture will perform a genuine loginctl terminate-session
        # of the separately established GUI PAM session.
        end=time.monotonic()+45
        while time.monotonic()<end:
            try:
                active=check_live_session(witness)
            except Refused:
                active=False
            if not active:
                record('R2B_NATIVE_OPERATOR_SESSION_REVOKED_PASS')
                return
            time.sleep(.5)
        raise RuntimeError('operator_session_not_revoked')


if __name__=='__main__':
    try:main()
    except BaseException as e:
        record('FIXTURE_ERROR_'+type(e).__name__)
        raise
