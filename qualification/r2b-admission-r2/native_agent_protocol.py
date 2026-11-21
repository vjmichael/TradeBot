"""R2B native read-only review UI agent (operator LightDM process only).

The caller supplies neither approval request ID nor review context. The
separate broker selects one pending owner-created request, presents only
fixed fields, and accepts a single response on THIS SAME AF_UNIX
connection. It must pin the actual GUI process's kernel PID/session.
This module does not sign, schedule, run or verify any provider call.
"""
from __future__ import annotations
import json
import os
import pwd
import socket
import struct

AGENT_SOCKET='/run/maj-vaj-r2b-approval/agent.sock'
MAX=1024


class Refused(ValueError):
    pass


def _read(sock):
    data=bytearray()
    while len(data)<=MAX:
        chunk=sock.recv(min(128,MAX+1-len(data)))
        if not chunk:break
        data.extend(chunk)
        if data.endswith(b'\n'):break
    if not data or len(data)>MAX or data[-1:]!=b'\n' or data.count(b'\n')!=1:
        raise Refused('invalid_review_frame')
    try:
        return json.loads(bytes(data[:-1]).decode('ascii','strict'))
    except (ValueError,UnicodeError,TypeError):
        raise Refused('invalid_review_frame') from None


def _write(sock,value):
    data=json.dumps(value,sort_keys=True,separators=(',',':')).encode('ascii')+b'\n'
    if len(data)>MAX:
        raise Refused('invalid_review_frame')
    sock.sendall(data)


def show_pending(root,guard,*,connect=None,prompt=None):
    """Called synchronously by the already-authenticated original Tk process.

    Prompt function is injectable for source-level tests ONLY. Installed
    app always uses the broker-supplied fixed confirm dialog.
    """
    guard()
    if connect is None:
        connect=lambda:socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    if prompt is None:
        from tkinter import messagebox
        prompt=lambda message:messagebox.askokcancel(
            'Maj Vaj — one-use read-only permission review',message,
            parent=root,icon='warning')
    with connect() as s:
        s.settimeout(120)
        s.connect(AGENT_SOCKET)
        expected=pwd.getpwnam('maj-vaj-approval').pw_uid
        pid,uid,gid=struct.unpack('3i',s.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if uid!=expected or uid<=0 or pid<=1:
            raise Refused('approval_broker_peer_refused')
        _write(s,{'op':'review_next'})
        preview=_read(s)
        if (type(preview) is not dict
                or set(preview)!={'kind','request_id','role','generation','policy'}
                or preview['kind']!='preview' or preview['role']!='kraken_read'
                or type(preview['request_id']) is not str
                or len(preview['request_id'])!=32
                or type(preview['generation']) is not str
                or len(preview['generation'])!=32
                or type(preview['policy']) is not str
                or len(preview['policy'])!=64):
            raise Refused('approval_preview_refused')
        message=(
            'One-time Kraken READ-ONLY permission check (synthetic worker).\n\n'
            'Credential generation: '+preview['generation'][:8]+'…\n'
            'Policy fingerprint: '+preview['policy'][:12]+'…\n\n'
            'No external provider connection, trading, withdrawal, or '
            'credential export is enabled by this review.\n'
            'Approval expires on cancellation, lock, logout or restart.'
        )
        choice=prompt(message)
        # A session revocation while Tk is open must fail without sending yes.
        guard()
        answer={'op':'answer','request_id':preview['request_id'],
                'choice':'approve' if choice is True else 'decline'}
        _write(s,answer)
        s.shutdown(socket.SHUT_WR)
        final=_read(s)
        guard()
        if type(final) is not dict or set(final)!={'ok'} or type(final['ok']) is not bool:
            raise Refused('approval_ack_refused')
        return final['ok'] is True
