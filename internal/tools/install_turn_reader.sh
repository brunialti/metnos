#!/usr/bin/env bash
# Install the read-only turn reader where the working user can run it without
# a password prompt, so reading a turn stops being a manual round trip.
#
#   sudo bash internal/tools/install_turn_reader.sh
#
# The copy lives in a root-owned directory: the working user can run it but
# cannot change what runs as root, which the worktree copy would allow.
# It reads the service diagnostics and copies screenshots; it writes nothing
# else, takes no path argument, and cannot execute anything it is given.
set -uo pipefail

USER_NAME="roberto"
SOURCE="/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/inspect_sites_turn.py"
TARGET_DIR="/usr/local/lib/metnos-diagnostics"
TARGET="$TARGET_DIR/inspect_sites_turn.py"
SUDOERS="/etc/sudoers.d/metnos-turn-reader"

if [ "$(id -u)" -ne 0 ]; then
    echo "REFUSED: run this with sudo" >&2
    exit 1
fi
if [ ! -f "$SOURCE" ]; then
    echo "REFUSED: missing $SOURCE" >&2
    exit 1
fi

install -d -o root -g root -m 0755 "$TARGET_DIR"
install -o root -g root -m 0755 "$SOURCE" "$TARGET"

printf '%s ALL=(root) NOPASSWD: /usr/bin/python3.12 %s *\n' "$USER_NAME" "$TARGET" \
    > "$SUDOERS.tmp"
chmod 0440 "$SUDOERS.tmp"
chown root:root "$SUDOERS.tmp"
if visudo -cqf "$SUDOERS.tmp"; then
    mv "$SUDOERS.tmp" "$SUDOERS"
    echo "INSTALLATO $TARGET"
    echo "REGOLA     $SUDOERS"
    echo
    echo "Da ora il lettore si invoca senza password:"
    echo "  sudo -n /usr/bin/python3.12 $TARGET <turno>"
else
    rm -f "$SUDOERS.tmp"
    echo "REFUSED: regola sudo non valida, nulla installato" >&2
    exit 1
fi
