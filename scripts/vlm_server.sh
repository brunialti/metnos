#!/usr/bin/env bash
# vlm_server.sh — start/stop/status/watchdog del llama-server VLM Qwen3-VL su :8081.
#
# Uso:
#   vlm_server.sh start [--auto-stop-idle SECS]
#       Avvia in background. Se --auto-stop-idle e' specificato, parte anche un
#       watchdog che killa il server dopo SECS senza connessioni TCP attive.
#       Default ricomandato: 600 (10 min). 0 = no watchdog.
#   vlm_server.sh stop      — kill pulito server + watchdog (SIGTERM, poi SIGKILL dopo 10s)
#   vlm_server.sh status    — alive? PID? eta'? memoria? client connessi? watchdog?
#   vlm_server.sh restart   — stop+start (preserva --auto-stop-idle se gia' attivo)
#   vlm_server.sh watchdog SECS — modalita' interna del daemon watchdog (non chiamare a mano)
#
# Indicizzazioni rare e una tantum (Roberto, 10/5/2026): VLM tenuto spento di
# default. Lazy auto-start da `create_images_indices` quando rileva
# ConnectionRefused su :8081, che invoca questo script con --auto-stop-idle 600.
set -euo pipefail

PORT=8081
# Path config-driven (§7.11): l'installer punta questi all'install layout via
# env; senza env i default restano i path storici di esercizio (prod invariata).
MODEL="${METNOS_VLM_MODEL:-$HOME/models/Qwen3VL-2B-Instruct-Q4_K_M.gguf}"
MMPROJ="${METNOS_VLM_MMPROJ:-$HOME/models/mmproj-Qwen3VL-2B-Instruct-F16.gguf}"
LLAMA_BIN="${METNOS_VLM_LLAMA_BIN:-$HOME/llama.cpp/build/bin/llama-server}"
# Contesto totale e slot paralleli settabili. Ctx/slot = CTX/NPAR deve
# coprire i vision-token dell'immagine (a long-edge 1536 ~1950 tok) + il
# budget di output. Default 16384/4 = 4096/slot (room per 1536 + caption
# ricca). A 1024 bastava 8192/4=2048; il bump risolve il troncamento JSON.
CTX="${METNOS_VLM_CTX:-16384}"
NPAR="${METNOS_VLM_SLOTS:-4}"
LOG_DIR="$HOME/.local/share/metnos/logs"
LOG_FILE="$LOG_DIR/vlm_server.log"
WD_LOG_FILE="$LOG_DIR/vlm_watchdog.log"
PID_FILE="$HOME/.local/state/metnos/vlm_server.pid"
WD_PID_FILE="$HOME/.local/state/metnos/vlm_watchdog.pid"

mkdir -p "$LOG_DIR" "$(dirname "$PID_FILE")"

_pid_alive() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

_find_server_pid() {
  # `|| true`: pgrep ritorna 1 se nessun match → con `set -euo pipefail` la
  # pipeline propagherebbe l'errore e abortirebbe lo script al primo cold-start
  # (server non ancora avviato). Empty stdout E' il segnale "non trovato".
  pgrep -f "llama-server.*Qwen3VL.*--port $PORT" 2>/dev/null | head -1 || true
}

_find_watchdog_pid() {
  if [ -f "$WD_PID_FILE" ]; then
    local pid
    pid=$(cat "$WD_PID_FILE" 2>/dev/null)
    if _pid_alive "$pid"; then
      echo "$pid"
      return
    fi
  fi
  pgrep -f "vlm_server.sh watchdog" 2>/dev/null | head -1
}

cmd_status() {
  local pid age conns rss wd_pid
  pid="$(_find_server_pid)"
  if ! _pid_alive "$pid"; then
    echo "stato: STOPPED (porta $PORT libera)"
    return 1
  fi
  age=$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ')
  rss=$(ps -p "$pid" -o rss= 2>/dev/null | awk '{printf "%.1fGB", $1/1024/1024}')
  conns=$(ss -tn state established "( sport = :$PORT )" 2>/dev/null | tail -n +2 | wc -l)
  local hh="N/A"
  if curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    hh="OK"
  fi
  wd_pid="$(_find_watchdog_pid)"
  local wd_info="off"
  if _pid_alive "$wd_pid"; then
    local wd_age
    wd_age=$(ps -p "$wd_pid" -o etime= 2>/dev/null | tr -d ' ')
    wd_info="pid=$wd_pid eta=$wd_age"
  fi
  echo "stato: RUNNING pid=$pid eta=$age rss=$rss client=$conns health=$hh watchdog=$wd_info"
  echo "log: $LOG_FILE"
}

cmd_start() {
  local auto_stop_idle=0
  if [ "${1:-}" = "--auto-stop-idle" ]; then
    auto_stop_idle="${2:-0}"
    shift 2 || true
  fi

  local pid
  pid="$(_find_server_pid)"
  if _pid_alive "$pid"; then
    echo "server gia avviato (pid=$pid)."
    # Se watchdog richiesto e non gia attivo, lo avvio comunque
    if [ "$auto_stop_idle" -gt 0 ] && ! _pid_alive "$(_find_watchdog_pid)"; then
      _spawn_watchdog "$auto_stop_idle"
    fi
    return 0
  fi
  echo "avvio llama-server VLM su :$PORT ..."
  nohup "$LLAMA_BIN" \
    -m "$MODEL" \
    --mmproj "$MMPROJ" \
    -ngl 999 \
    --host 127.0.0.1 --port "$PORT" \
    -c "$CTX" --parallel "$NPAR" --cont-batching --jinja \
    -fa on --batch-size 2048 --ubatch-size 512 \
    >> "$LOG_FILE" 2>&1 &
  pid=$!
  echo "$pid" > "$PID_FILE"
  echo "pid=$pid avviato. attendo health..."
  for i in $(seq 1 30); do
    if curl -fsS -m 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
      echo "ready in ${i}s"
      if [ "$auto_stop_idle" -gt 0 ]; then
        _spawn_watchdog "$auto_stop_idle"
      fi
      return 0
    fi
    sleep 1
  done
  echo "WARNING: server non risponde a /health entro 30s. controlla $LOG_FILE"
  return 2
}

_spawn_watchdog() {
  local idle_secs="$1"
  echo "spawn watchdog auto-stop idle=${idle_secs}s..."
  nohup "$0" watchdog "$idle_secs" >> "$WD_LOG_FILE" 2>&1 &
  echo "$!" > "$WD_PID_FILE"
  echo "watchdog pid=$!"
}

cmd_watchdog() {
  local idle_secs="${1:-600}"
  local poll_secs=30
  local last_active_ts
  last_active_ts=$(date +%s)
  echo "[$(date +%T)] watchdog START idle_threshold=${idle_secs}s poll=${poll_secs}s server_port=$PORT"
  while true; do
    sleep "$poll_secs"
    local pid
    pid="$(_find_server_pid)"
    if ! _pid_alive "$pid"; then
      echo "[$(date +%T)] server gia spento, watchdog esce"
      rm -f "$WD_PID_FILE"
      exit 0
    fi
    local conns
    conns=$(ss -tn state established "( sport = :$PORT )" 2>/dev/null | tail -n +2 | wc -l)
    if [ "$conns" -gt 0 ]; then
      last_active_ts=$(date +%s)
      echo "[$(date +%T)] active=$conns, reset idle timer"
      continue
    fi
    local now idle
    now=$(date +%s)
    idle=$((now - last_active_ts))
    if [ "$idle" -ge "$idle_secs" ]; then
      echo "[$(date +%T)] idle ${idle}s >= threshold ${idle_secs}s, kill server pid=$pid"
      kill -TERM "$pid" 2>/dev/null || true
      sleep 5
      if _pid_alive "$pid"; then
        kill -KILL "$pid" 2>/dev/null || true
      fi
      rm -f "$PID_FILE" "$WD_PID_FILE"
      echo "[$(date +%T)] server stopped, watchdog exit"
      exit 0
    fi
    echo "[$(date +%T)] idle=${idle}s (limit ${idle_secs}s), continue"
  done
}

cmd_stop() {
  # Stop watchdog first (cosi' non riavvia o non killa di nuovo)
  local wd_pid
  wd_pid="$(_find_watchdog_pid)"
  if _pid_alive "$wd_pid"; then
    echo "stop watchdog pid=$wd_pid..."
    kill -TERM "$wd_pid" 2>/dev/null || true
    rm -f "$WD_PID_FILE"
  fi
  local pid
  pid="$(_find_server_pid)"
  if ! _pid_alive "$pid"; then
    echo "server non in esecuzione."
    rm -f "$PID_FILE"
    return 0
  fi
  echo "fermo server pid=$pid (SIGTERM)..."
  kill -TERM "$pid" 2>/dev/null || true
  for i in $(seq 1 10); do
    if ! _pid_alive "$pid"; then
      echo "spento in ${i}s."
      rm -f "$PID_FILE"
      return 0
    fi
    sleep 1
  done
  echo "non risponde a SIGTERM, forzo SIGKILL..."
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
}

cmd_restart() {
  local args="$@"
  cmd_stop
  cmd_start "$@"
}

case "${1:-status}" in
  start)    shift; cmd_start "$@" ;;
  stop)     cmd_stop ;;
  status)   cmd_status ;;
  restart)  shift; cmd_restart "$@" ;;
  watchdog) shift; cmd_watchdog "$@" ;;
  *) echo "uso: $0 {start [--auto-stop-idle SECS] | stop | status | restart | watchdog SECS}"; exit 2 ;;
esac
