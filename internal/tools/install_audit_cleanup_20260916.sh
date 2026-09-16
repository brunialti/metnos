#!/bin/bash
# One-off, owner-authorized removal of the exact interrupted test environment.
# Production data and shared model caches are never cleanup targets.
set -euo pipefail
umask 077
run_dir=${1:?reviewed runner directory required}
mode=${2:-inspect}
[[ "$mode" == inspect || "$mode" == clean || "$mode" == verify ]] || exit 64
[[ $EUID == 0 && -d "$run_dir" && ! -L "$run_dir" ]]

state_dir=/var/lib/metnos-admin/install-audit-20260916
rootfs=/var/lib/machines/metnos-audit-20260916-LoHdw4SC
machine=metnos-audit-20260916-LoHdw4SC
audit_source=/opt/metnos/.claude/worktrees/rm0008-reboot/internal/coordination/install-audit-20260916
host_units=(
  metnos-install-audit-container-20260916.service
  metnos-install-audit-recovery-20260916.service
  metnos-install-audit-watchdog-20260916.service
  metnos-install-audit-watchdog-20260916.timer
  metnos-install-audit-deadline-20260916.timer
)
production_units=(
  llama-server.service photon.service searxng.service
  metnos-side-display.service metnos-playwright.service metnos-http.service
  metnos-durable-worker.service metnos-telegram-daemon.service
  metnos-i18n-translator.timer metnos-stack-watchdog.timer
  metnos-stack-ready.service metnos.target
)
user_ctl() {
  runuser -u roberto -- env XDG_RUNTIME_DIR=/run/user/1000 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user "$@"
}
assert_no_test_mounts() {
  ! findmnt -rn -o TARGET | awk -v base="$rootfs" \
    '$0 == base || index($0, base "/") == 1 {found=1} END {exit !found}'
}
production_snapshot() {
  for unit in "${production_units[@]}"; do
    systemctl is-active --quiet "$unit"
  done
  systemctl show metnos-http.service metnos-durable-worker.service \
    metnos-telegram-daemon.service -p Id -p MainPID -p ActiveEnterTimestamp
  curl -fsS --max-time 10 http://127.0.0.1:8770/agent/health \
    | jq -ce '{ok:.ok,version:.version} | select(.ok == true)'
}

if [[ "$mode" == verify ]]; then
  [[ ! -e "$rootfs" && ! -L "$rootfs" && ! -e "$state_dir" ]]
  assert_no_test_mounts
  ! getent passwd metnos-audit >/dev/null
  for unit in "${host_units[@]}"; do
    [[ "$(systemctl show "$unit" -p LoadState --value)" == not-found ]]
    [[ ! -e "/etc/systemd/system/$unit" ]]
  done
  for name in metnos-install-audit-20260916.service metnos-install-audit-20260916.timer; do
    [[ "$(user_ctl show "$name" -p LoadState --value)" == not-found ]]
    [[ ! -e "/home/roberto/.config/systemd/user/$name" && ! -L "/home/roberto/.config/systemd/user/$name" ]]
  done
  [[ ! -e /usr/local/sbin/metnos-install-audit-recovery-20260916 ]]
  [[ ! -e /usr/local/sbin/metnos-install-audit-watchdog-20260916 ]]
  production_snapshot >"$run_dir/production-verified.txt"
  # The first run compared the full health response as well as stable facts. Verify
  # process identity independently; never repeat the completed deletion.
  grep -E '^(Id|MainPID|ActiveEnterTimestamp)=' \
    /var/lib/metnos-admin/agent-runs/run-lzezmrmq/production-before.txt \
    >"$run_dir/processes-before.txt"
  grep -E '^(Id|MainPID|ActiveEnterTimestamp)=' "$run_dir/production-verified.txt" \
    >"$run_dir/processes-after.txt"
  cmp "$run_dir/processes-before.txt" "$run_dir/processes-after.txt"
  sha256sum -c /var/lib/metnos-admin/agent-runs/run-lzezmrmq/evidence-sha256.txt
  free_after=$(df --output=avail -B1 /var/lib/machines | tail -1 | tr -d ' ')
  jq --argjson free_after "$free_after" \
    '.status="cleaned_verified" | .free_after=$free_after |
     .cleanup_evidence_directory="/var/lib/metnos-admin/agent-runs/run-lzezmrmq" |
     .production_processes_unchanged=true' \
    /var/lib/metnos-admin/agent-runs/run-37vwlaqc/result.json >"$run_dir/result.json"
  exit 0
fi

[[ "$(stat -c '%u:%g:%a' "$state_dir")" == 0:0:700 && ! -L "$state_dir" ]]
[[ "$(<"$state_dir/machine-name")" == "$machine" ]]
[[ "$(<"$state_dir/rootfs")" == "$rootfs" ]]
[[ -f "$state_dir/restored" && ! -L "$state_dir/restored" ]]
[[ -d "$rootfs" && ! -L "$rootfs" && "$(realpath -e "$rootfs")" == "$rootfs" ]]
[[ "$(stat -c '%u:%g' "$rootfs")" == 0:0 ]]
[[ "$(<"$rootfs/etc/hostname")" == "$machine" ]]
[[ -f "$rootfs/home/metnos-audit/metnos/.git/HEAD" ]]
[[ -f "$audit_source/agent.log" ]]
[[ "$(systemctl show metnos-install-audit-container-20260916.service -p MainPID --value)" == 0 ]]
! user_ctl is-active --quiet metnos-install-audit-20260916.service
! getent passwd metnos-audit >/dev/null
assert_no_test_mounts
for unit in "${host_units[@]}"; do
  path="/etc/systemd/system/$unit"
  [[ -f "$path" && ! -L "$path" && "$(stat -c '%u:%g' "$path")" == 0:0 ]]
done
for name in metnos-install-audit-20260916.service metnos-install-audit-20260916.timer; do
  [[ "$(readlink "/home/roberto/.config/systemd/user/$name")" == "$audit_source/$name" ]]
done
for path in /usr/local/sbin/metnos-install-audit-recovery-20260916 \
  /usr/local/sbin/metnos-install-audit-watchdog-20260916; do
  [[ -f "$path" && ! -L "$path" && "$(stat -c '%u:%g' "$path")" == 0:0 ]]
done
production_snapshot >"$run_dir/production-before.txt"
size_bytes=$(timeout 45s du -sx --block-size=1 "$rootfs" | awk '{print $1}')
free_before=$(df --output=avail -B1 /var/lib/machines | tail -1 | tr -d ' ')
root_identity=$(stat -c '%d:%i' "$rootfs")
status=inspected

if [[ "$mode" == clean ]]; then
  # Preserve package and phase evidence before deleting disposable binaries.
  dpkg-query --admindir="$rootfs/var/lib/dpkg" -W \
    -f='${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' \
    >"$run_dir/guest-packages.tsv"
  evidence=(var/log/apt var/log/dpkg.log home/metnos-audit/metnos/.git/HEAD)
  if [[ -d "$rootfs/home/metnos-audit/.local/state/metnos/install" ]]; then
    evidence+=(home/metnos-audit/.local/state/metnos/install)
  fi
  tar --one-file-system -czf "$run_dir/guest-evidence.tar.gz" \
    -C "$rootfs" -- "${evidence[@]}"
  tar -tzf "$run_dir/guest-evidence.tar.gz" >"$run_dir/guest-evidence-index.txt"
  sha256sum "$audit_source/agent.log" "$run_dir/guest-evidence.tar.gz" \
    >"$run_dir/evidence-sha256.txt"

  # Retire only this audit's schedulers; ordinary production timers stay live.
  user_ctl disable --now metnos-install-audit-20260916.timer
  user_ctl disable metnos-install-audit-20260916.service
  user_ctl daemon-reload
  systemctl disable --now metnos-install-audit-watchdog-20260916.timer \
    metnos-install-audit-deadline-20260916.timer
  systemctl stop "${host_units[@]}"
  exec 9>"$state_dir/recovery.lock"
  flock -x -w 15 9
  assert_no_test_mounts
  [[ "$(stat -c '%d:%i' "$rootfs")" == "$root_identity" ]]
  [[ "$(systemctl show metnos-install-audit-container-20260916.service -p MainPID --value)" == 0 ]]
  # The exact directory above was validated and contains no mounted subtree.
  rm -rf --one-file-system -- /var/lib/machines/metnos-audit-20260916-LoHdw4SC
  [[ ! -e "$rootfs" && ! -L "$rootfs" ]]

  mkdir -m 0700 "$run_dir/retired-host-files"
  for unit in "${host_units[@]}"; do
    mv -- "/etc/systemd/system/$unit" "$run_dir/retired-host-files/$unit"
  done
  mv -- /usr/local/sbin/metnos-install-audit-recovery-20260916 \
    /usr/local/sbin/metnos-install-audit-watchdog-20260916 "$run_dir/retired-host-files/"
  mv -- /var/lib/metnos-admin/install-audit-20260916 "$run_dir/audit-state"
  systemctl daemon-reload
  for unit in "${host_units[@]}"; do
    [[ "$(systemctl show "$unit" -p LoadState --value)" == not-found ]]
  done
  for name in metnos-install-audit-20260916.service metnos-install-audit-20260916.timer; do
    [[ ! -e "/home/roberto/.config/systemd/user/$name" && ! -L "/home/roberto/.config/systemd/user/$name" ]]
  done
  production_snapshot >"$run_dir/production-after.txt"
  cmp "$run_dir/production-before.txt" "$run_dir/production-after.txt"
  status=cleaned
fi

free_after=$(df --output=avail -B1 /var/lib/machines | tail -1 | tr -d ' ')
jq -n --arg status "$status" --arg rootfs "$rootfs" \
  --argjson test_bytes "$size_bytes" --argjson free_before "$free_before" \
  --argjson free_after "$free_after" --arg evidence "$run_dir" \
  '{schema:"metnos-install-audit-cleanup/1",status:$status,rootfs:$rootfs,
    test_bytes:$test_bytes,free_before:$free_before,free_after:$free_after,
    evidence_directory:$evidence,production_active:true}' >"$run_dir/result.json"
