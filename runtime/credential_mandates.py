# SPDX-License-Identifier: AGPL-3.0-only
"""Canonical usage mandates stored as encrypted credential metadata."""
from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path

import credentials
from messages import get as _msg
import sites_audit

SITES_READ_SCOPE = "sites.read"
INTERACTIVE_PROFILE = "interactive"
SITES_READ_PROFILE = SITES_READ_SCOPE
SITE_MODE_DEFAULT = "default"
SITE_MODE_NONE = "none"
_MANAGED_SCOPES = frozenset({SITES_READ_SCOPE})


def choice_schema() -> dict:
    """Friendly labels with stable values; no secret enters the dialog."""
    return {
        "kind": "choice",
        "choices": [
            f"{SITES_READ_PROFILE}: "
            f"{_msg('MSG_CREDENTIAL_MANDATE_OPTION_SITES_READ')}",
            f"{INTERACTIVE_PROFILE}: "
            f"{_msg('MSG_CREDENTIAL_MANDATE_OPTION_INTERACTIVE')}",
        ],
    }


def dialog_step() -> dict:
    return {
        "var": "credential_mandate",
        "prompt": _msg("MSG_CREDENTIAL_MANDATE_PROMPT"),
        "schema": choice_schema(),
    }


def apply_profile(existing_scopes, profile: str) -> list[str]:
    """Replace only scopes managed by this form; preserve unrelated OAuth scopes."""
    profile = str(profile or "").split(":", 1)[0].strip()
    scopes = {
        str(item) for item in (existing_scopes or [])
        if isinstance(item, str) and item and item not in _MANAGED_SCOPES
    }
    if profile == SITES_READ_PROFILE:
        scopes.add(SITES_READ_SCOPE)
    elif profile != INTERACTIVE_PROFILE:
        raise ValueError("unknown credential mandate profile")
    return sorted(scopes)


def validate_scopes(value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("scopes must be a list")
    out = []
    for item in value:
        if (not isinstance(item, str) or not item.strip()
                or "*" in item or "?" in item):
            raise ValueError("each scope must be an exact non-empty string")
        out.append(item.strip())
    return sorted(set(out))


def site_mode_for_query(query: str) -> str:
    """Resolve an explicit natural-language restriction for one query."""
    try:
        import detection_lexicon
        disabled = detection_lexicon.match("sites.no_credentials", query)
    except Exception:
        normalized = " ".join(str(query or "").casefold().split())
        disabled = bool(re.search(
            r"\b(senza credenziali|senza login|without credentials|"
            r"without login)\b", normalized))
    return SITE_MODE_NONE if disabled else SITE_MODE_DEFAULT


def canonical_site_host(value: str) -> str:
    if not isinstance(value, str):
        return ""
    host = value.strip().rstrip(".").lower()
    if not host:
        return ""
    try:
        return ipaddress.ip_address(host).compressed.lower()
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return ""
    if len(host) > 253:
        return ""
    labels = host.split(".")
    if any(not label or len(label) > 63
           or not re.fullmatch(
               r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
           for label in labels):
        return ""
    return host


def _read_sites_audit(path: Path | None = None) -> list[dict]:
    source = Path(path or sites_audit.AUDIT_PATH)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def verified_site_topology(owner: str, *,
                           audit_path: Path | None = None) -> dict:
    """Reconstruct exact hosts from completed, approved broker operations."""
    sessions: dict[str, dict] = {}
    for event in _read_sites_audit(audit_path):
        if event.get("owner") != owner:
            continue
        sid = str(event.get("session_id") or "")
        domain = canonical_site_host(str(event.get("domain") or ""))
        if not sid or not domain:
            continue
        current = sessions.setdefault(sid, {
            "domain": domain, "hosts": set(), "origins": set(),
            "opened": False,
        })
        if current["domain"] != domain:
            continue
        kind = event.get("event")
        if kind == "session_open":
            current["opened"] = True
            current["hosts"].update(
                host for raw in (event.get("allowlist") or [])
                if (host := canonical_site_host(str(raw))))
        elif kind == "allowlist_change" and str(
                event.get("source") or "").startswith("approved_"):
            host = canonical_site_host(str(event.get("added_host") or ""))
            if host:
                current["hosts"].add(host)
        elif (kind == "credential_origin_approval"
              and event.get("outcome") is True):
            origin = canonical_site_host(str(event.get("origin") or ""))
            if origin:
                current["origins"].add(origin)

    profiles: dict[str, dict] = {}
    for session in sessions.values():
        if not session["opened"]:
            continue
        domain = session["domain"]
        profile = profiles.setdefault(
            domain, {"hosts": {domain}, "origins": set()})
        profile["hosts"].update(session["hosts"])
        profile["origins"].update(session["origins"])
    return profiles


def has_scope(binding: str, scope: str) -> bool:
    """Read encrypted policy live so revocation affects every future use."""
    if not binding or not scope:
        return False
    try:
        payload = credentials.load(binding)
    except (OSError, ValueError):
        return False
    scopes = (payload or {}).get("scopes")
    return isinstance(scopes, list) and scope in {
        str(item) for item in scopes if isinstance(item, str)}


def resolve_sites_binding(owner: str, host: str, *,
                          audit_path: Path | None = None) -> dict | None:
    """Resolve the credential's persistent default for interactive use."""
    canonical = canonical_site_host(host)
    profiles = verified_site_topology(owner, audit_path=audit_path)
    candidates = [
        (root, profile) for root, profile in profiles.items()
        if ((canonical == root or canonical in profile["hosts"])
            and has_scope(root, SITES_READ_SCOPE))
    ]
    if not candidates:
        return None
    root, profile = min(candidates, key=lambda item: (
        item[0] != canonical, len(item[0]), item[0]))
    return {
        "root_host": root,
        "entry_hosts": sorted({canonical, root}),
        "allowed_hosts": sorted(profile["hosts"] | {canonical, root}),
        "credential_origins": sorted(profile["origins"]),
        "operations": ["login", "navigate", "open", "read"],
        "credential_default": True,
        "query": "",
    }
