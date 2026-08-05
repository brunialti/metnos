#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/opt/metnos"
LOG_DIR="${ROOT}/tests/e2e/reports/admin_runs"
mkdir -p "$LOG_DIR"
cd "$ROOT"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Esegui con sudo: sudo $0" >&2
  exit 2
fi

python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", 0))
    print("loopback TCP OK", s.getsockname())
finally:
    s.close()
PY

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -n "${E2E_PYTHON:-}" ]]; then
  PYTHON_BIN="$E2E_PYTHON"
else
  PYTHON_BIN=""
  for candidate in \
    "${ROOT}/.venv/bin/python" \
    "/home/roberto/.venv/bin/python" \
    "/home/roberto/.local/bin/python3" \
    "/usr/local/bin/python3"; do
    if [[ -x "$candidate" ]] && "$candidate" -c 'import pytest, pytest_asyncio, aiohttp' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [[ -z "$PYTHON_BIN" ]] || ! "$PYTHON_BIN" -c 'import pytest, pytest_asyncio, aiohttp' >/dev/null 2>&1; then
  VENV="${ROOT}/.venv"
  echo "pytest non trovato: preparo ${VENV}"
  if [[ ! -x "${VENV}/bin/python" ]]; then
    if ! python3 -m venv "$VENV"; then
      echo "python3-venv mancante: installo il pacchetto di sistema"
      apt-get update
      apt-get install -y python3-venv
      python3 -m venv "$VENV"
    fi
  fi
  if ! "${VENV}/bin/python" -m pip install --disable-pip-version-check -q \
      pytest 'pytest-asyncio>=0.24' 'aiohttp>=3.13'; then
    echo "pip non riuscito: provo i pacchetti apt"
    apt-get update
    apt-get install -y python3-pytest python3-pytest-asyncio python3-aiohttp
    PYTHON_BIN="/usr/bin/python3"
  fi
  PYTHON_BIN="${VENV}/bin/python"
  "$PYTHON_BIN" -c 'import pytest, pytest_asyncio, aiohttp' >/dev/null 2>&1 || {
    echo "installazione pytest fallita" >&2
    exit 3
  }
fi
echo "Python E2E: $PYTHON_BIN"

TESTS=(
  tests/e2e/scenarios/test_catalog_coverage.py
  tests/e2e/scenarios/test_chat_image_index.py
)

for cycle in 1 2; do
  log="${LOG_DIR}/${STAMP}_cycle${cycle}.log"
  echo "E2E cycle ${cycle}/2 — log: ${log}"
  if ! "$PYTHON_BIN" -m pytest -q "${TESTS[@]}" 2>&1 | tee "$log"; then
    echo "E2E cycle ${cycle} FAILED; ciclo successivo non eseguito." >&2
    exit 1
  fi
done

echo "Due cicli E2E consecutivi verdi. Log in ${LOG_DIR}/${STAMP}_cycle{1,2}.log"
