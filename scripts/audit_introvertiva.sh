#!/usr/bin/env bash
# audit_introvertiva.sh — invoca Claude Code in modalità non-interattiva
# per fare audit del ciclo introvertiva notturno di Metnos.
#
# Schedulato via crontab utente per i prossimi 3 giorni (5-6-7 maggio 2026).
# Vedi ADR 0078 (auto-audit introvertiva, se promosso) e
# ~/.claude/projects/-opt-myclaw/memory/metnos_auto_audit_*.md per i report.
#
# Output completo loggato in ~/.local/share/metnos/auto_audit/<date>.log

set -u

PROMPT_FILE="/opt/metnos/scripts/audit_introvertiva_prompt.txt"
LOG_DIR="$HOME/.local/share/metnos/auto_audit"
TODAY="$(date +%Y-%m-%d)"
LOG_FILE="$LOG_DIR/${TODAY}.log"

mkdir -p "$LOG_DIR"

cd /opt/metnos || exit 1

{
  echo "=== auto-audit introvertiva start $(date -Iseconds) ==="
  echo "claude version: $(/home/roberto/.local/bin/claude --version 2>&1 | head -1)"
  echo "cwd: $(pwd)"
  echo
} >> "$LOG_FILE"

# `--print` (alias `-p`) = single-prompt non-interattivo, stdout-only.
# `--dangerously-skip-permissions` = no conferme tool (richiesto in cron).
# Prompt da stdin tramite redirect (evita argv length limits).
/home/roberto/.local/bin/claude \
  --print \
  --dangerously-skip-permissions \
  < "$PROMPT_FILE" \
  >> "$LOG_FILE" 2>&1

EXIT_CODE=$?

{
  echo
  echo "=== auto-audit end $(date -Iseconds), exit=$EXIT_CODE ==="
} >> "$LOG_FILE"

exit "$EXIT_CODE"
