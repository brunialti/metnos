#!/usr/bin/env bash
# c7-isolate.sh — sourced by c7-validate-selfupdate.sh right after TMP is made and
# before any import: every root Metnos resolves goes under $TMP, keys included.
# The scripts must never reach the account that runs them.
export PYTHONUSERBASE="${PYTHONUSERBASE:-$HOME/.local}"
export HOME="$TMP/home"
export METNOS_USER_DATA="$TMP/user-data" METNOS_USER_STATE="$TMP/user-state"
export METNOS_USER_CONFIG="$TMP/user-config" METNOS_USER_CACHE="$TMP/user-cache"
mkdir -p "$HOME" "$METNOS_USER_DATA" "$METNOS_USER_STATE" "$METNOS_USER_CONFIG" "$METNOS_USER_CACHE"
( cd "$RUNTIME" && python3 - "$TMP" <<'PYEOF'
import sys
from pathlib import Path
import config as C
root = Path(sys.argv[1]).resolve()
for path in (C.PATH_USER_DATA, C.PATH_USER_STATE, C.PATH_USER_CONFIG, C.PATH_USER_CACHE):
    if not Path(path).resolve().is_relative_to(root):
        sys.exit(f"isolation broken: {path} is outside {root}")
PYEOF
) || exit 1
# Temporary signing keys: the account's own are never loaded.
( cd "$RUNTIME" && python3 sign.py keygen author >/dev/null ) || exit 1
