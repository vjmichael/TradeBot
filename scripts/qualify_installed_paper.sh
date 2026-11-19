#!/usr/bin/env bash
# Only a fresh GitHub-hosted Linux runner, never an operator installation script.
set -Eeuo pipefail
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && $EUID == 0 ]]
report=${1:?report directory required}
[[ -d "$report" && ! -e /var/lib/maj-vaj && ! -e /etc/systemd/system/maj-vaj-paper-protection@.service ]]
[[ $(cat /proc/1/comm) == systemd && $(stat -fc %T /sys/fs/cgroup) == cgroup2fs ]]
! getent passwd maj-vaj >/dev/null
useradd --system --user-group --home-dir /var/lib/maj-vaj --no-create-home --shell /usr/sbin/nologin maj-vaj
install -d -m 0700 -o maj-vaj -g maj-vaj /var/lib/maj-vaj
install -m 0644 /opt/maj-vaj/deploy/maj-vaj-paper-protection@.service /etc/systemd/system/
systemctl daemon-reload
py=/opt/maj-vaj/venv/bin/python
script=/opt/maj-vaj/scripts/qualify_installed_paper.py
runq() { runuser -u maj-vaj -- env GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted "$py" "$script" "$@"; }
paused_unit=; scenario_unit=; audit_pid=
cleanup() {
    set +e
    if [[ -n $audit_pid ]]; then kill "$audit_pid" 2>/dev/null; fi
    for unit in "$paused_unit" "$scenario_unit"; do
        if [[ -n $unit ]]; then
            systemctl stop "$unit"
            journalctl --no-pager -u "$unit" > "$report/$unit.log"
        fi
    done
    if [[ -f /var/lib/maj-vaj/qualification/audit.jsonl ]]; then
        install -m 0644 /var/lib/maj-vaj/qualification/audit.jsonl "$report/public-paper-audit.jsonl"
    fi
}
trap cleanup EXIT

wait_healthy() {
    local unit=$1 instance=$2 old=$3 output=$4
    shift 4
    local pid
    for _ in $(seq 1 90); do
        pid=$(systemctl show -p MainPID --value "$unit")
        if [[ $pid != 0 && $pid != "$old" ]] && systemctl is-active --quiet "$unit" &&
           runq health --instance "$instance" --pid "$pid" "$@" > "$output.tmp" 2>&1; then
            mv "$output.tmp" "$output"; printf '%s\n' "$pid"; return
        fi
        sleep 2
    done
    cat "$output.tmp" >&2 || true
    return 1
}

stop_empty() {
    local unit=$1 cg
    cg=$(systemctl show -p ControlGroup --value "$unit")
    [[ $cg == /system.slice/* ]]
    systemctl stop "$unit"
    if systemctl is-active --quiet "$unit"; then return 1; fi
    if [[ -e /sys/fs/cgroup$cg/cgroup.events ]]; then
        grep -qx 'populated 0' "/sys/fs/cgroup$cg/cgroup.events"
    fi
}

instance=$(runq prepare-paused | tail -n 1)
[[ $instance =~ ^[a-f0-9]{32}$ ]]
paused_unit=maj-vaj-paper-protection@$instance.service
systemctl start "$paused_unit"
pid=$(wait_healthy "$paused_unit" "$instance" 0 "$report/paused-before.json")
# An actual second native launcher must lose the workspace lease.
if timeout 15 runuser -u maj-vaj -- "$py" /opt/maj-vaj/scripts/run_paper_worker.py /var/lib/maj-vaj/data "$instance" > "$report/duplicate-worker.log" 2>&1; then
    echo 'duplicate native worker unexpectedly started' >&2; exit 1
fi
grep -E 'BlockingIOError|Resource temporarily unavailable' "$report/duplicate-worker.log"
[[ $(systemctl show -p MainPID --value "$paused_unit") == "$pid" ]]
kill -KILL "$pid"
newpid=$(wait_healthy "$paused_unit" "$instance" "$pid" "$report/paused-restarted.json")
[[ $newpid != "$pid" && ! -d /proc/$pid ]]
[[ $(systemctl show -p NRestarts --value "$paused_unit") -ge 1 ]]
stop_empty "$paused_unit"
printf '%s\n' 'RECEIPT: installed native paused worker, exclusive lease, SIGKILL restart, signed health, zero trades and empty cgroup' | tee "$report/installed-receipt.txt"

# Separate, new fixture schema; no RUN mutation of the real Store workspace.
probe=$(runq prepare-execution | tail -n 1)
[[ $probe =~ ^[a-f0-9]{32}$ && $probe != "$instance" ]]
scenario_unit=maj-vaj-paper-protection@$probe.service
install -d -m 0755 "/etc/systemd/system/$scenario_unit.d"
cat > "/etc/systemd/system/$scenario_unit.d/qualification.conf" <<EOF
[Unit]
ConditionPathExists=
ConditionPathExists=/var/lib/maj-vaj/qualification/paper/$probe/paper.sqlite
ConditionPathExists=/var/lib/maj-vaj/qualification/paper/$probe/receipt.key
[Service]
ExecStart=
ExecStart=$py /opt/maj-vaj/scripts/run_paper_worker.py /var/lib/maj-vaj/qualification $probe
ReadWritePaths=
ReadWritePaths=/var/lib/maj-vaj/qualification
EOF
systemctl daemon-reload
systemctl start "$scenario_unit"
wait_healthy "$scenario_unit" "$probe" 0 "$report/execution-start.json" --execution
runq audit --instance "$probe" --output /var/lib/maj-vaj/qualification/audit.jsonl > "$report/audit-console.log" 2>&1 &
audit_pid=$!
for _ in $(seq 1 60); do
    kill -0 "$audit_pid"
    [[ -s /var/lib/maj-vaj/qualification/audit.jsonl ]] && break
    sleep 1
done
[[ -s /var/lib/maj-vaj/qualification/audit.jsonl ]]
runq authorize --instance "$probe" | tee "$report/fixture-authority.json"
wait "$audit_pid"
audit_pid=
stop_empty "$scenario_unit"
printf '%s\n' 'RECEIPT: one-hour actual public depth and installed PAPER fixture settlement, no residual holds; not a trading strategy qualification' | tee "$report/public-receipt.txt"
