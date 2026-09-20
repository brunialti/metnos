#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""delete_sites — chiude/revoca sessioni web / kill-switch (spec sites §9 [ALTO]).

Verbo `delete` (ratificato §2.2) su oggetto `sites`: terminare una sessione =
distruggerla (la revoca NON e' reversibile: nessun reverse_pattern). `close` NON
e' un verbo del vocab chiuso (§10.4) → si usa `delete`.

Vettoriale (§2.1): `session_ids: array[str]` (o `from_step`) → chiude quelle
sessioni; `all: true` → «revoca tutte le mie sessioni web» (chiude ogni
sessione dell'owner). Idempotente e onesto (§2.8): chiudere una sessione già
morta ritorna count 0, non un errore. Un utente non può chiudere le sessioni
di un altro (owner-filtro nel broker).

OUT: results=[{session_id, closed: bool}]  (§2.6: verbo trasformativo).
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
    ents = args.get("entries")
    if isinstance(ents, list):
        return [e.get("session_id") for e in ents
                if isinstance(e, dict) and e.get("session_id")]
    one = args.get("session_id")
    return [one] if isinstance(one, str) and one else []


def invoke(args: dict) -> dict:
    owner = os.environ.get("METNOS_ACTOR") or "host"
    if args.get("all"):
        res = session_client.session_close(owner=owner, all=True)
        if not res.get("ok"):
            return {"ok": False, "results": [],
                    "error": _msg("ERR_OP_FAILED", reason="delete_sites"),
                    "error_class": res.get("error_class") or "close_failed"}
        closed = res.get("closed") or []
        return {"ok": True,
                "results": [{"session_id": s, "closed": True} for s in closed],
                "metadata": {"closed": len(closed), "kill_switch": True}}

    session_ids = _collect_session_ids(args)
    if not session_ids:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="session_ids"),
                "error_class": "invalid_args", "results": []}

    results = []
    for sid in session_ids:
        res = session_client.session_close(session_id=sid, owner=owner)
        results.append({"session_id": sid,
                        "closed": bool(res.get("count", 0) > 0),
                        "ok": bool(res.get("ok")),
                        **({"reason_code": res.get("error_class")}
                           if not res.get("ok") else {})})
    ok = all(r["ok"] for r in results)
    out = {"ok": ok, "results": results,
           "metadata": {"closed": sum(1 for r in results if r["closed"]),
                        "total": len(results)}}
    if not ok:
        out["error"] = _msg("ERR_OP_FAILED", reason="delete_sites")
        out["error_class"] = next(
            (r.get("reason_code") for r in results if not r["ok"]),
            "close_failed")
    return out


def main():
    run_stdio(invoke, error_extra={"results": []})


if __name__ == "__main__":
    main()
