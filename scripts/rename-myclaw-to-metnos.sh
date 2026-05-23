#!/usr/bin/env bash
# rename-myclaw-to-metnos.sh — esegue il rename /opt/metnos → /opt/metnos.
#
# IDEMPOTENTE: re-run è sicuro (skip dei passaggi già fatti).
# TRANSITIONAL SYMLINK: dopo il rename viene creato `/opt/metnos -> /opt/metnos`
# in modo che eventuali path hardcoded sfuggiti continuino a funzionare per
# un periodo di transizione (rimovibile dopo verifica completa).
#
# Uso:
#     sudo bash /opt/metnos/scripts/rename-myclaw-to-metnos.sh        # esegue
#     sudo bash /opt/metnos/scripts/rename-myclaw-to-metnos.sh --dry  # mostra solo
#
# Pre-flight discovery già fatta (18/5/2026):
# - /opt/metnos owner roberto:roberto 755
# - systemd system units affected: 4 (metnos-i18n-translator, metnos-backup,
#   metnos-prompts-translator, myclaw-docs)
# - systemd user units affected: 2 (metnos-http, metnos-telegram-daemon)
# - llama-server NON referenzia /opt/metnos (safe lasciare running)
# - /usr/local/bin/metnos-cli → /opt/metnos/scripts/metnos-cli (symlink da aggiornare)
# - crontab utente: 3 entry referenziano /opt/metnos/scripts/audit_introvertiva.sh
set -euo pipefail

DRY=0
[ "${1:-}" = "--dry" ] && DRY=1

run() {
    if [ "$DRY" = "1" ]; then
        echo "  [dry] $*"
    else
        echo "  $ $*"
        eval "$@"
    fi
}

OLD="/opt/metnos"
NEW="/opt/metnos"
INVOKING_USER="${SUDO_USER:-$USER}"

echo "═══ Metnos rename: $OLD → $NEW ═══"
[ "$DRY" = "1" ] && echo "  (DRY-RUN: no changes)"
echo

# ─── 0. Sanity ─────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "  ✗ richiede sudo: sudo bash $0" >&2
    exit 1
fi
if [ ! -d "$OLD" ] && [ -d "$NEW" ]; then
    echo "  ! $OLD non esiste e $NEW esiste già — re-run sui passi non ancora fatti"
elif [ -d "$OLD" ] && [ -d "$NEW" ] && [ ! -L "$OLD" ]; then
    echo "  ✗ ambedue $OLD e $NEW esistono e nessuno è symlink — stato inconsistente" >&2
    exit 1
fi

# ─── 1. Stop services using $OLD (in ordine: user prima, system poi) ──
echo "── Step 1: stop services"
if [ "$DRY" = "1" ]; then
    echo "  [dry] systemctl --user stop metnos-http metnos-telegram-daemon"
else
    sudo -u "$INVOKING_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$INVOKING_USER")" \
        bash -c "systemctl --user stop metnos-http metnos-telegram-daemon 2>/dev/null || true"
    echo "  ✓ user services stopped (best-effort)"
fi
for unit in myclaw-docs metnos-i18n-translator metnos-backup metnos-prompts-translator; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
        run "systemctl stop $unit"
    fi
done
[ "$DRY" = "0" ] && echo "  ✓ system services stopped"

# ─── 2. Move directory + transitional symlink ─────────────────────
echo
echo "── Step 2: move + transitional symlink"
if [ -d "$OLD" ] && [ ! -L "$OLD" ] && [ ! -d "$NEW" ]; then
    run "mv $OLD $NEW"
    echo "  ✓ moved $OLD → $NEW"
elif [ -d "$NEW" ]; then
    echo "  ! $NEW already exists, skipping mv"
fi
if [ ! -L "$OLD" ] && [ ! -d "$OLD" ]; then
    run "ln -sfn $NEW $OLD"
    echo "  ✓ symlink $OLD → $NEW (transitional, rimovibile dopo verifica)"
fi

# ─── 3. Update /usr/local/bin/metnos-cli symlink ──────────────────
echo
echo "── Step 3: update /usr/local/bin/metnos-cli"
if [ -L /usr/local/bin/metnos-cli ]; then
    TARGET=$(readlink /usr/local/bin/metnos-cli)
    if [[ "$TARGET" == "$OLD"* ]]; then
        NEW_TARGET="${TARGET/$OLD/$NEW}"
        run "ln -sfn $NEW_TARGET /usr/local/bin/metnos-cli"
        echo "  ✓ metnos-cli now points at $NEW_TARGET"
    else
        echo "  ! metnos-cli already points at $TARGET"
    fi
fi

# ─── 4. Update systemd system unit files ─────────────────────────
echo
echo "── Step 4: update systemd system unit files"
for u in $(grep -rln "$OLD" /etc/systemd/system 2>/dev/null); do
    run "sed -i 's|$OLD|$NEW|g' $u"
    echo "  ✓ $u"
done

# ─── 5. Update systemd user unit files ───────────────────────────
echo
echo "── Step 5: update systemd user unit files"
USER_SYSTEMD="/home/$INVOKING_USER/.config/systemd"
for u in $(grep -rln "$OLD" "$USER_SYSTEMD" 2>/dev/null); do
    if [ "$DRY" = "1" ]; then
        echo "  [dry] sed -i $u"
    else
        sudo -u "$INVOKING_USER" bash -c "sed -i 's|$OLD|$NEW|g' $u"
        echo "  ✓ $u (as $INVOKING_USER)"
    fi
done

# ─── 5b. Deep sweep: shell/yaml/json/toml in $NEW/{scripts,deploy,install,runtime,executors} ──
echo
echo "── Step 5b: deep sweep di file non-Python in $NEW (sh/yaml/json/toml)"
SWEPT=0
while IFS= read -r f; do
    if grep -q "$OLD" "$f" 2>/dev/null; then
        if [ "$DRY" = "1" ]; then
            echo "  [dry] sed -i $f"
        else
            sed -i "s|$OLD|$NEW|g" "$f"
        fi
        SWEPT=$((SWEPT + 1))
    fi
done < <(find "$NEW" \( -name '*.sh' -o -name '*.yaml' -o -name '*.yml' \
                      -o -name '*.json' -o -name '*.toml' -o -name '*.service' \
                      -o -name '*.timer' \) -type f 2>/dev/null)
echo "  ✓ deep sweep: $SWEPT file aggiornati"

# ─── 5c. Specifico backup script ── ──────────────────────────────
echo
echo "── Step 5c: verifica backup script ($NEW/deploy/backup_nas.sh)"
BACKUP_SCRIPT="$NEW/deploy/backup_nas.sh"
if [ -f "$BACKUP_SCRIPT" ]; then
    if grep -q '/opt/metnos\|/opt/metnos' "$BACKUP_SCRIPT"; then
        echo "  → backup_nas.sh aggiornato dalla deep sweep (METNOS_DIR + LOG_FILE)"
        grep -nE 'METNOS_DIR=|LOG_FILE=' "$BACKUP_SCRIPT" | head -3
    fi
fi

# ─── 5d. Re-sign executor con manifest modificato ────────────────
echo
echo "── Step 5d: re-sign executor con manifest.toml modificato dal deep sweep"
SIGNED=0
for manifest in $(find "$NEW/executors" -name 'manifest.toml' 2>/dev/null); do
    exec_dir=$(dirname "$manifest")
    sig="$exec_dir/manifest.toml.sig"
    if [ -f "$sig" ] && [ "$manifest" -nt "$sig" ]; then
        if [ "$DRY" = "1" ]; then
            echo "  [dry] re-sign $exec_dir"
        else
            sudo -u "$INVOKING_USER" \
                PYTHONPATH="$NEW/runtime:/opt/suprastructure/src" \
                /opt/suprastructure/.venv/bin/python "$NEW/runtime/sign.py" sign "$exec_dir" 2>/dev/null \
                && SIGNED=$((SIGNED + 1))
        fi
    fi
done
echo "  ✓ re-signed: $SIGNED executor"

# ─── 6. Update crontab (user) ────────────────────────────────────
echo
echo "── Step 6: update user crontab"
if sudo -u "$INVOKING_USER" crontab -l 2>/dev/null | grep -q "$OLD"; then
    if [ "$DRY" = "1" ]; then
        echo "  [dry] sudo -u $INVOKING_USER crontab -l | sed 's|$OLD|$NEW|g' | sudo -u $INVOKING_USER crontab -"
    else
        sudo -u "$INVOKING_USER" bash -c "crontab -l | sed 's|$OLD|$NEW|g' | crontab -"
        echo "  ✓ crontab updated"
    fi
else
    echo "  (nothing to update)"
fi

# ─── 7. daemon-reload + restart ───────────────────────────────────
echo
echo "── Step 7: reload + restart"
run "systemctl daemon-reload"
if [ "$DRY" = "1" ]; then
    echo "  [dry] systemctl --user daemon-reload"
    echo "  [dry] systemctl --user reset-failed metnos-http metnos-telegram-daemon"
    echo "  [dry] systemctl --user start metnos-http metnos-telegram-daemon"
else
    sudo -u "$INVOKING_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$INVOKING_USER")" \
        bash -c "systemctl --user daemon-reload"
fi

# Start system services
for unit in myclaw-docs metnos-i18n-translator metnos-backup metnos-prompts-translator; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        run "systemctl start $unit" || true
    fi
done

# Start user services (reset-failed first in case of prior failure)
if [ "$DRY" = "0" ]; then
    sudo -u "$INVOKING_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$INVOKING_USER")" \
        bash -c "systemctl --user reset-failed metnos-http metnos-telegram-daemon 2>/dev/null; \
                 systemctl --user start metnos-http metnos-telegram-daemon 2>/dev/null || true"
fi

# ─── 8. Verify ────────────────────────────────────────────────────
echo
echo "── Step 8: verify"
if [ "$DRY" = "1" ]; then
    echo "  [dry] (skipped: no changes were applied)"
    echo
    echo "═══ DRY-RUN COMPLETE — nothing changed ═══"
    exit 0
fi
sleep 3
if sudo -u "$INVOKING_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$INVOKING_USER")" \
        systemctl --user is-active metnos-http >/dev/null 2>&1; then
    echo "  ✓ metnos-http active"
else
    echo "  ✗ metnos-http NOT active — check: journalctl --user -u metnos-http" >&2
fi

curl -s -m 5 http://127.0.0.1:8770/agent/health > /tmp/post-rename-health.json 2>&1
if grep -q '"ok": true' /tmp/post-rename-health.json 2>/dev/null; then
    echo "  ✓ /agent/health 200"
    cat /tmp/post-rename-health.json
    echo
else
    echo "  ✗ /agent/health not OK:" >&2
    cat /tmp/post-rename-health.json
fi

# Verify PATH_ROOT auto-resolves correctly
ROOT_RESOLVED=$(sudo -u "$INVOKING_USER" \
    PYTHONPATH="$NEW/runtime:/opt/suprastructure/src" \
    /opt/suprastructure/.venv/bin/python -c \
    'from runtime import config as C; print(C.PATH_ROOT)' 2>/dev/null || echo "FAIL")
echo "  PATH_ROOT auto-resolved: $ROOT_RESOLVED"
if [ "$ROOT_RESOLVED" = "$NEW" ]; then
    echo "  ✓ rename-resilient code working"
fi

echo
echo "═══ DONE ═══"
echo "  Symlink transizione: $OLD → $NEW (rimovi con: sudo rm $OLD dopo verifica prolungata)"
echo "  Backup mnest/scheduler: ~/.local/state/metnos/rename-verify/ (se hai eseguito baseline)"
