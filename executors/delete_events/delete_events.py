#!/usr/bin/env python3
"""delete_events — dispatcher canonical (sequel di create_events, 13/5/2026).

Tool UNICO per eliminare eventi dal calendario. Dispatcher sottile che
instrada al backend giusto in base a `client` (default `local`).

Architettura (refactor 13/5/2026, Q1 canonical+args):
- Dispatcher sottile: instrada al backend giusto in base a `client`.
- Backend builtin in `runtime/backends/calendar/<provider>.py`.
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  `_HANDLERS` cablato esplicitamente (§7.2 + §7.9).

§2.3 revertible via module.reverse(): LOCAL ICS re-inserisce i blocchi
VEVENT catturati dal delete; GOOGLE non e' ricreabile dal solo payload
del delete (undo onesto: non-ribaltabile §2.8). I results elencano
sempre gli `id`/`uid` cancellati.

Vettoriale §2.1: accetta `event_ids: list[str]`, `event_id: str`
(singolare, normalizzato a list len-1), oppure `entries: list[{id, ...}]`
(da from_step di un produttore tipo read_events).

Selezione interna per finestra (9/6/2026, §2.1): `time_window: str`
(es. "yesterday", "last-7d") risolve INTERNAMENTE finestra -> eventi ->
cancellazione, senza obbligare il piano read_events -> delete_events a
2 step. Parsing della spec = `time_window_parser` canonico (lo stesso
di read_events/find_events_empty, §7.2); risoluzione finestra -> eventi
delegata a `backend.read` (stesso filtro di read_events). `time_window`
e id espliciti sono MUTUAMENTE ESCLUSIVI: su un'azione distruttiva due
selezioni concorrenti = ambiguita' -> errore chiaro, mai guess (§2.8).
Cap superiore esplicito `max_total` (0 = nessun limite, §2.1).

Contratto:
    stdin: JSON {event_ids?|event_id?|entries?|time_window?, max_total?,
                 calendar_id?, client?: 'local' (default)}
    stdout: JSON {ok, results, n_deleted, used, n_matched?, time_window?,
                  partial?, failures?, truncated?, error?, error_class?}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from time_window_parser import parse_time_window  # noqa: E402
from backends.events import local_ics, google_workspace  # noqa: E402

_HANDLERS = {
    "local": local_ics,
    "google_workspace": google_workspace,
}


def _default_client() -> str:
    try:
        return "google_workspace" if google_workspace._has_creds() else "local"
    except Exception:
        return "local"


def _fail(error: str, error_class: str = "invalid_args") -> dict:
    """Esito di errore con shape trasformativa §2.6 (results vuoti)."""
    return {"ok": False, "error": error, "error_class": error_class,
            "results": [], "used": 0, "n_deleted": 0}


def _delete_by_window(backend, client: str, args: dict) -> dict:
    """Selezione interna §2.1: finestra -> eventi -> cancellazione.

    Riuso §7.2 (niente parsing/filtri reinventati):
    - la spec e' validata col canonico `time_window_parser.parse_time_window`
      (lo stesso di read_events/find_events_empty);
    - la risoluzione finestra -> eventi delega a `backend.read`, cioe' lo
      stesso filtro per-finestra che usa read_events (local: overlap su
      ICS; google: --start/--end API).
    """
    spec = str(args.get("time_window") or "").strip()
    try:
        parse_time_window(spec)  # fail-fast: spec invalida = zero side-effect
    except ValueError:
        return _fail(_msg("ERR_TIME_WINDOW_INVALID", label=spec))

    # Cap superiore esplicito §2.1 (0 = nessun limite).
    raw_cap = args.get("max_total")
    try:
        max_total = int(raw_cap) if raw_cap is not None else 0
    except (TypeError, ValueError):
        return _fail(_msg("ERR_ARG_NOT_INT", arg="max_total"))
    if max_total < 0:
        return _fail(_msg("ERR_ARG_INVALID", arg="max_total",
                          reason="must be >= 0 (0 = no limit)"))

    # calendar_id CONCRETO (default primary) per coerenza read<->delete:
    # mai risolvere la finestra su "all" e poi cancellare su primary.
    read_args = {
        "time_window": spec,
        "calendar_id": args.get("calendar_id") or "primary",
        "client": client,
        "top_k": 0,           # backend local: nessun cap
        "max_results": 250,   # backend google: pagina massima API
    }
    rr = backend.read(read_args)
    if not isinstance(rr, dict):
        rr = {}
    if rr.get("decision") == "needs_inputs":
        return rr  # setup OAuth: propaga as-is (il runtime sa gestirlo)
    if not rr.get("ok"):
        return _fail(rr.get("error") or _msg("ERR_GENERIC"),
                     rr.get("error_class") or "op_failed")

    found = [e for e in (rr.get("entries") or []) if isinstance(e, dict)]
    selected = found[:max_total] if max_total > 0 else found

    if not selected:
        # §2.8: finestra senza eventi = no-op ONESTO (ok, results vuoti),
        # non un errore finto.
        return {"ok": True, "results": [], "n_deleted": 0, "used": 0,
                "n_matched": 0, "time_window": spec,
                "calendar_source": rr.get("calendar_source")}

    sub = {k: v for k, v in args.items()
           if k not in ("time_window", "max_total")}
    sub["entries"] = selected  # backend.delete estrae uid|id da ogni entry
    out = backend.delete(sub)
    if isinstance(out, dict):
        out.setdefault("time_window", spec)
        out.setdefault("n_matched", len(found))
        out.setdefault("used", len(selected))
        if len(found) > len(selected):
            # §2.7/§2.11 truncation visibility; intentional = cap richiesto
            # esplicitamente via max_total (niente proposta di allargamento).
            out["truncated"] = True
            out["truncated_what"] = "events"
            out["available_total"] = len(found)
            out["cap_field"] = "max_total"
            out["cap_value"] = max_total
            out["truncated_intentional"] = True
    return out


def invoke(args):
    if not isinstance(args, dict):
        return _fail(_msg("ERR_ARGS_NOT_OBJECT"))
    client = args.get("client") or _default_client()
    backend = _HANDLERS.get(client)
    if backend is None:
        return _fail(_msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"))
    if args.get("time_window"):
        # Due selezioni concorrenti su un'azione distruttiva = ambiguita'
        # (quale vince?) -> errore chiaro, mai guess (§2.8).
        if args.get("event_ids") or args.get("event_id") or args.get("entries"):
            return _fail(_msg(
                "ERR_ARG_INVALID", arg="time_window",
                reason="mutually exclusive with event_ids/event_id/entries"))
        return _delete_by_window(backend, client, args)
    return backend.delete(args)


def reverse(plan, results):
    """Undo §2.3 (module.reverse): ricrea gli eventi cancellati.

    LOCAL ICS: re-inserisce i blocchi VEVENT catturati dal delete (restore
    verbatim, stesso uid). GOOGLE: l'evento esterno non e' ricreabile dal solo
    payload del delete → onesti, quei record contano come non-ribaltabili
    (§2.8). Dispatch sul campo `vevent` presente nei result locali.
    """
    res = results or {}
    rows = res.get("results") or []
    vevents = [r.get("vevent") for r in rows
               if isinstance(r, dict) and r.get("ok") and r.get("vevent")]
    not_reversible = [r.get("uid") for r in rows
                      if isinstance(r, dict) and r.get("ok") and not r.get("vevent")]
    out, restored = [], 0
    if vevents:
        rr = local_ics.restore({"vevents": vevents})
        out = rr.get("results") or []
        restored = int(rr.get("n_restored") or 0)
    failed = [{"uid": u, "error": "evento esterno (google): non ricreabile da undo"}
              for u in not_reversible]
    return {"ok": len(failed) == 0, "ok_count": restored,
            "fail_count": len(failed), "results": out, "failed": failed}


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps(_fail(_msg("ERR_JSON_INVALID"))))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
