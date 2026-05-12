#!/bin/bash
# install.sh — setup del sidecar Playwright (ADR 0125).
#
# Scarica ~300MB (Chromium headless). NON eseguito automaticamente: Roberto
# lancia manualmente quando vuole abilitare il JS-rendering.
#
# Uso:
#   ./install.sh                # default venv /opt/suprastructure/.venv
#   PYTHON=python3 ./install.sh # override

set -euo pipefail

PYTHON="${PYTHON:-/opt/suprastructure/.venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: python not found at $PYTHON — set PYTHON=..." >&2
    exit 1
fi

echo "[1/2] pip install playwright + aiohttp..."
"$PYTHON" -m pip install --upgrade "playwright>=1.40" "aiohttp>=3.9"

echo "[2/2] playwright install chromium (~300MB download)..."
"$PYTHON" -m playwright install chromium

echo
echo "OK. Avvia il sidecar:"
echo "  $PYTHON -m playwright_sidecar.server --host 127.0.0.1 --port 8771"
echo "Oppure abilita il servizio systemd-user:"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now metnos-playwright.service"
