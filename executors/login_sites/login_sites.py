#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""login_sites — login web con credenziali cifrate (spec sites F1 §3.4).

Vettoriale (§2.1): `session_ids: array[str]` (o `from_step` da open_sites) →
un login per sessione. `critical=true`. Zero segreti nel result: `reason_code`
è uno slug i18n (mai username/password). Il broker (credential_injection) fa
l'iniezione §3.2 (origine verificata, destinazione risolta dal broker,
no-segreto-negli-shot); QUI passa solo session_id + domain (handle vault).

OUT: entries=[{session_id, logged_in: bool, reason_code?}]  (§3.4).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_RT = os.environ.get("METNOS_RUNTIME") or str(
    Path(__file__).resolve().parents[2] / "runtime")
if _RT not in sys.path:
    sys.path.insert(0, _RT)

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from playwright_sidecar import session_client  # noqa: E402


def _collect_session_ids(args: dict) -> list[str]:
    sids = args.get("session_ids")
    if isinstance(sids, str):
        sids = [sids]
    if isinstance(sids, list) and sids:
        return [s for s in sids if isinstance(s, str) and s]
    # from_step materializzato come `entries` (§4.1): estrai session_id.
    ents = args.get("entries")
    if isinstance(ents, list):
        return [e.get("session_id") for e in ents
                if isinstance(e, dict) and e.get("session_id")]
    one = args.get("session_id")
    return [one] if isinstance(one, str) and one else []


def _reason_message(reason_code: str | None) -> str | None:
    """Mappa lo slug della tassonomia fallimento (§9) al messaggio i18n.
    Nessun eco di credenziali. Fallback onesto se lo slug non ha una chiave."""
    if not reason_code:
        return None
    msg = _msg(f"MSG_SITES_RC_{reason_code.upper()}")
    if msg.startswith("<missing:"):
        return None  # slug senza messaggio dedicato: nessun eco, solo il code
    return msg


def invoke(args: dict) -> dict:
    session_ids = _collect_session_ids(args)
    if not session_ids:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="session_ids"),
                "error_class": "invalid_args", "entries": []}

    domain = args.get("domain")  # handle vault opzionale; default = origine pagina
    form_hint = args.get("form_hint")

    entries = []
    for sid in session_ids:
        res = session_client.session_login(
            session_id=sid, domain=domain, form_hint=form_hint)
        logged_in = bool(res.get("logged_in"))
        reason = res.get("reason_code")
        entry = {"session_id": sid, "logged_in": logged_in}
        if reason:
            entry["reason_code"] = reason  # slug i18n, MAI un segreto
            msg = _reason_message(reason)
            if msg:
                entry["message"] = msg
        entries.append(entry)

    ok = any(e["logged_in"] for e in entries)
    out = {"ok": ok, "entries": entries,
           "metadata": {"logged_in": sum(1 for e in entries if e["logged_in"]),
                        "total": len(entries)}}
    if not ok:
        out["error"] = _msg("ERR_OP_FAILED", reason="login_sites")
        first_reason = next((e.get("reason_code") for e in entries
                             if e.get("reason_code")), "login_fallito")
        out["error_class"] = first_reason
    return out


def main():
    run_stdio(invoke, error_extra={"entries": []})


if __name__ == "__main__":
    main()
