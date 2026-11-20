"""One-time local PAPER permit-key enrollment under the independent authority UID."""
import argparse
import getpass
import hashlib
import os
import pwd
import secrets
import stat
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from reserve_checkpoint import require, CheckpointError
from reserve_ipc import protected_dir, canonical, strict

DOMAIN = b'maj-vaj:PAPER:checkpoint-enrollment:v1'
NAME = 'checkpoint-credential.json'


def key(password, salt):
    require(type(password) is str and 16 <= len(password) <= 1024, 'invalid_passphrase')
    return hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64*1024*1024, dklen=32)


def principal(authority_uid, desk_uid):
    require(authority_uid > 0 and desk_uid > 0 and authority_uid != desk_uid and os.geteuid() == authority_uid, 'enrollment_identity')


def enroll(directory, password, *, authority_uid, desk_uid):
    principal(authority_uid, desk_uid)
    with protected_dir(directory, authority_uid, 0o700) as fd:
        seed = secrets.token_bytes(32); salt = secrets.token_bytes(32); nonce = secrets.token_bytes(12)
        public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        encrypted = AESGCM(key(password, salt)).encrypt(nonce, seed, DOMAIN + public)
        record = canonical({'v':1, 'salt':salt.hex(), 'nonce':nonce.hex(), 'public':public.hex(), 'ciphertext':encrypted.hex()})
        # Exclusive creation: no overwrite, even if earlier enrollment is incomplete.
        out = os.open(NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        with os.fdopen(out, 'wb') as f:
            f.write(record); f.flush(); os.fsync(f.fileno())
        os.fsync(fd)
        return public


def unlock(directory, password, *, authority_uid, desk_uid):
    principal(authority_uid, desk_uid)
    with protected_dir(directory, authority_uid, 0o700) as fd:
        raw = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        with os.fdopen(raw, 'rb') as f:
            s = os.fstat(f.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_uid == authority_uid and s.st_nlink == 1 and stat.S_IMODE(s.st_mode) == 0o600 and 0 < s.st_size <= 1024, 'credential_custody')
            record = strict(f.read(1025))
        require(set(record) == {'v','salt','nonce','public','ciphertext'} and type(record['v']) is int and record['v'] == 1, 'credential_schema')
        salt, nonce, public, ciphertext = (bytes.fromhex(record[x]) for x in ('salt','nonce','public','ciphertext'))
        require(tuple(map(len,(salt,nonce,public,ciphertext))) == (32,12,32,48), 'credential_size')
        try:
            seed = AESGCM(key(password,salt)).decrypt(nonce,ciphertext,DOMAIN+public)
        except InvalidTag as exc:
            raise CheckpointError('authentication_failed') from exc
        require(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw) == public, 'credential_public_mismatch')
        return seed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory'); p.add_argument('--authority-user', required=True); p.add_argument('--desk-user', required=True)
    a = p.parse_args(); authority = pwd.getpwnam(a.authority_user).pw_uid; desk = pwd.getpwnam(a.desk_user).pw_uid
    principal(authority, desk)
    require(os.isatty(0) and os.isatty(2), 'local_operator_terminal_required')
    password = getpass.getpass('New checkpoint passphrase: ')
    require(password == getpass.getpass('Repeat checkpoint passphrase: '), 'passphrase_mismatch')
    public = enroll(a.directory,password,authority_uid=authority,desk_uid=desk)
    print('Public key (independently review before root pinning):', public.hex())
    print('SHA256 fingerprint:', hashlib.sha256(public).hexdigest())


if __name__ == '__main__':
    main()
