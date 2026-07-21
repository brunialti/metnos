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
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import normalize_vector_result, run_stdio  # noqa: E402
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

_VIA_CHANNEL_ALIAS = {"mail": "email"}


# Default uniforme: IMAP Migadu per email, Telegram bot per telegram.
# Gmail richiede `client="google_workspace"` esplicito.
_DEFAULT_CLIENT = "metnos"


def invoke(args):
    via_raw = args.get("via_channel") or "email"
    via_channel = _VIA_CHANNEL_ALIAS.get(via_raw, via_raw)
    client = args.get("client") or _DEFAULT_CLIENT
    backend = _HANDLERS.get((via_channel, client))
    if backend is None:
        avail = sorted({k[0] for k in _HANDLERS})
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"{via_channel}/{client}")}
    # Attribute lookup a call-time: i test possono patchare `backend.read`.
    return normalize_vector_result(backend.read(args), entry_key="entries")


def _json_safe(o):
    """Fallback serializzazione (§2.8 mai crashare): un campo non-JSON di una
    mail malformata (bytes header non-UTF8, datetime, ...) diventa stringa invece
    di far morire l'executor con `non-JSON output`. Bug live 22/6 su account=all
    (una mailbox aveva un campo bytes)."""
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", "replace")
    return str(o)


def main():
    run_stdio(invoke, default=_json_safe)


if __name__ == "__main__":
    main()
