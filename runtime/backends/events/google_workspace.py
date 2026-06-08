"""runtime/backends/calendar/google_workspace.py — Google Calendar backend.

Wrappa lo skill `~/.local/share/metnos/skills/google-workspace/scripts/google_api.py`
(sub-commands `calendar list | create | delete`) via `skill_wrapper._run_api`.

Coerente con i backend gmail/drive importati da agentskills.io (ADR 0123):
- subprocess su google_api.py (OAuth gestito dallo script: token JSON in
  `~/.local/share/metnos/skills/google-workspace/google_token.json`).
- error_class deterministico via `_classify_error` (ADR 0101).
- `auth_required` ritorna `decision="needs_inputs"` con payload OAuth
  setup (skill_oauth_providers.json), coerente con il pattern delle
  altre integrazioni Google.

Funzioni:
- `read(args)`         → events nel range time_window | start/end.
- `create(args)`       → 1 evento (summary/start/end/+optional).
- `delete(args)`       → vettoriale per id.
- `find_events_empty(args)` → riusa `local_ics._generate_slots` per
  computare gap dai busy events ottenuti via API.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_RUNTIME = Path(__file__).resolve().parent.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from skill_wrapper import (  # noqa: E402
    _skill_home, _needs_inputs_oauth_setup,
    _get_oauth_provider_for_skill,
)
from backends._google_api_runner import run_with_retry  # noqa: E402
from backends.events import local_ics as _li  # noqa: E402

SKILL_NAME = "google-workspace"
ROME = ZoneInfo("Europe/Rome")

# Alias calendar_id → identity Google. Lookup deterministico §7.9.
# Bug live 15/5/2026: LLM emette `calendar_id=roberto` (nome utente Metnos),
# Google API ritorna 404 perche' "roberto" non e' un valid Google calendar
# ID. Pattern utili: `primary` (default), email completa, oppure alias
# semantici tradotti qui.
_CALENDAR_ID_ALIASES = {
    "primary": "primary",
    "default": "primary",
    "me": "primary",
    "self": "primary",
    "roberto": "primary",  # nome utente Metnos → primary del proprietario
    "user": "primary",
    "utente": "primary",
}


def _resolve_calendar_id(cal_id: str | None) -> str:
    """Risolve alias → calendar ID valido. Pass-through per email valide
    (contengono `@`). Default `primary` se None/empty.

    Test override (24/5/2026): se l'env `METNOS_TEST_CALENDAR_ID` e' set
    e cal_id e' None/empty/'primary', usa il calendar dedicato test
    (evita pollution del calendario reale Roberto durante E2E).
    """
    import os as _os
    if (not cal_id) or (isinstance(cal_id, str)
                         and cal_id.strip().lower() in ("", "primary")):
        test_id = _os.environ.get("METNOS_TEST_CALENDAR_ID", "").strip()
        if test_id:
            return test_id
    if not cal_id or not isinstance(cal_id, str):
        return "primary"
    norm = cal_id.strip().lower()
    if "@" in norm:
        # E' un'email Google Calendar valida → passa raw.
        return cal_id.strip()
    return _CALENDAR_ID_ALIASES.get(norm, cal_id.strip())


def _has_creds() -> bool:
    """True se il token OAuth Google e' presente sul filesystem."""
    return (_skill_home(SKILL_NAME) / "google_token.json").is_file()


def _err(msg: str, error_class: str, *, with_entries=False,
         with_results=False) -> dict:
    out = {"ok": False, "error": msg, "error_class": error_class}
    if with_entries:
        out["entries"] = []; out["used"] = 0
    if with_results:
        out["results"] = []; out["used"] = 0; out["n_created"] = 0
    return out


def _auth_needs_inputs(args_base: dict, *, executor: str) -> dict:
    """OAuth flow init payload (coerente con send_messages_google_workspace).

    Fix 18/5/2026: legge la provider config dal JSON canonico
    `skill_oauth_providers.json` via SKILL_NAME. Prima usava
    `_get_skill_oauth_config(__file__)` che cercava il manifest del
    backend (inesistente) → choices vuote nel form OAuth.
    """
    try:
        payload = _needs_inputs_oauth_setup(
            skill_name=SKILL_NAME, executor=executor,
            args_base=args_base,
            **_get_oauth_provider_for_skill(SKILL_NAME),
        )
    except Exception as ex:
        return {"ok": False, "error_class": "auth_required",
                "error": f"OAuth setup payload fallito: {ex}",
                "entries": [], "used": 0}
    return {
        "ok": True,
        "decision": "needs_inputs",
        "needs_inputs": payload,
        "entries": [], "used": 0,
        "error_class": "auth_required",
        "final_message_hint": payload.get("title", ""),
    }


def _run_calendar(argv: list[str], *, executor: str,
                  args_base: dict) -> tuple[dict | None, dict | None]:
    """Thin wrapper su `run_with_retry` per CLI `google_api.py calendar ...`.
    Retry deterministico §7.9 + OAuth needs_inputs via `_auth_needs_inputs`."""
    return run_with_retry(
        argv, executor=executor, args_base=args_base,
        auth_handler=lambda ab: _auth_needs_inputs(ab, executor=executor),
    )


# --------------------------------------------------------------------------
# READ
# --------------------------------------------------------------------------

def read(args: dict) -> dict:
    """Legge eventi da Google Calendar. Accetta `time_window` canonical o
    `start`/`end` ISO espliciti. Output: `entries: list[{id, summary,
    start, end, location, description, status, htmlLink}]`."""
    if not isinstance(args, dict):
        return _err("args must be an object", "invalid_args", with_entries=True)

    start_iso = args.get("start")
    end_iso = args.get("end")
    tw = args.get("time_window")
    if tw:
        try:
            from time_window_parser import parse_time_window
            s, e = parse_time_window(tw)
            start_iso = start_iso or s
            end_iso = end_iso or e
        except (ImportError, ValueError) as ex:
            return _err(str(ex), "invalid_args", with_entries=True)

    # Default "all" calendars per query non specificata: include primary +
    # secondari (work, shared, family birthdays, ecc.). User può forzare
    # singolo calendar via calendar_id esplicito.
    raw_cid = args.get("calendar_id")
    if raw_cid is None or str(raw_cid).strip().lower() in ("", "all", "tutti"):
        calendar_id = "all"
    else:
        calendar_id = _resolve_calendar_id(raw_cid)
    max_results = int(args.get("max_results") or 100)  # was 25, bumped per all
    argv = ["calendar", "list", "--calendar", calendar_id,
            "--max", str(max_results)]
    if start_iso: argv.extend(["--start", str(start_iso)])
    if end_iso:   argv.extend(["--end",   str(end_iso)])

    data, err = _run_calendar(argv, executor="read_events",
                              args_base=dict(args))
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "entries": [], "used": 0}
    entries = data if isinstance(data, list) else []
    return {
        "ok": True,
        "entries": entries,
        "used": len(entries),
        "available_total": len(entries),
        "calendar_source": "google_workspace",
        "calendar_id": calendar_id,
    }


# --------------------------------------------------------------------------
# CREATE
# --------------------------------------------------------------------------

def create(args: dict) -> dict:
    """Crea UN evento. Args: summary, start (ISO+TZ), end (ISO+TZ),
    location?, description?, attendees? (list[str] o str CSV),
    calendar_id? (default 'primary').

    Output trasformativo §2.6: `results: [{ok, id, summary, htmlLink, ...}]`.
    `_undo` reverse_pattern §2.3: `delete_events_by_id`.
    """
    if not isinstance(args, dict):
        return _err("args must be an object", "invalid_args",
                    with_results=True)

    summary = args.get("summary")
    start = args.get("start"); end = args.get("end")
    if not (isinstance(summary, str) and summary.strip()
            and isinstance(start, str) and start.strip()
            and isinstance(end, str) and end.strip()):
        return _err("summary/start/end mandatory (start/end ISO con TZ)",
                    "invalid_args", with_results=True)

    calendar_id = _resolve_calendar_id(args.get("calendar_id"))
    argv = ["calendar", "create",
            "--summary", summary, "--start", start, "--end", end,
            "--calendar", calendar_id]
    if args.get("location"):
        argv.extend(["--location", str(args["location"])])
    if args.get("description"):
        argv.extend(["--description", str(args["description"])])
    # Filtra attendees: solo email valide (contengono `@`). Bug live
    # 15/5/2026: LLM emette `attendees=["bob"]` → Google API 400
    # "Invalid attendee email". Soluzione: pass-through delle email,
    # scarta i non-email (rimangono visibili nel summary dell'evento).
    attendees = args.get("attendees")
    skipped_attendees: list[str] = []
    if attendees:
        if isinstance(attendees, str):
            attendees = [a.strip() for a in attendees.split(",") if a.strip()]
        elif not isinstance(attendees, list):
            attendees = []
        valid = []
        for a in attendees:
            s = str(a).strip()
            if not s:
                continue
            if "@" in s:
                valid.append(s)
            else:
                skipped_attendees.append(s)
        if valid:
            argv.extend(["--attendees", ",".join(valid)])

    data, err = _run_calendar(argv, executor="create_events",
                              args_base=dict(args))
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "results": [], "used": 0, "n_created": 0}

    rid = (data or {}).get("id", "")
    rec = {
        "ok": True,
        "id": rid, "uid": rid,
        "summary": (data or {}).get("summary", summary),
        "start": start, "end": end,
        "location": args.get("location") or "",
        "calendar_source": "google_workspace",
        "calendar_id": calendar_id,
        "htmlLink": (data or {}).get("htmlLink", ""),
    }
    out = {
        "ok": True,
        "n_created": 1,
        "results": [rec],
        "used": 1,
    }
    if skipped_attendees:
        out["warnings"] = [
            f"Attendee {a!r} ignorato (manca email valida)"
            for a in skipped_attendees
        ]
    if rid:
        out["_undo"] = {
            "reverse_pattern": "delete_events_by_id",
            "ids": [rid],
            "scope": {"calendar_id": calendar_id, "client": "google_workspace"},
        }
    return out


# --------------------------------------------------------------------------
# CALENDARS (container — non eventi): create / list / delete
# --------------------------------------------------------------------------

def create_calendar(args: dict) -> dict:
    """Crea un CALENDARIO-contenitore (non un evento). Args: summary (nome),
    description?, timezone?. Output §2.6: results:[{ok, calendar_id, summary}].
    Reverse §2.3: delete_calendars_by_id."""
    if not isinstance(args, dict):
        return _err("args must be an object", "invalid_args", with_results=True)
    summary = args.get("summary") or args.get("name") or args.get("title")
    if not (isinstance(summary, str) and summary.strip()):
        return _err("summary (nome calendario) obbligatorio", "invalid_args",
                    with_results=True)
    argv = ["calendar", "new-calendar", "--summary", summary]
    if args.get("description"):
        argv.extend(["--description", str(args["description"])])
    if args.get("timezone"):
        argv.extend(["--timezone", str(args["timezone"])])
    data, err = _run_calendar(argv, executor="create_calendars",
                              args_base=dict(args))
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "results": [], "used": 0, "n_created": 0}
    cid = (data or {}).get("calendarId", "")
    rec = {"ok": True, "created": True, "calendar_id": cid,
           "summary": (data or {}).get("summary", summary), "kind": "calendar",
           "calendar_source": "google_workspace"}
    out = {"ok": True, "n_created": 1, "results": [rec], "used": 1}
    if cid:
        out["_undo"] = {"reverse_pattern": "delete_calendars_by_id",
                        "ids": [cid], "scope": {"client": "google_workspace"}}
    return out


def list_calendars(args: dict) -> dict:
    """Elenca i CALENDARI dell'utente. Output §2.6: entries:[{id, summary,
    primary, access_role}]."""
    if not isinstance(args, dict):
        return _err("args must be an object", "invalid_args", with_entries=True)
    data, err = _run_calendar(["calendar", "list-calendars"],
                              executor="list_calendars", args_base=dict(args))
    if err is not None:
        return err if err.get("decision") == "needs_inputs" else {**err, "entries": []}
    cals = (data or {}).get("calendars") or []
    return {"ok": True, "entries": [
        {"id": c.get("id"), "summary": c.get("summary"),
         "primary": bool(c.get("primary")), "access_role": c.get("accessRole")}
        for c in cals]}


def delete_calendar(args: dict) -> dict:
    """Cancella uno o piu' CALENDARI-contenitore. Args: ids (list) o
    calendar_id. Output §2.6: results."""
    if not isinstance(args, dict):
        return _err("args must be an object", "invalid_args", with_results=True)
    ids = args.get("ids") or args.get("calendar_ids") or []
    if not ids and args.get("calendar_id"):
        ids = [args["calendar_id"]]
    ids = [str(i) for i in (ids if isinstance(ids, list) else [ids]) if i]
    if not ids:
        return _err("ids / calendar_id obbligatorio", "invalid_args",
                    with_results=True)
    results = []
    for cid in ids:
        data, err = _run_calendar(["calendar", "delete-calendar", cid],
                                  executor="delete_calendars", args_base=dict(args))
        if err is not None and err.get("decision") == "needs_inputs":
            return err
        if err is not None:
            results.append({"ok": False, "calendar_id": cid,
                            "error": err.get("error")})
        else:
            results.append({"ok": True, "deleted": True, "calendar_id": cid,
                            "kind": "calendar"})
    return {"ok": all(r["ok"] for r in results), "results": results,
            "used": len(results), "ok_count": sum(1 for r in results if r["ok"])}


# --------------------------------------------------------------------------
# UPDATE  (PATCH semantics — solo i field passati vengono modificati)
# --------------------------------------------------------------------------

def update(args: dict) -> dict:
    """Patch update di UN event esistente. Args:
      - `event_id` (mandatory): id evento da modificare.
      - `calendar_id` (default 'primary').
      - Patch fields (tutti opzionali, almeno uno richiesto):
        summary, start (ISO+TZ), end (ISO+TZ), location, description,
        attendees (list[str] o CSV).

    Output trasformativo §2.6: `results: [{ok, id, summary, htmlLink,
    updated_fields}]`. Niente `_undo`: PATCH non e' triviale da invertire
    (servirebbe pre-fetch dello state precedente; al momento non
    supportato — l'utente puo' re-update manualmente).
    """
    if not isinstance(args, dict):
        return _err("args must be an object", "invalid_args",
                    with_results=True)
    event_id = args.get("event_id") or args.get("uid")
    if not (isinstance(event_id, str) and event_id.strip()):
        return _err("event_id mandatory", "invalid_args",
                    with_results=True)
    calendar_id = _resolve_calendar_id(args.get("calendar_id"))
    # Almeno un patch field richiesto
    patch_fields = (args.get("summary"), args.get("start"), args.get("end"),
                    args.get("location"), args.get("description"),
                    args.get("attendees"))
    if not any(v is not None for v in patch_fields):
        return _err("at least one of summary/start/end/location/"
                    "description/attendees required", "invalid_args",
                    with_results=True)

    argv = ["calendar", "update", event_id.strip(), "--calendar", calendar_id]
    if args.get("summary"):
        argv.extend(["--summary", str(args["summary"])])
    if args.get("start"):
        argv.extend(["--start", str(args["start"])])
    if args.get("end"):
        argv.extend(["--end", str(args["end"])])
    if args.get("location") is not None:
        argv.extend(["--location", str(args["location"])])
    if args.get("description") is not None:
        argv.extend(["--description", str(args["description"])])

    attendees = args.get("attendees")
    skipped_attendees: list[str] = []
    if attendees:
        if isinstance(attendees, str):
            attendees = [a.strip() for a in attendees.split(",") if a.strip()]
        elif not isinstance(attendees, list):
            attendees = []
        valid = []
        for a in attendees:
            s = str(a).strip()
            if not s:
                continue
            if "@" in s:
                valid.append(s)
            else:
                skipped_attendees.append(s)
        if valid:
            argv.extend(["--attendees", ",".join(valid)])

    data, err = _run_calendar(argv, executor="set_events",
                              args_base=dict(args))
    if err is not None:
        if err.get("decision") == "needs_inputs":
            return err
        return {**err, "results": [], "used": 0, "n_updated": 0}

    rec = {
        "ok": True,
        "id": (data or {}).get("id", event_id),
        "uid": (data or {}).get("id", event_id),
        "summary": (data or {}).get("summary", ""),
        "calendar_source": "google_workspace",
        "calendar_id": calendar_id,
        "htmlLink": (data or {}).get("htmlLink", ""),
        "updated_fields": (data or {}).get("updated_fields", []),
    }
    out = {
        "ok": True,
        "n_updated": 1,
        "results": [rec],
        "used": 1,
    }
    if skipped_attendees:
        out["warnings"] = [
            f"Attendee {a!r} ignorato (manca email valida)"
            for a in skipped_attendees
        ]
    return out


# --------------------------------------------------------------------------
# DELETE
# --------------------------------------------------------------------------

def delete(args: dict) -> dict:
    """Cancella 1+ eventi per id (vettoriale §2.1)."""
    if not isinstance(args, dict):
        return _err("args must be an object", "invalid_args",
                    with_results=True)

    ids: list[str] = []
    if isinstance(args.get("event_ids"), list):
        ids.extend(str(x).strip() for x in args["event_ids"] if x)
    eid = args.get("event_id")
    if isinstance(eid, str) and eid.strip():
        ids.append(eid.strip())
    entries = args.get("entries") or []
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict):
                v = e.get("uid") or e.get("id")
                if isinstance(v, str) and v.strip():
                    ids.append(v.strip())
    if not ids:
        return _err("nessun event_id/event_ids/entries fornito",
                    "invalid_args", with_results=True)

    calendar_id = _resolve_calendar_id(args.get("calendar_id"))
    results: list[dict] = []
    failed: list[dict] = []
    for rid in ids:
        argv = ["calendar", "delete", rid, "--calendar", calendar_id]
        _, err = _run_calendar(argv, executor="delete_events",
                                args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            failed.append({"id": rid, **err})
            continue
        results.append({"ok": True, "id": rid, "uid": rid,
                         "status": "deleted"})

    # §2.8: una delete su id INESISTENTE (error_class=not_found) è un no-op
    # idempotente, NON un fallimento. ok=False solo per errori VERI (auth, rete,
    # quota). not_found esposto per un esito onesto. Prima `len(results)>0 or
    # not failed` → ok=False su id assente → terminator "azione non completata".
    not_found = [f.get("id") for f in failed if f.get("error_class") == "not_found"]
    real_failed = [f for f in failed if f.get("error_class") != "not_found"]
    return {
        "ok": not real_failed,
        "n_deleted": len(results),
        "not_found": not_found,
        "results": results,
        "failed": real_failed,
        "used": len(results),
        "calendar_source": "google_workspace",
        "calendar_id": calendar_id,
    }


# --------------------------------------------------------------------------
# FIND_EVENTS_EMPTY
# --------------------------------------------------------------------------

def find_events_empty(args: dict) -> dict:
    """Computa finestre VUOTE su Google Calendar. Riusa `_generate_slots`
    di `local_ics` per la logica deterministica di gap.
    """
    if not isinstance(args, dict):
        return _err("args must be an object", "invalid_args",
                    with_entries=True)

    tw_raw = args.get("time_windows")
    if tw_raw is None:
        time_windows = ["next-week"]
    elif isinstance(tw_raw, str):
        time_windows = [tw_raw]
    else:
        time_windows = tw_raw
    if not isinstance(time_windows, list) or not time_windows:
        return _err("time_windows must be non-empty list",
                    "invalid_args", with_entries=True)

    size = args.get("size") or "1hour"
    time_of_day = args.get("time_of_day") or "morning"
    max_results = args.get("max_results")
    if max_results is None:
        max_results = 10
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        return _err(f"max_results must be int, got {max_results!r}",
                    "invalid_args", with_entries=True)
    if max_results < 0:
        return _err(f"max_results must be >= 0, got {max_results}",
                    "invalid_args", with_entries=True)
    if max_results == 0:
        max_results = 100

    try:
        size_min = _li._parse_size_minutes(size)
    except ValueError as ex:
        return _err(str(ex), "invalid_args", with_entries=True)

    cal_id = args.get("calendar_id")
    if cal_id is not None and (not isinstance(cal_id, str)
                                or not cal_id.strip()):
        return _err("calendar_id must be a non-empty string",
                    "invalid_args", with_entries=True)
    cal_id_norm = _resolve_calendar_id(cal_id)

    try:
        tod_start, tod_end = _li._parse_time_of_day(time_of_day)
    except ValueError as ex:
        return _err(str(ex), "invalid_args", with_entries=True)
    if tod_start >= tod_end:
        return _err(f"time_of_day range invalid: {time_of_day!r}",
                    "invalid_args", with_entries=True)

    try:
        from time_window_parser import parse_time_window
        windows = [parse_time_window(w) for w in time_windows]
    except (ImportError, ValueError) as ex:
        return _err(str(ex), "invalid_args", with_entries=True)

    # Read events da Google Calendar per ognuna delle finestre (merge).
    busy: list[dict] = []
    for (s_iso, e_iso) in windows:
        argv = ["calendar", "list", "--calendar", cal_id_norm,
                "--start", s_iso, "--end", e_iso, "--max", "250"]
        data, err = _run_calendar(argv, executor="find_events_empty",
                                    args_base=dict(args))
        if err is not None:
            if err.get("decision") == "needs_inputs":
                return err
            return {**err, "entries": [], "used": 0}
        if isinstance(data, list):
            for e in data:
                s = e.get("start"); en = e.get("end")
                if not (s and en):
                    continue
                try:
                    s_dt = datetime.fromisoformat(s)
                    e_dt = datetime.fromisoformat(en)
                except ValueError:
                    continue
                # All-day events da Google: start/end sono `date` (no TZ).
                # Normalizziamo a Europe/Rome cosi' i confronti con slot_start
                # (offset-aware) non sollevano TypeError. §2.8 fail-safe.
                if s_dt.tzinfo is None:
                    s_dt = s_dt.replace(tzinfo=ROME)
                if e_dt.tzinfo is None:
                    e_dt = e_dt.replace(tzinfo=ROME)
                busy.append({
                    "start": s_dt, "end": e_dt,
                    "summary": e.get("summary", ""),
                    "uid": e.get("id", ""),
                })

    now = datetime.now(tz=ROME)
    total_days = 0
    for (s_iso, e_iso) in windows:
        ws = datetime.fromisoformat(s_iso).date()
        we = datetime.fromisoformat(e_iso).date()
        total_days += (we - ws).days + 1
    _arg_one = args.get("one_per_day")
    one_per_day = (max_results <= total_days
                    if _arg_one is None else bool(_arg_one))

    all_slots: list[dict] = []
    for (s_iso, e_iso) in windows:
        win_start = datetime.fromisoformat(s_iso)
        win_end = datetime.fromisoformat(e_iso)
        remaining = max_results - len(all_slots)
        if remaining <= 0:
            break
        slots = _li._generate_slots(
            win_start, win_end, tod_start, tod_end, size_min,
            remaining, busy, now,
            one_per_day=one_per_day,
        )
        all_slots.extend(slots)

    for s in all_slots:
        s.setdefault("calendar_id", cal_id_norm)

    return {
        "ok": True,
        "entries": all_slots,
        "used": len(all_slots),
        "available_total": len(all_slots),
        "calendar_source": "google_workspace",
        "calendar_id": cal_id_norm,
    }
