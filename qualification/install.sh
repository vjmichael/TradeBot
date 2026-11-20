#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 && $(cat /proc/1/comm) == systemd ]]
[[ ! -e /opt/maj-vaj && ! -e /etc/maj-vaj && ! -e /var/lib/maj-vaj ]]
source="$1"
python="$2"
install -d -m755 /opt/maj-vaj
# Copy the verified, staged tree, including locally built locked assets.
tar -C "$source" --exclude=.git --exclude=node_modules -cf - . | tar -C /opt/maj-vaj -xf -
chmod -R a+rX,go-w /opt/maj-vaj
chown -R root:root /opt/maj-vaj
"$python" -m venv /opt/maj-vaj/venv
/opt/maj-vaj/venv/bin/python -m pip install --disable-pip-version-check -r /opt/maj-vaj/requirements.txt
/opt/maj-vaj/venv/bin/python -m pip check
for u in maj-vaj maj-vaj-vault maj-vaj-dashboard-auth maj-vaj-credential-operator maj-vaj-native maj-vaj-approval; do
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin "$u"
done
groupadd --system maj-vaj-transfer
install -d -m755 /etc/maj-vaj
install -d -m700 /etc/maj-vaj/credential-keys
install -d -o maj-vaj -g maj-vaj -m700 /var/lib/maj-vaj/data /var/lib/maj-vaj/data-recovery-copies
/opt/maj-vaj/venv/bin/python - <<'PY'
from pathlib import Path
from cryptography.fernet import Fernet
import os, sys
sys.path.insert(0, '/opt/maj-vaj/scripts')
from qualify_account_browser import TOKEN
os.umask(0o077)
for name,value in [('venue-vault-key',Fernet.generate_key()),('dashboard-token',TOKEN.encode())]:
    with open(Path('/etc/maj-vaj/credential-keys') / name,'xb') as f: f.write(value)
PY
for f in maj-vaj-paper-vault.service maj-vaj-paper-dashboard.service maj-vaj-paper-dashboard-backend.service maj-vaj-dashboard-backend.socket; do
 install -m644 /opt/maj-vaj/deploy/"$f" /etc/systemd/system/"$f"
 cmp /opt/maj-vaj/deploy/"$f" /etc/systemd/system/"$f"
done
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/maj-vaj-paper-vault.service /etc/systemd/system/maj-vaj-paper-dashboard.service /etc/systemd/system/maj-vaj-paper-dashboard-backend.service /etc/systemd/system/maj-vaj-dashboard-backend.socket
systemctl start maj-vaj-paper-vault.service maj-vaj-paper-dashboard.service || true
! systemctl is-active --quiet maj-vaj-paper-vault.service
! systemctl is-active --quiet maj-vaj-paper-dashboard.service
! systemctl is-active --quiet maj-vaj-dashboard-backend.socket
echo PASS_MARKER_REFUSAL
install -m644 /dev/null /etc/maj-vaj/paper-vault-enabled
install -m644 /dev/null /etc/maj-vaj/paper-dashboard-qualification-enabled
systemctl start maj-vaj-paper-vault.service maj-vaj-paper-dashboard.service
for i in $(seq 1 100); do
 if test -S /run/maj-vaj-paper-vault-status/status.sock && curl --max-time 2 -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then break; fi
 sleep 1
done
test -S /run/maj-vaj-paper-vault-status/status.sock
curl --max-time 3 -fsS http://127.0.0.1:8765/health >/dev/null
systemctl is-active maj-vaj-paper-vault.service maj-vaj-paper-dashboard.service maj-vaj-paper-dashboard-backend.service maj-vaj-dashboard-backend.socket
runuser -u maj-vaj-credential-operator -- /opt/maj-vaj/venv/bin/python -I -B -c 'import sys;sys.path[:0]=["/opt/maj-vaj","/opt/maj-vaj/scripts"];from paper_credential_vault import send;from qualify_account_browser import CANARY;assert send({"op":"enroll","values":{"grok":CANARY}})["ok"]'
echo PASS_INSTALLED_SERVICE_START_AND_SYNTHETIC_ENROLLMENT
