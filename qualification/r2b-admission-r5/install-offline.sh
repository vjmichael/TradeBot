#!/usr/bin/env bash
# R5 source-locked, *QEMU-only* offline synthetic custody installer.
# Never installs a real provider adapter, venue access or LIVE authority.
set -euo pipefail
umask 077
root=/opt/maj-vaj
config=/etc/maj-vaj
marker="$config/r2b-approval-test-enabled"
seal="$config/r2b-r5-offline-installed"
vault=maj-vaj-paper-vault.service
broker=maj-vaj-r2b-approval.service
worker=maj-vaj-r2b-synthetic-worker.service
key="$config/credential-keys/r2b-worker-signing-key"
die() { echo "R5_INSTALL_REFUSED:$1" >&2; exit 1; }
[[ $# == 1 ]] || die argument_count
action="$1"
case "$action" in install|enable|disable|verify) ;; *) die action ;; esac
[[ $(id -u) == 0 ]] || die root_required
[[ $(systemd-detect-virt) == qemu ]] || die offline_qemu_only
[[ "$(ls -1 /sys/class/net)" == lo ]] || die network_interface_refused
[[ -d "$root" && ! -L "$root" && -d "$config" && ! -L "$config" ]] || die fixed_directory_refused
[[ "$(stat -c '%u' "$root")" == 0 && "$(stat -c '%u' "$config")" == 0 ]] || die root_custody_refused
[[ -d "$config/credential-keys" && ! -L "$config/credential-keys" ]] || die credential_directory_refused
[[ "$(stat -c '%u:%a' "$config/credential-keys")" == '0:700' ]] || die credential_directory_custody_refused

check_bundle() {
  /usr/bin/python3 -I -B - "$root" <<'PY'
import hashlib, pathlib, stat, sys
base=pathlib.Path(sys.argv[1])
# Original accepted R3/R4 Git blob identity; no caller-named modules.
expected={
 'r2b-admission-r1/broker_service.py':'c25093157840a38adb838377d17e62b707c489b3',
 'r2b-admission-r1/owner_execution_bridge.py':'882875e35b046a13a94ffb7f24ca52c7d70c37d7',
 'r2b-admission-r2/approval_ledger.py':'39cd2c1b06823f025655b2191bff45988ef2c858',
 'r2b-admission-r2/operator_session.py':'b61a656740989cb6b284498503837d2252258fec',
 'r2b-admission-r2/broker_gateway.py':'22155167d8ce887770c2b7b500341e358268a0f0',
 'r2b-admission-r2/receipt_channel.py':'c1550d83d299ae061e71e00e2e937139f6f84618',
 'r2b-admission-r2/worker_receipt_verifier.py':'6d3dd869086febb21b2c1f8a50cd1fcc8d62e0dc',
 'r2b-admission-r2/native_agent_protocol.py':'62bc716449395b8fbd3aeb933cf3cd37e5d8c257',
 'r2b-admission-r2/agent_broker_channel.py':'5b04b1d8ec8db889f1f55628ec4d1a867db39b68',
 'r2b-admission-r2/broker_service_r2.py':'a2537e0f1a2a2b9a9e61ee69c2898d943d82f94b',
 'r2b-admission-r2/synthetic_worker_protocol.py':'a26e5651658bafbd0bf86dc1af3d83a1d1d7d217',
 'r2b-admission-r2/synthetic_worker_service.py':'3f16e6519215f03af81a374f643303fa78e4a293',
 'r2b-admission-r2/owner_synthetic_bridge.py':'eb22638d04f105bda522cbf0a143419407562b87',
 'r2b-admission-r2/owner_rpc_extension.py':'5aa2b11afd1f3108f6c7d16cb3b7639271371054',
 'r2b-admission-r2/patch_owner_vault.py':'c647db518e922f352acc0ce19e22e4aa32608393',
 'r2b-admission-r2/maj-vaj-r2b-approval.service':'379a0cd1f34795c3b1361d69a5ad63e3d3fb7fc2',
 'r2b-admission-r2/maj-vaj-r2b-synthetic-worker.service':'1f0414e72e6985623fec94892eb28db70e208be8'
}
for path,expected_hash in expected.items():
    file=base/path
    st=file.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_nlink!=1 or st.st_mode & 0o022:
        raise SystemExit('R5_INSTALL_REFUSED:bundle_custody')
    data=file.read_bytes()
    actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    if actual!=expected_hash:
        raise SystemExit('R5_INSTALL_REFUSED:bundle_source_lock')
print('R5_FIXED_NATIVE_SOURCE_LOCK_PASS')
PY
}

check_key() {
  [[ -f "$key" && ! -L "$key" ]] || die signing_key_missing
  [[ "$(stat -c '%u:%g:%a:%h:%s' "$key")" == '0:0:600:1:32' ]] || die signing_key_custody
}
check_seal() {
  [[ -f "$seal" && ! -L "$seal" ]] || die not_installed
  [[ "$(stat -c '%u:%a:%h' "$seal")" == '0:644:1' ]] || die installation_seal_refused
  [[ "$(cat "$seal")" == 'R5:offline-synthetic-only:v1' ]] || die installation_seal_refused
}
wait_socket() {
  local unit="$1" socket="$2" i
  for ((i=0;i<45;i++)); do
    if [[ -S "$socket" ]] && systemctl is-active --quiet "$unit"; then
      return 0
    fi
    if systemctl is-failed --quiet "$unit"; then
      die service_failed
    fi
    sleep 1
  done
  die service_readiness_unavailable
}

check_units() {
  cmp -s "$root/r2b-admission-r2/$broker" "/etc/systemd/system/$broker" || die broker_unit_changed
  cmp -s "$root/r2b-admission-r2/$worker" "/etc/systemd/system/$worker" || die worker_unit_changed
  cmp -s "$root/deploy/$vault" "/etc/systemd/system/$vault" || die vault_unit_changed
  for unit in "$broker" "$worker"; do
    [[ -f "/etc/systemd/system/$unit.d/10-r5-qemu-only.conf" ]] || die qemu_unit_gate_missing
    grep -Fxq 'ConditionVirtualization=qemu' "/etc/systemd/system/$unit.d/10-r5-qemu-only.conf" || die qemu_unit_gate_invalid
  done
}

case "$action" in
  install)
    [[ ! -e "$seal" && ! -L "$seal" ]] || die already_installed
    [[ ! -e "$marker" && ! -L "$marker" ]] || die approval_marker_preexisting
    check_bundle
    for u in maj-vaj-vault maj-vaj-approval maj-vaj-credential-operator; do
      id -u "$u" >/dev/null || die expected_principal_missing
    done
    [[ ! -e "$key" && ! -L "$key" ]] || die signing_key_preexisting
    [[ -f "$root/paper_credential_vault.py" && -f "$root/managed_rpc.py" ]] || die original_vault_missing
    [[ -f "$root/credential_graphical.py" && ! -L "$root/credential_graphical.py" ]] || die native_gui_missing
    [[ ! -e "$root/.r5-stage" && ! -L "$root/.r5-stage" ]] || die stale_stage
    install -d -m 0700 "$root/.r5-stage/deploy"
    trap 'rm -rf -- "$root/.r5-stage"' EXIT
    for p in paper_credential_vault.py managed_rpc.py credential_graphical.py; do
      install -m 0644 "$root/$p" "$root/.r5-stage/$p"
    done
    install -m 0644 "$root/deploy/$vault" "$root/.r5-stage/deploy/$vault"
    "$root/venv/bin/python" -I -B "$root/r2b-admission-r2/patch_owner_vault.py" "$root/.r5-stage"
    "$root/venv/bin/python" -I -B -m py_compile "$root/.r5-stage/"*.py
    # All immutable source and patch checks have completed before mutation.
    groupadd --system maj-vaj-r2b-approval-rpc
    usermod -aG maj-vaj-r2b-approval-rpc maj-vaj-vault
    useradd --system --user-group --no-create-home --shell /usr/sbin/nologin maj-vaj-r2b-worker
    /usr/bin/python3 -I -B - "$key" <<'PY'
import os,sys
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
try:
    os.write(fd,os.urandom(32))
    os.fsync(fd)
finally:
    os.close(fd)
PY
    check_key
    # Fail closed if installation is interrupted after committing begins.
    trap 'rm -f -- "$marker"; systemctl stop maj-vaj-r2b-approval.service maj-vaj-r2b-synthetic-worker.service maj-vaj-paper-vault.service >/dev/null 2>&1 || : ' ERR
    systemctl stop "$vault"
    for p in paper_credential_vault.py managed_rpc.py credential_graphical.py; do
      install -o root -g root -m 0644 "$root/.r5-stage/$p" "$root/$p"
    done
    install -o root -g root -m 0644 "$root/.r5-stage/deploy/$vault" "$root/deploy/$vault"
    install -o root -g root -m 0644 "$root/deploy/$vault" "/etc/systemd/system/$vault"
    for unit in "$broker" "$worker"; do
      install -o root -g root -m 0644 "$root/r2b-admission-r2/$unit" "/etc/systemd/system/$unit"
      install -d -o root -g root -m 0755 "/etc/systemd/system/$unit.d"
      printf '[Unit]\nConditionVirtualization=qemu\n' >"/etc/systemd/system/$unit.d/10-r5-qemu-only.conf"
      chmod 0644 "/etc/systemd/system/$unit.d/10-r5-qemu-only.conf"
    done
    systemctl daemon-reload
    check_units
    systemctl restart "$vault"
    wait_socket "$vault" /run/maj-vaj-paper-vault-status/status.sock
    # Both independent systemd services are NOT enabled or active.
    ! systemctl is-active --quiet "$broker" || die broker_not_disabled
    ! systemctl is-active --quiet "$worker" || die worker_not_disabled
    test ! -e "$marker" || die unsafe_marker
    printf 'R5:offline-synthetic-only:v1\n' | install -o root -g root -m 0644 /dev/stdin "$seal"
    trap - ERR
    echo R5_INSTALLED_DISABLED_DEFAULT_PASS
    ;;
  verify)
    check_seal; check_bundle; check_key; check_units
    echo R5_INSTALLED_CUSTODY_VERIFIED_PASS
    ;;
  enable)
    check_seal; check_bundle; check_key; check_units
    if [[ -e "$marker" || -L "$marker" ]]; then
      [[ -f "$marker" && ! -L "$marker" && "$(stat -c '%u:%a:%h' "$marker")" == '0:644:1' ]] || die marker_custody_refused
    else
      install -o root -g root -m 0644 /dev/null "$marker"
    fi
    systemctl start "$broker" "$worker"
    systemctl restart "$vault"
    wait_socket "$vault" /run/maj-vaj-paper-vault-status/status.sock
    wait_socket "$broker" /run/maj-vaj-r2b-approval/agent.sock
    wait_socket "$worker" /run/maj-vaj-r2b-synthetic-worker/result.sock
    echo R5_EXPLICIT_OFFLINE_ACTIVATION_PASS
    ;;
  disable)
    check_seal
    if [[ -e "$marker" || -L "$marker" ]]; then
      [[ -f "$marker" && ! -L "$marker" && "$(stat -c '%u:%a:%h' "$marker")" == '0:644:1' ]] || die marker_custody_refused
      rm -f -- "$marker"
    fi
    systemctl stop "$broker" "$worker"
    systemctl restart "$vault"
    ! systemctl is-active --quiet "$broker" || die broker_active_after_disable
    ! systemctl is-active --quiet "$worker" || die worker_active_after_disable
    echo R5_OFFLINE_REVOCATION_PASS
    ;;
esac
