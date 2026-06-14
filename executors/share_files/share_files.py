#!/usr/bin/env python3
"""share_files — dispatcher canonical (24/5/2026).

Tool UNICO per grant ACL su 1+ file (verb `share` §2.2 ADR 0128).
OUTBOUND CONSENT: grant access remoto SENZA spostare/duplicare il file.

Distinto da:
- `send_messages` (outbound COPY: recipient riceve un OGGETTO).
- `set_messages` (upsert stato interno labels/metadata).
- `write_files`  (crea/sovrascrive contenuto file).

Backend supportati:
- google_workspace -> Drive permissions.create (user/group/domain/anyone).

Filesystem locale (`local`): non applicabile, niente ACL nel scope.

Contratto:
    stdin: JSON {
        file_ids? | file_id?,
        email?: str,                # required per type=user|group
        domain?: str,               # required per type=domain
        role?: 'reader' (default) | 'commenter' | 'writer' | 'fileOrganizer' | 'organizer' | 'owner',
        type?: 'user' (default) | 'group' | 'domain' | 'anyone',
        notify?: bool (default false),
        client?: 'google_workspace' (default)
    }
    stdout: JSON {ok, n_shared, results: [{ok, id, role, type, email, permission_id}], failed}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from backends.files import google_workspace  # noqa: E402

_HANDLERS = {
    "google_workspace": google_workspace,
}

# Default uniforme: il verb `share` ha senso solo su cloud filesystems.
# Local fs non ha ACL standard cross-platform; il PLANNER deve scegliere
# esplicitamente il client provider.
_DEFAULT_CLIENT = "google_workspace"


def invoke(args):
    client = args.get("client") or _DEFAULT_CLIENT
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    return backend.share(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
