#!/usr/bin/env bash
# install_release_authority.sh - install the one command the developer may run
# as root without a password, so that shipping a change stops being a manual
# ritual.
#
# Run this ONCE, with sudo. From then on the release cycle is:
#
#     <tool> prepare                      # developer, no privileges
#     sudo -n metnos-release-authority apply --cross
#
# and both halves can be run by an agent without a human typing anything.
#
# WHAT THIS GRANTS, PLAINLY
#
# The launcher is root-owned and pins three things: the interpreter, the tool
# it runs, and the only two argument forms it accepts. The tool it runs lives
# in the developer's worktree and is NOT root-owned, and it deliberately
# executes the candidate release as root - that is what releasing new code
# means. So in practice this grants the named account passwordless root by way
# of whatever it can write into that worktree and into the staging tree.
#
# On a single-administrator machine that changes convenience, not who is
# trusted: the account already has full sudo. What it removes is the
# per-run checkpoint of a human typing a password. Do not install this on a
# host where the developer account is less trusted than root, and do not
# install it on a shared machine.
#
# Remove it with:  rm /etc/sudoers.d/metnos-release-authority
set -euo pipefail

LAUNCHER_DIR=/usr/local/lib/metnos-admin
LAUNCHER="$LAUNCHER_DIR/metnos-release-authority"
SUDOERS=/etc/sudoers.d/metnos-release-authority
INTERPRETER=/usr/bin/python3.12
TOOL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rm0008_release_cycle.py"

if [ "$(id -u)" != 0 ]; then
  echo "ABORT: run this with sudo, once" >&2
  exit 1
fi

ACCOUNT="${1:-${SUDO_USER:-}}"
if [ -z "$ACCOUNT" ]; then
  echo "ABORT: name the developer account: $0 <user>" >&2
  exit 1
fi
if ! id -u "$ACCOUNT" >/dev/null 2>&1; then
  echo "ABORT: no such account: $ACCOUNT" >&2
  exit 1
fi
if [ ! -x "$INTERPRETER" ]; then
  echo "ABORT: interpreter not found: $INTERPRETER" >&2
  exit 1
fi
if [ ! -f "$TOOL" ]; then
  echo "ABORT: release cycle tool not found: $TOOL" >&2
  exit 1
fi

install -d -o root -g root -m 0755 "$LAUNCHER_DIR"

# The launcher is written from here, not copied, so what runs as root is
# exactly what this file says and cannot drift with the worktree.
cat > "$LAUNCHER.incoming" <<LAUNCHER_EOF
#!/bin/sh
# Metnos release authority. Installed by install_release_authority.sh.
# Pins the interpreter, the tool and the argument set; refuses anything else.
set -eu
case "\${1:-}\${2:+ \$2}" in
  "apply"|"apply --cross") ;;
  *) echo "refused: only 'apply' or 'apply --cross'" >&2; exit 2 ;;
esac
exec $INTERPRETER $TOOL "\$@"
LAUNCHER_EOF
chown root:root "$LAUNCHER.incoming"
chmod 0755 "$LAUNCHER.incoming"
mv -f "$LAUNCHER.incoming" "$LAUNCHER"

cat > "$SUDOERS.incoming" <<SUDOERS_EOF
# Metnos: the release cycle, and nothing else. See
# internal/tools/install_release_authority.sh for what this grants.
$ACCOUNT ALL=(root) NOPASSWD: $LAUNCHER apply, $LAUNCHER apply --cross
SUDOERS_EOF
chown root:root "$SUDOERS.incoming"
chmod 0440 "$SUDOERS.incoming"
if ! visudo -cf "$SUDOERS.incoming" >/dev/null; then
  rm -f "$SUDOERS.incoming"
  echo "ABORT: the generated sudoers rule does not parse; nothing installed" >&2
  exit 1
fi
mv -f "$SUDOERS.incoming" "$SUDOERS"

echo "installed $LAUNCHER"
echo "         tool        $TOOL"
echo "         interpreter $INTERPRETER"
echo "         account     $ACCOUNT (passwordless, only 'apply' and 'apply --cross')"
echo
echo "check it with:  sudo -n $LAUNCHER apply"
echo "remove it with: rm $SUDOERS"
