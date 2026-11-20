#!/bin/bash
set -euo pipefail
[[ $(id -u) == 0 && $(systemd-detect-virt) == qemu && $(ls /sys/class/net) == lo ]]
[[ ! -e /etc/maj-vaj && ! -e /var/lib/maj-vaj ]]
for u in maj-vaj maj-vaj-vault maj-vaj-dashboard-auth maj-vaj-native maj-vaj-approval; do
 useradd --system --user-group --no-create-home --shell /usr/sbin/nologin "$u"
done
for u in maj-vaj-console maj-vaj-credential-operator; do
 useradd --create-home --user-group --shell /bin/bash "$u"
 chmod 700 "/home/$u"
done
printf 'maj-vaj-console:d2-console-fixture-813\nmaj-vaj-credential-operator:d2-operator-fixture-916\n' | chpasswd
for pair in 'maj-vaj-console:maj-vaj-console' 'maj-vaj-credential-operator:maj-vaj-account-connections'; do
 u=${pair%%:*}; session=${pair#*:}
 printf '[Desktop]\nSession=%s\n' "$session" > "/home/$u/.dmrc"
 chown "$u:$u" "/home/$u/.dmrc"; chmod 600 "/home/$u/.dmrc"
done
groupadd --system maj-vaj-transfer
install -d -m755 /etc/maj-vaj
install -d -m700 /etc/maj-vaj/credential-keys
install -d -o maj-vaj -g maj-vaj -m700 /var/lib/maj-vaj/data /var/lib/maj-vaj/data-recovery-copies
/opt/maj-vaj/venv/bin/python - <<'PY'
from pathlib import Path
from cryptography.fernet import Fernet
import os,sys
sys.path.insert(0,'/opt/maj-vaj/scripts')
from qualify_account_browser import TOKEN
os.umask(0o077)
for name,value in [('venue-vault-key',Fernet.generate_key()),('dashboard-token',TOKEN.encode()),('provider-tag-key',os.urandom(32))]:
    with open(Path('/etc/maj-vaj/credential-keys')/name,'xb') as f:f.write(value)
PY
for f in maj-vaj-paper-vault.service maj-vaj-paper-dashboard.service maj-vaj-paper-dashboard-backend.service maj-vaj-dashboard-backend.socket; do
 install -m644 /opt/maj-vaj/deploy/"$f" /etc/systemd/system/"$f"
 cmp /opt/maj-vaj/deploy/"$f" /etc/systemd/system/"$f"
done
systemctl daemon-reload
systemctl start maj-vaj-paper-vault.service maj-vaj-paper-dashboard.service || true
! systemctl is-active --quiet maj-vaj-paper-vault.service
! systemctl is-active --quiet maj-vaj-paper-dashboard.service
install -m644 /dev/null /etc/maj-vaj/paper-vault-enabled
install -m644 /dev/null /etc/maj-vaj/paper-dashboard-qualification-enabled
systemctl start maj-vaj-paper-vault.service maj-vaj-paper-dashboard.service
for i in $(seq 1 100); do
 if test -S /run/maj-vaj-paper-vault-status/status.sock && curl --max-time 2 -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then break; fi
 sleep 1
done
test -S /run/maj-vaj-paper-vault-status/status.sock
curl --max-time 3 -fsS http://127.0.0.1:8765/health >/dev/null
systemctl is-active maj-vaj-paper-vault.service maj-vaj-paper-dashboard.service maj-vaj-paper-dashboard-backend.service
bash /opt/maj-vaj/scripts/provision_credential_desktop.sh --install
bash /opt/maj-vaj/scripts/provision_account_handoff.sh --install
install -d -m755 /etc/lightdm/lightdm.conf.d
cat >/etc/lightdm/lightdm.conf.d/99-maj-vaj-d2.conf <<'CONF'
[Seat:*]
greeter-session=lightdm-gtk-greeter
user-session=maj-vaj-console
greeter-hide-users=true
greeter-show-manual-login=true
allow-guest=false
autologin-user=
xserver-allow-tcp=false
user-authority-in-system-dir=false
session-wrapper=/opt/maj-vaj/scripts/review_session_wrapper.sh
CONF
install -m644 /dev/null /etc/maj-vaj/account-handoff-enabled
install -m644 /dev/null /etc/maj-vaj/credential-desktop-enabled
if runuser -u maj-vaj-console -- /opt/maj-vaj/scripts/account_handoff_wrapper.sh majvaj-account:open >/dev/null 2>&1;then exit 1;fi
if runuser -u maj-vaj-console -- /opt/maj-vaj/venv/bin/python -I -B -c 'import sys;sys.path.insert(0,"/opt/maj-vaj");from paper_credential_vault import send;assert send({"op":"status"})["ok"]' >/dev/null 2>&1;then exit 1;fi
echo PASS_INACTIVE_AND_ENROLLMENT_REFUSAL
systemctl start lightdm
systemctl is-active lightdm
test -s /etc/maj-vaj/credential-keys/provider-tag-key
systemctl is-active maj-vaj-paper-vault.service
# Runtime health is current status only; no assertion that internet is available.
echo PASS_INSTALLED_R2B_PREFLIGHT
