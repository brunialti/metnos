#!/usr/bin/env bash
#
# myclaw-unattended.sh — esegue Claude in background su /opt/metnos/
# continuando la microprogettazione fino a esaurimento dei doc pianificati.
#
# Uso:
#   myclaw-unattended.sh start   # lancia in background
#   myclaw-unattended.sh status  # stato + ultima riga del log
#   myclaw-unattended.sh log     # tail -f del log
#   myclaw-unattended.sh stop    # ferma pulitamente
#   myclaw-unattended.sh restart # stop + start
#
# Sopravvive a logout/chiusura terminale (nohup + disown).
# Progress: http://192.168.1.33:8810/architecture/

set -euo pipefail

readonly PROJECT_DIR="/opt/metnos"
readonly LOG_FILE="/tmp/myclaw-unattended.log"
readonly PID_FILE="/tmp/myclaw-unattended.pid"
readonly CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"

readonly INSTRUCTION='Esegui /avanza iterativamente finché la tabella di docs/architecture/index.html non ha più righe con stato "pianificato". Un doc alla volta, aggiorna index, stop a esaurimento. Se un doc esiste già non riscriverlo.'

# ---- colors (disabled se non TTY) ----
if [[ -t 1 ]]; then
  C_GREEN=$'\033[0;32m'; C_RED=$'\033[0;31m'; C_YELLOW=$'\033[0;33m'
  C_BLUE=$'\033[0;34m'; C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
else
  C_GREEN=''; C_RED=''; C_YELLOW=''; C_BLUE=''; C_RESET=''; C_BOLD=''
fi

info()  { echo -e "${C_BLUE}→${C_RESET} $*"; }
ok()    { echo -e "${C_GREEN}✓${C_RESET} $*"; }
warn()  { echo -e "${C_YELLOW}⚠${C_RESET} $*"; }
error() { echo -e "${C_RED}✗${C_RESET} $*" >&2; }

# ---- preflight checks ----
preflight() {
  [[ -d "$PROJECT_DIR" ]] || { error "$PROJECT_DIR non esiste"; exit 1; }
  [[ -x "$CLAUDE_BIN" ]]  || { error "claude CLI non trovato a $CLAUDE_BIN"; exit 1; }
  [[ -f "$PROJECT_DIR/.claude/commands/avanza.md" ]] || {
    error "slash command /avanza non trovato in $PROJECT_DIR/.claude/commands/avanza.md"
    exit 1
  }
}

# ---- is running? ----
is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

current_pid() {
  [[ -f "$PID_FILE" ]] && cat "$PID_FILE" 2>/dev/null || echo ""
}

# ---- commands ----
cmd_start() {
  preflight
  if is_running; then
    warn "già in esecuzione (PID $(current_pid))"
    exit 0
  fi

  # rotate log if > 5MB
  if [[ -f "$LOG_FILE" ]] && [[ $(stat -c%s "$LOG_FILE") -gt 5242880 ]]; then
    mv "$LOG_FILE" "${LOG_FILE}.old"
    info "log ruotato: ${LOG_FILE}.old"
  fi

  info "avvio Claude in background..."
  info "  cwd:    $PROJECT_DIR"
  info "  log:    $LOG_FILE"
  info "  pid:    $PID_FILE"

  cd "$PROJECT_DIR"

  # nohup + disown per sopravvivere a logout
  nohup "$CLAUDE_BIN" \
    --permission-mode acceptEdits \
    "$INSTRUCTION" \
    >> "$LOG_FILE" 2>&1 &

  local pid=$!
  echo "$pid" > "$PID_FILE"
  disown %1 2>/dev/null || true

  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    ok "avviato con PID $pid"
    echo ""
    info "controlla progresso con:"
    echo "    $0 status"
    echo "    $0 log"
    echo "    http://192.168.1.33:8810/architecture/"
  else
    error "il processo non è sopravvissuto al primo secondo. Vedi $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi
}

cmd_stop() {
  if ! is_running; then
    warn "non in esecuzione"
    rm -f "$PID_FILE"
    exit 0
  fi
  local pid
  pid=$(current_pid)
  info "fermo PID $pid..."
  kill -TERM "$pid" 2>/dev/null || true

  # wait max 10s for graceful shutdown
  for _ in $(seq 1 10); do
    if ! kill -0 "$pid" 2>/dev/null; then
      ok "fermato puliamente"
      rm -f "$PID_FILE"
      return 0
    fi
    sleep 1
  done

  warn "non risponde a TERM, uso KILL"
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  ok "ucciso"
}

cmd_status() {
  if is_running; then
    local pid
    pid=$(current_pid)
    ok "in esecuzione · PID $pid"

    # uptime
    if command -v ps &>/dev/null; then
      local etime
      etime=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
      [[ -n "$etime" ]] && info "uptime: $etime"
    fi

    # progress check
    if command -v curl &>/dev/null; then
      local done_count pending_count
      done_count=$(curl -s --max-time 2 http://192.168.1.33:8810/architecture/ 2>/dev/null | grep -c 'status approved' || echo "?")
      pending_count=$(curl -s --max-time 2 http://192.168.1.33:8810/architecture/ 2>/dev/null | grep -c 'status planned' || echo "?")
      info "doc approvati: $done_count · pianificati: $pending_count"
    fi

    # last log line
    if [[ -f "$LOG_FILE" ]]; then
      echo ""
      echo "${C_BOLD}ultima riga del log:${C_RESET}"
      tail -1 "$LOG_FILE" | sed 's/^/    /'
    fi
  else
    warn "non in esecuzione"
    [[ -f "$PID_FILE" ]] && rm -f "$PID_FILE"
    if [[ -f "$LOG_FILE" ]]; then
      echo ""
      echo "${C_BOLD}ultima riga del log:${C_RESET}"
      tail -1 "$LOG_FILE" | sed 's/^/    /'
    fi
    exit 1
  fi
}

cmd_log() {
  [[ -f "$LOG_FILE" ]] || { error "nessun log in $LOG_FILE"; exit 1; }
  exec tail -f "$LOG_FILE"
}

cmd_restart() {
  cmd_stop
  sleep 1
  cmd_start
}

# ---- dispatch ----
case "${1:-}" in
  start)    cmd_start ;;
  stop)     cmd_stop ;;
  status)   cmd_status ;;
  log)      cmd_log ;;
  restart)  cmd_restart ;;
  ""|help|-h|--help)
    sed -n '3,16p' "$0" | sed 's/^# \?//'
    ;;
  *)
    error "comando sconosciuto: $1"
    echo "usa: $0 {start|stop|status|log|restart|help}" >&2
    exit 1
    ;;
esac
