#!/usr/bin/env bash
# Installer hook git Metnos.
#
# Idempotente: installa (o re-installa) un `pre-commit` che incatena gli hook
# attualmente in uso:
#   1. pre-commit-symmetry-it-en.sh — anti-drift IT/EN su prompt .j2 (ADR 0092).
#   2. pre-commit-prompts.sh        — lint sintassi MiniJinja sui .j2 modificati.
#
# Onboarding: dopo `git clone` (o `git init` la prima volta), eseguire:
#     bash /opt/metnos/scripts/install_git_hooks.sh
#
# Bypass legittimi (vedi singoli script):
#   METNOS_LANG_DEFER=en|it git commit ...
#
# Bypass emergenza (sconsigliato): git commit --no-verify

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/metnos}"
HOOK_DIR="$REPO_DIR/.git/hooks"
TARGET="$HOOK_DIR/pre-commit"

if [[ ! -d "$HOOK_DIR" ]]; then
    echo "install_git_hooks: $HOOK_DIR non esiste. Lancia git init prima." >&2
    exit 1
fi

cat > "$TARGET" <<'HOOK_EOF'
#!/usr/bin/env bash
# Metnos pre-commit composito. Generato da scripts/install_git_hooks.sh.
# Per modifiche: edita gli script chiamati, NON questo file (verra' sovrascritto).
set -e
REPO_DIR="$(git rev-parse --show-toplevel)"
"$REPO_DIR/scripts/pre-commit-symmetry-it-en.sh"
"$REPO_DIR/scripts/pre-commit-prompts.sh"
HOOK_EOF

chmod +x "$TARGET"
echo "install_git_hooks: installato $TARGET"
