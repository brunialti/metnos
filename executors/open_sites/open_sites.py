#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""open_sites — apre sessioni browser autenticabili su siti (spec sites F1 §3.4).

Vettoriale (§2.1): `urls: array[str]` → una sessione per url (fan-out nel
broker). Non vede mai un segreto: passa owner/url/allowlist al session-broker e
riceve solo metadata. Il `session_id` è interno (§12-bis: l'utente parla di
«il sito X», il planner cabla il session_id via from_step).

OUT: entries=[{session_id, url, title, ok, reason_code?}]  (§3.4).
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


def invoke(args: dict) -> dict:
    owner = os.environ.get("METNOS_ACTOR") or "host"
    urls = args.get("urls")
    if not urls:
        one = args.get("url")
        urls = [one] if isinstance(one, str) and one else []
    if isinstance(urls, str):
        urls = [urls]
    urls = [u for u in urls if isinstance(u, str) and u.strip()]
    if not urls:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="urls"),
                "error_class": "invalid_args", "entries": []}

    allowlist = args.get("allowlist")
    if isinstance(allowlist, str):
        allowlist = [allowlist]
    label = args.get("session_label") or ""
    max_total = int(args.get("max_total") or 4)

    entries = []
    truncated = False
    for url in urls:
        if len(entries) >= max_total:
            truncated = True
            break
        res = session_client.session_open(
            owner=owner, url=url, allowlist=allowlist, session_label=label)
        if res.get("ok"):
            entries.append({
                "session_id": res.get("session_id"),
                "url": res.get("url"), "title": res.get("title", ""),
                "ok": True,
            })
        else:
            entries.append({
                "session_id": None, "url": url, "title": "", "ok": False,
                "reason_code": res.get("error_class") or "open_failed",
            })

    out = {
        "ok": any(e["ok"] for e in entries),
        "entries": entries,
        "metadata": {"requested": len(urls), "opened":
                     sum(1 for e in entries if e["ok"])},
    }
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "sessions"
        out["used"] = len(entries)
        out["available_total"] = len(urls)
    if not out["ok"]:
        # onestà §2.8: nessuna sessione aperta → error esplicito
        out["error"] = _msg("ERR_OP_FAILED", reason="open_sites")
        out["error_class"] = entries[0].get("reason_code") if entries else "open_failed"
    return out


def main():
    run_stdio(invoke, error_extra={"entries": []})


if __name__ == "__main__":
    main()
