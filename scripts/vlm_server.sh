#!/usr/bin/env bash
# Managed local VLM: bounded healthy startup and generation-owned idle watchdog.
# Usage: vlm_server.sh start [--auto-stop-idle SECS] | stop | status | restart | adopt-watchdog
# Defaults preserve the production port and ten-minute idle policy.
set -euo pipefail

PORT="${METNOS_VLM_PORT:-8081}"
MODEL="${METNOS_VLM_MODEL:-$HOME/models/Qwen3VL-2B-Instruct-Q4_K_M.gguf}"
MMPROJ="${METNOS_VLM_MMPROJ:-$HOME/models/mmproj-Qwen3VL-2B-Instruct-F16.gguf}"
LLAMA_BIN="${METNOS_VLM_LLAMA_BIN:-$HOME/llama.cpp/build/bin/llama-server}"
CTX="${METNOS_VLM_CTX:-16384}"
NPAR="${METNOS_VLM_SLOTS:-4}"
LOG_DIR="${METNOS_USER_DATA:-$HOME/.local/share/metnos}/logs"
LOG_FILE="$LOG_DIR/vlm_server.log"
LOG_ARCHIVE_DIR="$LOG_DIR/archive/vlm"
LOG_MAX_MB="${METNOS_VLM_LOG_MAX_MB:-32}"
LOG_ARCHIVES="${METNOS_VLM_LOG_ARCHIVES:-6}"
LOG_VERBOSITY="${METNOS_VLM_LOG_VERBOSITY:-2}"
WD_LOG_FILE="$LOG_DIR/vlm_watchdog.log"
WD_LOG_ARCHIVE_DIR="$LOG_DIR/archive/vlm-watchdog"
WD_LOG_MAX_MB="${METNOS_VLM_WATCHDOG_LOG_MAX_MB:-4}"
WD_LOG_ARCHIVES="${METNOS_VLM_WATCHDOG_LOG_ARCHIVES:-4}"
STATE_ROOT="$(readlink -m -- "${METNOS_USER_STATE:-$HOME/.local/state/metnos}")"
PID_FILE="$STATE_ROOT/vlm_server.pid"
WD_PID_FILE="$STATE_ROOT/vlm_watchdog.pid"
WD_OWNER_FILE="$STATE_ROOT/vlm_watchdog.owner"
USE_FILE="$STATE_ROOT/vlm_last_use"
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname -- "$SCRIPT_PATH")"
RUNTIME_DIR="${METNOS_RUNTIME_DIR:-$(dirname "$SCRIPT_DIR")/runtime}"
RESOLVED_BIN="$(readlink -f -- "$LLAMA_BIN")"
START_WAIT="${METNOS_VLM_START_WAIT_S:-30}"
POLL_SECS="${METNOS_VLM_WATCHDOG_POLL_S:-30}"

mkdir -p "$LOG_DIR" "$STATE_ROOT"
exec 9>"$STATE_ROOT/vlm_lifecycle.lock"

_now_ms() { date +%s%3N; }
_duration_ms() {
  [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
  awk -v seconds="$1" 'BEGIN {printf "%.0f", seconds * 1000}'
}
_seconds() { awk -v ms="$1" 'BEGIN {printf "%.3f", ms / 1000}'; }

# Linux start time distinguishes PID reuse; zombies are already stopped.
_process_token() {
  local pid="$1" record rest
  local -a fields
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] && [ -O "/proc/$pid" ] || return 1
  IFS= read -r record < "/proc/$pid/stat" 2>/dev/null || return 1
  rest="${record##*) }"
  read -r -a fields <<< "$rest"
  [ "${fields[0]:-Z}" != Z ] && [ "${fields[0]:-X}" != X ] || return 1
  printf '%s\n' "${fields[19]}"
}
_pid_alive() { _process_token "${1:-}" >/dev/null 2>&1; }
_same_process() { [ "$(_process_token "$1" 2>/dev/null || true)" = "$2" ] && [ -n "$2" ]; }

_server_is_ours() {
  local pid="$1" i has_bin=0 has_model=0 has_port=0
  local -a args=()
  _pid_alive "$pid" || return 1
  mapfile -d '' -t args < "/proc/$pid/cmdline" 2>/dev/null || return 1
  for i in 0 1; do
    [ "${args[$i]:-}" != "$RESOLVED_BIN" ] || has_bin=1
  done
  for ((i=0; i+1<${#args[@]}; i++)); do
    if [ "${args[$i]}" = -m ] && [ "${args[$((i+1))]}" = "$MODEL" ]; then has_model=1; fi
    if [ "${args[$i]}" = --port ] && [ "${args[$((i+1))]}" = "$PORT" ]; then has_port=1; fi
  done
  [ "$has_bin$has_model$has_port" = 111 ]
}
_find_server_pid() {
  local pid path
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if _server_is_ours "$pid"; then echo "$pid"; return; fi
  for path in /proc/[0-9]*/cmdline; do
    pid="${path#/proc/}"; pid="${pid%/cmdline}"
    if _server_is_ours "$pid"; then echo "$pid"; return; fi
  done
}

# Watchdogs carry their model generation and state root in argv. An old
# retiring watchdog must never claim or remove a new generation's records.
_watchdog_is_ours() {
  local pid="$1" target="$2" token="$3" n owner_pid owner_token owner_target owner_generation
  local -a args=()
  _pid_alive "$pid" || return 1
  # The launch receipt also covers the short interval before the child execs.
  if read -r owner_pid owner_token owner_target owner_generation 2>/dev/null < "$WD_OWNER_FILE"; then
    if [ "$owner_pid" = "$pid" ] && [ "$owner_target" = "$target" ] &&
        [ "$owner_generation" = "$token" ] && _same_process "$pid" "$owner_token"; then return 0; fi
  fi
  mapfile -d '' -t args < "/proc/$pid/cmdline" 2>/dev/null || return 1
  n=${#args[@]}
  [ "$n" -ge 7 ] || return 1
  [[ "${args[$((n-6))]}" = */vlm_server.sh ]] || return 1
  [ "${args[$((n-5))]}" = watchdog ] &&
    [ "${args[$((n-3))]}" = "$target" ] &&
    [ "${args[$((n-2))]}" = "$token" ] &&
    [ "${args[$((n-1))]}" = "$STATE_ROOT" ]
}
_find_watchdog_pid() {
  local target="$1" token="$2" pid path
  pid="$(cat "$WD_PID_FILE" 2>/dev/null || true)"
  if _watchdog_is_ours "$pid" "$target" "$token"; then echo "$pid"; return; fi
  for path in /proc/[0-9]*/cmdline; do
    pid="${path#/proc/}"; pid="${pid%/cmdline}"
    if _watchdog_is_ours "$pid" "$target" "$token"; then echo "$pid"; return; fi
  done
}
_remove_if_named() {
  [ "$(cat "$1" 2>/dev/null || true)" != "$2" ] || rm -f -- "$1"
}
_remove_server_record() {
  local current
  current="$(_process_token "$1" 2>/dev/null || true)"
  # A reused PID can already name the new model generation.
  if [ -z "$current" ] || [ "$current" = "$2" ]; then _remove_if_named "$PID_FILE" "$1"; fi
}
_legacy_watchdog_is_ours() {
  local pid="$1" item home_dir= state_dir= model= port=8081
  local -a args=()
  _pid_alive "$pid" || return 1
  mapfile -d '' -t args < "/proc/$pid/cmdline" 2>/dev/null || return 1
  [ "${#args[@]}" -eq 4 ] && [[ "${args[1]}" = */vlm_server.sh ]] &&
    [ "${args[2]}" = watchdog ] || return 1
  while IFS= read -r -d '' item; do
    case "$item" in
      HOME=*) home_dir="${item#HOME=}" ;;
      METNOS_USER_STATE=*) state_dir="${item#METNOS_USER_STATE=}" ;;
      METNOS_VLM_MODEL=*) model="${item#METNOS_VLM_MODEL=}" ;;
      METNOS_VLM_PORT=*) port="${item#METNOS_VLM_PORT=}" ;;
    esac
  done < "/proc/$pid/environ"
  state_dir="${state_dir:-$home_dir/.local/state/metnos}"
  model="${model:-$home_dir/models/Qwen3VL-2B-Instruct-Q4_K_M.gguf}"
  [ "$(readlink -m -- "$state_dir")" = "$STATE_ROOT" ] && [ "$model" = "$MODEL" ] && [ "$port" = "$PORT" ]
}
_retire_legacy_watchdog() {
  local stop_at="$1" pid token
  pid="$(cat "$WD_PID_FILE" 2>/dev/null || true)"
  if ! _legacy_watchdog_is_ours "$pid"; then return 0; fi
  token="$(_process_token "$pid")"
  kill -TERM "$pid" 2>/dev/null || true
  while _same_process "$pid" "$token"; do
    [ "$(_now_ms)" -lt "$stop_at" ] || return 2
    sleep 0.02
  done
  _remove_if_named "$WD_PID_FILE" "$pid"
}
_server_listening() {
  [[ "$(ss -H -ltnp "( sport = :$PORT )" 2>/dev/null)" = *"pid=$1,"* ]]
}
_health_ok() {
  [ "$(curl -s -o /dev/null -w '%{http_code}' -m "$1" "http://127.0.0.1:$PORT/health" 2>/dev/null || true)" = 200 ]
}
_port_busy() {
  local listeners
  listeners="$(ss -H -ltn "( sport = :$PORT )" 2>/dev/null)" || return 0
  [ -n "$listeners" ]
}
_connections() { ss -H -tn state established "( sport = :$PORT )" 2>/dev/null | wc -l; }
_rotate_inactive_log() {
  local path="$1" archive_dir="$2" max_mb="$3" keep="$4"
  if [ -f "$RUNTIME_DIR/log_lifecycle.py" ]; then
    python3 "$RUNTIME_DIR/log_lifecycle.py" rotate --path "$path" \
      --archive-dir "$archive_dir" --max-bytes "$((max_mb * 1024 * 1024))" --keep "$keep" \
      || echo "WARNING: rotazione log fallita ($path); continuo" >&2
  fi
}
_spawn_watchdog() {
  local idle_secs="$1" pid="$2" token="$3" current_wd
  current_wd="$(_find_watchdog_pid "$pid" "$token")"
  if _pid_alive "$current_wd"; then
    echo "$current_wd" > "$WD_PID_FILE"
    return
  fi
  _rotate_inactive_log "$WD_LOG_FILE" "$WD_LOG_ARCHIVE_DIR" "$WD_LOG_MAX_MB" "$WD_LOG_ARCHIVES"
  nohup "$SCRIPT_PATH" watchdog "$idle_secs" "$pid" "$token" "$STATE_ROOT" \
    9>&- >> "$WD_LOG_FILE" 2>&1 &
  local wd_pid=$! wd_token
  wd_token="$(_process_token "$wd_pid" 2>/dev/null || true)"
  echo "$wd_pid $wd_token $pid $token" > "$WD_OWNER_FILE"
  echo "$wd_pid" > "$WD_PID_FILE"
  echo "watchdog pid=$wd_pid"
}

cmd_start() {
  local auto_stop_idle=0 wait_ms stop_at now remaining pid token launched=0
  if [ "${1:-}" = --auto-stop-idle ]; then auto_stop_idle="${2:-0}"; fi
  [[ "$auto_stop_idle" =~ ^[0-9]+$ ]] || return 1
  wait_ms="$(_duration_ms "$START_WAIT")" || return 1
  [ "$wait_ms" -gt 0 ] || return 2
  stop_at=$(($(_now_ms) + wait_ms))
  while true; do
    now="$(_now_ms)"; remaining=$((stop_at - now))
    if [ "$remaining" -le 0 ]; then
      echo "WARNING: server non risponde a /health entro ${START_WAIT}s. controlla $LOG_FILE"
      return 2
    fi
    flock -w "$(_seconds "$remaining")" 9 || return 2
    _retire_legacy_watchdog "$stop_at" || return 2
    now="$(_now_ms)"; remaining=$((stop_at - now))
    [ "$remaining" -gt 0 ] || return 2
    pid="$(_find_server_pid)"
    if _pid_alive "$pid"; then
      token="$(_process_token "$pid")"
      if _server_listening "$pid" && _health_ok "$(_seconds "$((remaining < 1000 ? remaining : 1000))")" && _same_process "$pid" "$token"; then
        echo "$pid" > "$PID_FILE"
        if [ "$auto_stop_idle" -gt 0 ]; then _spawn_watchdog "$auto_stop_idle" "$pid" "$token"; fi
        # Recheck at the success boundary; an alive PID alone is insufficient.
        now="$(_now_ms)"; remaining=$((stop_at - now))
        if [ "$remaining" -gt 0 ] && _server_listening "$pid" && _health_ok "$(_seconds "$((remaining < 1000 ? remaining : 1000))")" && _same_process "$pid" "$token"; then
          echo "server gia avviato e pronto (pid=$pid)."
          return 0
        fi
      fi
    else
      if _port_busy; then
        echo "porta $PORT occupata da un altro processo; nessun avvio." >&2
        return 1
      fi
      if [ "$launched" -eq 1 ]; then
        echo "server avviato ma terminato prima della salute." >&2
        return 2
      fi
      if [ ! -x "$RESOLVED_BIN" ]; then
        echo "binario VLM mancante o non eseguibile: $RESOLVED_BIN" >&2
        return 1
      fi
      _rotate_inactive_log "$LOG_FILE" "$LOG_ARCHIVE_DIR" "$LOG_MAX_MB" "$LOG_ARCHIVES"
      echo "avvio llama-server VLM su :$PORT ..."
      nohup env LD_LIBRARY_PATH="$(dirname -- "$RESOLVED_BIN")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$RESOLVED_BIN" \
        -m "$MODEL" --mmproj "$MMPROJ" -ngl 999 --host 127.0.0.1 --port "$PORT" \
        -c "$CTX" --parallel "$NPAR" --cont-batching --jinja -fa on \
        --batch-size 2048 --ubatch-size 512 --log-verbosity "$LOG_VERBOSITY" \
        9>&- >> "$LOG_FILE" 2>&1 &
      pid=$!; launched=1
      echo "$pid" > "$PID_FILE"
      touch "$USE_FILE"
      # The child may not have exec'd yet; its start time still owns this generation.
      token="$(_process_token "$pid" 2>/dev/null || true)"
      if [ "$auto_stop_idle" -gt 0 ] && [ -n "$token" ]; then _spawn_watchdog "$auto_stop_idle" "$pid" "$token"; fi
    fi
    flock -u 9
    now="$(_now_ms)"; remaining=$((stop_at - now))
    if [ "$remaining" -gt 0 ]; then sleep "$(_seconds "$((remaining < 200 ? remaining : 200))")"; fi
  done
}

# Release migration only: never start, stop or probe a model here.
cmd_adopt_watchdog() {
  local idle_secs=600 wait_ms stop_at pid token
  if [ "${1:-}" = --auto-stop-idle ]; then idle_secs="${2:-0}"; fi
  [[ "$idle_secs" =~ ^[1-9][0-9]*$ ]] || return 1
  wait_ms="$(_duration_ms "$START_WAIT")" || return 1
  [ "$wait_ms" -gt 0 ] || return 2
  stop_at=$(($(_now_ms) + wait_ms))
  flock -w "$(_seconds "$wait_ms")" 9 || return 2
  _retire_legacy_watchdog "$stop_at" || return 2
  pid="$(_find_server_pid)"
  if ! _pid_alive "$pid"; then echo "nessun modello presente; nessun avvio."; return 0; fi
  token="$(_process_token "$pid")"
  echo "$pid" > "$PID_FILE"
  _spawn_watchdog "$idle_secs" "$pid" "$token"
}

cmd_watchdog() {
  local idle_secs="${1:-600}" target="${2:-}" token="${3:-}" owner_state="${4:-}"
  local idle_ms last_active now marker conns
  [ "$owner_state" = "$STATE_ROOT" ] && [ -n "$token" ] || return 1
  idle_ms="$(_duration_ms "$idle_secs")" || return 1
  _duration_ms "$POLL_SECS" >/dev/null || return 1
  last_active="$(_now_ms)"
  while true; do
    sleep "$POLL_SECS"
    flock 9
    if ! _same_process "$target" "$token" || ! _server_is_ours "$target"; then
      _remove_server_record "$target" "$token"
      _remove_if_named "$WD_PID_FILE" "$$"
      return 0
    fi
    now="$(_now_ms)"
    marker="$(date -r "$USE_FILE" +%s%3N 2>/dev/null || echo 0)"
    if [ "$marker" -gt "$last_active" ]; then last_active="$marker"; fi
    conns="$(_connections)"
    if [ "$conns" -gt 0 ]; then last_active="$now"; fi
    if [ "$((now - last_active))" -lt "$idle_ms" ]; then flock -u 9; continue; fi
    # Recheck immediately before TERM under the same lock used by readiness
    # activity updates, so an accepted use cannot be lost during retirement.
    marker="$(date -r "$USE_FILE" +%s%3N 2>/dev/null || echo 0)"
    now="$(_now_ms)"
    if [ "$((now - marker))" -lt "$idle_ms" ]; then last_active="$marker"; flock -u 9; continue; fi
    echo "idle >= ${idle_secs}s, kill server pid=$target"
    kill -TERM "$target" 2>/dev/null || true
    _remove_if_named "$WD_PID_FILE" "$$"
    flock -u 9
    # New generations may start during this grace period. Cleanup is conditional.
    sleep 5
    flock 9
    if _same_process "$target" "$token" && _server_is_ours "$target"; then kill -KILL "$target" 2>/dev/null || true; fi
    _remove_server_record "$target" "$token"
    _remove_if_named "$WD_PID_FILE" "$$"
    return 0
  done
}

cmd_stop() {
  local pid token wd_pid i
  flock 9
  _retire_legacy_watchdog "$(($(_now_ms) + 10000))" || return 2
  pid="$(_find_server_pid)"
  if ! _pid_alive "$pid"; then echo "server non in esecuzione."; return; fi
  token="$(_process_token "$pid")"
  wd_pid="$(_find_watchdog_pid "$pid" "$token")"
  if _pid_alive "$wd_pid"; then kill -TERM "$wd_pid" 2>/dev/null || true; _remove_if_named "$WD_PID_FILE" "$wd_pid"; fi
  kill -TERM "$pid" 2>/dev/null || true
  for i in $(seq 1 100); do
    if ! _same_process "$pid" "$token"; then _remove_server_record "$pid" "$token"; return; fi
    sleep 0.1
  done
  if _same_process "$pid" "$token" && _server_is_ours "$pid"; then kill -KILL "$pid" 2>/dev/null || true; fi
  _remove_server_record "$pid" "$token"
}
cmd_status() {
  local pid token wd_pid age rss wd_age wd_info=off hh=N/A
  pid="$(_find_server_pid)"
  if ! _pid_alive "$pid"; then echo "stato: STOPPED (nessun modello gestito su porta $PORT)"; return 1; fi
  token="$(_process_token "$pid")"
  if _server_listening "$pid" && _health_ok 2; then hh=OK; fi
  wd_pid="$(_find_watchdog_pid "$pid" "$token")"
  age=$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ' || true)
  rss=$(ps -p "$pid" -o rss= 2>/dev/null | awk '{printf "%.1fGB", $1/1024/1024}' || true)
  if _pid_alive "$wd_pid"; then
    wd_age=$(ps -p "$wd_pid" -o etime= 2>/dev/null | tr -d ' ' || true)
    wd_info="pid=$wd_pid eta=$wd_age"
  fi
  echo "stato: RUNNING pid=$pid eta=$age rss=$rss client=$(_connections) health=$hh watchdog=$wd_info"
  echo "log: $LOG_FILE"
}

case "${1:-status}" in
  start) shift; cmd_start "$@" ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  restart) shift; cmd_stop; flock -u 9; cmd_start "$@" ;;
  adopt-watchdog) shift; cmd_adopt_watchdog "$@" ;;
  watchdog) shift; cmd_watchdog "$@" ;;
  *) echo "uso: $0 {start [--auto-stop-idle SECS] | stop | status | restart | adopt-watchdog [--auto-stop-idle SECS]}"; exit 2 ;;
esac
