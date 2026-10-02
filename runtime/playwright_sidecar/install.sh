#!/bin/bash
# install.sh — setup del sidecar Playwright (ADR 0125).
#
# Prepara il motore selezionato senza avviare servizi. Chromium e' il default;
# Camoufox (Linux x86_64) richiede METNOS_SITES_BROWSER_ENGINE=camoufox
# e l'eccezione esplicita METNOS_SITES_WEBSOCKETS_ALLOWED=1.
#
# Uso:
#   ./install.sh                # venv Metnos canonico
#   METNOS_VENV=/path ./install.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
METNOS_USER_DATA="${METNOS_USER_DATA:-$HOME/.local/share/metnos}"
METNOS_VENV="${METNOS_VENV:-$ROOT/.venv}"
PYTHON="$METNOS_VENV/bin/python"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$METNOS_USER_DATA/playwright-browsers}"
export METNOS_USER_DATA METNOS_VENV
export METNOS_INSTALL_ROOT="$ROOT"

if [ ! -x "$PYTHON" ]; then
    BASE_PYTHON="${BASE_PYTHON:-python3}"
    echo "[0/2] creo il venv Metnos in $METNOS_VENV..."
    mkdir -p "$(dirname "$METNOS_VENV")"
    "$BASE_PYTHON" -m venv "$METNOS_VENV"
fi

cd "$ROOT"
"$PYTHON" -m install.playwright_sidecar --prepare
echo "Motore preparato; configurazione: $METNOS_USER_DATA/browser-engine.env"
