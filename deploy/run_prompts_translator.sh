#!/bin/bash
# Nightly RM-0005 localization worker.
#
# The signed instance request and the persistent resource registry are the
# only authorities. The worker discovers the requested/active target,
# refreshes the deterministic inventory, processes a bounded translation
# batch, runs semantic checks and publishes a readiness report. It never
# activates a language: activation remains an explicit administrative step.

set -euo pipefail

INSTALL_ROOT="${METNOS_INSTALL_ROOT:-/opt/metnos}"
PYTHON="${METNOS_VENV:-$INSTALL_ROOT/.venv}/bin/python"
LOG="${METNOS_LOCALIZATION_LOG:-/var/log/metnos/localization.log}"
CAP="${METNOS_LOCALIZATION_CAP_PER_FIRE:-20}"

if ! mkdir -p "$(dirname "$LOG")" 2>/dev/null; then
    LOG="${TMPDIR:-/tmp}/metnos-localization.log"
fi

"$PYTHON" "$INSTALL_ROOT/runtime/admin/i18n_cli.py" \
    advance-requested --limit "$CAP" >>"$LOG" 2>&1
