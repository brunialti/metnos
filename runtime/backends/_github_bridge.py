# SPDX-License-Identifier: MIT
"""Shared adapter to the installed GitHub skill, without direct network access."""
from __future__ import annotations

import json
import re

from messages import get as _msg

SKILL = "github"
# Accepted owner and repository name segments.
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def bridge():
    """Import the skill runner only when a provider is actually requested."""
    from skill_wrapper import _classify_error, _run_api, _skill_home
    return _skill_home(SKILL) / "scripts/github_api.py", _run_api, _classify_error


def fail(error_code: str, error: str, *, error_class: str,
         shape: str = "entries") -> dict:
    out = {"ok": False, "error_code": error_code, "error": error,
           "error_class": error_class, "used": 0}
    out[shape] = []
    return out


def arg_missing(arg: str, *, shape: str = "entries") -> dict:
    return fail("ERR_ARG_MISSING", _msg("ERR_ARG_MISSING", arg=arg),
                error_class="invalid_args", shape=shape)


def arg_invalid(arg: str, reason: str, *, shape: str = "entries") -> dict:
    return fail("ERR_ARG_INVALID", _msg("ERR_ARG_INVALID", arg=arg,
                                        reason=reason),
                error_class="invalid_args", shape=shape)


def not_an_object(shape: str = "entries") -> dict:
    return fail("ERR_ARG_INVALID",
                _msg("ERR_ARGS_NOT_OBJECT"),
                error_class="invalid_args", shape=shape)


def auth_required(shape: str = "entries") -> dict:
    """Report unavailable credentials through the shared localized message."""
    return fail("ERR_NOT_APPLICABLE",
                _msg("ERR_NOT_APPLICABLE", what=f"client '{SKILL}'"),
                error_class="auth_required", shape=shape)


def repo_of(args: dict) -> str | None:
    """Resolve owner/name from file arguments without treating local paths as repos."""
    for key in ("repo", "base_path", "path"):
        value = args.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        explicit = candidate.startswith("github:")
        if explicit:
            candidate = candidate[len("github:"):].strip()
        if not explicit and (candidate.startswith("/")
                             or candidate.startswith("~")
                             or candidate.startswith(".")):
            continue
        parts = candidate.strip("/").split("/")
        if len(parts) == 2 and all(_SEGMENT.fullmatch(part) for part in parts):
            return "/".join(parts)
    return None


def as_list(value) -> list[str]:
    """Normalize scalar or plural textual/numeric identifiers, excluding bools."""
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return [str(value)] if str(value).strip() else []
    if isinstance(value, list):
        return [str(item) for item in value
                if isinstance(item, (str, int)) and not isinstance(item, bool)
                and str(item).strip()]
    return []


def call(argv: list[str]) -> tuple[list[dict] | None, dict | None]:
    """Return records or an explicit error; malformed output is never success."""
    script, run_api, classify = bridge()
    returncode, stdout, stderr = run_api(script, argv, skill_name=SKILL)
    if returncode != 0:
        return None, {"error_class": classify(returncode, stderr),
                      "error": stderr.strip() or f"rc={returncode}"}
    try:
        raw = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        return None, {"error_class": "server_error",
                      "error": _msg("ERR_DURABLE_RESULT_CONTRACT_VIOLATION")}
    if isinstance(raw, dict) and (raw.get("ok") is False or raw.get("error")
                                  or raw.get("error_class")):
        return None, {"error_class": raw.get("error_class") or "server_error",
                      "error": str(raw.get("error") or
                                   _msg("ERR_DURABLE_RESULT_CONTRACT_VIOLATION"))}
    records = raw.get("results") if isinstance(raw, dict) else raw
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        return None, {"error_class": "server_error",
                      "error": _msg("ERR_DURABLE_RESULT_CONTRACT_VIOLATION")}
    return records, None


def flags(args: dict, mapping: dict[str, str]) -> list[str]:
    """Encode declared flags: CSV for lists, JSON for objects, switches for bools."""
    argv: list[str] = []
    for name, flag in mapping.items():
        value = args.get(name)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            value = ",".join(str(item) for item in value)
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            if not value:
                continue
            argv.append(flag)
            continue
        argv.extend([flag, str(value)])
    return argv


def cap(args: dict, entries: list[dict], *, default: int = 1000,
        field: str = "top_k", what: str = "entries") -> dict:
    """Apply the declared result cap and expose any truncation."""
    available = len(entries)
    limit = args.get(field, default)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = default
    kept = entries if limit <= 0 else entries[:limit]
    out = {"ok": True, "entries": kept, "used": len(kept),
           "available_total": available, "source": SKILL}
    if limit > 0 and available > limit:
        out.update({"truncated": True, "truncated_what": what,
                    "cap_field": field, "cap_value": limit})
    return out
