#!/usr/bin/env python3
"""set_messages — dispatcher canonical (24/5/2026).

Tool UNICO per upsert di labels/metadata su 1+ messaggi. Verbo `set`
§2.2 = upsert idempotente di stato (vs `move` che cambia folder, vs
`send` che crea outbound).

Per Gmail: equivalente a "gmail modify --add-labels X --remove-labels Y".
Per IMAP Migadu: non implementato in MVP (le flag IMAP \\Flagged/\\Seen
sono gestite altrove); ritorna unsupported_backend.

Contratto:
    stdin: JSON {
        message_ids? | message_id?,
        add?: list[str],         # label ids/nomi da aggiungere
        remove?: list[str],      # label ids/nomi da rimuovere
        via_channel?: 'email' (default),
        client?: 'google_workspace' (default per labels)
    }
    stdout: JSON {ok, ok_count, fail_count, results: [{id, labels_now}], failed}
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
from executor_helpers import normalize_vector_result, run_stdio  # noqa: E402
from backends.messages import gmail_google_workspace  # noqa: E402

_VIA_CHANNEL_ALIAS = {"mail": "email"}

# Dispatch table (channel, client) → modulo.
# IMAP labels = flags: non implementato (out-of-scope MVP).
_HANDLERS = {
    ("email", "google_workspace"): gmail_google_workspace,
}

# Default uniforme: per `set_messages` il default ragionevole e' Gmail
# (le system labels sono il vero use case). Il PLANNER puo' forzare
# `client="metnos"` ma ritornera' unsupported.
_DEFAULT_CLIENT = "google_workspace"


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error_class": "invalid_args",
                "error": _msg("ERR_ARGS_NOT_OBJECT"), "results": []}
    ids = []
    if isinstance(args.get("message_ids"), list):
        ids.extend(str(x).strip() for x in args["message_ids"] if x)
    if isinstance(args.get("message_id"), str) and args["message_id"].strip():
        ids.append(args["message_id"].strip())
    if not ids:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error_class": "invalid_args",
                "error": _msg("ERR_ARG_MISSING", arg="message_id/message_ids"),
                "results": []}
    add = args.get("add") or []
    remove = args.get("remove") or []
    if not add and not remove:
        return {"ok": False, "error_code": "ERR_ARG_MISSING",
                "error_class": "invalid_args",
                "error": _msg("ERR_ARG_MISSING", arg="add/remove"),
                "results": []}
    via_raw = args.get("via_channel") or "email"
    via_channel = _VIA_CHANNEL_ALIAS.get(via_raw, via_raw)
    client = args.get("client") or _DEFAULT_CLIENT
    backend = _HANDLERS.get((via_channel, client))
    if backend is None:
        avail = sorted({k for k in _HANDLERS})
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"{via_channel}/{client}")}
    return normalize_vector_result(backend.labels(args), entry_key="results")


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
