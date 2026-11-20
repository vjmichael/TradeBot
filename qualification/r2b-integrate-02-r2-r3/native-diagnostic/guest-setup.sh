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
# DIAGNOSTIC FIXTURE ONLY. This changes the *disposable VM's* desktop
# Exec target and inserts a fixed-code refusal reporter. It is NOT an
# acceptance of the unchanged product. Never install on a real host.
cat >/opt/maj-vaj/scripts/r2b-dispatch-probe.sh <<'PROBE'
#!/bin/sh
set -u
[ "$#" -eq 1 ] && [ "$1" = 'majvaj-account:open' ] || exit 1
dir="/run/user/$(id -u)"
printf 'wrapper_invoked\n' >"$dir/r2b-dispatch-invoked"
# Exit code only; no argv, environment, secrets or native stderr in artifacts.
/opt/maj-vaj/scripts/account_handoff_wrapper.sh "$1" >/dev/null 2>&1
status=$?
if [ "$status" -eq 0 ]; then
  printf 'exited_zero\n' >"$dir/r2b-dispatch-exit"
else
  printf 'exited_nonzero\n' >"$dir/r2b-dispatch-exit"
fi
exit "$status"
PROBE
chmod 755 /opt/maj-vaj/scripts/r2b-dispatch-probe.sh
python3 - <<'PYDIAG'
from pathlib import Path
p=Path('/opt/maj-vaj/credential_handoff.py')
source=p.read_text()
before="""    except Exception:
        raise SystemExit('Local Maj Vaj account handoff unavailable. Sign out and choose Account connections at login.') from None"""
after="""    except Exception as exc:
        # Disposable VM diagnosis: only fixed allowlisted refusal identifiers,
        # never exception messages, secrets, process arguments or stack traces.
        import re
        reason=str(exc)
        if re.fullmatch(r'(?:account|review)_[a-z0-9_]{1,64}',reason) is None:
            reason='opaque_other'
        try:
            Path(f'/run/user/{os.getuid()}/r2b-dispatch-guard').write_text(reason+'\\\\n')
        except OSError:
            pass
        raise SystemExit('Local Maj Vaj account handoff unavailable. Sign out and choose Account connections at login.') from None"""
assert source.count(before)==1
p.write_text(source.replace(before,after))
desktop=Path('/usr/share/applications/maj-vaj-account-handoff.desktop')
entry=desktop.read_text()
expected="Exec=/opt/maj-vaj/scripts/account_handoff_wrapper.sh %u"
assert entry.count(expected)==1
desktop.write_text(entry.replace(expected,"Exec=/opt/maj-vaj/scripts/r2b-dispatch-probe.sh %u"))
PYDIAG
update-desktop-database /usr/share/applications
systemctl start lightdm
systemctl is-active lightdm
test -s /etc/maj-vaj/credential-keys/provider-tag-key
systemctl is-active maj-vaj-paper-vault.service
# Runtime health is current status only; no assertion that internet is available.
echo PASS_INSTALLED_R2B_PREFLIGHT
