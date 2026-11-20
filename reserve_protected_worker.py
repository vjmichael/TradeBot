"""Staged installed reserve scheduler: signed delivery only, no legacy fallback."""
import argparse
import json
import pwd
from pathlib import Path
from reserve_checkpoint import require
from reserve_ipc import pinned_key
from reserve_recovery import RecoveryClient
from reserve_delivery import Mailbox, schedule_once


def main():
    from reserve_runtime import desk_transaction
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('data',type=Path)
    a=p.parse_args()
    authority=pwd.getpwnam('maj-vaj-approval').pw_uid
    desk=pwd.getpwnam('maj-vaj').pw_uid
    native=pwd.getpwnam('maj-vaj-native').pw_uid
    require(len({authority,desk,native})==3 and min(authority,desk,native)>0,'independent_principals_required')
    public=pinned_key('/etc/maj-vaj/reserve-checkpoint.pub')
    # Loss of native certification blocks adoption and cycles, not revocation.
    try:native_public=pinned_key('/etc/maj-vaj/reserve-native.pub')
    except (ValueError,OSError):native_public=None
    if native_public==public:native_public=None
    client=RecoveryClient('/run/maj-vaj-reserve-authority',authority_uid=authority,desk_uid=desk,workspace='installed-paper')
    result=schedule_once(desk_transaction(a.data),client,Mailbox('/var/lib/maj-vaj-reserve-human-outbox',authority,'human'),Mailbox('/var/lib/maj-vaj-reserve-native-outbox',native,'native'),public,native_public)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
