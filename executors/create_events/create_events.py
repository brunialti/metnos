#!/usr/bin/env python3
"""create_events — dispatcher canonical (sequel di read_events, 13/5/2026).

Tool UNICO per creare un evento sul calendario. Dispatcher sottile che
instrada al backend giusto in base a `client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
  Default `local` (calendar locale via ICS file — stub NON IMPLEMENTATO).
- Backend builtin in `runtime/backends/calendar/<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

§2.3 reverse_pattern: `delete_events_by_id` (catalogo deterministico).

Contratto:
    stdin: JSON {summary, start, end, location?, attendees?, calendar_id?,
                 client?: 'local' (default)}
    stdout: JSON {ok, results, n_created, used, error?, error_class?,
                  _undo?: {pattern, ids}}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
import re  # noqa: E402
from datetime import datetime, time as _dtime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from messages import get as _msg  # noqa: E402
from backends.events import local_ics, google_workspace  # noqa: E402

_HANDLERS = {
    "local": local_ics,
    "google_workspace": google_workspace,
}

_ROME = ZoneInfo("Europe/Rome")
# Giorni relativi IT+EN → offset. §2.4: il proposer passa spesso date in
# linguaggio naturale ("domani alle 10" / "tomorrow 10:00") che fromisoformat
# del backend rifiuta. Risoluzione DETERMINISTICA (data su `now` Rome).
_DAY_KW = {
    "dopodomani": 2, "day after tomorrow": 2,
    "domani": 1, "tomorrow": 1,
    "stamattina": 0, "stasera": 0, "stanotte": 0, "oggi": 0, "today": 0,
    "ieri": -1, "yesterday": -1,
}


def _normalize_hybrid_dt(s: str, now: datetime) -> str:
    """§2.4: il proposer talvolta emette forme IBRIDE (placeholder-RUNTIME
    risolto o token relativo) con un'ora APPESA: "now_plus_1dT16:00:00" o
    "<iso>T16:00:00" (bias verso il pattern placeholder, lesson A3). Normalizza
    a ISO: base (relativa/ISO) + ora finale. Passthrough se non riconosciuto."""
    m = re.search(r"[T ](\d{1,2}):(\d{2})(?::\d{2})?\s*$", s)
    appended = None
    base = s
    if m:
        head = s[:m.start()]
        if re.search(r"\d{4}-\d{2}-\d{2}|now|today|domani|tomorrow|oggi|"
                     r"dopodomani|day after", head, re.I):
            appended = (int(m.group(1)), int(m.group(2)))
            base = head
    low = base.lower().strip()
    rel = re.match(
        r"^now(?:_(plus|minus)_(\d+)_?(d|days?|h|hours?|m|min|minutes?))?$", low)
    base_dt = None
    if low in ("now", "today") or rel:
        delta = timedelta()
        if rel and rel.group(1):
            n = int(rel.group(2)); unit = rel.group(3)[0]
            step = {"d": timedelta(days=n), "h": timedelta(hours=n),
                    "m": timedelta(minutes=n)}[unit]
            delta = step if rel.group(1) == "plus" else -step
        base_dt = now + delta
        if low == "today":
            base_dt = base_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if base_dt is not None:
        if appended:
            base_dt = base_dt.replace(hour=appended[0], minute=appended[1],
                                      second=0, microsecond=0)
        return base_dt.isoformat()
    if appended:
        try:
            d = datetime.fromisoformat(base.strip().rstrip("T"))
            return d.replace(hour=appended[0], minute=appended[1],
                             second=0, microsecond=0).isoformat()
        except ValueError:
            pass
    return s


def _resolve_dt_nl(val, now: datetime):
    """NL relative date/time → ISO 8601 con offset Rome. Passthrough se già
    ISO valido o non risolvibile (il backend darà errore onesto). Deterministico
    dato `now`. §7.9/§7.3 generale, niente LLM."""
    if not isinstance(val, str) or not val.strip():
        return val
    s = val.strip()
    try:
        datetime.fromisoformat(s)
        return s  # già ISO valido
    except ValueError:
        pass
    # §2.4: normalizza forme ibride proposer (placeholder+ora) → riprova ISO
    s = _normalize_hybrid_dt(s, now)
    try:
        datetime.fromisoformat(s)
        return s
    except ValueError:
        pass
    low = s.lower()
    base = None
    for kw, delta in _DAY_KW.items():
        if kw in low:
            base = (now + timedelta(days=delta)).date()
            low = low.replace(kw, " ")
            break
    # ora: HH:MM / HH.MM oppure singolo HH (es. "alle 10")
    hh, mm = None, 0
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\b", low)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"\b(\d{1,2})\b", low)
        if m:
            hh = int(m.group(1))
    if base is not None:
        if hh is None:
            hh = 9  # default mattino se giorno senza ora
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return datetime.combine(base, _dtime(hh, mm), tzinfo=_ROME).isoformat()
    # nessun giorno relativo: prova parsing assoluto tollerante (dateutil)
    try:
        from dateutil import parser as _dup
        dt = _dup.parse(s, default=now)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_ROME)
        return dt.isoformat()
    except Exception:
        return val  # non risolvibile → backend errore onesto §2.8


def _default_client() -> str:
    """Auto-default: google_workspace se OAuth token presente, altrimenti
    local_ics. Detect deterministico §7.9 (filesystem check)."""
    try:
        return "google_workspace" if google_workspace._has_creds() else "local"
    except Exception:
        return "local"


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    client = args.get("client") or _default_client()
    backend = _HANDLERS.get(client)
    if backend is None:
        avail = sorted(_HANDLERS.keys())
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    now = datetime.now(_ROME)
    # §2.1 VETTORIALE: se arrivano `entries` (lista di record evento via
    # from_step — es. extract_entries(web)→create_events), crea UN evento per
    # entry. Senza questo create_events ignorava la lista e cercava
    # summary/start/end top-level → "mandatory" sui dati piped (bug ROCm 3/6).
    entries = args.get("entries")
    # §2.1 cap inferiore = 0: la lista piped (from_step) può essere VUOTA
    # (es. extract_entries non ha trovato eventi databili). NON è un errore di
    # create_events: 0 in ingresso → 0 creati, esito ONESTO (§2.8), non il
    # criptico "summary/start/end mandatory".
    if "entries" in args and isinstance(entries, list) and not entries:
        return {"ok": True, "n_created": 0, "results": [], "used": 0,
                "summary": _msg("MSG_EVENTS_NONE_TO_CREATE")}
    if isinstance(entries, list) and entries:
        _EV = ("summary", "start", "end", "location", "description", "attendees")
        results, ok_count, undo_ids = [], 0, []
        for rec in entries:
            if not isinstance(rec, dict):
                continue
            ev = {k: rec[k] for k in _EV if rec.get(k) not in (None, "")}
            for k in ("client", "calendar_id"):  # eredita config top-level
                if args.get(k) and not ev.get(k):
                    ev[k] = args[k]
            for _k in ("start", "end"):
                if _k in ev:
                    ev[_k] = _resolve_dt_nl(ev[_k], now)
            r = backend.create(ev)
            results.extend(r.get("results") or [])
            if r.get("ok"):
                ok_count += int(r.get("n_created") or len(r.get("results") or []))
            undo_ids.extend((r.get("_undo") or {}).get("ids") or [])
        out = {"ok": ok_count > 0, "results": results,
               "used": len(results), "n_created": ok_count}
        if ok_count == 0:
            out["error"] = _msg("ERR_ARG_MISSING", arg="summary/start/end")
            out["error_class"] = "invalid_args"
        if undo_ids:
            out["_undo"] = {"reverse_pattern": "delete_events_by_id",
                            "ids": undo_ids, "scope": {"client": client}}
        return out
    # Singolo evento (campi top-level). §2.4: normalizza date NL → ISO.
    for _k in ("start", "end"):
        if _k in args:
            args[_k] = _resolve_dt_nl(args[_k], now)
    return backend.create(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID"),
                                      "error_class": "invalid_args",
                                      "results": [], "used": 0, "n_created": 0}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
