#!/usr/bin/env python3
"""reply_messages — dispatcher canonical (24/5/2026).

Tool UNICO per rispondere in-thread a 1+ messaggi. Verbo riusabile §2.2
(send sussume reply ma il pattern in-thread richiede l'id originale +
auto-Re:+headers In-Reply-To/References).

Distinguibile da `send_messages` perche':
- input richiede `message_id` (o `message_ids`) originale.
- thread/Subject/In-Reply-To risolti dal backend (Gmail API).
- body plain text; HTML opzionale.

Contratto:
    stdin: JSON {
        message_ids? | message_id?,
        body: str,
        from_header?: str,
        via_channel?: 'email' (default),
        client?: 'google_workspace' (default per reply)
    }
    stdout: JSON {ok, ok_count, fail_count, results: [{ok, in_reply_to, id, thread_id}]}
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
from backends.messages import gmail_google_workspace  # noqa: E402

_VIA_CHANNEL_ALIAS = {"mail": "email"}

# Dispatch table (channel, client) → modulo.
# Reply IMAP/SMTP Migadu: out-of-scope MVP (richiede fetch original +
# build MIME con In-Reply-To/References + SMTP send manuale).
_HANDLERS = {
    ("email", "google_workspace"): gmail_google_workspace,
}

_DEFAULT_CLIENT = "google_workspace"


def invoke(args):
    via_raw = args.get("via_channel") or "email"
    via_channel = _VIA_CHANNEL_ALIAS.get(via_raw, via_raw)
    client = args.get("client") or _DEFAULT_CLIENT
    backend = _HANDLERS.get((via_channel, client))
    if backend is None:
        avail = sorted({k for k in _HANDLERS})
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"{via_channel}/{client}")}
    return backend.reply(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
