#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""read_sites — legge il contenuto (testo + screenshot redatto) di sessioni
web aperte (spec sites F1 §3.4).

Vettoriale (§2.1): `session_ids: array[str]` (o `from_step`) → una entry per
sessione. Lo screenshot è SEMPRE redatto dal broker (§3.3: overlay nero sui
campi segreti prima del capture). Il contenuto post-login è marcato
`sensitive:true` → resta LOCALE, mai frontier (§3.5 taint `no_frontier`).

OUT: entries=[{session_id, url, title, text, screenshot_path, sensitive}] +
     attachments (gli screenshot → gallery in chat via signed-URL per-owner, §6).
"""
from __future__ import annotations

import mimetypes
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
from sites_url_scrub import scrub_url  # noqa: E402


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
    session_ids = _collect_session_ids(args)
    if not session_ids:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="session_ids"),
                "error_class": "invalid_args", "entries": []}

    include_screenshot = bool(args.get("include_screenshot", True))
    include_forms = bool(args.get("include_forms", False))

    entries = []
    attachments = []
    any_sensitive = False
    for sid in session_ids:
        res = session_client.session_read(
            session_id=sid, owner=owner, include_screenshot=include_screenshot,
            include_forms=include_forms)
        if not res.get("ok"):
            entries.append({"session_id": sid, "ok": False,
                            "reason_code": res.get("error_class") or "read_failed"})
            continue
        sensitive = bool(res.get("sensitive"))
        any_sensitive = any_sensitive or sensitive
        entry = {
            "session_id": sid, "ok": True,
            # Defense in depth at the executor boundary: a stale or
            # non-standard sidecar response cannot place a session-bearing URL
            # into extraction, the final answer or the persistent turn log.
            "url": scrub_url(res.get("url")), "title": res.get("title", ""),
            "text": res.get("text", ""), "sensitive": sensitive,
        }
        if include_forms:
            entry["forms"] = res.get("forms") or []
        shot = res.get("screenshot_path")
        if shot:
            entry["screenshot_path"] = shot
            # §6: lo screenshot redatto → gallery in chat. Il photo_endpoint
            # firma per-owner e serve `attachments[idx].path`.
            attachments.append({
                "kind": "image", "path": shot, "basename": Path(shot).name,
                "mime": mimetypes.guess_type(shot)[0] or "image/png",
                "sensitive": sensitive,
            })
        entries.append(entry)

    ok = any(e.get("ok") for e in entries)
    out = {"ok": ok, "entries": entries}
    if attachments:
        out["attachments"] = attachments
    if any_sensitive:
        # §3.5: contenuto autenticato → describe/sintesi restano LOCALI.
        out["no_frontier"] = True
    if not ok:
        out["error"] = _msg("ERR_OP_FAILED", reason="read_sites")
        out["error_class"] = (entries[0].get("reason_code")
                              if entries else "read_failed")
    return out


def main():
    run_stdio(invoke, error_extra={"entries": []})


if __name__ == "__main__":
    main()
