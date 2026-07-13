#!/bin/bash
# install.sh — setup del sidecar Playwright (ADR 0125).
#
# Scarica ~300MB (Chromium headless). NON eseguito automaticamente: Roberto
# lancia manualmente quando vuole abilitare il JS-rendering.
#
# Uso:
#   ./install.sh                # venv Metnos canonico
#   METNOS_VENV=/path ./install.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
METNOS_USER_DATA="${METNOS_USER_DATA:-$HOME/.local/share/metnos}"
METNOS_VENV="${METNOS_VENV:-$METNOS_USER_DATA/.venv}"
PYTHON="$METNOS_VENV/bin/python"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$METNOS_USER_DATA/playwright-browsers}"

if [ ! -x "$PYTHON" ]; then
    BASE_PYTHON="${BASE_PYTHON:-python3}"
    echo "[0/2] creo il venv Metnos in $METNOS_VENV..."
    mkdir -p "$(dirname "$METNOS_VENV")"
    "$BASE_PYTHON" -m venv "$METNOS_VENV"
fi

echo "[1/2] pip install dipendenze Metnos + playwright..."
"$PYTHON" -m pip install --upgrade-strategy only-if-needed \
    -r "$ROOT/requirements.txt"
"$PYTHON" -m pip install --upgrade "playwright==1.61.0"

echo "[2/2] playwright install chromium (~300MB download)..."
"$PYTHON" -m playwright install chromium

echo
echo "OK. Avvia il sidecar:"
echo "  $PYTHON -m playwright_sidecar.server --host 127.0.0.1 --port 8771"
echo "Oppure abilita il servizio systemd-user:"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now metnos-playwright.service"
