#!/usr/bin/env bash
# Invia un messaggio dalla casella user@example.com (example.com).
# Credenziali e host SMTP letti da ~/.config/account_personal/mail.env (chmod 600).
#
# Uso:
#   ./send-mail.sh --to <indirizzo> --subject "<oggetto>" --body-file <path>
#
# Variabili attese in mail.env (riusa quelle di IMAP, aggiunge SMTP host/port):
#   account_personal_MAIL_USER   (es. user@example.com)
#   account_personal_MAIL_PASS
#   account_personal_SMTP_HOST   (default: smtp.example.com — provider example.com)
#   account_personal_SMTP_PORT   (default: 465, SMTPS)
#
# Il certificato del server di example.com è rilasciato per *.example.com:
# si usa --insecure come per check-mail.sh. TLS resta attivo, salta solo la
# verifica del CN.
set -euo pipefail

ENV_FILE="${HOME}/.config/account_personal/mail.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE non esiste." >&2
    exit 1
fi

perms=$(stat -c '%a' "$ENV_FILE")
if [ "$perms" != "600" ] && [ "$perms" != "400" ]; then
    echo "ERROR: $ENV_FILE ha permessi $perms. Deve essere 600 o 400." >&2
    exit 1
fi

set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a

: "${account_personal_MAIL_USER:?manca account_personal_MAIL_USER in $ENV_FILE}"
: "${account_personal_MAIL_PASS:?manca account_personal_MAIL_PASS in $ENV_FILE}"
SMTP_HOST="${account_personal_SMTP_HOST:-smtp.example.com}"
SMTP_PORT="${account_personal_SMTP_PORT:-465}"

# --- argomenti -----------------------------------------------------------
TO=""
SUBJECT=""
BODY_FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --to)         TO="${2:-}"; shift ;;
        --subject)    SUBJECT="${2:-}"; shift ;;
        --body-file)  BODY_FILE="${2:-}"; shift ;;
        --help)
            sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "Argomento sconosciuto: $1" >&2; exit 2 ;;
    esac
    shift
done

[ -n "$TO" ]        || { echo "ERROR: --to mancante" >&2; exit 2; }
[ -n "$SUBJECT" ]   || { echo "ERROR: --subject mancante" >&2; exit 2; }
[ -n "$BODY_FILE" ] || { echo "ERROR: --body-file mancante" >&2; exit 2; }
[ -f "$BODY_FILE" ] || { echo "ERROR: $BODY_FILE non esiste" >&2; exit 2; }

# --- composizione messaggio (RFC 5322) -----------------------------------
TMP_MSG=$(mktemp)
trap 'rm -f "$TMP_MSG"' EXIT

DATE_RFC=$(date -R)
MSG_ID="<$(date +%s).$$@$(hostname -f 2>/dev/null || hostname)>"

{
    printf 'From: %s\r\n' "$account_personal_MAIL_USER"
    printf 'To: %s\r\n' "$TO"
    printf 'Subject: %s\r\n' "$SUBJECT"
    printf 'Date: %s\r\n' "$DATE_RFC"
    printf 'Message-ID: %s\r\n' "$MSG_ID"
    printf 'MIME-Version: 1.0\r\n'
    printf 'Content-Type: text/plain; charset=UTF-8\r\n'
    printf 'Content-Transfer-Encoding: 8bit\r\n'
    printf '\r\n'
    # CRLF normalization del body
    sed 's/\r$//; s/$/\r/' "$BODY_FILE"
} > "$TMP_MSG"

# --- invio ---------------------------------------------------------------
curl -sS -k --max-time 30 \
    --url "smtps://${SMTP_HOST}:${SMTP_PORT}" \
    --user "${account_personal_MAIL_USER}:${account_personal_MAIL_PASS}" \
    --mail-from "$account_personal_MAIL_USER" \
    --mail-rcpt "$TO" \
    --upload-file "$TMP_MSG"

echo "Inviato a $TO (oggetto: $SUBJECT)"
