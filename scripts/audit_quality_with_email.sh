#!/usr/bin/env bash
# audit_quality_with_email.sh — esegue audit qualità traduzione
# (metnos-prompts audit-quality) in BG OS-level e invia email con report.
#
# Pattern: systemd-run user transient unit (zero zombie, sopravvive a
# chiusura sessione Claude/SSH, log via journalctl --user). Stessa
# robustezza di ADR 0093 async build.
#
# Uso (lancio diretto, sincrono):
#   /opt/myclaw/scripts/audit_quality_with_email.sh
#
# Uso (lancio OS-level via systemd, sopravvive a chiusura sessione):
#   systemd-run --user --transient --unit=metnos-audit-quality \
#       /opt/myclaw/scripts/audit_quality_with_email.sh
#
# Variabili env opzionali:
#   AUDIT_TARGET_LANG  (default: en)
#   AUDIT_SAMPLE       (default: all)
#   AUDIT_APPLY        (default: 1 — auto-apply config dopo audit)
#   AUDIT_EMAIL_TO     (default: roberto.brunialti@knowcastle.com)
#
# Output:
#   /tmp/metnos_audit_<ts>/  contiene:
#     - report.json (output strutturato)
#     - report.txt  (output testuale tabellare)
#     - audit.log   (stdout/stderr completo)

set -euo pipefail

VENV_PY=/opt/suprastructure/.venv/bin/python
TARGET_LANG="${AUDIT_TARGET_LANG:-en}"
SAMPLE="${AUDIT_SAMPLE:-all}"
APPLY="${AUDIT_APPLY:-1}"
EMAIL_TO="${AUDIT_EMAIL_TO:-roberto.brunialti@knowcastle.com}"

TS=$(date +%Y%m%d_%H%M%S)
WORKDIR=/tmp/metnos_audit_$TS
mkdir -p "$WORKDIR"
LOG="$WORKDIR/audit.log"
REPORT_TXT="$WORKDIR/report.txt"
REPORT_JSON="$WORKDIR/report.json"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

log "audit start: target=$TARGET_LANG sample=$SAMPLE apply=$APPLY"
log "workdir: $WORKDIR"

# Costruzione argomenti audit-quality
AUDIT_ARGS=(audit-quality --to "$TARGET_LANG")
if [ "$SAMPLE" != "all" ]; then
    AUDIT_ARGS+=(--sample "$SAMPLE")
fi
if [ "$APPLY" = "1" ]; then
    AUDIT_ARGS+=(--apply)
fi
AUDIT_ARGS+=(--report)  # JSON dettagliato per-prompt in stdout (extracted poi)

# --- Esegui audit ---
log "running: $VENV_PY -m admin.prompts_cli ${AUDIT_ARGS[*]}"

cd /opt/myclaw/runtime
export PYTHONPATH=/opt/myclaw/runtime

EXIT_CODE=0
{
    "$VENV_PY" -m admin.prompts_cli "${AUDIT_ARGS[@]}" 2>&1 | tee -a "$LOG"
} || EXIT_CODE=$?

log "audit exit code: $EXIT_CODE"

# Estrai report testuale (intero output) e JSON (se stampato)
cp "$LOG" "$REPORT_TXT"

# Cerca config attiva post-audit
CONFIG_TOML="$HOME/.config/metnos/translator_tier.toml"
if [ -f "$CONFIG_TOML" ]; then
    {
        echo ""
        echo "=== Config translator_tier.toml dopo audit ==="
        cat "$CONFIG_TOML"
    } >> "$REPORT_TXT"
fi

# --- Costruisci email ---
SUBJECT="[Metnos] Audit qualità traduzione $TARGET_LANG — $(date '+%Y-%m-%d %H:%M')"
BODY_FILE="$WORKDIR/email_body.txt"

{
    echo "Audit qualità traduzione automatico Metnos"
    echo "==========================================="
    echo "Data:      $(date -Iseconds)"
    echo "Target:    $TARGET_LANG"
    echo "Sample:    $SAMPLE"
    echo "Apply:     $APPLY (1=auto-update config, 0=solo report)"
    echo "Exit code: $EXIT_CODE"
    echo ""
    echo "Report completo (testuale):"
    echo "---"
    cat "$REPORT_TXT"
    echo ""
    echo "---"
    echo "Workdir locale: $WORKDIR"
    echo ""
    if [ "$EXIT_CODE" -eq 0 ]; then
        echo "Esito: OK"
    else
        echo "Esito: FAIL (vedi exit code + log sopra)"
    fi
    echo ""
    echo "Per modificare la decisione manualmente:"
    echo "  - edit ~/.config/metnos/translator_tier.toml"
    echo "  - edit Environment=METNOS_TRANSLATOR_QUALITY=... in"
    echo "    /etc/systemd/system/metnos-prompts-translator.service"
    echo "  - sudo systemctl daemon-reload && sudo systemctl restart metnos-prompts-translator.timer"
} > "$BODY_FILE"

log "email body composed: $BODY_FILE ($(wc -l <"$BODY_FILE") righe)"

# --- Invia email ---
SEND_MAIL=/opt/myclaw/scripts/send-mail.sh
if [ ! -x "$SEND_MAIL" ]; then
    log "ERROR: $SEND_MAIL non eseguibile, skip invio email"
    log "report disponibile in $WORKDIR"
    exit "$EXIT_CODE"
fi

log "invio email a $EMAIL_TO"
if "$SEND_MAIL" --to "$EMAIL_TO" --subject "$SUBJECT" --body-file "$BODY_FILE" 2>>"$LOG"; then
    log "email inviata con successo"
else
    rc=$?
    log "ERROR: invio email fallito (rc=$rc). Report comunque disponibile in $WORKDIR"
fi

log "audit end"
exit "$EXIT_CODE"
