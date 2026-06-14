"""Calendar backend local ICS — implementazione minimale (14/5/2026).

Builtin per `client="local"` dei verbi events. `read`, `create`, `delete`,
`find_empty` sono TUTTI implementati su storage
`~/.local/share/metnos/calendar.ics` (singolo file iCal): read = parser
tollerante, create = append VEVENT, delete = rewrite atomico idempotente.
"local" e' un backend a pieno titolo come google_workspace (non un default
forzato): la SELEZIONE del backend e' compito del runtime, non dell'LLM.

Calendar vuoto: file mancante o vuoto = nessun evento. `read` ritorna
entries=[] ok=true (stato legittimo, non not_implemented). `find_empty`
computa slot liberi nella finestra × time_of_day × size, escludendo
overlap con events caricati.

Storage iCal: lettura tollerante via parser minimale (VEVENT con DTSTART/
DTEND ISO). Stesura completa rimandata quando `create` sara' necessario.

Verbi esposti:
- `read(args) -> dict`: lettura eventi per finestra (entries §2.6).
- `create(args) -> dict`: append VEVENT al file iCal (results §2.6).
- `delete(args) -> dict`: rewrite atomico idempotente per uid (results §2.6).
- `find_events_empty(args) -> dict`: gap calculation (entries §2.6, ADR 0127).
  Nome 1:1 con l'executor `find_events_empty` perche' il qualifier `_empty`
  da solo (`find_empty`) violerebbe la lettura intuitiva del vocab §2.2
  («empty» non e' fra i 17 oggetti). `read`/`create`/`delete` mantengono
  l'oggetto implicito (calendar gestisce solo events).
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# §7.11 — risali alla runtime root per import config
_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
import config as _C  # noqa: E402

ROME = ZoneInfo("Europe/Rome")
_DEFAULT_STORAGE = _C.PATH_USER_DATA / "calendar.ics"


def _storage_path() -> Path:
    env = os.environ.get("METNOS_CALENDAR_ICS")
    return Path(env) if env else _DEFAULT_STORAGE


# ---------------------------------------------------------------------------
# Messaggio canonico stub (residuo per create/delete)
# ---------------------------------------------------------------------------

_NOT_IMPL_MSG = (
    "Calendar locale: scrittura non ancora implementata (backend `local_ics` "
    "supporta solo read+find_empty). Per create/delete events, installa un "
    "plugin calendar (es. `cloud-calendar-gcal`) oppure configura le "
    "credenziali OAuth Google Workspace (skill imported `google-workspace`)."
)


def _err(msg=_NOT_IMPL_MSG, *, with_entries=False, with_results=False, extra=None):
    out = {"ok": False, "error": msg, "error_class": "not_implemented"}
    if with_entries:
        out["entries"] = []
        out["used"] = 0
    if with_results:
        out["results"] = []
        out["used"] = 0
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Parser size e time_of_day
# ---------------------------------------------------------------------------

_RE_SIZE = re.compile(r"^\s*(\d+)\s*(hour|hours|h|min|mins|minutes|m)?\s*$", re.I)
_RE_HHMM_RANGE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")

# Lingua-specifica IT (locale-free, table-driven §7.9). Per altre lingue
# estendere il dict tramite vocab oppure prompt_loader; per ora IT canonico
# perche' display_template e' user-facing italiano.
_WEEKDAY_SHORT_IT = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
_MONTH_SHORT_IT = (
    "gen", "feb", "mar", "apr", "mag", "giu",
    "lug", "ago", "set", "ott", "nov", "dic",
)


def _fmt_human_dt(dt: datetime) -> str:
    """Format human-readable IT compatto: «lun 18 mag, 09:00» (no zero-pad
    per il giorno). §7.9 deterministico, no locale module (fragile)."""
    return (f"{_WEEKDAY_SHORT_IT[dt.weekday()]} {dt.day} "
            f"{_MONTH_SHORT_IT[dt.month - 1]}, "
            f"{dt.hour:02d}:{dt.minute:02d}")


def _fmt_human_slot(start: datetime, end: datetime) -> str:
    """Format compatto «lun 18 mag, 09:00–10:00» se stesso giorno; altrimenti
    «<start_human> – <end_human>». Dash unicode `–` (en dash) per range orari."""
    if start.date() == end.date():
        return (f"{_WEEKDAY_SHORT_IT[start.weekday()]} {start.day} "
                f"{_MONTH_SHORT_IT[start.month - 1]}, "
                f"{start.hour:02d}:{start.minute:02d}"
                f"–{end.hour:02d}:{end.minute:02d}")
    return f"{_fmt_human_dt(start)} – {_fmt_human_dt(end)}"

_TOD_RANGES = {
    "morning": (dtime(9, 0), dtime(12, 0)),
    "afternoon": (dtime(14, 0), dtime(18, 0)),
    "evening": (dtime(19, 0), dtime(22, 0)),
    "any": (dtime(9, 0), dtime(18, 0)),
}


def _parse_size_minutes(spec) -> int:
    if not isinstance(spec, str):
        raise ValueError(f"size must be str, got {type(spec).__name__}")
    m = _RE_SIZE.match(spec)
    if not m:
        raise ValueError(f"unrecognized size {spec!r}: use '1hour', '30min', '90'")
    n = int(m.group(1))
    if n <= 0:
        raise ValueError(f"size must be positive, got {n}")
    unit = (m.group(2) or "min").lower()
    return n * 60 if unit in ("hour", "hours", "h") else n


def _parse_time_of_day(spec):
    if not isinstance(spec, str):
        return _TOD_RANGES["any"]
    s = spec.strip().lower()
    if s in _TOD_RANGES:
        return _TOD_RANGES[s]
    m = _RE_HHMM_RANGE.match(s)
    if m:
        return (
            dtime(int(m.group(1)), int(m.group(2))),
            dtime(int(m.group(3)), int(m.group(4))),
        )
    raise ValueError(
        f"unrecognized time_of_day {spec!r}: "
        f"use 'morning'|'afternoon'|'evening'|'any'|'HH:MM-HH:MM'"
    )


# ---------------------------------------------------------------------------
# Parser iCal minimale (solo VEVENT con DTSTART/DTEND)
# ---------------------------------------------------------------------------

_RE_VEVENT = re.compile(r"BEGIN:VEVENT(.*?)END:VEVENT", re.S | re.I)
_RE_DTSTART = re.compile(r"^DTSTART(?:;[^:]*)?:(\S+)\s*$", re.M | re.I)
_RE_DTEND = re.compile(r"^DTEND(?:;[^:]*)?:(\S+)\s*$", re.M | re.I)
_RE_SUMMARY = re.compile(r"^SUMMARY:(.+)$", re.M | re.I)
_RE_UID = re.compile(r"^UID:(\S+)\s*$", re.M | re.I)


def _parse_ical_dt(s: str) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        dt = datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ROME)
    try:
        return datetime.fromisoformat(s).astimezone(ROME)
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=ROME)
    except ValueError:
        return None


def _load_events(path: Path) -> list[dict]:
    """Calendar vuoto = file mancante o vuoto. Parser tollerante: skip VEVENT
    senza DTSTART/DTEND validi (§2.8: niente errori, solo skip silenti — l'unico
    impatto e' meno conflitti rilevati, e il calendar non viene MUTATO qui).
    """
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for body in _RE_VEVENT.findall(raw):
        ms = _RE_DTSTART.search(body)
        me = _RE_DTEND.search(body)
        if not (ms and me):
            continue
        s = _parse_ical_dt(ms.group(1))
        e = _parse_ical_dt(me.group(1))
        if not (s and e and s < e):
            continue
        out.append({
            "start": s,
            "end": e,
            "summary": (_RE_SUMMARY.search(body) or [None, ""])[1] if _RE_SUMMARY.search(body) else "",
            "uid": (_RE_UID.search(body).group(1) if _RE_UID.search(body) else ""),
        })
    return out


# ---------------------------------------------------------------------------
# Formattazione ISO con offset Europe/Rome
# ---------------------------------------------------------------------------

def _fmt_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ROME)
    s = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return s[:-2] + ":" + s[-2:]


def _overlaps_any(slot_start: datetime, slot_end: datetime, events: list[dict]) -> bool:
    for ev in events:
        if slot_start < ev["end"] and ev["start"] < slot_end:
            return True
    return False


def _generate_slots(win_start, win_end, tod_start, tod_end, size_min,
                    max_results, events, now, *, one_per_day=False):
    """Genera slot liberi della finestra × time_of_day × size. Output
    record `free_slot` con campi user-facing `start_human`/`end_human`/
    `when_human` (locale IT, formattazione deterministica §7.9).

    `one_per_day=True` ritorna un solo slot per giorno (il primo libero,
    UX «3 orari su 3 giorni» quando max_results <= giorni della finestra).
    Default False: tutti gli slot consecutivi del giorno corrente prima del
    successivo. Il caller (`find_events_empty`) decide la politica.
    """
    slots = []
    day = win_start.date()
    last_day = win_end.date()
    while day <= last_day:
        day_tod_start = datetime.combine(day, tod_start, tzinfo=ROME)
        day_tod_end = datetime.combine(day, tod_end, tzinfo=ROME)
        slot_start = max(day_tod_start, win_start, now)
        if slot_start > day_tod_start:
            delta_min = int((slot_start - day_tod_start).total_seconds() // 60)
            rem = delta_min % size_min
            if rem:
                slot_start = slot_start + timedelta(minutes=size_min - rem)
        picked_today = False
        while slot_start + timedelta(minutes=size_min) <= min(day_tod_end, win_end):
            slot_end = slot_start + timedelta(minutes=size_min)
            if not _overlaps_any(slot_start, slot_end, events):
                slots.append({
                    "kind": "free_slot",
                    "start": _fmt_iso(slot_start),
                    "end": _fmt_iso(slot_end),
                    "start_human": _fmt_human_dt(slot_start),
                    "end_human": _fmt_human_dt(slot_end),
                    "when_human": _fmt_human_slot(slot_start, slot_end),
                    "duration_min": size_min,
                    "weekday": slot_start.strftime("%A").lower(),
                })
                if len(slots) >= max_results:
                    return slots
                picked_today = True
                if one_per_day:
                    break
            slot_start = slot_end
        day = day + timedelta(days=1)
        if one_per_day and picked_today:
            # se one_per_day esaurisce i giorni prima di max_results, ok:
            # continueremo coi prossimi giorni della finestra.
            pass
    return slots


# ---------------------------------------------------------------------------
# read — legge eventi per finestra temporale
# ---------------------------------------------------------------------------

def read(args: dict) -> dict:
    """Legge eventi del calendario locale per la finestra data.

    Args:
        time_window: str (default 'next-7d').
        top_k:       int >= 0 (default 50; 0 = no cap §2.4).
        calendar_id: str (ignorato dal backend local).

    Output produttore §2.6: entries: list[{start, end, summary, uid}].
    Calendar vuoto = entries=[] ok=true.
    """
    if not isinstance(args, dict):
        return _err("args must be an object", with_entries=True,
                    extra={"error_class": "invalid_args"})

    spec = args.get("time_window") or "next-7d"
    top_k = args.get("top_k")
    if top_k is None:
        top_k = 50
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        return _err(f"top_k must be int, got {top_k!r}", with_entries=True,
                    extra={"error_class": "invalid_args"})
    if top_k < 0:
        return _err(f"top_k must be >= 0, got {top_k}", with_entries=True,
                    extra={"error_class": "invalid_args"})

    try:
        from time_window_parser import parse_time_window
        start_iso, end_iso = parse_time_window(spec)
    except (ImportError, ValueError) as e:
        return _err(f"time_window {spec!r}: {e}", with_entries=True,
                    extra={"error_class": "invalid_args"})

    win_start = datetime.fromisoformat(start_iso)
    win_end = datetime.fromisoformat(end_iso)

    events = _load_events(_storage_path())
    in_window = [
        ev for ev in events
        if ev["start"] < win_end and ev["end"] > win_start
    ]
    in_window.sort(key=lambda ev: ev["start"])
    cap = top_k if top_k > 0 else len(in_window)
    truncated = top_k > 0 and len(in_window) > top_k
    entries = [
        {
            "start": _fmt_iso(ev["start"]),
            "end": _fmt_iso(ev["end"]),
            "summary": ev["summary"],
            "uid": ev["uid"],
        }
        for ev in in_window[:cap]
    ]
    out = {
        "ok": True,
        "entries": entries,
        "used": len(entries),
        "calendar_source": "local_ics" if _storage_path().exists() else "empty",
    }
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "events"
        out["available_total"] = len(in_window)
        out["cap_field"] = "top_k"
        out["cap_value"] = top_k
    return out


# ---------------------------------------------------------------------------
# create / delete — writer iCal (append VEVENT / rewrite atomico idempotente)
# ---------------------------------------------------------------------------

def _ical_escape(s: str) -> str:
    """Escape minimo iCal (RFC 5545 §3.3.11): \\ , ; CRLF."""
    if not isinstance(s, str):
        return ""
    return (s.replace("\\", "\\\\")
             .replace(";", "\\;")
             .replace(",", "\\,")
             .replace("\n", "\\n")
             .replace("\r", ""))


def _ical_dt_utc(dt: datetime) -> str:
    """Format datetime iCal UTC (DTSTART/DTEND): `YYYYMMDDTHHMMSSZ`."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ROME)
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")


def _gen_uid(start: datetime, summary: str) -> str:
    """UID stabile: sha8(start_iso + summary) + @metnos.local. Sufficiente
    per evitare collisioni tra eventi distinti, deterministico §7.9."""
    import hashlib
    src = f"{_fmt_iso(start)}|{summary}".encode("utf-8")
    return hashlib.sha256(src).hexdigest()[:16] + "@metnos.local"


def create(args: dict) -> dict:
    """Crea UN evento nel calendar locale `~/.local/share/metnos/calendar.ics`.

    Args:
        summary: str (richiesto). Titolo evento.
        start:   ISO 8601 con offset (richiesto). `2026-05-18T09:00:00+02:00`.
        end:     ISO 8601 con offset > start (richiesto).
        location:    str (opzionale).
        description: str (opzionale).
        attendees:   list[str] o str (opzionale).
        calendar_id: str (ignorato dal backend local).

    Output trasformativo §2.6: `results: [{uid, summary, start, end, ...}]`.
    Append-only VEVENT al file iCal (crea il file se mancante con
    PRODID/VERSION minimi). `_undo: {ids: [<uid>], reverse_pattern:
    "delete_events_by_id"}` per il reverse handler §2.3.
    """
    if not isinstance(args, dict):
        return _err("args must be an object", with_results=True,
                    extra={"error_class": "invalid_args", "n_created": 0})

    missing = [k for k in ("summary", "start", "end") if not args.get(k)]
    if missing:
        return _err(
            f"campi mancanti: {', '.join(missing)}. create_events richiede "
            f"summary, start e end in ISO 8601 con offset.",
            with_results=True,
            extra={"error_class": "invalid_args", "n_created": 0},
        )

    summary = str(args["summary"]).strip()
    try:
        start_dt = datetime.fromisoformat(str(args["start"]))
        end_dt = datetime.fromisoformat(str(args["end"]))
    except ValueError as ex:
        return _err(f"start/end non ISO 8601 validi: {ex}", with_results=True,
                    extra={"error_class": "invalid_args", "n_created": 0})
    if end_dt <= start_dt:
        return _err("end deve essere > start", with_results=True,
                    extra={"error_class": "invalid_args", "n_created": 0})

    location = str(args.get("location") or "").strip()
    description = str(args.get("description") or "").strip()
    attendees = args.get("attendees") or []
    if isinstance(attendees, str):
        attendees = [attendees]
    if not isinstance(attendees, list):
        attendees = []

    uid = _gen_uid(start_dt, summary)
    now_dt = datetime.now(tz=ZoneInfo("UTC"))

    vevent_lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_ical_dt_utc(now_dt)}",
        f"DTSTART:{_ical_dt_utc(start_dt)}",
        f"DTEND:{_ical_dt_utc(end_dt)}",
        f"SUMMARY:{_ical_escape(summary)}",
    ]
    if location:
        vevent_lines.append(f"LOCATION:{_ical_escape(location)}")
    if description:
        vevent_lines.append(f"DESCRIPTION:{_ical_escape(description)}")
    for att in attendees:
        att_s = str(att).strip()
        if att_s:
            prefix = "" if att_s.lower().startswith("mailto:") else "mailto:"
            vevent_lines.append(f"ATTENDEE:{prefix}{att_s}")
    vevent_lines.append("END:VEVENT")
    vevent_block = "\r\n".join(vevent_lines) + "\r\n"

    path = _storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        header = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Metnos//local_ics//IT\r\n"
            "CALSCALE:GREGORIAN\r\n"
            "END:VCALENDAR\r\n"
        )
        path.write_text(header, encoding="utf-8")

    # Append VEVENT prima di END:VCALENDAR. Lettura tollerante: se END manca,
    # appendi e basta (riconosciamo "raw" fallback). §2.8 no silent failure
    # sull'I/O — eccezioni propagate al caller.
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError as ex:
        return _err(f"calendar.ics non leggibile: {ex}", with_results=True,
                    extra={"error_class": "storage_error", "n_created": 0})

    end_marker = "END:VCALENDAR"
    if end_marker in existing:
        new_content = existing.replace(end_marker, vevent_block + end_marker, 1)
    else:
        new_content = existing.rstrip("\r\n") + "\r\n" + vevent_block + end_marker + "\r\n"

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(new_content, encoding="utf-8")
    tmp_path.replace(path)

    return {
        "ok": True,
        "n_created": 1,
        "results": [{
            "ok": True,
            "id": uid,
            "uid": uid,
            "summary": summary,
            "start": _fmt_iso(start_dt),
            "end": _fmt_iso(end_dt),
            "location": location,
            "calendar_source": "local_ics",
            "htmlLink": "",  # local non ha URL
        }],
        "_undo": {
            "ids": [uid],
            "reverse_pattern": "delete_events_by_id",
        },
    }


def delete(args: dict) -> dict:
    """Cancella 1+ eventi per uid/id dal calendar locale.

    Args:
        event_ids: list[str] (plurale §2.1).
        event_id:  str (singolare, normalizzato a list).
        entries:   list[{id|uid, ...}] (da from_step di executor produttore).
        calendar_id: str (ignorato).

    Output trasformativo §2.6: `results: list`. Idempotente (uid non
    presenti = skip silente). Storage del file e' un rewrite atomico.
    """
    if not isinstance(args, dict):
        return _err("args must be an object", with_results=True,
                    extra={"error_class": "invalid_args", "n_deleted": 0})

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
                    with_results=True,
                    extra={"error_class": "invalid_args", "n_deleted": 0})

    path = _storage_path()
    if not path.exists():
        return {"ok": True, "n_deleted": 0, "results": [],
                "calendar_source": "empty"}

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as ex:
        return _err(f"calendar.ics non leggibile: {ex}", with_results=True,
                    extra={"error_class": "storage_error", "n_deleted": 0})

    deleted: list[dict] = []
    for uid in ids:
        # Pattern: BEGIN:VEVENT...UID:<uid>...END:VEVENT, rimosso integralmente.
        # Match non-greedy per non azzerare blocchi adiacenti.
        pat = re.compile(
            r"BEGIN:VEVENT.*?UID:" + re.escape(uid) + r".*?END:VEVENT\r?\n",
            re.S,
        )
        # Cattura il blocco VEVENT PRIMA di rimuoverlo: serve all'undo (§2.3)
        # per re-inserirlo identico via `restore()`.
        m = pat.search(raw)
        new_raw, n_sub = pat.subn("", raw, count=1)
        if n_sub > 0:
            raw = new_raw
            deleted.append({"ok": True, "uid": uid, "id": uid,
                            "vevent": m.group(0) if m else None})
        else:
            deleted.append({"ok": False, "uid": uid, "id": uid,
                            "error": "uid_not_found"})

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(raw, encoding="utf-8")
    tmp_path.replace(path)

    n_deleted = sum(1 for d in deleted if d.get("ok"))
    not_found = [d.get("uid") for d in deleted if not d.get("ok")]
    # §2.8 + docstring "idempotente": cancellare un uid inesistente NON è un
    # fallimento, è un no-op onesto. ok=True (azione eseguita), con n_deleted e
    # not_found per un esito veritiero. Prima `ok = n_deleted > 0` faceva
    # scattare il terminator "azione delete non completata" su id assenti.
    return {
        "ok": True,
        "n_deleted": n_deleted,
        "not_found": not_found,
        "results": deleted,
        "calendar_source": "local_ics",
    }


def restore(args: dict) -> dict:
    """Undo §2.3 di `delete`: re-inserisce blocchi VEVENT (testo) nel calendar.

    Args: `vevents: list[str]` = blocchi `BEGIN:VEVENT...END:VEVENT` catturati
    dal delete. Idempotente: un uid gia' presente nel calendar viene saltato.
    Rewrite atomico, stesso storage di create/delete.
    """
    if not isinstance(args, dict):
        return _err("args must be an object", with_results=True,
                    extra={"error_class": "invalid_args", "n_restored": 0})
    vevents = [v for v in (args.get("vevents") or []) if isinstance(v, str) and v.strip()]
    if not vevents:
        return {"ok": True, "n_restored": 0, "results": [],
                "calendar_source": "local_ics"}

    path = _storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "PRODID:-//Metnos//local_ics//IT\r\nCALSCALE:GREGORIAN\r\n"
            "END:VCALENDAR\r\n", encoding="utf-8")
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError as ex:
        return _err(f"calendar.ics non leggibile: {ex}", with_results=True,
                    extra={"error_class": "storage_error", "n_restored": 0})

    present_uids = {ev["uid"] for ev in _load_events(path) if ev.get("uid")}
    end_marker = "END:VCALENDAR"
    results = []
    for block in vevents:
        mu = _RE_UID.search(block)
        uid = mu.group(1) if mu else ""
        if uid and uid in present_uids:
            results.append({"ok": True, "uid": uid, "restored": False,
                            "reason": "already_present"})
            continue
        blk = block if block.endswith("\n") else block + "\r\n"
        if end_marker in existing:
            existing = existing.replace(end_marker, blk + end_marker, 1)
        else:
            existing = existing.rstrip("\r\n") + "\r\n" + blk + end_marker + "\r\n"
        if uid:
            present_uids.add(uid)
        results.append({"ok": True, "uid": uid, "restored": True})

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(existing, encoding="utf-8")
    tmp_path.replace(path)
    n_restored = sum(1 for r in results if r.get("restored"))
    return {"ok": True, "n_restored": n_restored, "results": results,
            "calendar_source": "local_ics"}


# ---------------------------------------------------------------------------
# find_empty — gap calculation (ADR 0127)
# ---------------------------------------------------------------------------

def find_events_empty(args: dict) -> dict:
    """Computa finestre VUOTE del calendar nella finestra × time_of_day × size.

    Args:
        time_windows: list[str] (default ['next-week']).
        size:         str unit-aware ('1hour', '30min', '90'). Default '1hour'.
        time_of_day:  'morning'|'afternoon'|'evening'|'any'|'HH:MM-HH:MM'.
        max_results:  int >= 0 (default 10; 0 = cap 100 §2.4).
        calendar_id:  str (ignorato).

    Output: entries: list[{kind:'free_slot', start, end, duration_min, weekday}].
    Calendar vuoto = tutti gli slot della finestra×tod sono liberi.
    """
    if not isinstance(args, dict):
        return _err("args must be an object", with_entries=True,
                    extra={"error_class": "invalid_args"})

    tw_raw = args.get("time_windows")
    if tw_raw is None:
        time_windows = ["next-week"]
    elif isinstance(tw_raw, str):
        time_windows = [tw_raw]
    else:
        time_windows = tw_raw
    if not isinstance(time_windows, list) or not time_windows:
        return _err("time_windows must be non-empty list", with_entries=True,
                    extra={"error_class": "invalid_args"})

    size = args.get("size") or "1hour"
    time_of_day = args.get("time_of_day") or "morning"

    max_results = args.get("max_results")
    if max_results is None:
        max_results = 10
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        return _err(f"max_results must be int, got {max_results!r}",
                    with_entries=True, extra={"error_class": "invalid_args"})
    if max_results < 0:
        return _err(f"max_results must be >= 0, got {max_results}",
                    with_entries=True, extra={"error_class": "invalid_args"})
    if max_results == 0:
        max_results = 100

    try:
        size_min = _parse_size_minutes(size)
    except ValueError as e:
        return _err(str(e), with_entries=True,
                    extra={"error_class": "invalid_args"})

    # calendar_id: stringa NON vuota se passata (lookup ID semantic);
    # None/missing accettato (default backend).
    cal_id = args.get("calendar_id")
    if cal_id is not None and (not isinstance(cal_id, str) or not cal_id.strip()):
        return _err("calendar_id must be a non-empty string",
                    with_entries=True, extra={"error_class": "invalid_args"})

    try:
        tod_start, tod_end = _parse_time_of_day(time_of_day)
    except ValueError as e:
        return _err(str(e), with_entries=True,
                    extra={"error_class": "invalid_args"})
    if tod_start >= tod_end:
        return _err(f"time_of_day range invalid: {time_of_day!r}",
                    with_entries=True, extra={"error_class": "invalid_args"})

    try:
        from time_window_parser import parse_time_window
        windows = [parse_time_window(w) for w in time_windows]
    except (ImportError, ValueError) as e:
        return _err(str(e), with_entries=True,
                    extra={"error_class": "invalid_args"})

    events = _load_events(_storage_path())
    now = datetime.now(tz=ROME)

    # one_per_day default: se max_results <= giorni totali delle finestre,
    # spreading 1-per-giorno produce UX «3 giorni distinti» invece di «3
    # slot consecutivi stesso giorno». Override esplicito via arg
    # `one_per_day` (bool, default heuristica). §7.9.
    _arg_one = args.get("one_per_day")
    if _arg_one is None:
        total_days = 0
        for (s_iso, e_iso) in windows:
            ws = datetime.fromisoformat(s_iso).date()
            we = datetime.fromisoformat(e_iso).date()
            total_days += (we - ws).days + 1
        one_per_day = max_results <= total_days
    else:
        one_per_day = bool(_arg_one)

    all_slots: list[dict] = []
    for (start_iso, end_iso) in windows:
        win_start = datetime.fromisoformat(start_iso)
        win_end = datetime.fromisoformat(end_iso)
        remaining = max_results - len(all_slots)
        if remaining <= 0:
            break
        slots = _generate_slots(
            win_start, win_end, tod_start, tod_end, size_min,
            remaining, events, now,
            one_per_day=one_per_day,
        )
        all_slots.extend(slots)

    cal_id_norm = cal_id if cal_id else "primary"
    for s in all_slots:
        s.setdefault("calendar_id", cal_id_norm)

    return {
        "ok": True,
        "entries": all_slots,
        "used": len(all_slots),
        "available_total": len(all_slots),
        "calendar_source": "local_ics" if _storage_path().exists() else "empty",
    }
