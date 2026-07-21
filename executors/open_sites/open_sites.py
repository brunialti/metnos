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
_ROOT = Path(__file__).resolve().parents[2]
if _RT not in sys.path:
    sys.path.insert(0, _RT)

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from playwright_sidecar import session_client  # noqa: E402
from playwright_sidecar import stealth as stealth_registry  # noqa: E402


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
    task_name = os.environ.get("METNOS_TASK_NAME") or None
    credential_mode = str(args.get("_credential_mode") or "default")
    if credential_mode not in {"default", "none"}:
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="_credential_mode",
                              reason="default|none"),
                "error_class": "invalid_args", "entries": []}
    # ADR 0191 P1: master e sotto-opzioni indipendenti sono runtime-owned e
    # conservati integralmente nei replay di approvazione.
    stealth_pref = str(args.get("_stealth") or "off").strip().lower()
    if stealth_pref not in {"on", "off"}:
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="_stealth",
                              reason="on|off"),
                "error_class": "invalid_args", "entries": []}
    stealth = (stealth_pref == "on")
    raw_techniques = args.get("_stealth_techniques") or []
    unknown_techniques = stealth_registry.unknown_techniques(raw_techniques)
    if unknown_techniques:
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="_stealth_techniques",
                              reason="closed technique list"),
                "error_class": "invalid_args", "entries": []}
    stealth_techniques = list(
        stealth_registry.normalize_selection(raw_techniques))
    browser_mode = str(
        args.get("_browser_mode") or "headless").strip().lower()
    if browser_mode not in {"headless", "side"}:
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="_browser_mode",
                              reason="headless|side"),
                "error_class": "invalid_args", "entries": []}
    # ADR 0191 fix #9: lingua del turno (runtime-resolved) per locale/timezone.
    lang = str(args.get("_lang") or "").strip() or None
    max_total = int(args.get("max_total") or 4)
    approval_tokens = args.get("_allowlist_tokens") or []
    if approval_tokens and (not isinstance(approval_tokens, list)
                            or len(approval_tokens) != len(urls)):
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="_allowlist_tokens",
                              reason="token count"),
                "error_class": "invalid_args", "entries": []}
    open_approvals = args.get("_open_approvals") or []
    if open_approvals:
        if (not isinstance(open_approvals, list)
                or len(open_approvals) != len(urls)
                or any(not isinstance(spec, dict)
                       or spec.get("url") != urls[idx]
                       for idx, spec in enumerate(open_approvals))):
            return {"ok": False,
                    "error": _msg("ERR_ARG_INVALID", arg="_open_approvals",
                                  reason="approval binding"),
                    "error_class": "invalid_args", "entries": []}

    if allowlist and not approval_tokens and not task_name:
        import urllib.parse
        extras = set()
        pending_tokens = []
        for url in urls:
            host = (urllib.parse.urlsplit(url).hostname or "").lower()
            url_extras = {h.lower() for h in allowlist if isinstance(h, str)
                          and h.lower() != host}
            extras.update(url_extras)
            if not url_extras:
                pending_tokens.append(None)
                continue
            prepared = session_client.session_open(
                owner=owner, url=url, allowlist=allowlist,
                session_label=label, task_name=task_name,
                credential_mode=credential_mode, stealth=stealth,
                stealth_techniques=stealth_techniques,
                browser_mode=browser_mode, lang=lang)
            token = prepared.get("approval_token")
            if prepared.get("error_class") != "approval_required" or not token:
                return {"ok": False,
                        "error": _msg("ERR_OP_FAILED", reason="open_sites"),
                        "error_class": (prepared.get("error_class")
                                        or "approval_invalid"), "entries": []}
            pending_tokens.append(token)
        if extras:
            approval_dir = _ROOT / "executors" / "get_approval"
            if str(approval_dir) not in sys.path:
                sys.path.insert(0, str(approval_dir))
            from get_approval import invoke as approval_invoke
            return approval_invoke({
                "prompt": _msg("MSG_SITES_ALLOWLIST_APPROVAL_PROMPT",
                               hosts=", ".join(sorted(extras))),
                "title": _msg("MSG_SITES_ALLOWLIST_APPROVAL_TITLE"),
                "actor": owner,
                "channel": os.environ.get("METNOS_CHANNEL") or "",
                "timeout_s": 3600,
                "on_approve": {"tool": "open_sites", "args": {
                    "urls": urls, "allowlist": allowlist,
                    "session_label": label, "max_total": max_total,
                    "_credential_mode": credential_mode,
                    "_stealth": stealth_pref,
                    "_stealth_techniques": stealth_techniques,
                    "_browser_mode": browser_mode,
                    "_lang": lang or "",
                    "_allowlist_tokens": pending_tokens,
                }},
            })

    entries = []
    attempted_specs = []
    pending_hosts = set()
    pending_open = False
    truncated = False
    for idx, url in enumerate(urls):
        if idx >= max_total:
            truncated = True
            break
        spec = open_approvals[idx] if open_approvals else {}
        attempt_allowlist = spec.get("allowlist", allowlist)
        attempt_token = (spec.get("approval_token")
                         if open_approvals else
                         (approval_tokens[idx] if approval_tokens else None))
        res = session_client.session_open(
            owner=owner, url=url, allowlist=attempt_allowlist,
            session_label=label, approval_token=attempt_token,
            task_name=task_name, credential_mode=credential_mode,
            stealth=stealth, stealth_techniques=stealth_techniques,
            browser_mode=browser_mode, lang=lang)
        next_allowlist = (res.get("approved_allowlist")
                          if res.get("error_class") == "approval_required"
                          else attempt_allowlist)
        attempted_specs.append({
            "url": url, "allowlist": next_allowlist,
            "approval_token": (res.get("approval_token")
                               if res.get("error_class") == "approval_required"
                               else None),
        })
        if res.get("error_class") == "approval_required":
            pending_open = True
            pending_hosts.update(
                str(h) for h in (res.get("extra_hosts") or []) if h)
            continue
        if res.get("ok"):
            entries.append({
                "session_id": res.get("session_id"),
                "url": res.get("url"), "title": res.get("title", ""),
                "ok": True,
                **({"reused": True} if res.get("reused") else {}),
                # Fix adversarial #10: superficie osservata (403/429/5xx/vuota)
                # su un'apertura RIUSCITA va esposta, non scartata.
                **({"reason_code": res["reason_code"]}
                   if res.get("reason_code") else {}),
            })
        else:
            entries.append({
                "session_id": None, "url": url, "title": "", "ok": False,
                "reason_code": res.get("error_class") or "open_failed",
            })

    if pending_open:
        # Il replay ricostruisce il vettore da zero: nessun context orfano
        # resta aperto durante l'attesa del consenso.
        for entry in entries:
            if entry.get("ok") and entry.get("session_id"):
                session_client.session_close(
                    session_id=entry["session_id"], owner=owner)
        approval_dir = _ROOT / "executors" / "get_approval"
        if str(approval_dir) not in sys.path:
            sys.path.insert(0, str(approval_dir))
        from get_approval import invoke as approval_invoke
        return approval_invoke({
            "prompt": _msg("MSG_SITES_ALLOWLIST_APPROVAL_PROMPT",
                           hosts=", ".join(sorted(pending_hosts))),
            "title": _msg("MSG_SITES_ALLOWLIST_APPROVAL_TITLE"),
            "actor": owner,
            "channel": os.environ.get("METNOS_CHANNEL") or "",
            "timeout_s": 3600,
            "on_approve": {"tool": "open_sites", "args": {
                "urls": urls, "session_label": label,
                "max_total": max_total,
                "_credential_mode": credential_mode,
                "_stealth": stealth_pref,
                "_stealth_techniques": stealth_techniques,
                "_browser_mode": browser_mode,
                "_lang": lang or "",
                "_open_approvals": attempted_specs,
            }},
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
        out["error_class"] = entries[0].get("reason_code") if entries else "open_failed"
        if out["error_class"] == "mandate_scope_exceeded":
            out["error"] = _msg("MSG_SITES_RC_MANDATE_SCOPE_EXCEEDED")
        elif out["error_class"] == "side_browser_unavailable":
            out["error"] = _msg("MSG_SITES_RC_SIDE_BROWSER_UNAVAILABLE")
        else:
            out["error"] = _msg("ERR_OP_FAILED", reason="open_sites")
    return out


def main():
    run_stdio(invoke, error_extra={"entries": []})


if __name__ == "__main__":
    main()
