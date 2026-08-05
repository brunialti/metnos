#!/usr/bin/env python3
"""read_persons — aggregator profilo identitario umano (ADR 0163).

§2.2 PRODUCER_VERBS: `read` = id sorgente → contenuto. Qui id = name
(slugify) o role filter. Output = profilo COMPLETO della persona,
combinando:
  - persons.sqlite (biometric enrollment: examples ArcFace)
  - users.db      (account paired ADR 0083: role, autonomy, channels)

Distinzione semantica vs `get_persons`:
  get_persons     → scheda registro (slug, name, n_examples) — single store
  read_persons    → profilo completo (account + biometric + channels) — JOIN

Determinismo §7.9: nessun LLM, solo sqlite JOIN. Cross-link via slug
(persons.slug == slugify(users.name)).

Pattern `${RUNTIME:actor}` (ADR 0163): l'arg `name` accetta placeholder
runtime risolto a monte dall'engine. Es. `name="${RUNTIME:actor}"`
viene risolto in `name="host"` (o nome configurato) prima dell'invoke.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from persons_registry import PersonsRegistry, slugify  # noqa: E402
import config as _C  # noqa: E402


def _persons_db_path() -> Path | None:
    v = os.environ.get("METNOS_USER_DATA")
    return (Path(v) / "persons.sqlite") if v else None


def _users_db_path() -> Path:
    root = os.environ.get("METNOS_USER_DATA")
    return (Path(root) if root else Path(_C.PATH_USER_DATA)) / "users.db"


def _load_users_by_slug() -> dict:
    """Ritorna {slug: user_dict} da users.db. Slug = slugify(users.name).

    Robusto §7.9: se users.db assente o schema incompatibile → {}.
    """
    udb = _users_db_path()
    if not udb.exists():
        return {}
    conn = None
    try:
        conn = sqlite3.connect(
            udb.resolve().as_uri() + "?mode=ro",
            uri=True,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT id, name, display_name, role, autonomy_level, "
            "created_at, email FROM users").fetchall()
        out: dict = {}
        for r in rows:
            d = dict(r)
            d["slug"] = slugify(d.get("name") or "")
            display_name = d.get("display_name")
            d["display_slug"] = slugify(display_name) if display_name else ""
            out[d["slug"]] = d
        # Allega una proiezione minima dei canali. recipient_id e token di
        # pairing restano fuori dal processo e dall'output.
        try:
            ch_rows = conn.execute(
                "SELECT user_id, channel, verified_at "
                "FROM user_channels").fetchall()
            chs_by_user: dict = {}
            for c in ch_rows:
                chs_by_user.setdefault(c["user_id"], []).append({
                    "channel": c["channel"],
                    "verified": bool(c["verified_at"]),
                    "verified_at": c["verified_at"],
                })
            for d in out.values():
                d["channels"] = chs_by_user.get(d["id"], [])
        except sqlite3.OperationalError:
            pass
        # Preferenze dalla stessa connessione read-only: importare users.py
        # aprirebbe invece una connessione bootstrap capace di scrivere.
        try:
            pref_rows = conn.execute(
                "SELECT user_id, key, value FROM user_prefs"
            ).fetchall()
            prefs_by_user: dict = {}
            for pref in pref_rows:
                prefs_by_user.setdefault(pref["user_id"], {})[
                    pref["key"]
                ] = pref["value"]
            for d in out.values():
                d["prefs"] = prefs_by_user.get(d["id"], {})
        except sqlite3.OperationalError:
            pass
        return out
    finally:
        if conn is not None:
            conn.close()


def _merge_entry(person: dict | None, user: dict | None,
                  *, is_self: bool = False,
                  include_examples: bool = True,
                  include_channels: bool = True) -> dict:
    """Fonde un record persons + un record users in una entry unificata."""
    if person is None and user is None:
        return {}
    entry: dict = {}
    if person:
        entry.update({
            "slug": person.get("slug"),
            "name": person.get("name"),
            "n_examples": int(person.get("n_examples") or 0),
            "created_at": person.get("created_at"),
            "updated_at": person.get("updated_at"),
        })
        if include_examples and person.get("examples"):
            entry["examples"] = person["examples"]
    if user:
        # slug user prevale solo se persons assente
        entry.setdefault("slug", user.get("slug"))
        entry.setdefault("name", user.get("display_name") or user.get("name"))
        entry["role"] = user.get("role")
        entry["autonomy_level"] = user.get("autonomy_level")
        if user.get("email"):
            entry["email"] = user["email"]
        if user.get("prefs"):
            entry["prefs"] = user["prefs"]
        if include_channels and user.get("channels") is not None:
            entry["channels"] = user["channels"]
    if is_self:
        entry["is_self"] = True
    return entry


def _match_user_to_person(reg, user: dict) -> dict | None:
    """Risolve cross-link user → person via token-anywhere su display_name/name.

    users.name="roberto" può corrispondere a persons.slug="roberto_brunialti"
    (token "roberto" sussiste in entrambi). PersonsRegistry.resolve_name fa
    già token-anywhere lookup; lo riusa qui.
    """
    candidates = []
    for key in ("display_name", "name"):
        val = user.get(key)
        if val:
            slugs = reg.resolve_name(val)
            if slugs:
                candidates.extend(slugs)
                break  # display_name prevale se trova
    if not candidates:
        return None
    return reg.get(candidates[0])


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error": _msg("ERR_ARGS_NOT_OBJECT"),
            "error_class": "invalid_input",
            "error_code": "args_not_object",
        }
    name = args.get("name")
    role = args.get("role")  # filter: host | guest | None
    include_examples = args.get("include_examples", True)
    include_channels = args.get("include_channels", True)
    actor = args.get("_actor") or os.environ.get("METNOS_ACTOR") or ""

    if name is not None and not isinstance(name, str):
        return {
            "ok": False,
            "error": _msg("ERR_ARG_NOT_STRING", arg="name"),
            "error_class": "invalid_input",
            "error_code": "name_not_string",
        }
    if role is not None and role not in {"host", "guest"}:
        return {
            "ok": False,
            "error": _msg("ERR_ADMIN_ROLE_INVALID", role=role),
            "error_class": "invalid_input",
            "error_code": "role_invalid",
        }
    if name and role:
        return {
            "ok": False,
            "error": _msg(
                "ERR_ARG_INVALID", arg="name/role",
                reason="mutuamente esclusivi / mutually exclusive",
            ),
            "error_class": "invalid_input",
            "error_code": "name_role_conflict",
        }
    for field, value in (
        ("include_examples", include_examples),
        ("include_channels", include_channels),
    ):
        if not isinstance(value, bool):
            return {
                "ok": False,
                "error": _msg("ERR_ARG_ENUM", arg=field, allowed="true, false"),
                "error_class": "invalid_input",
                "error_code": f"{field}_not_boolean",
            }

    try:
        reg = PersonsRegistry(db_path=_persons_db_path(), read_only=True)
        users_by_slug = _load_users_by_slug()
    except Exception as exc:
        return {
            "ok": False,
            "error": _msg("ERR_DEPENDENCY_MISSING", what="identity profile"),
            "error_class": "resource_unavailable",
            "error_code": "identity_profile_unavailable",
            "detail": str(exc),
        }

    # Generic "host"/"guest" actor → risolvi via users.role
    if actor.lower() in ("host", "guest"):
        for u in users_by_slug.values():
            if u.get("role") == actor.lower():
                actor = u.get("name") or actor
                break

    try:
        # Modalita' A: lookup specifico per name
        if name:
            slugs = reg.resolve_name(name)
            person = reg.get(slugs[0]) if slugs else None
            # User match: prima slug diretto, poi token-anywhere su display_name/name
            user_slug = slugify(name)
            user = users_by_slug.get(user_slug)
            if user is None:
                # Fallback: cerca user il cui name/display matcha token con
                # `name` query (es. name="Roberto Brunialti" → user.name="roberto")
                for u in users_by_slug.values():
                    query_slug = slugify(name)
                    account_slug = slugify(u.get("name") or "")
                    display_name = u.get("display_name")
                    display_slug = slugify(display_name) if display_name else ""
                    if (account_slug in query_slug
                            or (display_slug and display_slug in query_slug)
                            or query_slug in account_slug):
                        user = u
                        break
            if person is None and user is None:
                return {
                    "ok": True,
                    "entries": [],
                    "status": "unknown_name",
                    "final_message_hint": _msg(
                        "MSG_PERSONS_UNKNOWN_NAME", name=name,
                    ),
                }
            target_slug = (person and person.get("slug")) or (
                user and user.get("slug")) or slugify(name)
            is_self = bool(actor) and (
                slugify(actor) in target_slug
                or target_slug in slugify(actor)
                or (user is not None and user.get("name") == actor))
            entry = _merge_entry(
                person, user, is_self=is_self,
                include_examples=include_examples,
                include_channels=include_channels)
            return {
                "ok": True,
                "entries": [entry],
                "n_entries": 1,
            }

        # Modalita' B: lista filtrata per role
        all_persons = reg.list_all()
        persons_by_slug = {p["slug"]: reg.get(p["slug"]) for p in all_persons}

        # Pre-match users → persons via token-anywhere (per cross-link)
        user_to_person: dict = {}  # user_slug → person_slug
        for u_slug, u in users_by_slug.items():
            matched = _match_user_to_person(reg, u)
            if matched and matched.get("slug"):
                user_to_person[u_slug] = matched["slug"]
        matched_person_slugs = set(user_to_person.values())

        entries: list[dict] = []
        # Unione delle chiavi: persone enrollate + utenti paired (con cross-link)
        all_keys = set(persons_by_slug.keys()) | set(users_by_slug.keys())
        for key in sorted(all_keys):
            p = persons_by_slug.get(key)
            u = users_by_slug.get(key)
            # Se key è user_slug e mappa a un person_slug → emetti combinato
            if u is not None and key in user_to_person:
                pslug = user_to_person[key]
                p = persons_by_slug.get(pslug)
            # Le persone collegate sono emesse dalla chiave user anche quando
            # il person_slug viene prima alfabeticamente: niente duplicati
            # dipendenti dall'ordine di iterazione.
            if p is not None and key in matched_person_slugs and u is None:
                continue
            if role:
                if u is None or u.get("role") != role:
                    continue
            is_self = bool(actor) and (
                slugify(actor) == key
                or (u is not None and u.get("name") == actor))
            entry = _merge_entry(
                p, u, is_self=is_self,
                include_examples=include_examples,
                include_channels=include_channels)
            entries.append(entry)
        return {
            "ok": True,
            "entries": entries,
            "n_entries": len(entries),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": _msg("ERR_DEPENDENCY_MISSING", what="identity profile"),
            "error_class": "resource_unavailable",
            "error_code": "identity_profile_unavailable",
            "detail": str(exc),
        }
    finally:
        reg.close()


def main():
    run_stdio(invoke, default=str)


if __name__ == "__main__":
    main()
