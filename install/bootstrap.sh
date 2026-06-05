#!/usr/bin/env bash
# Metnos installer — bootstrap entry point.
#
# Two invocation modes:
#
#   1) From a clone (recommended for review):
#        git clone https://github.com/brunialti/metnos.git
#        cd metnos
#        bash install/bootstrap.sh
#
#   2) From the web (convenience):
#        curl -fsSL https://metnos.com/install.sh | sh
#
# This shell layer is intentionally minimal. It only does what cannot be
# done in Python yet: find a working python3.12+, create a venv, install
# the bootstrap dependencies (rich, httpx, tomli on py<3.11), then hand
# off to `python -m install`. Everything else lives in Python so it can
# be reviewed and stepped through.
#
# Safe to re-run: idempotent (skips venv creation if present, skips pip
# install if packages already importable).

set -euo pipefail

# ─────── Config ───────────────────────────────────────────────────
METNOS_HOME="${METNOS_HOME:-$HOME/.local/share/metnos}"
METNOS_STATE="${METNOS_STATE:-$HOME/.local/state/metnos}"
METNOS_VENV="$METNOS_HOME/.venv"
METNOS_REPO_URL="${METNOS_REPO_URL:-https://github.com/brunialti/metnos.git}"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=12

# ─────── Pretty output (works without rich) ───────────────────────
if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; RED=$'\033[31m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; BLUE=""; RESET=""
fi

banner() { printf "\n%s━━━ %s ━━━%s\n" "$BOLD" "$1" "$RESET"; }
step()   { printf "  %s•%s %s\n" "$BLUE" "$RESET" "$1"; }
ok()     { printf "  %s✓%s %s\n" "$GREEN" "$RESET" "$1"; }
warn()   { printf "  %s!%s %s\n" "$YELLOW" "$RESET" "$1"; }
fail()   { printf "  %s✗%s %s\n" "$RED" "$RESET" "$1" >&2; exit 1; }
info()   { printf "    %s%s%s\n" "$DIM" "$1" "$RESET"; }

# ─────── 0. Welcome ───────────────────────────────────────────────
banner "Metnos installer · bootstrap"
printf "  %sA personal assistant that runs on your hardware.%s\n" "$DIM" "$RESET"
printf "  %sAGPL-3.0 · metnos.com%s\n\n" "$DIM" "$RESET"

# ─────── 1. Find a suitable python ────────────────────────────────
step "Locating Python ≥ ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}"
PY_BIN=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (${PYTHON_MIN_MAJOR}, ${PYTHON_MIN_MINOR}) else 1)" 2>/dev/null; then
      PY_BIN="$candidate"
      break
    fi
  fi
done
if [ -z "$PY_BIN" ]; then
  fail "No Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ found. Install python3.12 or newer and re-run."
fi
PY_VER=$("$PY_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
ok "Python ${PY_VER} at $(command -v "$PY_BIN")"

# ─────── 2. Locate or fetch the repo ──────────────────────────────
step "Locating Metnos source tree"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_DIR=""
if [ -f "$SCRIPT_DIR/../install/manifest.toml" ]; then
  REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  ok "Found local clone at $REPO_DIR"
elif [ -d "${METNOS_REPO_DIR:-}" ] && [ -f "${METNOS_REPO_DIR}/install/manifest.toml" ]; then
  REPO_DIR="$METNOS_REPO_DIR"
  ok "Found repo at $REPO_DIR (METNOS_REPO_DIR override)"
else
  # curl-pipe-sh mode: clone fresh
  REPO_DIR="${METNOS_INSTALL_ROOT:-$HOME/metnos}"
  if [ -d "$REPO_DIR/.git" ]; then
    info "Existing repo at $REPO_DIR, pulling latest"
    (cd "$REPO_DIR" && git pull --ff-only) || warn "git pull failed, continuing with current revision"
  else
    info "Cloning $METNOS_REPO_URL into $REPO_DIR"
    git clone --depth 1 "$METNOS_REPO_URL" "$REPO_DIR" || fail "git clone failed"
  fi
  ok "Source tree ready at $REPO_DIR"
fi

# ─────── 3. Create / verify venv ──────────────────────────────────
step "Setting up Python virtual environment"
if [ -d "$METNOS_VENV" ] && [ -x "$METNOS_VENV/bin/python" ]; then
  EXISTING_VER=$("$METNOS_VENV/bin/python" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
  ok "Reusing existing venv ($METNOS_VENV, Python $EXISTING_VER)"
else
  mkdir -p "$(dirname "$METNOS_VENV")"
  "$PY_BIN" -m venv "$METNOS_VENV" || fail "venv creation failed"
  ok "Created venv at $METNOS_VENV"
fi

VENV_PY="$METNOS_VENV/bin/python"
VENV_PIP="$METNOS_VENV/bin/pip"

# ─────── 4. Install bootstrap dependencies ────────────────────────
step "Installing bootstrap dependencies (rich, httpx)"
"$VENV_PIP" install --quiet --upgrade pip 2>&1 | grep -v 'already' || true
"$VENV_PIP" install --quiet rich httpx 2>&1 | tail -3 || fail "pip install failed"
ok "Bootstrap dependencies installed"

# ─────── 4.bis Install Metnos runtime dependencies ────────────────
# Core runtime deps (HTTP server boot + base operation) are declared in
# requirements.txt. Optional deps (torch/google-*/playwright) live in
# requirements-optional.txt and are pulled by the skill selection (phase6).
if [ -f "$REPO_DIR/requirements.txt" ]; then
  step "Installing Metnos runtime dependencies (requirements.txt)"
  "$VENV_PIP" install --quiet -r "$REPO_DIR/requirements.txt" 2>&1 | tail -3 \
    || fail "runtime dependency install failed (see requirements.txt)"
  ok "Runtime dependencies installed"
else
  warn "requirements.txt not found in $REPO_DIR — the runtime may fail to start"
fi

# ─────── 5. Hand off to Python installer ──────────────────────────
mkdir -p "$METNOS_STATE/install"
export METNOS_HOME METNOS_STATE METNOS_VENV
export METNOS_REPO_DIR="$REPO_DIR"

banner "Handing off to Python installer"
exec "$VENV_PY" -m install "$@"
