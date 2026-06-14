#!/usr/bin/env python3
"""move_messages — dispatcher canonical (24/5/2026).

Tool UNICO per spostare 1+ messaggi fra cartelle (mail folders, Gmail
labels). Dispatcher sottile che instrada al backend (channel, client).

Architettura coerente con send_messages/read_messages:
- (email, metnos)           -> email_metnos.move (IMAP COPY+STORE+EXPUNGE)
- (email, google_workspace) -> gmail_google_workspace.modify (labels)
- (telegram, *)             -> non supportato (chat senza folder)

the design guide §5: "Cancellazione = `move_messages(dst_folder='Trash')`".
Reversibile §2.3: swap_src_dst (folder ↔ folder).

Contratto:
    stdin: JSON {
        message_ids? | message_id?,        # gmail (id stringa) o uids IMAP
        uids?: list[str],                  # IMAP only (alias message_ids)
        dst_folder: str,                   # 'Trash'/'Junk'/'Posta indesiderata'/user-label
        src_folder?: str,                  # IMAP only, default 'INBOX'
        account?: str,                     # IMAP backend
        via_channel?: 'email' (default),
        client?: 'metnos' (default) | 'google_workspace'
    }
    stdout: JSON {ok, ok_count, fail_count, results, failed}
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
from backends.messages import email_metnos  # noqa: E402
from backends.messages import gmail_google_workspace  # noqa: E402

_VIA_CHANNEL_ALIAS = {"mail": "email"}

# Dispatch table (channel, client) → (module, method_name).
# Gmail "move" → modify labels; email IMAP → move (COPY+STORE+EXPUNGE).
_HANDLERS = {
    ("email", "metnos"):           (email_metnos, "move"),
    ("email", "google_workspace"): (gmail_google_workspace, "modify"),
}

_DEFAULT_CLIENT = "metnos"


def invoke(args):
    via_raw = args.get("via_channel") or "email"
    via_channel = _VIA_CHANNEL_ALIAS.get(via_raw, via_raw)
    client = args.get("client") or _DEFAULT_CLIENT
    entry = _HANDLERS.get((via_channel, client))
    if entry is None:
        avail = sorted({k[0] for k in _HANDLERS})
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"{via_channel}/{client}")}
    backend, method = entry
    # Normalize uids → message_ids for gmail; preserve uids for IMAP.
    if client == "metnos" and "message_ids" in args and "uids" not in args:
        args = dict(args)
        args["uids"] = args.get("message_ids")
    return getattr(backend, method)(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
