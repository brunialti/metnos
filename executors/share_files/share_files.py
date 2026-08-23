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
    stdout: JSON {ok, n_shared, results: [{ok, id, role, type, email,
                  permission_id}], failed, _undo}

Undo §2.3: il forward conserva per ogni grant la coppia opaca
`file_id + permission_id`; `reverse()` revoca soltanto quei permessi tramite
Drive permissions.delete. Non risolve nomi e non modifica/cancella i file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
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
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    return backend.share(args)


def reverse(plan, results):
    """Revoca esattamente i grant creati dal forward.

    La ricevuta `_undo.permissions` e' prodotta dal backend firmato al momento
    della mutation. Il fallback sulle righe `results` mantiene annullabili le
    operazioni registrate dalla prima versione che esponeva `permission_id`.
    In entrambi i casi servono sia file ID sia permission ID: nessuna ricerca
    per nome, email o ruolo e nessuna revoca euristica.
    """
    if not isinstance(results, dict):
        return {
            "ok": False, "ok_count": 0, "fail_count": 1,
            "results": [], "failed": [{"error": "results must be an object"}],
        }

    undo = results.get("_undo")
    permissions = undo.get("permissions") if isinstance(undo, dict) else None
    if not isinstance(permissions, list) or not permissions:
        permissions = [
            {
                "file_id": row.get("file_id") or row.get("id"),
                "permission_id": row.get("permission_id"),
            }
            for row in (results.get("results") or [])
            if isinstance(row, dict)
        ]
    return google_workspace.revoke_permissions({"permissions": permissions})


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
