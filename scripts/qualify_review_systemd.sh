#!/bin/bash
# Isolated guest only. Uses the prior independently provisioned pristine fixture.
set -euo pipefail
Q=/mnt/qualification
cd /opt/maj-vaj
[[ $(cat /proc/1/comm) == systemd && $(ls /sys/class/net) == lo ]]
[[ $(findmnt -n -o FSTYPE /sys/fs/cgroup) == cgroup2 ]]
venv/bin/python -B - <<'PY'
import sqlite3
with sqlite3.connect('/var/lib/maj-vaj/data/desk.sqlite3') as db:
 assert db.execute('select id,pair,virtual_cents,mode from instances').fetchall()==[('a'*32,'BTC/USD',10000,'PAPER')]
 assert db.execute('select paused,human_pause from desk').fetchall()==[(1,1)]
 assert db.execute('select slot,ciphertext from secrets').fetchall()==[('test',b'synthetic-vault-canary')]
 assert db.execute('select count(*) from gate_intents').fetchone()[0]==0
PY
U=maj-vaj-persistent-native.service
N=maj-vaj-reviewed-recovery-native.service
D=maj-vaj-reviewed-recovery-desk.service
for u in "$U" "$N" "$D" maj-vaj-entry-desk.service;do systemctl stop "$u";done
# Test-only display dependencies copied from the matching native Debian host,
# with manifest verification; no network and no production desktop changes.
B=/opt/maj-review-test-display
mkdir -p "$B"; tar xzf "$Q/r5r4-display.tar.gz" --no-same-owner -C "$B"
(cd "$B";sha256sum -c SHA256SUMS >/dev/null)
if [[ ! -e /usr/bin/xkbcomp ]]; then install -m755 "$B/bin/xkbcomp" /usr/bin/xkbcomp;fi
install -d -m1777 /tmp/.X11-unix
install -d -o maj-vaj-approval -g maj-vaj-approval -m700 /run/maj-vaj-review-display
A=/run/maj-vaj-review-display/Xauthority
setpriv --reuid=maj-vaj-approval --regid=maj-vaj-approval --clear-groups sh -c 'umask 077;touch "$1"' sh "$A"
COOKIE=$(venv/bin/python -c 'import secrets; print(secrets.token_hex(16))')
printf 'add :99 . %s\n' "$COOKIE" | setpriv --reuid=maj-vaj-approval --regid=maj-vaj-approval --clear-groups env LD_LIBRARY_PATH="$B/lib" "$B/bin/xauth" -f "$A" >/dev/null
unset COOKIE
systemd-run --unit=maj-review-test-display --property=User=maj-vaj-approval --property=Group=maj-vaj-approval --setenv=LD_LIBRARY_PATH="$B/lib" --setenv=FONTCONFIG_FILE="$B/fonts.conf" "$B/bin/Xvfb" :99 -auth "$A" -nolisten tcp -screen 0 1024x768x24 -xkbdir "$B/xkb" -fp built-ins >/dev/null
cleanup(){
 systemctl stop maj-vaj-reserve-relay.timer maj-vaj-terminal-desk.timer || true
 systemctl stop "$U" "$N" "$D" maj-vaj-entry-desk.service maj-vaj-terminal-desk.service maj-vaj-reserve-relay.service maj-review-test-display.service || true
 setpriv --reuid=maj-vaj --regid=maj-vaj --groups=maj-vaj-transfer venv/bin/python -B -c 'from reserve_runtime import desk_transaction; t=desk_transaction("/var/lib/maj-vaj/data");exec("with t() as db:\n db.execute(\"UPDATE desk SET paused=1,human_pause=1\")")'
 rm -f /run/systemd/system/$U.d/fixture.conf /run/systemd/system/$N.d/fixture.conf
 systemctl daemon-reload
}
trap cleanup EXIT
for ((i=0;i<25;i++));do [[ -S /tmp/.X11-unix/X99 ]] && break; sleep 1; done
[[ -S /tmp/.X11-unix/X99 ]]
# Real display auth: a desk process has no authority user's cookie and must fail.
if setpriv --reuid=maj-vaj --regid=maj-vaj --clear-groups env DISPLAY=:99 XAUTHORITY=/dev/null LD_LIBRARY_PATH="$B/lib" FONTCONFIG_FILE="$B/fonts.conf" venv/bin/python -B -c 'import tkinter as tk;tk.Tk()' >/dev/null 2>&1; then exit 21;fi
setpriv --reuid=maj-vaj-approval --regid=maj-vaj-approval --clear-groups env DISPLAY=:99 XAUTHORITY="$A" LD_LIBRARY_PATH="$B/lib" FONTCONFIG_FILE="$B/fonts.conf" venv/bin/python -B -c 'import tkinter as tk;r=tk.Tk();r.update();r.destroy()'
echo 'PASS isolated authenticated graphical display; desk cannot attach'
for spec in "$U worker" "$N proof";do
 read -r unit role <<<"$spec"
 install -d -m755 /run/systemd/system/$unit.d
 cat >/run/systemd/system/$unit.d/fixture.conf <<UNIT
[Service]
ExecStart=
ExecStart=/opt/maj-vaj/venv/bin/python -B /opt/maj-vaj/scripts/qualify_review_systemd.py $role
StandardOutput=journal
StandardError=journal
UNIT
done
systemctl daemon-reload
systemctl start maj-vaj-reserve-relay.service
systemctl start maj-vaj-reserve-relay.timer maj-vaj-terminal-desk.timer
review(){
 # Fixed transient local graphical authority service (synthetic input only).
 systemd-run --unit="maj-review-test-$1-$2" --wait --pipe --collect --property=User=maj-vaj-approval --property=Group=maj-vaj-approval --property=SupplementaryGroups=maj-vaj-recovery --property=NoNewPrivileges=yes --property=ProtectSystem=strict --property=ProtectHome=yes --property=ReadWritePaths=/var/lib/maj-vaj-recovery-human --property=InaccessiblePaths='/var/lib/maj-vaj /var/lib/maj-vaj-native /var/lib/maj-vaj-native-signing /var/lib/maj-vaj-reserve-relay' --property=RestrictAddressFamilies=AF_UNIX --property=LimitCORE=0 --setenv=DISPLAY=:99 --setenv=XAUTHORITY="$A" --setenv=LD_LIBRARY_PATH="$B/lib" --setenv=FONTCONFIG_FILE="$B/fonts.conf" /opt/maj-vaj/venv/bin/python -B /opt/maj-vaj/scripts/qualify_review_systemd.py "$1"
}
for cycle in 1 2;do
 start=$(date --iso-8601=seconds)
 systemctl reset-failed "$U" || true
 systemctl start "$U"
 P=$(systemctl show -P MainPID "$U"); [[ $P -gt 0 ]]
 for ((i=0;i<45;i++));do
  if setpriv --reuid=maj-vaj --regid=maj-vaj --groups=maj-vaj-transfer venv/bin/python -B -c 'import time;from reserve_entry_worker import installed_gate;from reserve_runtime import desk_transaction;t=desk_transaction("/var/lib/maj-vaj/data");g,_,_=installed_gate(t);exec("with t() as db:\n assert g.account_projection(db,int(time.time()))[0].settled_cash_cents==10000")' >/dev/null 2>&1;then break;fi
  sleep 2
 done
 setpriv --reuid=maj-vaj --regid=maj-vaj --groups=maj-vaj-transfer venv/bin/python -B scripts/qualify_review_systemd.py admit "$cycle"
 for ((i=0;i<60;i++));do
  if journalctl -b --since="$start" -u "$U" -o cat | grep -q '^CUT_PREPARED$';then break;fi
  sleep 1
 done
 journalctl -b --since="$start" -u "$U" -o cat | grep -q '^CUT_PREPARED$'
 [[ $(systemctl show -P MainPID "$U") == "$P" ]]
 # Stop manager kills stalled worker at unchanged stop timeout; no new attempt.
 systemctl stop "$U" || true
 [[ $(systemctl show -P MainPID "$U") == 0 ]]
 setpriv --reuid=maj-vaj --regid=maj-vaj --groups=maj-vaj-transfer venv/bin/python -B -c 'from reserve_runtime import desk_transaction; t=desk_transaction("/var/lib/maj-vaj/data");exec("with t() as db:\n db.execute(\"UPDATE desk SET paused=1,human_pause=1\")")'
 systemctl start maj-vaj-reserve-relay.service
 systemctl start "$N"
 [[ -f /var/lib/maj-vaj-recovery-native/pending.json ]]
 systemctl start "$D" # no approval yet
 CYCLE=$cycle venv/bin/python -B - <<'PY'
import os,sqlite3
from financial_ledger import gate_reserve
with sqlite3.connect('/var/lib/maj-vaj/data/desk.sqlite3',isolation_level=None) as db:
 db.row_factory=sqlite3.Row;db.execute('BEGIN');assert gate_reserve(db,'a'*32)>0
 assert db.execute("SELECT count(*) FROM gate_events WHERE action='native_reviewed_no_effect'").fetchone()[0]==int(os.environ['CYCLE'])-1
with sqlite3.connect('/var/lib/maj-vaj-native/paper/'+'a'*32+'/paper.sqlite') as db:
 assert db.execute('SELECT count(*) FROM orders').fetchone()[0]==0
PY
 if [[ $cycle == 1 ]];then
  review cancel "$cycle";[[ ! -e /var/lib/maj-vaj-recovery-human/pending.json ]]
  review wrong "$cycle";[[ ! -e /var/lib/maj-vaj-recovery-human/pending.json ]]
 fi
 review confirm "$cycle"
 [[ -f /var/lib/maj-vaj-recovery-human/pending.json ]]
 systemctl start "$D";systemctl start "$D"
 systemctl start maj-vaj-reserve-relay.service
 systemctl start "$N" # native archive committed, authority copy still pending
 [[ -f /var/lib/maj-vaj-recovery-native/pending.json && -f /var/lib/maj-vaj-recovery-human/pending.json ]]
 review archive "$cycle"
 [[ ! -e /var/lib/maj-vaj-recovery-human/pending.json ]]
 systemctl start "$N"
 [[ ! -e /var/lib/maj-vaj-recovery-native/pending.json ]]
 CYCLE=$cycle venv/bin/python -B - <<'PY'
import os,sqlite3
from pathlib import Path
from financial_ledger import replay,observed,gate_reserve,SCALE,sha
from reserve_recovery_archive import verify_archive
from reserve_ipc import pinned_key,strict
c=int(os.environ['CYCLE']);p=Path('/var/lib/maj-vaj-recovery-native');a=Path('/var/lib/maj-vaj-recovery-human');files=list(p.glob('review-*.json'))
assert len(files)==len(list(a.glob('review-*.json')))==c
for f in files:
 assert f.read_bytes()==(a/f.name).read_bytes();b=verify_archive(pinned_key('/etc/maj-vaj/reserve-native.pub'),pinned_key('/etc/maj-vaj/reserve-checkpoint.pub'),'installed-paper',strict(f.read_bytes()));assert b['status']=='accounted'
with sqlite3.connect('/var/lib/maj-vaj/data/desk.sqlite3',isolation_level=None) as db:
 db.row_factory=sqlite3.Row;db.execute('BEGIN');v=observed(replay(db)[0]['a'*32]);assert v['active']==100*SCALE and v['protected']==v['qty']==v['pending']==gate_reserve(db,'a'*32)==0
 assert tuple(db.execute('SELECT paused,human_pause FROM desk').fetchone())==(1,1)
 assert db.execute("SELECT count(*) FROM gate_events WHERE action='native_reviewed_no_effect'").fetchone()[0]==c
 assert db.execute("SELECT count(*) FROM gate_intents WHERE state='NO_FILL'").fetchone()[0]==c
with sqlite3.connect('/var/lib/maj-vaj-native/paper/'+'a'*32+'/paper.sqlite') as db:assert db.execute('SELECT count(*) FROM orders').fetchone()[0]==0
print('PASS cycle',c,'exact signed archive copies, no orders/reservation, paused principal unchanged')
PY
done
