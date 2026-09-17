#!/usr/bin/env bash
# install_f5_authority.sh - install the four commands the developer may run as
# root without a password, so the F5 entry stops needing a human at each step.
#
# Run this ONCE, with sudo, AFTER the release that ships the F5 code:
#
#     sudo -n metnos-f5-authority provision-key
#     sudo -n metnos-f5-authority evidence < declaration.json
#     sudo -n metnos-f5-authority migrate plan      (then: migrate apply)
#     sudo -n metnos-f5-authority certify derive    (then: certify issue)
#
# WHAT THIS GRANTS, PLAINLY
#
# The launcher asks the installed verifier which release is selected and runs
# only that one. It duplicates none of the chain logic, keeps no pointer of its
# own, and runs no code from anyone's worktree: the tool it executes is the
# installed release's `install.f5_authority`, root-owned and signed as part of
# the distribution. The named account can invoke six exact argument forms and
# nothing else, and cannot change what they do.
#
# That is why this runs after the release rather than before. A launcher over a
# development worktree would run new modules against an older installed runtime
# - what the closed build exists to prevent - and would hand the account
# passwordless root by way of files it can write.
#
# The operations still refuse on their own terms: the migration needs a
# quiescent stack, the certificate needs a completed migration and recorded
# evidence, and neither accepts a supplied number.
#
# Remove it with:  rm /etc/sudoers.d/metnos-f5-authority
set -euo pipefail

LAUNCHER_DIR=/usr/local/lib/metnos-admin
LAUNCHER="$LAUNCHER_DIR/metnos-f5-authority"
SUDOERS=/etc/sudoers.d/metnos-f5-authority
VERIFIER=/usr/libexec/metnos/executor-birth-v1/preflight.py
INTERPRETER=/usr/bin/python3.12

if [ "$(id -u)" != 0 ]; then
  echo "ABORT: run this with sudo, once" >&2
  exit 1
fi
ACCOUNT="${1:-${SUDO_USER:-}}"
if [ -z "$ACCOUNT" ] || ! id -u "$ACCOUNT" >/dev/null 2>&1; then
  echo "ABORT: name an existing developer account: $0 <user>" >&2
  exit 1
fi
for path in "$VERIFIER" "$INTERPRETER"; do
  if [ ! -f "$path" ]; then
    echo "ABORT: $path is missing; install Metnos first" >&2
    exit 1
  fi
  if [ "$(stat -c '%U' "$path")" != root ]; then
    echo "ABORT: $path is not root-owned" >&2
    exit 1
  fi
done

install -d -o root -g root -m 0755 "$LAUNCHER_DIR"

# Written from here, not copied, so what runs as root is exactly what this file
# says and cannot drift with any worktree.
cat > "$LAUNCHER.incoming" <<'LAUNCHER_EOF'
#!/bin/sh
# Metnos F5 authority. Installed by install_f5_authority.sh.
# Pins the argument set; asks the installed verifier which release is selected.
set -eu
# A direct call must be as narrow as one through sudo, so extra arguments are
# refused here too, not only by the sudoers rule.
if [ "$#" -gt 2 ]; then
  echo "refused: at most two arguments" >&2
  exit 2
fi
case "${1:-}${2:+ $2}" in
  "provision-key"|"evidence"|"migrate plan"|"migrate apply"|"certify derive"|"certify issue") ;;
  *) echo "refused: provision-key | evidence | migrate plan|apply | certify derive|issue" >&2; exit 2 ;;
esac
exec /usr/bin/python3.12 -I - "$@" <<'BOOTSTRAP'
import importlib.util, json, os, sys
from pathlib import Path

VERIFIER = "/usr/libexec/metnos/executor-birth-v1/preflight.py"
# Ask the installed verifier. It owns the signed chain; nothing is reimplemented
# here and no pointer of our own is kept or trusted.
spec = importlib.util.spec_from_file_location("installed_preflight", VERIFIER)
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)
snapshot = verifier._authenticate_fixed_ownership_snapshot_v1()
selected, _materials = verifier._load_installed_preflight_materials_v1(
    snapshot, review_sources=False)
release = Path(selected.build.facts.installation_root)
catalog = json.loads(
    (release / "deployment/executor-birth-service-catalog-v1.json").read_bytes())
# One entry names the interpreter this release runs. Two would mean the
# catalog disagrees with itself, which is not something to pick a side on.
named = {entry["target_executable"] for entry in catalog["entries"]
         if entry.get("entry_id") == "service-http"}
if len(named) != 1:
    raise SystemExit("refused: the selected release names no single interpreter")
interpreter = named.pop()
os.chdir(release)
os.execve(interpreter,
          [interpreter, "-I", "-s", "-B", "-m", "install.f5_authority", *sys.argv[1:]],
          {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
           "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": f"{release}:{release}/runtime"})
BOOTSTRAP
LAUNCHER_EOF
chown root:root "$LAUNCHER.incoming"
chmod 0755 "$LAUNCHER.incoming"
mv -f "$LAUNCHER.incoming" "$LAUNCHER"

cat > "$SUDOERS.incoming" <<SUDOERS_EOF
# Metnos: the four F5 entry operations, and nothing else. See
# internal/tools/install_f5_authority.sh for what this grants.
$ACCOUNT ALL=(root) NOPASSWD: $LAUNCHER provision-key, $LAUNCHER evidence, $LAUNCHER migrate plan, $LAUNCHER migrate apply, $LAUNCHER certify derive, $LAUNCHER certify issue
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
echo "         release     asked of $VERIFIER at each call"
echo "         account     $ACCOUNT (passwordless, only the six exact forms)"
echo
echo "check it with:  sudo -n $LAUNCHER certify derive"
echo "remove it with: rm $SUDOERS"
