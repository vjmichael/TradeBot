import os, pathlib, socket
from reserve_authority import pinned_public
assert len(pinned_public())==32
try:pathlib.Path('/var/lib/maj-vaj/approval/reserve-approval.json').read_bytes()
except OSError:pass
else:raise AssertionError('credential visible to scheduler service')
try:
    fd=os.open('/etc/maj-vaj/reserve-approval.pub',os.O_WRONLY);os.close(fd)
except OSError:pass
else:raise AssertionError('anchor writable')
try:socket.socket(socket.AF_INET,socket.SOCK_STREAM)
except OSError:pass
else:raise AssertionError('network socket allowed')
assert os.getuid()!=0
print('RECEIPT: scheduler service cannot read encrypted approval store, alter root-pinned public key or create IPv4 socket',flush=True)
