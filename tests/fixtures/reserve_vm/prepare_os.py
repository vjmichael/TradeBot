from pathlib import Path
import shutil,os
from reserve_runtime import provision,unlock
from reserve_authority import public_key
from test_reserve_policy import ReserveTests
h=ReserveTests('test_default_saved_inactive_and_nonmutating_preview');h.setUp()
try:
    with h.h.tx() as db:db.execute('PRAGMA user_version=4')
    dst=Path('/var/lib/maj-vaj/data/desk.sqlite3');shutil.copyfile(h.h.path,dst);dst.chmod(0o600)
    creds=Path('/var/lib/maj-vaj/approval');provision(creds,'only-temporary-guest-fixture-password')
    Path('/var/lib/maj-vaj/data/public-export').write_bytes(public_key(unlock(creds,'only-temporary-guest-fixture-password')))
finally:h.doCleanups()
