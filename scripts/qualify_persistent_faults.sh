#!/bin/bash
# Only a disposable, offline guest, already provisioned from the accepted source.
set -euo pipefail
Q=/mnt/qualification
cd /opt/maj-vaj
[[ $(cat /proc/1/comm) == systemd && $(ls /sys/class/net) == lo ]]
[[ $(findmnt -n -o FSTYPE /sys/fs/cgroup) == cgroup2 ]]
# Refuse an ordinary installed desk: only the pristine synthetic qualification
# portfolio and sole synthetic vault canary are permitted.
venv/bin/python -B - <<'CHECK'
import sqlite3
with sqlite3.connect('/var/lib/maj-vaj/data/desk.sqlite3',isolation_level=None) as db:
 assert db.execute('select id,pair,virtual_cents,mode from instances').fetchall()==[('a'*32,'BTC/USD',10000,'PAPER')]
 assert db.execute('select paused,human_pause from desk').fetchall()==[(1,1)]
 assert db.execute('select slot,ciphertext from secrets').fetchall()==[('test',b'synthetic-vault-canary')]
 assert db.execute('select count(*) from gate_intents').fetchone()[0]==0
with sqlite3.connect('/var/lib/maj-vaj-native/paper/'+'a'*32+'/paper.sqlite') as db:
 assert db.execute('select count(*) from trades').fetchone()[0]==0
CHECK
mode=$1
[[ $mode == watchdog || $mode == partial ]]
U=maj-vaj-persistent-native.service
start=$(date --iso-8601=seconds)
for u in maj-vaj-persistent-native maj-vaj-entry-desk maj-vaj-reserve-relay maj-vaj-terminal-desk; do [[ $(systemctl show -P MainPID "$u.service") == 0 ]]; done
# Root-owned fixture script copied into the isolated installation, not startup code.
install -o root -g root -m644 "$Q/qualify_persistent_faults.py" scripts/qualify_persistent_faults.py
install -d -m755 /run/systemd/system/$U.d
cat > /run/systemd/system/$U.d/fixture.conf <<UNIT
[Service]
ExecStart=
ExecStart=/opt/maj-vaj/venv/bin/python -B /opt/maj-vaj/scripts/qualify_persistent_faults.py $mode
StandardOutput=journal
StandardError=journal
UNIT
cleanup(){
 systemctl stop maj-vaj-reserve-relay.timer maj-vaj-terminal-desk.timer || true
 systemctl stop "$U" maj-vaj-entry-desk.service maj-vaj-terminal-desk.service maj-vaj-reserve-relay.service || true
 setpriv --reuid=maj-vaj --regid=maj-vaj --groups=maj-vaj-transfer venv/bin/python -B -c 'from reserve_runtime import desk_transaction; t=desk_transaction("/var/lib/maj-vaj/data");exec("with t() as db:\n db.execute(\"UPDATE desk SET paused=1,human_pause=1\")")'
 rm -f /run/systemd/system/$U.d/fixture.conf
 systemctl daemon-reload
}
trap cleanup EXIT
systemctl daemon-reload
[[ $(systemctl show -P TimeoutStartUSec "$U") == 2min ]]
systemctl start maj-vaj-reserve-relay.service
systemctl start maj-vaj-reserve-relay.timer maj-vaj-terminal-desk.timer
systemctl start "$U"
P=$(systemctl show -P MainPID "$U")
[[ $(systemctl show -P WatchdogUSec "$U") == 30s ]]
[[ $P -gt 0 && $(systemctl show -P Type "$U") == notify ]]
C=$(systemctl show -P ControlGroup "$U"); grep -qx "$P" "/sys/fs/cgroup$C/cgroup.procs"
echo "PASS $mode installed readiness and unchanged watchdog"
for ((i=0;i<40;i++));do
 if setpriv --reuid=maj-vaj --regid=maj-vaj --groups=maj-vaj-transfer venv/bin/python -B -c 'import time;from reserve_entry_worker import installed_gate;from reserve_runtime import desk_transaction;t=desk_transaction("/var/lib/maj-vaj/data");g,_,_=installed_gate(t);exec("with t() as db:\n assert g.account_projection(db,int(time.time()))[0].settled_cash_cents==10000")' >/dev/null 2>&1;then break;fi
 sleep 2
done
setpriv --reuid=maj-vaj --regid=maj-vaj --groups=maj-vaj-transfer venv/bin/python -B scripts/qualify_persistent_systemd_fixture.py admit
cut=CUT_PERSISTED_BUY; [[ $mode != partial ]] || cut=CUT_FROZEN_PARTIAL
for ((i=0;i<80;i++));do
 if journalctl -b --since="$start" -u "$U" -o cat | grep -q "^$cut$"; then break;fi
 sleep 1
done
journalctl -b --since="$start" -u "$U" -o cat | grep -q "^$cut$"
[[ $(systemctl show -P MainPID "$U") == "$P" ]]
# Positive receipt/actual order and still-held reservation before death.
MODE=$mode venv/bin/python -B - <<'PY'
import os,sqlite3
from financial_ledger import replay,observed,gate_reserve,SCALE
with sqlite3.connect('/var/lib/maj-vaj/data/desk.sqlite3',isolation_level=None) as db:
 db.row_factory=sqlite3.Row;db.execute('BEGIN'); assert db.execute("select state from gate_intents").fetchone()[0]=='CLAIMED'
 assert gate_reserve(db,'a'*32)>0 and db.execute("select count(*) from gate_events where action='native_attempt'").fetchone()[0]==1
 assert db.execute("select count(*) from gate_events where action='native_return'").fetchone()[0]==0
with sqlite3.connect('/var/lib/maj-vaj-native/paper/'+'a'*32+'/paper.sqlite') as db:
 assert db.execute('select count(*) from orders').fetchone()[0]==1
 if os.environ['MODE']=='partial':assert db.execute('select count(*) from orders where ft_is_open=1').fetchone()[0]==1
if os.environ['MODE']=='partial':
 with sqlite3.connect('/var/lib/maj-vaj-native/paper/'+'a'*32+'/cancel-receipts.sqlite') as db:
  assert db.execute("select count(*) from gate_events where action='native_cancel_frozen'").fetchone()[0]==1
print('PASS persisted in-flight evidence and retained reservation')
PY
if [[ $mode == partial ]]; then systemctl kill --kill-whom=main --signal=SIGKILL "$U"; fi
for ((i=0;i<110;i++));do
 NEW=$(systemctl show -P MainPID "$U")
 if [[ $NEW -gt 0 && $NEW != "$P" ]] && systemctl is-active --quiet "$U"; then break;fi
 sleep 2
done
[[ $NEW -gt 0 && $NEW != "$P" && ! -e /proc/$P ]];systemctl is-active --quiet "$U"
if [[ $mode == watchdog ]]; then journalctl -b --since="$start" -u "$U" -o cat | grep -qi 'watchdog timeout'; fi
[[ $(systemctl show -P NRestarts "$U") -ge 1 ]]
echo "PASS $mode systemd interruption and replacement readiness"
for ((i=0;i<90;i++));do
 if /usr/bin/python3 -c 'import sqlite3;d=sqlite3.connect("/var/lib/maj-vaj/data/desk.sqlite3");assert d.execute("select count(*) from gate_events where action=\x27native_released\x27").fetchone()[0]==1' >/dev/null 2>&1;then break;fi
 sleep 2
done
MODE=$mode venv/bin/python -B - <<'PY'
import os,sqlite3
from financial_ledger import replay,observed,gate_reserve,SCALE
with sqlite3.connect('/var/lib/maj-vaj/data/desk.sqlite3',isolation_level=None) as db:
 db.row_factory=sqlite3.Row;db.execute('BEGIN');v=observed(replay(db)[0]['a'*32]);gain=378000000 if os.environ['MODE']=='partial' else 489510000
 assert v['active']==100*SCALE and v['protected']==gain and v['qty']==v['pending']==gate_reserve(db,'a'*32)==0,v
 for a,n in [('native_attempt',1),('native_return',0),('native_released',1)]:assert db.execute('select count(*) from gate_events where action=?',(a,)).fetchone()[0]==n,a
with sqlite3.connect('/var/lib/maj-vaj-native/paper/'+'a'*32+'/paper.sqlite') as db:
 assert db.execute('select count(*) from trades').fetchone()[0]==1
 assert db.execute('select count(*) from orders').fetchone()[0]==2
 assert db.execute('select count(*) from orders where ft_is_open=1').fetchone()[0]==0
print('PASS exact single terminal settlement after interruption; active principal intact; protected units',v['protected'])
PY
[[ $(systemctl show -P MainPID "$U") == "$NEW" ]]
echo "PASS $mode settled with retained replacement runtime"
