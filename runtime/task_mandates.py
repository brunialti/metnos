# SPDX-License-Identifier: AGPL-3.0-only
"""Persistent, task-scoped authority envelopes for unattended execution.

The scheduler stores natural-language queries.  A mandate is the deterministic
authority derived when the task is created; it is not an approval token and it
never contains credentials.  Domain executors consume only their own section.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import config as _C
import credential_mandates

_VERSION = 2
_URL_RE = re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)
_SCHEMELESS_URL_RE = re.compile(
    r"(?<![/@\w-])"
    r"((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59}))"
    r"(?=[/?#])[^\s<>\"']*",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(
    r"(?<![/@\w-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59}))(?::\d{1,5})?",
    re.IGNORECASE,
)


def explicit_hosts(query: str) -> list[str]:
    """Extract exact hostnames without imposing a command grammar."""
    if not isinstance(query, str):
        return []
    hosts = set()
    remaining = list(query)

    # URL paths may contain dotted filenames. Parse the URL as a unit so only
    # its authority can become part of a mandate.
    for match in _URL_RE.finditer(query):
        try:
            raw_host = urlsplit(match.group(0)).hostname or ""
        except ValueError:
            raw_host = ""
        if host := credential_mandates.canonical_site_host(raw_host):
            hosts.add(host)
        remaining[match.start():match.end()] = " " * (
            match.end() - match.start())

    remainder = "".join(remaining)
    # The same rule applies to natural, scheme-less forms such as
    # ``example.test/invoices?page=2``.
    remaining = list(remainder)
    for match in _SCHEMELESS_URL_RE.finditer(remainder):
        if host := credential_mandates.canonical_site_host(match.group(1)):
            hosts.add(host)
        remaining[match.start():match.end()] = " " * (
            match.end() - match.start())

    hosts.update(
        host for raw in _DOMAIN_RE.findall("".join(remaining))
        if (host := credential_mandates.canonical_site_host(raw))
    )
    return sorted(hosts)


def needs_version_upgrade(query: str, raw_mandate) -> bool:
    """Return whether a host-bearing legacy envelope needs one-time rebuild."""
    if not explicit_hosts(query):
        return False
    try:
        value = (json.loads(raw_mandate or "{}")
                 if isinstance(raw_mandate, str) else raw_mandate)
    except (TypeError, json.JSONDecodeError):
        return True
    if not isinstance(value, dict):
        return True
    version = value.get("version")
    return version is None or (isinstance(version, int) and version < _VERSION)


def _query_hash(query: str) -> str:
    normalized = " ".join(str(query or "").casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _login_requested(query: str) -> bool:
    try:
        return (_detlex.match("sites.login_intent", query)
                or _detlex.match("sites.login_entry_target", query))
    except Exception:
        normalized = " ".join(str(query or "").casefold().split())
        return bool(re.search(
            r"\b(accedi|accesso|login|autenticati|sign in|log in)\b",
            normalized))


def _credentials_disabled(query: str) -> bool:
    return (credential_mandates.site_mode_for_query(query)
            == credential_mandates.SITE_MODE_NONE)


def build_for_task(query: str, actor: str, *,
                   audit_path: Path | None = None) -> dict:
    """Build the minimal domain envelopes derivable at task creation."""
    requested_hosts = explicit_hosts(query)
    if not requested_hosts:
        return {}
    profiles = credential_mandates.verified_site_topology(
        actor, audit_path=audit_path)
    login_requested = _login_requested(query)
    credentials_disabled = _credentials_disabled(query)
    bindings = []
    for requested in requested_hosts:
        resolved = credential_mandates.resolve_verified_site_profile(
            profiles, requested)
        if resolved is not None:
            root, profile = resolved
            hosts = set(profile["hosts"])
            origins = set(profile["origins"])
            commissioned = True
        else:
            root, hosts, origins = requested, {requested}, set()
            commissioned = False
        operations = ["navigate", "open", "read"]
        default_read = credential_mandates.has_scope(
            root, credential_mandates.SITES_READ_SCOPE)
        if not credentials_disabled and (login_requested or default_read):
            operations.append("login")
        bindings.append({
            "root_host": root,
            "entry_hosts": sorted({requested, root}),
            "allowed_hosts": sorted(hosts | {requested, root}),
            "credential_origins": sorted(origins),
            "operations": sorted(operations),
            "commissioned": commissioned,
        })
    return {
        "version": _VERSION,
        "query_hash": _query_hash(query),
        "capabilities": {"sites": {"bindings": bindings}},
    }


def load_for_task(task_name: str, actor: str, *,
                  db_path: Path | None = None) -> dict | None:
    """Load and integrity-check an active task mandate."""
    if not task_name or not actor:
        return None
    path = Path(db_path or _C.DB_RECURRING_TASKS)
    conn = None
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(recurring_tasks)").fetchall()}
        if "mandates" not in columns:
            return None
        row = conn.execute(
            "SELECT query, mandates, enabled FROM recurring_tasks "
            "WHERE name=? AND actor=? LIMIT 1", (task_name, actor)).fetchone()
    except (OSError, sqlite3.Error):
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    if not row or not bool(row["enabled"]):
        return None
    try:
        mandate = json.loads(row["mandates"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if (not isinstance(mandate, dict)
            or mandate.get("version") != _VERSION
            or mandate.get("query_hash") != _query_hash(row["query"])):
        return None
    mandate["_query"] = str(row["query"] or "")
    return mandate


def sites_binding(task_name: str, actor: str, host: str, *,
                  db_path: Path | None = None) -> dict | None:
    mandate = load_for_task(task_name, actor, db_path=db_path)
    sites = ((mandate or {}).get("capabilities") or {}).get("sites") or {}
    canonical = credential_mandates.canonical_site_host(host)
    for binding in sites.get("bindings") or []:
        if not isinstance(binding, dict):
            continue
        entries = {credential_mandates.canonical_site_host(str(item))
                   for item in (binding.get("entry_hosts") or [])}
        allowed = {credential_mandates.canonical_site_host(str(item))
                   for item in (binding.get("allowed_hosts") or [])}
        if canonical and canonical in (entries | allowed):
            query = str(mandate.get("_query") or "")
            resolved = {**binding, "task_name": task_name,
                        "query_hash": mandate.get("query_hash", ""),
                        "query": query}
            operations = set(resolved.get("operations") or ())
            root = str(resolved.get("root_host") or canonical)
            if (_credentials_disabled(query)
                    or not credential_mandates.has_scope(
                        root, credential_mandates.SITES_READ_SCOPE)):
                operations.discard("login")
            else:
                operations.add("login")
            resolved["operations"] = sorted(operations)
            return resolved
    return None
