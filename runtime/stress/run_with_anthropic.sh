#!/usr/bin/env bash
# Helper: imposta ANTHROPIC_API_KEY dal credentials.env e lancia lo stress test.
# Usato dalla fase E del synt-stress per testare Claude come tier wise.
set -euo pipefail
CRED=/home/roberto/.config/metnos/credentials.env
if [ ! -f "$CRED" ]; then
  echo "missing $CRED" >&2; exit 2
fi
# Esporta tutte le variabili dal credentials.env (inclusa ANTHROPIC_API_KEY)
set -a
# shellcheck disable=SC1090
source "$CRED"
set +a
exec "$@"
