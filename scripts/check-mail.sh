#!/usr/bin/env bash
# Controlla la posta in arrivo della casella user@example.com (register.it).
# Credenziali lette da ~/.config/mykleos/mail.env (chmod 600), non da ambiente
# interattivo n? da memoria della conversazione.
#
# Uso:
#   ./check-mail.sh                 # stato sintetico di tutte le cartelle
#   ./check-mail.sh --headers       # in pi?, intestazioni dei messaggi non letti in INBOX
#   ./check-mail.sh --folder NAME   # stato di una sola cartella
#
# Il certificato di imap.register.it ? rilasciato per *.securemail.pro (alias
# del provider): mismatch atteso, si usa --insecure per accettarlo. Il flusso
# IMAPS resta cifrato con TLS, ? solo la verifica del CN che viene saltata.
set -euo pipefail

ENV_FILE="${HOME}/.config/mykleos/mail.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE non esiste." >&2
    echo "Crealo con MYKLEOS_MAIL_USER, MYKLEOS_MAIL_PASS, MYKLEOS_MAIL_HOST, MYKLEOS_MAIL_PORT (chmod 600)." >&2
    exit 1
fi

perms=$(stat -c '%a' "$ENV_FILE")
if [ "$perms" != "600" ] && [ "$perms" != "400" ]; then
    echo "ERROR: $ENV_FILE ha permessi $perms. Deve essere 600 o 400." >&2
    echo "Esegui: chmod 600 $ENV_FILE" >&2
    exit 1
fi

set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a

: "${MYKLEOS_MAIL_USER:?manca MYKLEOS_MAIL_USER in $ENV_FILE}"
: "${MYKLEOS_MAIL_PASS:?manca MYKLEOS_MAIL_PASS in $ENV_FILE}"
MYKLEOS_MAIL_HOST="${MYKLEOS_MAIL_HOST:-imap.register.it}"
MYKLEOS_MAIL_PORT="${MYKLEOS_MAIL_PORT:-993}"

URL="imaps://${MYKLEOS_MAIL_HOST}:${MYKLEOS_MAIL_PORT}/INBOX"

# --- argomenti -----------------------------------------------------------
SHOW_HEADERS=0
SINGLE_FOLDER=""
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--headers) SHOW_HEADERS=1 ;;
        -f|--folder)  SINGLE_FOLDER="${2:-}"; shift ;;
        --help)
            sed -n '2,9p' "$0"
            exit 0 ;;
        *) echo "Argomento sconosciuto: $1" >&2; exit 2 ;;
    esac
    shift
done

# --- helper: STATUS di una cartella -------------------------------------
status_of() {
    local box="$1"
    curl -sS -k --max-time 10 --url "$URL" \
        -u "${MYKLEOS_MAIL_USER}:${MYKLEOS_MAIL_PASS}" \
        -X "STATUS \"$box\" (MESSAGES UNSEEN)" 2>/dev/null \
        | awk -v b="$box" '
            /^\* STATUS/ {
                tot=""; uns=""
                gsub(/[()]/,"",$0)
                n=split($0, f, /[ \t]+/)
                for (i=1;i<=n;i++) {
                    if (f[i]=="MESSAGES") tot=f[i+1]
                    if (f[i]=="UNSEEN")   uns=f[i+1]
                }
                printf "  %-18s  totali %4s  da leggere %4s\n", b, tot, uns
            }
        '
}

# --- esecuzione --------------------------------------------------------
echo "Casella: ${MYKLEOS_MAIL_USER}  (host ${MYKLEOS_MAIL_HOST})"
echo

if [ -n "$SINGLE_FOLDER" ]; then
    status_of "$SINGLE_FOLDER"
else
    for box in INBOX INBOX.Spam INBOX.Sent INBOX.Drafts INBOX.Trash; do
        status_of "$box"
    done
fi

# Conteggio non letti in INBOX (per decidere se mostrare le intestazioni).
INBOX_UNSEEN=$(curl -sS -k --max-time 10 --url "$URL" \
    -u "${MYKLEOS_MAIL_USER}:${MYKLEOS_MAIL_PASS}" \
    -X 'STATUS "INBOX" (UNSEEN)' 2>/dev/null \
    | awk '/^\* STATUS/ { gsub(/[()]/,"",$0); n=split($0,f,/[ \t]+/); for (i=1;i<=n;i++) if (f[i]=="UNSEEN") { print f[i+1]; exit } }')
INBOX_UNSEEN="${INBOX_UNSEEN:-0}"
case "$INBOX_UNSEEN" in
    ''|*[!0-9]*) INBOX_UNSEEN=0 ;;
esac

if [ "$SHOW_HEADERS" -eq 1 ] && [ "$INBOX_UNSEEN" -gt 0 ]; then
    echo
    echo "--- Intestazioni dei ${INBOX_UNSEEN} messaggi non letti in INBOX ---"
    # SEARCH degli UID non letti, poi FETCH degli envelope
    UIDS=$(curl -sS -k --max-time 15 --url "$URL" \
        -u "${MYKLEOS_MAIL_USER}:${MYKLEOS_MAIL_PASS}" \
        -X 'UID SEARCH UNSEEN' 2>/dev/null \
        | awk '/^\* SEARCH/ { for (i=3;i<=NF;i++) print $i }' \
        | tr '\n' ',' | sed 's/,$//')
    if [ -n "$UIDS" ]; then
        curl -sS -k --max-time 20 --url "$URL" \
            -u "${MYKLEOS_MAIL_USER}:${MYKLEOS_MAIL_PASS}" \
            -X "UID FETCH ${UIDS} (BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])" 2>/dev/null \
            | awk '
                /^From:/    { sub(/^From: */,""); print "  da:      " $0 }
                /^Subject:/ { sub(/^Subject: */,""); print "  oggetto: " $0 }
                /^Date:/    { sub(/^Date: */,""); print "  data:    " $0; print "" }
              '
    fi
fi

if [ "$INBOX_UNSEEN" -eq 0 ] && [ "$SHOW_HEADERS" -eq 1 ]; then
    echo
    echo "(nessun messaggio non letto in INBOX)"
fi
