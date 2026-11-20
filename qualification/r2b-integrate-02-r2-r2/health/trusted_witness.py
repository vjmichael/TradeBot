"""Bounded, read-only local service readiness witness collector.

No outbound internet, credentials, environment secrets or financial control.
Only a verified OS peer at a configured, non-writable Unix socket can attest
readiness. Absent or unqualified witness means unknown, never ready.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
import os
import socket
import stat
import struct
import time

ALLOWED = frozenset(('ready','failed','unknown'))
MAX = 512
FRESH = 45.0

@dataclass(frozen=True)
class Source:
    name: str
    socket_path: str
    peer_uid: int
    directory_owner_uid: int = 0

@dataclass(frozen=True)
class Witness:
    state: str
    observed_monotonic: float


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate_field')
        result[key]=value
    return result


def inspect_one(source: Source, clock=time.monotonic) -> Witness | None:
    """Untrusted/missing/stale source becomes None, not a positive signal.

    Specification values must come from immutable deployment-owned config;
    never from a browser, account provider or general plugin.
    """
    if (type(source) is not Source or not source.name or not source.socket_path.startswith('/run/')
            or type(source.peer_uid) is not int or source.peer_uid <= 0
            or type(source.directory_owner_uid) is not int or source.directory_owner_uid < 0):
        return None
    try:
        dirname=os.path.dirname(source.socket_path)
        d=os.lstat(dirname)
        if not stat.S_ISDIR(d.st_mode) or d.st_uid != source.directory_owner_uid or (d.st_mode & 0o022):
            return None
        st=os.lstat(source.socket_path)
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=source.peer_uid or (st.st_mode & 0o002):
            return None
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            s.connect(source.socket_path)
            pid,uid,gid=struct.unpack('3i',s.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if pid <= 0 or uid != source.peer_uid:
                return None
            s.sendall(b'GET HEALTH 1\n')
            data=b''
            while len(data)<=MAX and not data.endswith(b'\n'):
                chunk=s.recv(min(128,MAX+1-len(data)))
                if not chunk:break
                data+=chunk
            if not data.endswith(b'\n') or len(data)>MAX:
                return None
        obj=json.loads(data,object_pairs_hook=_unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError()))
        if (type(obj) is not dict or set(obj) != {'version','service','state','observed_monotonic'}
                or type(obj['version']) is not int or obj['version']!=1
                or obj['service']!=source.name or type(obj['state']) is not str or obj['state'] not in ALLOWED
                or type(obj['observed_monotonic']) not in (int,float)):
            return None
        now=clock()
        checked=obj['observed_monotonic']
        if not all(math.isfinite(v) for v in (now,checked)) or not 0 <= now-checked < FRESH:
            return None
        return Witness(obj['state'],checked)
    except (OSError,ValueError,TypeError,OverflowError,UnicodeError,TimeoutError):
        return None


def collect(specs: dict[str,Source], clock=time.monotonic):
    """Called by the existing bounded background sampler, never HTTP/UI loop."""
    if type(specs) is not dict or len(specs)>8 or any(k!=v.name for k,v in specs.items()):
        raise ValueError('invalid_witness_configuration')
    return {name:value for name,source in specs.items() if (value:=inspect_one(source,clock)) is not None}