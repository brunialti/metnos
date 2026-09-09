#!/usr/bin/env bash
# Run, as root, the two RM-0008 steps of 9 September 2026 in one go:
#
#   1. record the abandonment of the crossing the machine proves unattestable
#      (one write, no service stopped, nothing signed rewritten);
#   2. read back one browser turn, read-only, without printing any secret.
#
# Usage:  sudo bash internal/tools/rm0008_run_abandonment_and_turn.sh [turn-id]
#
# The second step runs even if the first refuses, so one failure never hides
# the other result. The exit code is non-zero if either step failed.
set -uo pipefail

TOOLS="/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools"
PYTHON="/usr/bin/python3.12"
TURN="${1:-5598f91b}"

if [ "$(id -u)" -ne 0 ]; then
    echo "REFUSED: run this with sudo" >&2
    exit 1
fi

status=0

if [ "${SKIP_ABANDONMENT:-0}" = "1" ]; then
    echo "=== 1. ABBANDONO: saltato su richiesta (gia' registrato) ==="
else
    echo "=== 1. ABBANDONO DELLA CROCIERA NON ATTESTABILE ==="
    "$PYTHON" "$TOOLS/rm0008_abandon_unattestable_crossing.py"
    first=$?
    if [ "$first" -ne 0 ]; then
        echo "--- passo 1 FALLITO (uscita $first): nulla e' stato scritto ---"
        status="$first"
    fi
fi

echo
echo "=== 2. LETTURA DEL TURNO $TURN (sola lettura) ==="
"$PYTHON" "$TOOLS/inspect_sites_turn.py" "$TURN"
second=$?
if [ "$second" -ne 0 ]; then
    echo "--- passo 2 FALLITO (uscita $second) ---"
    [ "$status" -eq 0 ] && status="$second"
fi

echo
echo "=== ESITO COMPLESSIVO: $status ==="
exit "$status"
