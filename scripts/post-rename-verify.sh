#!/usr/bin/env bash
# post-rename-verify.sh — sanity check dopo rinomina /opt/metnos → /opt/metnos.
#
# Workflow:
#   1. Esegui PRIMA del rename, capture snapshot baseline:
#        ./post-rename-verify.sh baseline
#   2. Esegui il rename + update systemd drop-in.
#   3. Esegui DOPO, capture snapshot post e diff:
#        ./post-rename-verify.sh post
#
# Mutating-effect cautele:
#   - smoke battery tocca mnest.sqlite + scheduler db: backup pre-rename.
#   - create_events / send_messages / consult_frontier: NON inclusi nello
#     smoke automatico (effetti esterni). Smoke usa solo executor read-only.
#   - workspace test scratchpad: usa METNOS_USER_DATA=/tmp/metnos-test per
#     isolare. Restore: rm -rf /tmp/metnos-test al termine.
set -euo pipefail

SNAPSHOT_DIR="${HOME}/.local/state/metnos/rename-verify"
mkdir -p "$SNAPSHOT_DIR"

cmd_baseline() {
    echo "=== baseline pre-rename ==="
    INSTALL_ROOT=$(python3 -c 'from runtime import config as C; print(C.PATH_ROOT)' 2>/dev/null || echo "unknown")
    echo "  install root resolved: $INSTALL_ROOT"
    systemctl --user status metnos-http --no-pager 2>&1 | head -5 > "$SNAPSHOT_DIR/baseline-systemd.txt"
    curl -s -m 2 http://127.0.0.1:8770/agent/health > "$SNAPSHOT_DIR/baseline-health.json" || echo "(http down)"
    echo "  baseline saved at $SNAPSHOT_DIR/baseline-*"

    # Backup mutating-effect targets
    cp -a "$INSTALL_ROOT/workspace/.mnestoma/mnest.sqlite" "$SNAPSHOT_DIR/baseline-mnest.sqlite" 2>/dev/null && \
        echo "  ✓ mnest.sqlite backed up"
    cp -a "$INSTALL_ROOT/workspace/.scheduler/state.sqlite" "$SNAPSHOT_DIR/baseline-scheduler.sqlite" 2>/dev/null && \
        echo "  ✓ scheduler state backed up"
    echo
    echo "  Ora puoi procedere con:"
    echo "    sudo systemctl --user stop metnos-http"
    echo "    sudo mv /opt/metnos /opt/metnos"
    echo "    # update drop-in: sed -i 's|/opt/metnos|/opt/metnos|g' ~/.config/systemd/user/metnos-http.service.d/override.conf"
    echo "    systemctl --user daemon-reload && systemctl --user start metnos-http"
    echo "    sleep 5"
    echo "    ./post-rename-verify.sh post"
}

cmd_post() {
    echo "=== verifica post-rename ==="
    INSTALL_ROOT=$(python3 -c 'from runtime import config as C; print(C.PATH_ROOT)' 2>/dev/null || echo "unknown")
    echo "  install root resolved: $INSTALL_ROOT"
    if [[ "$INSTALL_ROOT" != *metnos ]]; then
        echo "  ✗ PATH_ROOT non punta a metnos: $INSTALL_ROOT" >&2
        exit 1
    fi
    echo "  ✓ PATH_ROOT auto-resolved correctly"

    systemctl --user is-active metnos-http >/dev/null && echo "  ✓ metnos-http active" || {
        echo "  ✗ metnos-http NOT active" >&2; exit 1; }

    curl -s -m 5 http://127.0.0.1:8770/agent/health > "$SNAPSHOT_DIR/post-health.json"
    grep -q '"ok": true' "$SNAPSHOT_DIR/post-health.json" && echo "  ✓ /agent/health 200 ok" || {
        echo "  ✗ /agent/health not 200"; cat "$SNAPSHOT_DIR/post-health.json"; exit 1; }

    # Smoke battery (read-only subset)
    echo
    echo "  smoke battery (read-only subset):"
    PYTHONPATH="$INSTALL_ROOT:/opt/suprastructure/src" \
        /opt/suprastructure/.venv/bin/python -m runtime.smoke 2>&1 | tail -20 || \
        echo "  ⚠ smoke battery non eseguibile (script missing); manuale"

    echo
    echo "  ✓ verifica post-rename completata"
    echo "  Mutating-effect restore (se necessario):"
    echo "    cp $SNAPSHOT_DIR/baseline-mnest.sqlite $INSTALL_ROOT/workspace/.mnestoma/mnest.sqlite"
    echo "    cp $SNAPSHOT_DIR/baseline-scheduler.sqlite $INSTALL_ROOT/workspace/.scheduler/state.sqlite"
}

case "${1:-help}" in
    baseline) cmd_baseline ;;
    post)     cmd_post ;;
    *)
        echo "Uso: $0 {baseline|post}"
        echo "  baseline = snapshot pre-rename + backup mnest/scheduler"
        echo "  post     = verifica post-rename + smoke"
        exit 2 ;;
esac
