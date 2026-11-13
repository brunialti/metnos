"""runtime/backends/contacts/google_workspace.py — Google People backend.

Wrappa `~/.local/share/metnos/skills/google-workspace/scripts/google_api.py`
sub-command `contacts list` (Google People API
`people/me/connections.list`).

Funzioni esposte:
- `find(args)` → filtra contacts per `query` (match substring case-insensitive
  su name/emails/phones). Read-only.
- `read(args)` → ritorna il subset matched per `contact_id` (slug name) o
  alias `email`/`phone` esatto.
- `create_contacts(args)` → crea uno o piu' contatti (`people.createContact`),
  non duplica chi c'e' gia' con la stessa email o lo stesso telefono; ogni
  contatto creato porta il `resource_name` di Google, l'identificativo esatto
  per l'annullamento. Chiede lo scope `contacts` in scrittura.
- `delete_contacts(args)` → cancella per `resource_name` esatto (inverso di
  create_contacts, pattern §2.3 `delete_contacts_by_id`).

NB: Google People API NON ha endpoint search server-side per "free text
across contacts"; filtriamo client-side dopo list. Cap default 1000
(coerente con UI sane default).

§7.9 deterministico nella parte di filtering. Subprocess `google_api.py
contacts list` via `run_with_retry` (retry su transient/SSL).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from backends._google_auth_common import (  # noqa: E402
    auth_needs_inputs as _common_auth_needs_inputs,
)
from backends._google_api_runner import run_with_retry  # noqa: E402
from messages import get as _msg  # noqa: E402

SKILL_NAME = "google-workspace"


def _auth_needs_inputs(args_base: dict, *, executor: str,
                       result_kind: str = "entries") -> dict:
    return _common_auth_needs_inputs(
        args_base, executor=executor, result_kind=result_kind)


def _run_contacts(argv: list[str], *, executor: str, args_base: dict,
                  result_kind: str = "entries"
                  ) -> tuple[list | dict | None, dict | None]:
    return run_with_retry(
        argv, executor=executor, args_base=args_base,
        auth_handler=lambda ab: _auth_needs_inputs(
            ab, executor=executor, result_kind=result_kind),
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
    # `contact_id` e' invece l'identificativo esatto di Google (delete_contacts).
    for c in visible:
        c.setdefault("id", _slugify(c.get("name") or ""))
        if c.get("resource_name"):
            c.setdefault("contact_id", c["resource_name"])
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


# --------------------------------------------------------------------------
# WRITE: create / delete (scope OAuth `contacts`, 12/11/2026)
# --------------------------------------------------------------------------

# How many contacts one call creates at most (§2.1: an explicit upper cap).
DEFAULT_MAX_TOTAL = 50
_EXISTING_FETCH = 1000
# Google People resource names of contacts: `people/<opaque id>`.
_RESOURCE = re.compile(r"^people/[A-Za-z0-9_-]+$")


def _strings(value) -> list[str]:
    """A scalar or a list of scalars as stripped, non-empty strings."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [text for text in (str(item).strip() for item in items
                              if isinstance(item, (str, int)) and not isinstance(item, bool))
            if text]


def _is_address(text: str) -> bool:
    """A mailbox with one «@», something on both sides and no blank: enough
    to refuse a name typed in the wrong field; Google judges the rest."""
    local, at, domain = text.partition("@")
    return bool(at and local and domain and "@" not in domain
                and not any(char.isspace() for char in text))


def _wanted(args: dict) -> list[dict] | None:
    """The contacts asked for: the records in `entries`, or the one described
    by `name`/`email`/`phone`. None when the call names no contact at all."""
    entries = args.get("entries")
    if isinstance(entries, list):
        return [entry if isinstance(entry, dict) else {"name": entry} for entry in entries]
    if any(args.get(field) for field in ("name", "email", "emails", "phone", "phones")):
        return [{field: args.get(field)
                 for field in ("name", "email", "emails", "phone", "phones")}]
    return None


def _refused(code: str, arg: str, reason: str = "") -> dict:
    error = (_msg("ERR_ARG_MISSING", arg=arg) if code == "ERR_ARG_MISSING"
             else _msg(code, arg=arg, reason=reason))
    return {"ok": False, "error_code": code, "error": error,
            "error_class": "invalid_args", "results": [], "failed": [],
            "ok_count": 0, "fail_count": 0, "n_created": 0, "n_deleted": 0,
            "used": 0}


def create_contacts(args: dict) -> dict:
    """Create contacts in the Google address book, one per record.

    Args:
      entries: records `{name, email|emails?, phone|phones?}`; or, for one
        contact, `name` with `email` and/or `phone` beside it.
      max_total: upper cap of contacts handled by one call (default 50).

    A record already in the address book (same e-mail address, or same phone
    when it has no address) is not created twice: it comes back with
    `already_exists`. A record that cannot be created is in `failed` with its
    reason; the others are created all the same (§2.8). Every created contact
    carries `contact_id` = Google resource name, and `_undo` lists exactly
    those, so undo deletes what this call created and nothing else.
    """
    if not isinstance(args, dict):
        return _refused("ERR_ARG_INVALID", "args", "must be an object")
    wanted = _wanted(args)
    if wanted is None:
        return _refused("ERR_ARG_MISSING", "name")
    try:
        max_total = int(args.get("max_total") or DEFAULT_MAX_TOTAL)
    except (TypeError, ValueError):
        return _refused("ERR_ARG_INVALID", "max_total", "must be an integer")
    if max_total <= 0:                       # 0 = no explicit limit (§2.4)
        max_total = len(wanted) or 1
    available = len(wanted)
    wanted = wanted[:max_total]

    records, failed = [], []
    for record in wanted:
        name = " ".join(str(record.get("name") or "").split())
        emails = _strings(record.get("emails")) + _strings(record.get("email"))
        phones = _strings(record.get("phones")) + _strings(record.get("phone"))
        item = {"name": name, "emails": emails, "phones": phones}
        wrong = next((address for address in emails if not _is_address(address)), None)
        if not name:
            failed.append({**item, "error_code": "ERR_ARG_MISSING",
                           "error": _msg("ERR_ARG_MISSING", arg="name"),
                           "error_class": "invalid_args"})
        elif wrong is not None:
            failed.append({**item, "error_code": "ERR_ARG_INVALID",
                           "error": _msg("ERR_ARG_INVALID", arg="email", reason=wrong),
                           "error_class": "invalid_args"})
        else:
            records.append(item)

    def run(argv):
        return _run_contacts(argv, executor="create_contacts", args_base=dict(args),
                             result_kind="results")

    results: list[dict] = []
    if records:
        # What is already there, read once: a second request for the same
        # person must not leave two cards in the address book.
        existing, err = run(["contacts", "list", "--max", str(_EXISTING_FETCH)])
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            return {**err, "results": [], "failed": failed, "ok_count": 0,
                    "fail_count": len(failed), "n_created": 0, "used": 0}
        known_emails, known_phones = {}, {}
        for contact in existing if isinstance(existing, list) else []:
            for address in contact.get("emails") or []:
                known_emails.setdefault(str(address).strip().lower(), contact)
            for phone in contact.get("phones") or []:
                known_phones.setdefault(str(phone).strip(), contact)
        for item in records:
            twin = next((known_emails[a.lower()] for a in item["emails"]
                         if a.lower() in known_emails), None)
            if twin is None and not item["emails"]:
                twin = next((known_phones[p] for p in item["phones"] if p in known_phones), None)
            if twin is not None:
                results.append({**item, "created": False, "already_exists": True,
                                "existing_name": twin.get("name") or ""})
                continue
            argv = ["contacts", "create", "--name", item["name"]]
            for address in item["emails"]:
                argv += ["--email", address]
            for phone in item["phones"]:
                argv += ["--phone", phone]
            made, err = run(argv)
            if err is not None:
                if err.get("decision") == "needs_inputs" and not any(
                        row["created"] for row in results):
                    return err
                failed.append({**item, "error": err.get("error") or "",
                               "error_code": err.get("error_code") or "ERR_OP_FAILED",
                               "error_class": err.get("error_class") or "unknown"})
                continue
            made = made if isinstance(made, dict) else {}
            resource = str(made.get("resource_name") or "")
            if not _RESOURCE.match(resource):
                # §2.8: an answer without the exact handle is not a creation
                # we can stand behind (nor undo): failed, never done.
                failed.append({**item, "error_code": "ERR_OP_FAILED",
                               "error": _msg("ERR_OP_FAILED", reason="resource_name"),
                               "error_class": "server_error"})
                continue
            created = {**item, "created": True, "already_exists": False,
                       "contact_id": resource,
                       "id": _slugify(made.get("name") or item["name"]),
                       "client": "google_workspace"}
            results.append(created)
            for address in item["emails"]:
                known_emails.setdefault(address.lower(), created)
            for phone in item["phones"]:
                known_phones.setdefault(phone, created)

    identifiers = [row["contact_id"] for row in results if row["created"]]
    out = {
        "ok": not failed,
        "results": results,
        "failed": failed,
        "ok_count": len(results),
        "fail_count": len(failed),
        "n_created": len(identifiers),
        "contact_ids": identifiers,
        "used": len(wanted),
        "messaging_source": "contacts_google_workspace",
    }
    if failed and results:
        out["partial"] = True
    if failed and not results:
        out.update(error_class=failed[0]["error_class"],
                   error_code=failed[0]["error_code"], error=failed[0]["error"])
    if available > len(wanted):
        out.update(truncated=True, truncated_what="contact", available_total=available,
                   cap_field="max_total", cap_value=max_total)
    if identifiers:
        out["_undo"] = {"reverse_pattern": "delete_contacts_by_id",
                        "ids": identifiers,
                        "scope": {"client": "google_workspace"}}
    return out


def delete_contacts(args: dict) -> dict:
    """Delete contacts by exact Google resource name (closed domain, §2.4)."""
    if not isinstance(args, dict):
        return _refused("ERR_ARG_INVALID", "args", "must be an object")
    identifiers = _strings(args.get("contact_ids")) + _strings(args.get("contact_id"))
    entries = args.get("entries")
    if isinstance(entries, list):
        identifiers += [str(row.get("contact_id") or "").strip()
                        for row in entries if isinstance(row, dict)
                        and str(row.get("contact_id") or "").strip()]
    if not identifiers:
        return _refused("ERR_ARG_MISSING", "contact_ids")
    wrong = [value for value in identifiers if not _RESOURCE.match(value)]
    if wrong:
        return _refused("ERR_ARG_INVALID", "contact_ids", wrong[0])
    results: list[dict] = []
    failed: list[dict] = []
    for resource in dict.fromkeys(identifiers):
        data, err = _run_contacts(["contacts", "delete", resource],
                                  executor="delete_contacts", result_kind="results",
                                  args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs" and not results:
                return err
            failed.append({"contact_id": resource, "ok": False,
                           "error_class": err.get("error_class", "unknown"),
                           "error_code": err.get("error_code", "ERR_OP_FAILED"),
                           "error": err.get("error", "")})
            continue
        results.append({"contact_id": resource, "ok": True,
                        "status": "deleted", "client": "google_workspace"})
    out = {
        "ok": not failed,
        "ok_count": len(results),
        "fail_count": len(failed),
        "results": results,
        "failed": failed,
        "used": len(results),
        "n_deleted": len(results),
        "messaging_source": "contacts_google_workspace",
    }
    if failed and not results:
        out["error_class"] = failed[0].get("error_class", "unknown")
        out["error_code"] = failed[0].get("error_code", "ERR_OP_FAILED")
        out["error"] = failed[0].get("error", "")
    return out
