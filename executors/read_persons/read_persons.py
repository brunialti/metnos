#!/usr/bin/env python3
"""read_persons — aggregator profilo identitario umano (ADR 0163).

§2.2 PRODUCER_VERBS: `read` = id sorgente → contenuto. Qui id = name
(slugify) o role filter. Output = profilo COMPLETO della persona,
combinando:
  - persons.sqlite (biometric enrollment: examples ArcFace)
  - users.db      (account paired ADR 0083: role, autonomy, channels)
  - contacts      (futuro: email/phone)

Distinzione semantica vs `get_persons`:
  get_persons     → scheda registro (slug, name, n_examples) — single store
  read_persons    → profilo completo (account + biometric + channels) — JOIN

Determinismo §7.9: nessun LLM, solo sqlite JOIN. Cross-link via slug
(persons.slug == slugify(users.name)).

Pattern `${RUNTIME:actor}` (ADR 0163): l'arg `name` accetta placeholder
runtime risolto a monte da praxis_executor. Es. `name="${RUNTIME:actor}"`
viene risolto in `name="host"` (o nome configurato) prima dell'invoke.
"""
from __future__ import annotations

import json
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


def _provider_label(imap_host: str) -> str:
    """Etichetta provider leggibile dall'host IMAP (no lista hardcoded §7.3)."""
    h = (imap_host or "").strip().lower()
    for pfx in ("imap.", "imaps.", "mail.", "in."):
        if h.startswith(pfx):
            return h[len(pfx):]
    return h


def _list_actor_mail_accounts() -> list[dict]:
    """Vista LIVE degli account mail configurati (SoT = mail_client/env, ADR 0163
    «contacts futuro»). Profilo = vista, NON copia: zero duplicazione, niente
    doppio pool. Espone SOLO account+indirizzo+provider; MAI segreti.
    §7.9 deterministico (lettura env/config). Robusto: ogni errore → []."""
    try:
        import mail_client as _mc  # _RUNTIME gia' su sys.path
    except Exception:
        return []
    out: list[dict] = []
    try:
        known = _mc.list_known_accounts()
    except Exception:
        return []
    for acc in known:
        try:
            c = _mc._account_creds(acc)
        except Exception:
            continue
        addr = (c.get("user") or "").strip()
        if not addr:
            continue
        out.append({
            "account": acc,
            "address": addr,
            "provider": _provider_label(c.get("imap_host") or ""),
        })
    return out


def _persons_db_path() -> Path | None:
    v = os.environ.get("METNOS_USER_DATA")
    return (Path(v) / "persons.sqlite") if v else None


def _users_db_path() -> Path:
    return _C.PATH_USER_DATA / "users.db"


def _load_users_by_slug() -> dict:
    """Ritorna {slug: user_dict} da users.db. Slug = slugify(users.name).

    Robusto §7.9: se users.db assente o schema incompatibile → {}.
    """
    udb = _users_db_path()
    if not udb.exists():
        return {}
    try:
        conn = sqlite3.connect(str(udb))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, display_name, role, autonomy_level, "
            "created_at, email, notes FROM users").fetchall()
        out: dict = {}
        for r in rows:
            d = dict(r)
            d["slug"] = slugify(d.get("name") or "")
            d["display_slug"] = slugify(d.get("display_name") or "")
            out[d["slug"]] = d
        # Allega channels per ogni user
        try:
            ch_rows = conn.execute(
                "SELECT user_id, channel, verified, created_at "
                "FROM user_channels").fetchall()
            chs_by_user: dict = {}
            for c in ch_rows:
                chs_by_user.setdefault(c["user_id"], []).append(dict(c))
            for d in out.values():
                d["channels"] = chs_by_user.get(d["id"], [])
        except sqlite3.OperationalError:
            pass
        conn.close()
        return out
    except Exception:
        return {}


def _merge_entry(person: dict | None, user: dict | None,
                  *, is_self: bool = False,
                  include_examples: bool = True,
                  include_channels: bool = True,
                  include_mail_accounts: bool = False) -> dict:
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
        entry["user_id"] = user.get("id")
        if user.get("email"):
            entry["email"] = user["email"]
        if include_channels and user.get("channels") is not None:
            entry["channels"] = user["channels"]
    if is_self:
        entry["is_self"] = True
    # Account mail come VISTA del profilo (no duplicazione, ADR 0163).
    # Solo per host/self: gli account operati da Metnos appartengono al host.
    if include_mail_accounts:
        ma = _list_actor_mail_accounts()
        if ma:
            entry["mail_accounts"] = ma
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
    name = args.get("name")
    role = args.get("role")  # filter: host | guest | None
    include_examples = args.get("include_examples", True)
    include_channels = args.get("include_channels", True)
    actor = args.get("_actor") or ""  # passed via runtime if available

    reg = PersonsRegistry(db_path=_persons_db_path())
    users_by_slug = _load_users_by_slug()

    # Generic "host"/"guest" actor → risolvi via users.role
    if actor.lower() in ("host", "guest"):
        for u in users_by_slug.values():
            if u.get("role") == actor.lower():
                actor = u.get("name") or actor
                break

    try:
        # Modalita' A: lookup specifico per name
        if name:
            if not isinstance(name, str):
                return {"ok": False, "error": _msg("ERR_ARG_NOT_STRING", arg="name")}
            slugs = reg.resolve_name(name)
            person = reg.get(slugs[0]) if slugs else None
            # User match: prima slug diretto, poi token-anywhere su display_name/name
            user_slug = slugify(name)
            user = users_by_slug.get(user_slug)
            if user is None:
                # Fallback: cerca user il cui name/display matcha token con
                # `name` query (es. name="the owner" → user.name="roberto")
                for u in users_by_slug.values():
                    if (slugify(u.get("name") or "") in slugify(name)
                            or slugify(u.get("display_name") or "")
                            in slugify(name)
                            or slugify(name) in slugify(u.get("name") or "")):
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
            attach_mail = is_self or (
                user is not None and user.get("role") == "host")
            entry = _merge_entry(
                person, user, is_self=is_self,
                include_examples=include_examples,
                include_channels=include_channels,
                include_mail_accounts=attach_mail)
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

        entries: list[dict] = []
        # Unione delle chiavi: persone enrollate + utenti paired (con cross-link)
        all_keys = set(persons_by_slug.keys()) | set(users_by_slug.keys())
        emitted_person_slugs: set = set()
        for key in sorted(all_keys):
            p = persons_by_slug.get(key)
            u = users_by_slug.get(key)
            # Se key è user_slug e mappa a un person_slug → emetti combinato
            if u is not None and key in user_to_person:
                pslug = user_to_person[key]
                p = persons_by_slug.get(pslug)
                emitted_person_slugs.add(pslug)
            # Se key è person_slug e già emesso via cross-link → skip
            if p is not None and key in emitted_person_slugs and u is None:
                continue
            if role:
                if u is None or u.get("role") != role:
                    continue
            is_self = bool(actor) and (
                slugify(actor) == key
                or (u is not None and u.get("name") == actor))
            attach_mail = is_self or (u is not None and u.get("role") == "host")
            entry = _merge_entry(
                p, u, is_self=is_self,
                include_examples=include_examples,
                include_channels=include_channels,
                include_mail_accounts=attach_mail)
            entries.append(entry)
        return {
            "ok": True,
            "entries": entries,
            "n_entries": len(entries),
        }
    finally:
        reg.close()


def main():
    run_stdio(invoke, default=str)


if __name__ == "__main__":
    main()
