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
ADMIN_WORKSPACE=/var/lib/metnos-admin/f5-workspace-v1
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
# A root-only scratch outside every signed tree. Importing the release
# derives the workspace from the installation root, so without this the
# first call creates `workspace/.scheduler` and `workspace/.mnestoma`
# inside the signed release and every later verification refuses it.
install -d -o root -g root -m 0700 "$ADMIN_WORKSPACE"

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
# -B on both interpreters, and it is not a detail. Loading the verifier
# writes bytecode next to it, and importing the release writes it inside
# the signed tree; both then fail the exact-tree check with `extra
# distribution entry`, so the first call would break every later one.
# `-I` implies `-E`, so PYTHONDONTWRITEBYTECODE cannot do this job.
exec /usr/bin/python3.12 -I -B -c '
import importlib.util, json, os, sys
from pathlib import Path

# Ask the installed verifier. It owns the signed chain; nothing is
# reimplemented here and no pointer of our own is kept or trusted.
VERIFIER = "/usr/libexec/metnos/executor-birth-v1/preflight.py"
spec = importlib.util.spec_from_file_location("installed_preflight", VERIFIER)
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)
snapshot = verifier._authenticate_fixed_ownership_snapshot_v1()
selected, _materials = verifier._load_installed_preflight_materials_v1(
    snapshot, review_sources=False)
release = Path(selected.build.facts.installation_root)

# One entry names the interpreter this release runs. Two would mean the
# catalog disagrees with itself, which is not something to pick a side on.
catalog = json.loads(
    (release / "deployment/executor-birth-service-catalog-v1.json").read_bytes())
named = {entry["target_executable"] for entry in catalog["entries"]
         if entry.get("entry_id") == "service-http"}
if len(named) != 1:
    raise SystemExit("refused: the selected release names no single interpreter")
interpreter = named.pop()

# Isolation is kept, so PYTHONPATH is ignored on purpose and the paths of the
# verified release are inserted explicitly instead. Standard input is left
# alone: the evidence document arrives on it.
# The release derives its workspace from the installation root, and this
# process runs with the release as root. Name the scratch explicitly, or the
# import writes into the signed tree. `-I` implies `-E`, which drops PYTHON*
# variables only, so a METNOS_* one survives.
os.environ["METNOS_WORKSPACE"] = "/var/lib/metnos-admin/f5-workspace-v1"

# config.ensure_dirs runs during F5 imports, before the command privilege
# checks. Preserve legitimate administrative paths, but reject any mutable
# root that could create entries or repair permissions in either code tree.
# Resolve links before checking containment; relative paths would otherwise
# be interpreted after the chdir to the verified release below.
protected_roots = (release.resolve(), Path(VERIFIER).parent.resolve())

def require_external_path(name, path):
    if not path.is_absolute() or ".." in path.parts:
        raise SystemExit("refused: unsafe F5 path " + name)
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        raise SystemExit("refused: unsafe F5 path " + name) from None
    if any(resolved.is_relative_to(root) or root.is_relative_to(resolved)
           for root in protected_roots):
        raise SystemExit("refused: unsafe F5 path " + name)

try:
    user_home = Path.home()
except (RuntimeError, KeyError):
    raise SystemExit("refused: unsafe F5 path HOME") from None
require_external_path("HOME", user_home)
mutable_roots = {
    "METNOS_USER_DATA": user_home / ".local/share/metnos",
    "METNOS_USER_STATE": user_home / ".local/state/metnos",
    "METNOS_USER_CONFIG": user_home / ".config/metnos",
    "METNOS_USER_CACHE": Path(os.environ.get("XDG_CACHE_HOME")
                              or user_home / ".cache") / "metnos",
    "METNOS_WORKSPACE": release / "workspace",
}
for name, default in mutable_roots.items():
    require_external_path(name, Path(os.environ.get(name) or default))

stage = (
    "import sys; sys.path[:0] = [%r, %r]\n"
    "from install.f5_authority import main\n"
    "raise SystemExit(main())\n"
) % (str(release), str(release / "runtime"))
os.chdir(release)
os.execv(interpreter, [interpreter, "-I", "-B", "-c", stage, *sys.argv[1:]])
' "$@"
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
