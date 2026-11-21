"""R5-R2: authenticated, offline, tightly scoped v1 -> v2 code upgrade.

This is a QEMU-only fixture. Release v2 is an additive read-only version
declaration in managed_rpc.py, no schema mutation or provider authority.
Trust enrollment is OUTSIDE the package and must be root-authorized.
No caller-provided paths, tokens, migration or network operations.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Only the independently installed original root-owned transaction helper.
sys.path.insert(0,"/opt/maj-vaj/r2b-admission-r5")
from r5_transaction import Transaction, Refused

ROOT=Path("/opt/maj-vaj")
ETC=Path("/etc/maj-vaj")
SYSTEMD=Path("/etc/systemd/system")
PACKAGE=ROOT/"r5-r2-package"
PUB=ETC/"r5-r2-trust-root.pub"
COMMITMENT=ETC/"r5-r2-trust-root.sha256"
PENDING=ETC/"r5-r2-upgrade-pending"
BACKUP=ETC/"r5-r2-upgrade-backup"
RECORD=ETC/"r5-r2-release-receipt"
PAYLOAD=PACKAGE/"managed_rpc-v2.py"
MANIFEST=PACKAGE/"manifest.json"
SIGNATURE=PACKAGE/"signature.ed25519"
TARGET=ROOT/"managed_rpc.py"
SUFFIX=b"\nR5R2_OFFLINE_PACKAGE_VERSION = 2\n"
DOMAIN=b"MAJ-VAJ-R5-R2-OFFLINE-COMPATIBLE-RELEASE\n"
ALLOWED={"phase","original","identity","manifest_sha"}
PHASES={"PREPARING","APPLYING","RESTORED"}

def digest(data):
    return hashlib.sha256(data).hexdigest()

def encoded(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")

def read(file,private=False):
    return Transaction._bytes(Path(file),private=private)

def atomic(file,data,mode=0o600):
    Transaction._write(Path(file),data,mode)

def fixture_kill(name):
    flag=ETC/("r5-r2-fixture-abort-"+name)
    try: st=flag.lstat()
    except FileNotFoundError:return
    if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid()
            or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600):
        raise Refused("unsafe_fixture_flag")
    os.kill(os.getpid(),signal.SIGKILL)

def safe_dir(path):
    st=path.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid()
            or st.st_mode & 0o022 or path.is_symlink()):
        raise Refused("unsafe_directory")
    return st

def release():
    safe_dir(PACKAGE)
    pub=read(PUB,private=True)
    anchor=read(COMMITMENT,private=True)
    if len(pub)!=32 or anchor!=digest(pub).encode()+b"\n":
        raise Refused("offline_trust_root_mismatch")
    raw=read(MANIFEST)
    try: obj=json.loads(raw.decode("ascii"))
    except (ValueError,UnicodeError):
        raise Refused("signed_manifest_invalid") from None
    if (type(obj) is not dict or set(obj)!=
            {"format","from_version","to_version","file","before","after","schema","scope"}
            or obj["format"]!="maj-vaj-r5-r2-ed25519-v1"
            or type(obj["from_version"]) is not int or obj["from_version"]!=1
            or type(obj["to_version"]) is not int or obj["to_version"]!=2
            or obj["file"]!="managed_rpc.py"
            or obj["schema"]!="unchanged"
            or obj["scope"]!="offline-synthetic-only"
            or type(obj["before"]) is not str or len(obj["before"])!=64
            or type(obj["after"]) is not str or len(obj["after"])!=64
            or raw!=encoded(obj)):
        raise Refused("signed_manifest_invalid")
    sig=read(SIGNATURE)
    if len(sig)!=64:
        raise Refused("signature_invalid")
    try:Ed25519PublicKey.from_public_bytes(pub).verify(sig,DOMAIN+raw)
    except (InvalidSignature,ValueError,TypeError):
        raise Refused("untrusted_offline_package")
    payload=read(PAYLOAD)
    if digest(payload)!=obj["after"] or not payload.endswith(SUFFIX):
        raise Refused("package_payload_changed")
    # The only admitted source mutation is the harmless fixed version tag;
    # no modified imports, RPC, permissions, policy or provider adapter.
    if digest(payload[:-len(SUFFIX)])!=obj["before"]:
        raise Refused("incompatible_release_payload")
    compile(payload.decode("utf-8"),str(TARGET),"exec")
    return obj,payload,digest(raw)

def pending_state():
    state=json.loads(read(PENDING,private=True))
    if (type(state) is not dict or set(state)!=ALLOWED or
            state["phase"] not in PHASES or
            any(type(state[k]) is not str or len(state[k])!=64
                for k in ("original","identity","manifest_sha"))):
        raise Refused("upgrade_journal_invalid")
    return state

def write_state(phase,original,identity,manifest_sha):
    atomic(PENDING,encoded({"phase":phase,"original":original,
                            "identity":identity,"manifest_sha":manifest_sha}))

def stable_services():
    if (ETC/"r2b-approval-test-enabled").exists():
        raise Refused("upgrade_requires_disabled")
    for unit in ("maj-vaj-r2b-approval.service",
                 "maj-vaj-r2b-synthetic-worker.service"):
        proc=subprocess.run(["systemctl","is-active","--quiet",unit],check=False)
        if proc.returncode==0:
            raise Refused("upgrade_requires_disabled")
    proc=subprocess.run(["systemctl","is-active","--quiet",
                         "maj-vaj-paper-vault.service"],check=False)
    if proc.returncode==0:
        raise Refused("upgrade_owner_must_be_stopped")

def inventory_ok():
    # Must not confuse source-code compatibility with database migration.
    # This fixture deliberately requires an empty vault before upgrade.
    import sqlite3
    vault=Path("/var/lib/maj-vaj-paper-vault/vault.sqlite3")
    if vault.exists():
        try:
            with sqlite3.connect("file:"+str(vault)+"?mode=ro",uri=True) as db:
                if db.execute("PRAGMA quick_check").fetchone()!=("ok",):
                    raise Refused("vault_integrity_unavailable")
                if db.execute("SELECT count(*) FROM secrets").fetchone()[0]:
                    raise Refused("credential_bearing_upgrade_refused")
        except sqlite3.DatabaseError:
            raise Refused("vault_integrity_unavailable") from None

def backup_item(name):
    return BACKUP/name

def backup_original():
    orig=read(backup_item("managed_rpc.v1"),private=True)
    ident=read(backup_item("installed-identity.v1"),private=True)
    return orig,ident

def remove_backup(*, partial=False):
    if BACKUP.is_symlink():
        raise Refused("upgrade_backup_custody")
    if not BACKUP.exists():return
    st=BACKUP.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid()
            or stat.S_IMODE(st.st_mode)!=0o700):
        raise Refused("upgrade_backup_custody")
    expected={"managed_rpc.v1","installed-identity.v1"}
    found={x.name for x in BACKUP.iterdir()}
    if not found<=expected:raise Refused("unexpected_upgrade_backup")
    for p in BACKUP.iterdir():
        read(p,private=True)
        p.unlink()
        Transaction._syncdir(BACKUP)
        if partial:
            fixture_kill("recover-cleanup-first")
            partial=False
    BACKUP.rmdir()
    Transaction._syncdir(ETC)

def prepare():
    t=Transaction(ROOT,ETC,SYSTEMD)
    t.verify()
    stable_services()
    inventory_ok()
    obj,payload,mhash=release()
    original=read(TARGET)
    identity=read(t.identity,private=True)
    if digest(original)!=obj["before"]:
        raise Refused("upgrade_source_version_mismatch")
    if RECORD.exists() or RECORD.is_symlink() or PENDING.exists() or PENDING.is_symlink() or BACKUP.exists() or BACKUP.is_symlink():
        raise Refused("upgrade_already_started")
    write_state("PREPARING",digest(original),digest(identity),mhash)
    BACKUP.mkdir(mode=0o700)
    Transaction._syncdir(ETC)
    atomic(backup_item("managed_rpc.v1"),original)
    fixture_kill("prepare-first")
    atomic(backup_item("installed-identity.v1"),identity)
    Transaction._syncdir(BACKUP)
    write_state("APPLYING",digest(original),digest(identity),mhash)
    return obj,payload,mhash

def apply():
    obj,payload,mhash=prepare()
    t=Transaction(ROOT,ETC,SYSTEMD)
    atomic(TARGET,payload,0o644)
    fixture_kill("upgrade-after-code")
    old_record=json.loads(read(t.identity,private=True))
    if (set(old_record)!={"version","entries"} or old_record["version"]!=1
            or type(old_record["entries"]) is not dict
            or set(old_record["entries"])!=set(t._inventory())):
        raise Refused("upgrade_original_identity_invalid")
    old_record["entries"]["managed_rpc.py"]=obj["after"]
    atomic(t.identity,encoded(old_record))
    fixture_kill("upgrade-after-identity")
    receipt={"version":2,"signature_manifest":mhash,"source":obj["after"],
             "trust":digest(read(PUB,private=True))}
    atomic(RECORD,encoded(receipt))
    PENDING.unlink()
    Transaction._syncdir(ETC)
    verify()
    print("R5_R2_SIGNED_CROSS_VERSION_UPGRADE_PASS")

def verify():
    if PENDING.exists() or PENDING.is_symlink():
        raise Refused("upgrade_pending_refused")
    obj,payload,mhash=release()
    receipt=json.loads(read(RECORD,private=True))
    if (type(receipt) is not dict or receipt!=
            {"version":2,"signature_manifest":mhash,"source":obj["after"],
             "trust":digest(read(PUB,private=True))}):
        raise Refused("upgrade_receipt_invalid")
    t=Transaction(ROOT,ETC,SYSTEMD)
    t.verify()
    if digest(read(TARGET))!=obj["after"]:
        raise Refused("installed_upgrade_mutated")
    old,ident=backup_original()
    if digest(old)!=obj["before"] or digest(ident)!=json.loads(ident.decode("ascii")).get("no-such-key","") and not ident:
        # The installed identity's original digest is verified below.
        raise Refused("upgrade_backup_invalid")
    prior=json.loads(ident)
    if (type(prior) is not dict or prior.get("version")!=1
            or prior.get("entries",{}).get("managed_rpc.py")!=obj["before"]):
        raise Refused("upgrade_backup_invalid")
    return True

def recover():
    stable_services()
    inventory_ok()
    state=pending_state()
    original_digest=state["original"]
    identity_digest=state["identity"]
    if state["phase"]=="PREPARING":
        # No installed source can have changed: PREPARING is written before
        # backing up, and APPLYING is written only after BOTH fsynced.
        t=Transaction(ROOT,ETC,SYSTEMD)
        t.verify()
        if digest(read(TARGET))!=original_digest or digest(read(t.identity,private=True))!=identity_digest:
            raise Refused("unexpected_premutation_change")
        remove_backup()
        PENDING.unlink();Transaction._syncdir(ETC)
        print("R5_R2_ABORTED_PACKAGE_PREPARATION_RECOVERED_PASS")
        return
    if state["phase"]=="APPLYING":
        old,ident=backup_original()
        if digest(old)!=original_digest or digest(ident)!=identity_digest:
            raise Refused("upgrade_rollback_backup_changed")
        obj,payload,mhash=release()
        if mhash!=state["manifest_sha"] or obj["before"]!=original_digest:
            raise Refused("upgrade_manifest_swapped")
        if digest(read(TARGET)) not in (original_digest,obj["after"]):
            raise Refused("upgrade_partial_code_tamper")
        t=Transaction(ROOT,ETC,SYSTEMD)
        if digest(read(t.identity,private=True)) not in (
                identity_digest,digest(encoded({**json.loads(ident),
                    "entries":{**json.loads(ident)["entries"],
                               "managed_rpc.py":obj["after"]}}))):
            raise Refused("upgrade_partial_identity_tamper")
        atomic(TARGET,old,0o644)
        fixture_kill("recover-after-code")
        atomic(t.identity,ident)
        write_state("RESTORED",original_digest,identity_digest,state["manifest_sha"])
    if state["phase"]=="RESTORED" or pending_state()["phase"]=="RESTORED":
        t=Transaction(ROOT,ETC,SYSTEMD)
        if digest(read(TARGET))!=original_digest or digest(read(t.identity,private=True))!=identity_digest:
            raise Refused("restored_version_tampered")
        if RECORD.exists() or RECORD.is_symlink():
            read(RECORD,private=True)
            RECORD.unlink()
            Transaction._syncdir(ETC)
        remove_backup(partial=True)
        PENDING.unlink()
        Transaction._syncdir(ETC)
        t.verify()
        print("R5_R2_INTERRUPTED_UPGRADE_ROLLBACK_COMPLETE_PASS")

def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ("apply","recover","verify"):
        raise SystemExit("R5_R2_UPGRADE_REFUSED:action")
    if os.getuid()!=0 or os.geteuid()!=0:
        raise SystemExit("R5_R2_UPGRADE_REFUSED:root")
    vm=subprocess.run(["/usr/bin/systemd-detect-virt"],capture_output=True,text=True)
    if vm.returncode or vm.stdout.strip()!="qemu" or sorted(os.listdir("/sys/class/net"))!=["lo"]:
        raise SystemExit("R5_R2_UPGRADE_REFUSED:offline_qemu_only")
    try:
        globals()[sys.argv[1]]()
    except (OSError,ValueError,TypeError,Refused,InvalidSignature) as exc:
        raise SystemExit("R5_R2_UPGRADE_REFUSED:"+str(exc)) from None

if __name__=="__main__":
    main()
