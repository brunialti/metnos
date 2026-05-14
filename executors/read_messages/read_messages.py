#!/usr/bin/env python3
"""read_messages — dispatcher canonical (Q1 canonical+args, 13/5/2026).

Tool UNICO per leggere/cercare messaggi. Combina liberamente window
temporale e criteri testuali in UNA SOLA call.

Architettura (refactor 13/5/2026, ADR pending):
- Dispatcher sottile: instrada al backend giusto in base a
  (via_channel, client). Default `email`+`metnos`.
- Backend builtin in `runtime/backends/messaging/<channel>_<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

Predisposizione plugin esterni:
- Quando arrivera' l'ADR plugin esterni, `_HANDLERS` sara' arricchito da
  loader scan di `~/.local/share/metnos/plugins/messaging-*/backends/`.

Contratto:
    stdin: JSON {
        account?, folder?, max_results?, unseen_only?, time_window?,
        since?, before?, from_contains?, subject_contains?, body_contains?,
        max_total?, page_size?,
        via_channel?: 'email' | 'mail' | 'telegram'   (default 'email')
        client?:      'metnos'                          (default per channel)
    }
    stdout: JSON {ok, ok_count, fail_count, entries, failed, window?,
                  truncated?, available_total?, used?, truncated_what?}
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/opt/myclaw/runtime")
from backends.messages import email_metnos, telegram_bot  # noqa: E402
from backends.messages import gmail_google_workspace  # noqa: E402

# Dispatch table read-side (predisposta a plugin esterni).
# Valori = modulo: attribute lookup `module.read` a call-time per testabilita'.
_HANDLERS = {
    ("email",    "metnos"):           email_metnos,
    ("email",    "google_workspace"): gmail_google_workspace,
    ("telegram", "metnos"):           telegram_bot,
    ("telegram", None):               telegram_bot,
}

_VIA_CHANNEL_ALIAS = {
    "mail":     "email",
    "email":    "email",
    "telegram": "telegram",
}


def _default_client_for_channel(channel: str) -> str:
    """Auto-default §7.9: SEMPRE `metnos` (IMAP Migadu / Telegram).
    Gmail backend richiede `client="google_workspace"` esplicito + Gmail
    abilitato sull'account (alcuni Workspace tenant non lo hanno)."""
    return "metnos"


def invoke(args):
    via_channel = _VIA_CHANNEL_ALIAS.get(args.get("via_channel") or "email", "email")
    client = args.get("client") or _default_client_for_channel(via_channel)
    backend = _HANDLERS.get((via_channel, client))
    if backend is None:
        avail = sorted({k[0] for k in _HANDLERS})
        return {"ok": False,
                "error": f"unsupported backend ({via_channel}, {client}). "
                         f"Available channels: {avail}"}
    # Attribute lookup a call-time: i test possono patchare `backend.read`.
    return backend.read(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
