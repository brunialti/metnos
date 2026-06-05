#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Metnos installer bootstrap — locates a Python 3.11+ and runs the interactive
# setup (install/install.py). English-only. Pass --check for a dry run.
#
#   ./install.sh            # interactive setup
#   ./install.sh --check    # congruence checks only, writes nothing
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pick_python() {
  # Prefer an active venv, then a known repo venv, then system python3.
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
    echo "${VIRTUAL_ENV}/bin/python"; return
  fi
  for c in /opt/suprastructure/.venv/bin/python "${ROOT}/.venv/bin/python" python3; do
    if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then echo "$c"; return; fi
  done
  echo "";
}

PY="$(pick_python)"
if [ -z "$PY" ]; then
  echo "error: no Python 3 found. Install Python >= 3.11 and re-run." >&2
  exit 1
fi

exec "$PY" "${ROOT}/install/install.py" "$@"
