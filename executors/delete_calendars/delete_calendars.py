#!/usr/bin/env python3
"""delete_calendars — cancella CALENDARI-contenitore Google. DESTRUTTIVO.

SICUREZZA (richiesta utente 3/6, "non cancellare calendari utilizzati!!!"):
  - SOLO calendari di PROPRIETA' (accessRole=='owner') → mai i condivisi/iscritti
    (festività, fasi lunari, calendari altrui in sola lettura);
  - MAI il calendario PRIMARIO;
  - SEMPRE CONFERMA esplicita (yes_no) prima di cancellare, mostrando i NOMI e
    avvertendo che si eliminano anche gli eventi (IRREVERSIBILE);
  - se nulla e' cancellabile in sicurezza → errore onesto (§2.8), niente delete.

Backend FORZATO google_workspace (i calendari sono Google). VETTORIALE §2.1.
Accetta `ids` (calendar_id) o un NOME (`summary`) risolto via list_calendars.
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
from backends.events import google_workspace  # noqa: E402


def _is_safe(info: dict | None) -> bool:
    """Cancellabile in sicurezza: di proprieta' (owner) e NON primario."""
    return bool(info and info.get("access_role") == "owner"
                and not info.get("primary"))


def _calendar_index():
    """Mappa {id: {summary, access_role, primary}} dai calendari dell'utente."""
    lst = google_workspace.list_calendars({})
    idx = {}
    for e in (lst.get("entries") or []):
        if e.get("id"):
            idx[e["id"]] = {
                "summary": (e.get("summary") or ""),
                "access_role": (e.get("access_role") or "").lower(),
                "primary": bool(e.get("primary")),
            }
    return idx, lst


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args", "results": []}
    a = dict(args)
    client = a.get("client")
    if client and client != "google_workspace":
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE",
                              what=f"client '{client}' (i calendari sono solo Google)"),
                "error_class": "invalid_args", "results": []}

    idx, lst = _calendar_index()
    if not lst.get("ok"):
        return lst  # propaga needs_inputs (OAuth) o errore

    raw = a.get("ids") or a.get("calendar_ids") or []
    if not isinstance(raw, list):
        raw = [raw]
    if not raw and a.get("calendar_id"):
        raw = [a["calendar_id"]]
    name = a.get("summary") or a.get("name") or a.get("title")

    targets, skipped = [], []  # targets: (id, summary); skipped: (label, reason)

    def _consider(cid, info):
        if _is_safe(info):
            targets.append((cid, info["summary"]))
        elif info is None:
            skipped.append((cid, "non trovato"))
        elif info.get("primary"):
            skipped.append((info["summary"], "calendario primario (mai cancellabile)"))
        else:
            skipped.append((info["summary"],
                            f"non di tua proprieta' (accessRole={info.get('access_role') or '?'})"))

    for cid in [str(i) for i in raw if i]:
        _consider(cid, idx.get(cid))
    if not targets and isinstance(name, str) and name.strip():
        tgt = name.strip().lower()
        match = [(cid, info) for cid, info in idx.items()
                 if info["summary"].strip().lower() == tgt]
        if match:
            for cid, info in match:
                _consider(cid, info)
        else:
            skipped.append((name, "nessun calendario con questo nome"))

    if not targets:
        reason = "; ".join(f"{lbl}: {r}" for lbl, r in skipped) or "nessuna corrispondenza"
        return {"ok": False,
                "error": (f"Nessun calendario cancellabile in sicurezza ({reason}). "
                          f"Cancello SOLO calendari di tua proprieta', mai il primario "
                          f"ne' quelli condivisi/iscritti."),
                "error_class": "not_found", "results": []}

    # SEMPRE conferma (delete irreversibile + avviso eventi).
    if not a.get("_confirmed"):
        names = ", ".join(f"«{s}»" for _, s in targets)
        return {
            "ok": True,
            "decision": "needs_inputs",
            "needs_inputs": {
                "title": "Conferma cancellazione calendario",
                "description": (f"Stai per cancellare {names}. Verranno eliminati "
                                f"anche TUTTI gli eventi al loro interno. "
                                f"IRREVERSIBILE."),
                "dialog": [{
                    "var": "confirm",
                    "prompt": f"Cancellare definitivamente {names}?",
                    "schema": {"kind": "yes_no"},
                }],
                # form: radio Sì/No + pulsanti Invia/Annulla (HTTP). Su canali
                # non-HTTP degrada a dialogue lato runtime.
                "fmt": "form",
                "on_complete": {
                    "type": "resume_executor_with_values",
                    "executor": "delete_calendars",
                    "args_base": {**a, "_confirmed": True,
                                  "ids": [cid for cid, _ in targets]},
                },
            },
        }

    # Confermato: rispetta un eventuale "no" del dialog yes_no.
    confirm = a.get("confirm")
    if isinstance(confirm, str):
        confirm = confirm.strip().lower() in ("si", "sì", "yes", "y", "ok", "true", "1")
    if "confirm" in a and not confirm:
        return {"ok": True, "results": [], "used": 0, "ok_count": 0,
                "summary": _msg("MSG_ACTION_CANCELLED")}
    res = google_workspace.delete_calendar({**a, "ids": [cid for cid, _ in targets]})
    # summary user-facing (i18n) → chat pulita, non JSON grezzo (vedi
    # orchestration resume_executor_with_values fallback).
    if isinstance(res, dict) and res.get("ok") and "summary" not in res:
        names = ", ".join(s for _, s in targets)  # «» le aggiunge il template
        res["summary"] = _msg("MSG_CALENDAR_DELETED", name=names)
    return res


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps(
            {"ok": False, "error": _msg("ERR_JSON_INVALID"),
             "error_class": "invalid_args", "results": []}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
