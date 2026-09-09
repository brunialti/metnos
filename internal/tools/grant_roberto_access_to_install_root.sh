#!/usr/bin/env bash
# Give the working user write access back to the seven paths the RM-0008
# transition handed to root, while root keeps ownership.
#
#   sudo bash internal/tools/grant_roberto_access_to_install_root.sh [--dry-run]
#
# What it changes, and nothing else:
#   - group of the seven paths and their contents -> the working user;
#   - directories -> 2775, so the group can write and new files inherit it;
#   - files -> 664, or 775 when the owner can already execute them.
#
# The modes it writes are the ones git records for these files (100644 /
# 100755): the 0600 and 0700 entries left by the transition are a local
# deviation from the tracked tree, not a declared secret.
#
# This is a stopgap. The install root and the development checkout are the
# same directory, and every hardening that is right for one breaks the other.
# Separating them is the real fix.
set -uo pipefail

USER_NAME="roberto"
ROOT="/opt/metnos"
LOCKED=(deploy executors install runtime scripts tutor)
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

if [ "$(id -u)" -ne 0 ]; then
    echo "REFUSED: run this with sudo" >&2
    exit 1
fi
if ! id "$USER_NAME" >/dev/null 2>&1; then
    echo "REFUSED: unknown user $USER_NAME" >&2
    exit 1
fi

TARGETS=("$ROOT")
for name in "${LOCKED[@]}"; do
    if [ -d "$ROOT/$name" ]; then
        TARGETS+=("$ROOT/$name")
    else
        echo "SKIP missing $ROOT/$name"
    fi
done

summary() {
    echo "--- $1 ---"
    printf '%-28s %s\n' "$ROOT" "$(stat -c '%U:%G %a' "$ROOT")"
    for name in "${LOCKED[@]}"; do
        [ -d "$ROOT/$name" ] || continue
        printf '%-28s %s  (%s file non scrivibili dal gruppo)\n' \
            "$ROOT/$name" "$(stat -c '%U:%G %a' "$ROOT/$name")" \
            "$(find "$ROOT/$name" -type f ! -perm -g=w | wc -l)"
    done
}

summary "PRIMA"

if [ "$DRY_RUN" -eq 1 ]; then
    echo
    echo "--- prova a vuoto: nessuna modifica eseguita ---"
    exit 0
fi

# The top directory is changed on its own: -R from it would also walk the
# checkout, the worktrees and the git store, which already belong to the user.
chgrp "$USER_NAME" "$ROOT"
chmod 2775 "$ROOT"

for target in "${TARGETS[@]:1}"; do
    chgrp -R "$USER_NAME" "$target"
    find "$target" -type d -exec chmod 2775 {} +
    find "$target" -type f -perm -u=x -exec chmod 775 {} +
    find "$target" -type f ! -perm -u=x -exec chmod 664 {} +
done

echo
summary "DOPO"
echo
echo "Proprietario ancora root; il gruppo $USER_NAME ora scrive."
