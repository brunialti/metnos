"""runtime/backends/contacts/google_workspace.py — Google People backend.

Wrappa `~/.local/share/metnos/skills/google-workspace/scripts/google_api.py`
sub-command `contacts list` (Google People API
`people/me/connections.list`).

Funzioni esposte:
- `find(args)` → filtra contacts per `query` (match substring case-insensitive
  su name/emails/phones). Read-only.
- `read(args)` → ritorna il subset matched per `contact_id` (slug name) o
  alias `email`/`phone` esatto.

NB: Google People API NON ha endpoint search server-side per "free text
across contacts"; filtriamo client-side dopo list. Cap default 1000
(coerente con UI sane default).

§7.9 deterministico nella parte di filtering. Subprocess `google_api.py
contacts list` via `run_with_retry` (retry su transient/SSL).
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from skill_wrapper import (  # noqa: E402
    _skill_home, _needs_inputs_oauth_setup,
    _get_oauth_provider_for_skill,
)
from backends._google_api_runner import run_with_retry  # noqa: E402
from messages import get as _msg  # noqa: E402

SKILL_NAME = "google-workspace"


def _has_creds() -> bool:
    return (_skill_home(SKILL_NAME) / "google_token.json").is_file()


def _auth_needs_inputs(args_base: dict, *, executor: str) -> dict:
    try:
        payload = _needs_inputs_oauth_setup(
            skill_name=SKILL_NAME, executor=executor,
            args_base=args_base,
            **_get_oauth_provider_for_skill(SKILL_NAME),
        )
    except Exception as ex:
        return {"ok": False, "error_class": "auth_required",
                "error_code": "ERR_OAUTH_SETUP",
                "error": _msg("ERR_OAUTH_SETUP", reason=str(ex))}
    return {"ok": False, "decision": "needs_inputs", "needs_inputs": payload}


def _run_contacts(argv: list[str], *, executor: str, args_base: dict
                   ) -> tuple[list | dict | None, dict | None]:
    return run_with_retry(
        argv, executor=executor, args_base=args_base,
        auth_handler=lambda ab: _auth_needs_inputs(ab, executor=executor),
    )


def _slugify(name: str) -> str:
    """Slug deterministico §7.9: lower + alfa + dash. Per dedup/id stabile."""
    import re
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return s


def _match(contact: dict, query_lc: str) -> bool:
    """Match substring case-insensitive su name + email + phone."""
    if not query_lc:
        return True
    if query_lc in (contact.get("name") or "").lower():
        return True
    for e in contact.get("emails") or []:
        if query_lc in (e or "").lower():
            return True
    for p in contact.get("phones") or []:
        if query_lc in (p or "").lower():
            return True
    return False


def find(args: dict) -> dict:
    """Filtra contacts Google per `query` (substring CI su name/emails/phones).

    Args:
      query: stringa da matchare (vuota → ritorna tutti, cappato a max_results).
      max_results: cap default 50 (max teorico 1000).

    Output: `{ok, entries, used, available_total, ...}`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args",
                              reason="must be an object"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}
    query = (args.get("query") or "").strip()
    max_results = int(args.get("max_results") or 50)
    fetch_cap = max(int(args.get("fetch_cap") or 1000), max_results)
    argv = ["contacts", "list", "--max", str(fetch_cap)]
    data, err = _run_contacts(argv, executor="find_contacts",
                              args_base=dict(args))
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "entries": [], "used": 0}
    contacts = data if isinstance(data, list) else []
    query_lc = query.lower()
    matches = [c for c in contacts if _match(c, query_lc)]
    truncated = len(matches) > max_results
    visible = matches[:max_results]
    # Aggiungi slug deterministico per id stabile (chiave per read_contacts).
    for c in visible:
        c.setdefault("id", _slugify(c.get("name") or ""))
    return {
        "ok": True,
        "entries": visible,
        "used": len(visible),
        "available_total": len(matches),
        "truncated": truncated,
        "truncated_what": "contact",
        "cap_field": "max_results",
        "cap_value": max_results,
        "messaging_source": "contacts_google_workspace",
    }


def read(args: dict) -> dict:
    """Lettura puntuale di un contatto per `contact_id` (slug name) o
    alias `email` / `phone` esatto.

    Args (alternative):
      contact_id: slug name (es. "mario-rossi"). Match esatto su slug.
      email:      match esatto su un elemento di `emails`.
      phone:      match esatto su un elemento di `phones`.

    Almeno uno richiesto. Output: `{ok, entries: [contact], used}`.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="args",
                              reason="must be an object"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}
    contact_id = (args.get("contact_id") or "").strip().lower()
    email = (args.get("email") or "").strip().lower()
    phone = (args.get("phone") or "").strip()
    if not (contact_id or email or phone):
        return {"ok": False, "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="contact_id|email|phone",
                              reason="at least one required"),
                "error_class": "invalid_args",
                "entries": [], "used": 0}
    # Fetch all (cappato a 1000) e cerca match esatto.
    argv = ["contacts", "list", "--max", "1000"]
    data, err = _run_contacts(argv, executor="read_contacts",
                              args_base=dict(args))
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "entries": [], "used": 0}
    contacts = data if isinstance(data, list) else []
    found = None
    for c in contacts:
        slug = _slugify(c.get("name") or "")
        if contact_id and slug == contact_id:
            found = c
            break
        if email and email in (e.lower() for e in (c.get("emails") or [])):
            found = c
            break
        if phone and phone in (c.get("phones") or []):
            found = c
            break
    if not found:
        return {"ok": True, "entries": [], "used": 0,
                "available_total": 0,
                "messaging_source": "contacts_google_workspace"}
    found.setdefault("id", _slugify(found.get("name") or ""))
    return {
        "ok": True,
        "entries": [found],
        "used": 1,
        "available_total": 1,
        "messaging_source": "contacts_google_workspace",
    }
