"""CI-only: ephemeral Ed25519 signer for a disposable offline release.

Signing seed/private key is never written to disk or included in the guest.
The separately staged trust root is installed by root in the QEMU fixture;
production trust enrollment and revocation are deliberately NOT asserted.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

SUFFIX=b"\nR5R2_OFFLINE_PACKAGE_VERSION = 2\n"
DOMAIN=b"MAJ-VAJ-R5-R2-OFFLINE-COMPATIBLE-RELEASE\n"
def put(p,data):
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        os.write(fd,data)
        os.fsync(fd)
    finally:os.close(fd)

def main():
    if len(sys.argv)!=4:
        raise SystemExit("pass fixed patched R5 source, package destination, trust destination")
    source=Path(sys.argv[1]).read_bytes()
    assert b"R5R2_OFFLINE_PACKAGE_VERSION" not in source
    compile(source.decode("utf-8"),sys.argv[1],"exec")
    payload=source+SUFFIX
    compile(payload.decode("utf-8"),"managed_rpc-v2.py","exec")
    package,trust=Path(sys.argv[2]),Path(sys.argv[3])
    package.mkdir(mode=0o700)
    trust.mkdir(mode=0o700)
    manifest={
        "format":"maj-vaj-r5-r2-ed25519-v1",
        "from_version":1,"to_version":2,"file":"managed_rpc.py",
        "before":hashlib.sha256(source).hexdigest(),
        "after":hashlib.sha256(payload).hexdigest(),
        "schema":"unchanged","scope":"offline-synthetic-only"
    }
    data=json.dumps(manifest,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
    secret=Ed25519PrivateKey.generate()
    pub=secret.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    sig=secret.sign(DOMAIN+data)
    put(package/"manifest.json",data)
    put(package/"signature.ed25519",sig)
    put(package/"managed_rpc-v2.py",payload)
    put(trust/"trusted.pub",pub)
    put(trust/"trusted.pub.sha256",(hashlib.sha256(pub).hexdigest()+"\n").encode())
    print("R5_R2_EPHEMERAL_SIGNED_OFFLINE_PACKAGE_PASS")
    print("R5_R2_RELEASE_MANIFEST_SHA256="+hashlib.sha256(data).hexdigest())
    print("R5_R2_RELEASE_TRUST_SHA256="+hashlib.sha256(pub).hexdigest())

if __name__=="__main__":
    main()
