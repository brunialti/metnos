#!/usr/bin/env python3
"""Close exact registered desktop applications after an explicit user choice."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import sys

sys.path.insert(0, os.environ.get("METNOS_SHIM_DIR", ""))
from executor_helpers import run_stdio
from messages import get as _msg


_IDENTITY = re.compile(r"^desktop:[0-9a-f]{64}$")
_MODES = ("graceful", "force")


def _error(code, *, programs="", effects=False):
    message = _msg("ERR_SET_PROCESSES_FAILED", programs=programs, code=code)
    return {"ok": False, "results": [], "failed": [{"error_code": code, "error": message}],
            "ok_count": 0, "fail_count": 1, "error_code": code, "error": message,
            "error_class": "resource_unavailable",
            "_undo": {"outcome": "irreversible" if effects else "no_effect"}}


def _token(programs, targets, mode):
    payload = json.dumps({"programs": programs, "targets": targets, "mode": mode},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def invoke(args):
    if not isinstance(args, dict):
        return _error("args_not_object")
    programs = args.get("programs")
    if (not isinstance(programs, list) or len(programs) > 10
            or any(not isinstance(p, str) or not _IDENTITY.fullmatch(p) for p in programs)
            or len(set(programs)) != len(programs)):
        return _error("registered_desktop_identity_required")
    if not programs:
        return {"ok": True, "results": [], "failed": [], "ok_count": 0,
                "fail_count": 0, "_undo": {"outcome": "no_effect"}}
    if args.get("state", "closed") != "closed":
        return _error("state_invalid")
    if not sys.platform.startswith("win"):
        return _error("platform_unsupported")
    import windows_desktop_apps as desktop
    machine = socket.gethostname()
    consent = args.get("actor_consent_token") or ""
    if not consent:
        targets = {}
        for program in programs:
            result = desktop.call(program, "query")
            if not result.get("ok") or not desktop.valid_processes(result.get("processes")):
                return _error(str(result.get("error_code") or "process_query_failed"))
            name = result.get("name")
            if not isinstance(name, str) or not name:
                return _error("registered_name_missing")
            targets[program] = {"name": name, "processes": result["processes"]}
        names = ", ".join(targets[p]["name"] for p in programs)
        if not any(target["processes"] for target in targets.values()):
            message = _msg("MSG_SET_PROCESSES_ALREADY_CLOSED", programs=names, machine=machine)
            return {"ok": True, "results": [{"package_id": p, "closed": True,
                    "already_closed": True} for p in programs], "failed": [],
                    "ok_count": len(programs), "fail_count": 0,
                    "final_message_hint": message, "_undo": {"outcome": "no_effect"}}
        branches = {mode: {"tool": "set_processes", "args": {
            "programs": programs, "state": "closed", "close_mode": mode,
            "process_targets": targets, "actor_consent_token": _token(programs, targets, mode),
        }} for mode in _MODES}
        return {"ok": True, "decision": "needs_inputs", "results": [], "failed": [],
                "ok_count": 0, "fail_count": 0, "_undo": {"outcome": "no_effect"},
                "needs_inputs": {
                    "title": _msg("MSG_SET_PROCESSES_TITLE"),
                    "description": _msg("MSG_SET_PROCESSES_DESCRIPTION", programs=names, machine=machine),
                    "dialog": [{"var": "decision", "prompt": _msg("MSG_SET_PROCESSES_PROMPT"),
                        "schema": {"kind": "choice", "choices": [
                            {"value": "graceful", "label": _msg("MSG_SET_PROCESSES_GRACEFUL")},
                            {"value": "force", "label": _msg("MSG_SET_PROCESSES_FORCE")},
                            {"value": "reject", "label": _msg("MSG_BTN_REJECT")},
                        ]}}], "fmt": "auto", "on_complete": {"type": "gate_dispatch", "branches": branches}}}
    mode, targets = args.get("close_mode"), args.get("process_targets")
    if (mode not in _MODES or not isinstance(targets, dict) or set(targets) != set(programs)
            or any(not isinstance(t, dict) or set(t) != {"name", "processes"}
                   or not isinstance(t["name"], str) or not t["name"] or len(t["name"]) > 512
                   or not desktop.valid_processes(t["processes"]) for t in targets.values())
            or not isinstance(consent, str)
            or not hmac.compare_digest(consent, _token(programs, targets, mode))):
        return _error("consent_invalid")
    results, failed, effects = [], [], False
    for program in programs:
        target = targets[program]
        answer = desktop.close(program, target["processes"], force=(mode == "force"))
        effects |= answer.get("effects_attempted") is not False
        if answer.get("ok") is True and (answer.get("payload") or {}).get("closed") is True:
            results.append({"package_id": program, "name": target["name"], "closed": True})
        else:
            failed.append({"package_id": program, "name": target["name"],
                           "error_code": answer.get("error_code") or "package_close_unverified"})
    names = ", ".join(targets[p]["name"] for p in programs)
    message = _msg("MSG_SET_PROCESSES_NOT_CLOSED" if failed else "MSG_SET_PROCESSES_CLOSED",
                   programs=", ".join(t["name"] for t in failed) if failed else names, machine=machine)
    return {"ok": not failed, "results": results, "failed": failed, "ok_count": len(results),
            "fail_count": len(failed), "partial": bool(results and failed),
            "final_message_hint": message, "summary": message,
            "_undo": {"outcome": "irreversible" if effects else "no_effect"},
            **({"error": message, "error_code": failed[0]["error_code"],
                "error_class": "resource_unavailable"} if failed else {})}


if __name__ == "__main__":
    run_stdio(invoke)
