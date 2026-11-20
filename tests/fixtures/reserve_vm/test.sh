#!/bin/bash
set -Eeuo pipefail
trap 'echo "FAIL line=$LINENO"' ERR
[[ $(id -u) == 0 && $(systemd-detect-virt) == qemu && $(ls /sys/class/net) == lo && $(findmnt -n -o FSTYPE /sys/fs/cgroup) == cgroup2 ]]
useradd --system --user-group --no-create-home --shell /usr/sbin/nologin maj-vaj
install -d -m 755 /opt/maj-vaj /etc/maj-vaj /opt/maj-vaj/venv/bin
cp -r /mnt/test/. /opt/maj-vaj/
ln -s /usr/bin/python3 /opt/maj-vaj/venv/bin/python
install -d -m 700 -o maj-vaj -g maj-vaj /var/lib/maj-vaj /var/lib/maj-vaj/data /var/lib/maj-vaj/approval
export PYTHONPATH=/opt/maj-vaj:/opt/maj-vaj/tests:/opt/maj-vaj/deps PYTHONDONTWRITEBYTECODE=1
cd /opt/maj-vaj
runuser -u maj-vaj -- /usr/bin/python3 /opt/maj-vaj/prepare_os.py
install -m 644 -o root -g root /var/lib/maj-vaj/data/public-export /etc/maj-vaj/reserve-approval.pub
rm /var/lib/maj-vaj/data/public-export
# Verify key is readable outside service namespace, proving boundary rather than a missing file.
runuser -u maj-vaj -- test -r /var/lib/maj-vaj/approval/reserve-approval.json
install -m 644 /opt/maj-vaj/maj-vaj-reserve.{service,timer} /etc/systemd/system/
mkdir /etc/systemd/system/maj-vaj-reserve.service.d
cat >/etc/systemd/system/maj-vaj-reserve.service.d/fixture-deps.conf <<'EOF'
[Service]
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONPATH=/opt/maj-vaj:/opt/maj-vaj/deps
EOF
systemctl daemon-reload
systemctl start maj-vaj-reserve.service
[[ $(systemctl show -p Result --value maj-vaj-reserve.service) == success ]]
journalctl -u maj-vaj-reserve.service -o cat --no-pager | grep -Fx '{}'
echo 'RECEIPT: installed exact reserve scheduler executed OFF desk without modification'
# Observe an actual timer dispatch of the unchanged production scheduler.
systemctl start maj-vaj-reserve.timer
for i in $(seq 1 40); do
  [[ $(journalctl -u maj-vaj-reserve.service -o cat --no-pager | grep -Fxc '{}') -ge 2 ]] && break
  sleep 1
done
[[ $(journalctl -u maj-vaj-reserve.service -o cat --no-pager | grep -Fxc '{}') -ge 2 ]]
systemctl stop maj-vaj-reserve.timer
for i in $(seq 1 10); do
  [[ $(systemctl show -p ActiveState --value maj-vaj-reserve.service) == inactive ]] && break
  sleep 1
done
[[ $(systemctl show -p ActiveState --value maj-vaj-reserve.service) == inactive ]]
[[ $(systemctl show -p Result --value maj-vaj-reserve.service) == success ]]
echo 'RECEIPT: genuine systemd UTC timer triggered scheduler and stopped'
# Same exact sandbox directives, probe only instead of worker command.
cat >/etc/systemd/system/maj-vaj-reserve.service.d/probe.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/opt/maj-vaj/venv/bin/python /opt/maj-vaj/custody_probe.py
EOF
systemctl daemon-reload
systemctl start maj-vaj-reserve.service
journalctl -u maj-vaj-reserve.service -o cat --no-pager
[[ $(systemctl show -p Result --value maj-vaj-reserve.service) == success ]]
