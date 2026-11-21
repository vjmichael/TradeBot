"""R5-R1 durable root-owned offline installer file transaction.

This never handles API secrets, provider requests or approval. The CLI has
NO variable roots or caller-selected files. Test instances can use temp roots.
The independent Bash installer is responsible for systemd and QEMU custody.
A pending transaction is a hard fail-closed hold until explicit rollback.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

class Refused(Exception):
    pass

ORIGINAL=(
    "paper_credential_vault.py",
    "managed_rpc.py",
    "credential_graphical.py",
    "deploy/maj-vaj-paper-vault.service",
    "@systemd/maj-vaj-paper-vault.service",
)
IDENTITY=(
    "paper_credential_vault.py",
    "managed_rpc.py",
    "credential_graphical.py",
    "deploy/maj-vaj-paper-vault.service",
    "@systemd/maj-vaj-paper-vault.service",
    "@systemd/maj-vaj-r2b-approval.service",
    "@systemd/maj-vaj-r2b-synthetic-worker.service",
    "@systemd/maj-vaj-r2b-approval.service.d/10-r5-qemu-only.conf",
    "@systemd/maj-vaj-r2b-synthetic-worker.service.d/10-r5-qemu-only.conf",
    "@etc/credential-keys/r2b-worker-signing-key",
)
MAX_BYTES=2_000_000

class Transaction:
    def __init__(self, root:Path, etc:Path, systemd:Path):
        self.root, self.etc, self.systemd = Path(root),Path(etc),Path(systemd)
        self.backup=self.etc/"r2b-r5-rollback-snapshot"
        self.pending=self.etc/"r2b-r5-transaction-pending"
        self.identity=self.etc/"r2b-r5-installed-identity"

    def path(self, entry):
        if entry.startswith("@systemd/"):
            return self.systemd/entry.removeprefix("@systemd/")
        if entry.startswith("@etc/"):
            return self.etc/entry.removeprefix("@etc/")
        return self.root/entry

    @staticmethod
    def _secure(file, *, private=False):
        st=file.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid()
                or st.st_nlink!=1 or st.st_size>MAX_BYTES
                or st.st_mode & 0o022
                or (private and stat.S_IMODE(st.st_mode)!=0o600)):
            raise Refused("file_custody_refused")
        return st

    @classmethod
    def _bytes(cls,file, *, private=False):
        cls._secure(file,private=private)
        fd=os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>MAX_BYTES:
                raise Refused("file_custody_refused")
            data=os.read(fd,MAX_BYTES+1)
            if len(data)>MAX_BYTES:
                raise Refused("file_oversize")
            return data
        finally:
            os.close(fd)

    @staticmethod
    def _syncdir(directory):
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)

    @classmethod
    def _write(cls,path,data,mode=0o600):
        path=Path(path)
        # Never follow an existing destination, even through an atomic
        # replacement. The parent is a fixed root-owned installation path.
        if path.is_symlink():
            raise Refused("destination_symlink_refused")
        if path.exists():
            cls._secure(path)
        st=path.parent.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode & 0o022:
            raise Refused("parent_custody_refused")
        tmp=path.parent/("."+path.name+".r5new")
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode)
        try:
            os.fchmod(fd,mode)
            view=memoryview(data)
            while view:
                written=os.write(fd,view)
                if written<=0:raise Refused("atomic_write_failed")
                view=view[written:]
            os.fsync(fd)
        finally:os.close(fd)
        os.replace(tmp,path)
        cls._syncdir(path.parent)

    def _backup_name(self,entry):
        return entry.replace("/","__").replace("@","ROOT_")

    def _snapshot(self):
        st=self.backup.lstat()
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid()
                or stat.S_IMODE(st.st_mode)!=0o700):
            raise Refused("snapshot_directory_refused")
        manifest=json.loads(self._bytes(self.backup/"manifest.json",private=True))
        if type(manifest) is not dict or set(manifest)!={"version","entries"} or manifest["version"]!=1:
            raise Refused("snapshot_manifest_refused")
        if type(manifest["entries"]) is not dict or set(manifest["entries"])!=set(ORIGINAL):
            raise Refused("snapshot_manifest_refused")
        for entry in ORIGINAL:
            data=self._bytes(self.backup/self._backup_name(entry),private=True)
            if hashlib.sha256(data).hexdigest()!=manifest["entries"][entry]:
                raise Refused("snapshot_digest_refused")
        return manifest

    def prepare(self):
        if self.backup.exists() or self.backup.is_symlink() or self.pending.exists() or self.pending.is_symlink():
            raise Refused("unfinished_transaction_refused")
        if self.identity.exists() or self.identity.is_symlink():
            raise Refused("already_installed_refused")
        st=self.etc.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode & 0o022:
            raise Refused("configuration_directory_refused")
        self.backup.mkdir(mode=0o700)
        self._syncdir(self.etc)
        entries={}
        for entry in ORIGINAL:
            data=self._bytes(self.path(entry))
            entries[entry]=hashlib.sha256(data).hexdigest()
            self._write(self.backup/self._backup_name(entry),data)
        manifest={"version":1,"entries":entries}
        self._write(self.backup/"manifest.json",json.dumps(manifest,sort_keys=True,separators=(",",":")).encode())
        self._syncdir(self.backup)
        self._write(self.pending,b"R5-PENDING-ROLLBACK-ONLY\n")
        return True

    def _inventory(self):
        return {entry:hashlib.sha256(self._bytes(self.path(entry))).hexdigest()
                for entry in IDENTITY}

    def commit(self):
        self._snapshot()
        if self._bytes(self.pending,private=True)!=b"R5-PENDING-ROLLBACK-ONLY\n":
            raise Refused("pending_marker_refused")
        record={"version":1,"entries":self._inventory()}
        self._write(self.identity,json.dumps(record,sort_keys=True,separators=(",",":")).encode())
        self.pending.unlink()
        self._syncdir(self.etc)

    def verify(self):
        if self.pending.exists() or self.pending.is_symlink():
            raise Refused("pending_transaction_refused")
        self._snapshot()
        record=json.loads(self._bytes(self.identity,private=True))
        if (type(record) is not dict or set(record)!={"version","entries"}
                or record["version"]!=1 or type(record["entries"]) is not dict
                or set(record["entries"])!=set(IDENTITY)):
            raise Refused("identity_manifest_refused")
        if record["entries"]!=self._inventory():
            raise Refused("installed_identity_changed")
        return True

    def restore(self):
        self._snapshot()
        if self.pending.exists() or self.pending.is_symlink():
            if self._bytes(self.pending,private=True)!=b"R5-PENDING-ROLLBACK-ONLY\n":
                raise Refused("pending_marker_refused")
        for entry in ORIGINAL:
            data=self._bytes(self.backup/self._backup_name(entry),private=True)
            self._write(self.path(entry),data,mode=0o644)
        return True

    def cleanup(self):
        self._snapshot()
        for entry in ORIGINAL:
            backup=self.backup/self._backup_name(entry)
            if self._bytes(backup,private=True)!=self._bytes(self.path(entry)):
                raise Refused("rollback_not_restored")
        if self.identity.exists() or self.identity.is_symlink():
            self._secure(self.identity,private=True)
            self.identity.unlink()
        if self.pending.exists() or self.pending.is_symlink():
            self._secure(self.pending,private=True)
            self.pending.unlink()
        for entry in ORIGINAL:
            (self.backup/self._backup_name(entry)).unlink()
        (self.backup/"manifest.json").unlink()
        self.backup.rmdir()
        self._syncdir(self.etc)

def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ("prepare","commit","verify","restore","cleanup"):
        raise SystemExit("R5_R1_REFUSED:action")
    if os.getuid()!=0 or os.geteuid()!=0:
        raise SystemExit("R5_R1_REFUSED:root")
    # Bash's protected QEMU/network guard is repeated at this direct CLI.
    import subprocess
    vm=subprocess.run(["/usr/bin/systemd-detect-virt"],capture_output=True,text=True,check=False)
    if vm.returncode or vm.stdout.strip()!="qemu" or sorted(os.listdir("/sys/class/net"))!=["lo"]:
        raise SystemExit("R5_R1_REFUSED:offline_qemu")
    t=Transaction(Path("/opt/maj-vaj"),Path("/etc/maj-vaj"),Path("/etc/systemd/system"))
    try:
        getattr(t,sys.argv[1])()
    except (OSError,ValueError,TypeError,Refused) as exc:
        # Never print filenames, filesystem values or credentials.
        raise SystemExit("R5_R1_REFUSED:"+str(exc)) from None
    print("R5_R1_TRANSACTION_"+sys.argv[1].upper()+"_PASS")

if __name__=="__main__":
    main()
