#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Azioni browser sicure su sessioni ``sites`` (spec F2 §3.4/§4.2)."""
from __future__ import annotations

import mimetypes
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RT = os.environ.get("METNOS_RUNTIME") or str(_ROOT / "runtime")
for p in (_RT, str(_ROOT / "executors" / "get_approval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from executor_helpers import run_stdio  # noqa: E402
from messages import get as _msg  # noqa: E402
from playwright_sidecar import session_client  # noqa: E402


def _collect_session_ids(args: dict) -> list[str]:
    raw = args.get("session_ids")
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list) and raw:
        return [x for x in raw if isinstance(x, str) and x]
    entries = args.get("entries")
    if isinstance(entries, list):
        return [e["session_id"] for e in entries
                if isinstance(e, dict) and e.get("session_id")]
    one = args.get("session_id")
    return [one] if isinstance(one, str) and one else []


def _attachment(path: str, sensitive: bool) -> dict:
    return {"kind": "image", "path": path, "basename": Path(path).name,
            "mime": mimetypes.guess_type(path)[0] or "image/png",
            "sensitive": sensitive}


def invoke(args: dict) -> dict:
    owner = os.environ.get("METNOS_ACTOR") or "host"
    channel = os.environ.get("METNOS_CHANNEL") or ""
    session_ids = _collect_session_ids(args)
    action = args.get("action")
    if not session_ids:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="session_ids"),
                "error_class": "invalid_args", "results": []}
    if not isinstance(action, str) or not action.strip():
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="action"),
                "error_class": "invalid_args", "results": []}
    goal_mode = args.get("_goal_mode") is True
    value_ref = args.get("value_ref")
    approval_tokens = args.get("approval_tokens") or {}
    if not isinstance(approval_tokens, dict):
        approval_tokens = {}

    results = []
    pending = []
    attachments = []
    for sid in session_ids:
        res = session_client.session_act(
            session_id=sid, owner=owner, action=action, value_ref=value_ref,
            approval_token=approval_tokens.get(sid),
            goal_query=(action if goal_mode else None))
        if res.get("approval_required"):
            token = res.get("approval_token")
            if token:
                pending.append((sid, token, res))
            shot = res.get("screenshot_path")
            if shot:
                attachments.append(_attachment(shot, bool(res.get("sensitive"))))
            continue
        results.append({
            "session_id": sid, "ok": bool(res.get("ok")),
            "executed": bool(res.get("executed")),
            "primitive": res.get("primitive"), "url": res.get("url"),
            "reason_code": (None if res.get("ok") else
                            res.get("reason_code") or res.get("error_class")),
            **({"reason_detail": res.get("detail")} if res.get("detail") else {}),
            **({"observed_candidates": res.get("observed_candidates")}
               if res.get("observed_candidates") else {}),
        })
        shot = res.get("screenshot_path")
        if shot:
            attachments.append(_attachment(
                shot, bool(res.get("sensitive"))))

    if pending:
        # Un solo gate BATCH per l'intento multi-sessione (§12-bis).
        from get_approval import invoke as approval_invoke
        tokens = {sid: token for sid, token, _ in pending}
        descriptions = "; ".join(
            str(res.get("description") or action) for _, _, res in pending)
        prompt = _msg("MSG_SITES_APPROVAL_PROMPT", action=descriptions)
        gate = approval_invoke({
            "prompt": prompt,
            "title": _msg("MSG_SITES_APPROVAL_TITLE"),
            "actor": owner, "channel": channel,
            "timeout_s": 3600,
            "on_approve": {"tool": "act_sites", "args": {
                "session_ids": list(tokens), "action": action,
                "approval_tokens": tokens,
                **({"_goal_mode": True} if goal_mode else {}),
                **({"value_ref": value_ref} if value_ref is not None else {}),
            }},
            "on_reject": {"tool": "delete_sites", "args": {
                "session_ids": list(tokens),
            }},
        })
        if attachments:
            gate["attachments"] = attachments
        gate["pending_sessions"] = list(tokens)
        return gate

    ok = bool(results) and all(r["ok"] for r in results)
    out = {"ok": ok, "results": results,
           "metadata": {"executed": sum(1 for r in results if r["executed"]),
                        "total": len(results)}}
    if attachments:
        out["attachments"] = attachments
    if ok:
        out["final_message_hint"] = _msg(
            "MSG_SITES_ACTIONS_COMPLETED",
            n=out["metadata"]["executed"])
    else:
        out["error_class"] = next((r["reason_code"] for r in results
                                   if r["reason_code"]), "action_failed")
        if out["error_class"] == "mandate_scope_exceeded":
            out["error"] = _msg("MSG_SITES_RC_MANDATE_SCOPE_EXCEEDED")
        elif out["error_class"] == "navigation_failed":
            out["error"] = _msg("MSG_SITES_RC_UNAVAILABLE")
        elif out["error_class"] == "side_browser_unavailable":
            out["error"] = _msg("MSG_SITES_RC_SIDE_BROWSER_UNAVAILABLE")
        else:
            out["error"] = _msg("ERR_OP_FAILED", reason="act_sites")
        if str(out["error"]).startswith("<missing:"):
            out["error"] = _msg("ERR_OP_FAILED", reason="act_sites")
    return out


def main():
    run_stdio(invoke, error_extra={"results": []})


if __name__ == "__main__":
    main()
