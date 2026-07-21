#!/usr/bin/env python3
"""filter_lists — operatori logici set ops fra 2 liste di entries.

Pattern §7.3 general-purpose per pipeline "due liste, intersezione/
unione/differenza/sovrapposizione temporale". Parametrizzato via
`op` + `on_keys`.

Output: lista filtrata di entries (subset di A). Per aggregati
numerici (sum/prod/avg/min/max/count) usa `compute_entries`.

ESEMPI:
  filter_lists(op="intersect", from_step=2, with_step=3, on_keys=["path"])
    → entries di step 2 che hanno path = qualche entry di step 3
  filter_lists(op="overlap", from_step=2, with_step=3)
    → entries di step 2 con [start,end] sovrapposto a entry di step 3
    (auto-detect campi temporali: start/end, started_at/finished_at,
    taken_at_iso, mtime)
  filter_lists(op="difference", from_step=2, with_step=3, on_keys=["id"])
    → entries di step 2 con id non in step 3
  filter_lists(op="union", from_step=2, with_step=3, on_keys=["sha256"])
    → unione deduplicata
  filter_lists(op="symdiff", on_keys=["date"])
    → (A\\B) ∪ (B\\A)

Determinismo §7.9: zero LLM, zero I/O. Pure compute in memoria.
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
from executor_helpers import run_stdio  # noqa: E402


_VALID_OPS = {"intersect", "union", "difference", "symdiff", "overlap", "delta"}


def _is_advanced(av, bv) -> bool:
    """True se `av` (corrente) e' "piu' avanti" di `bv` (baseline/watermark).
    Universale §7.9: ISO timestamp (stringa) e numeri si confrontano con `>`;
    fallback a confronto stringa. bv assente = nessun baseline → avanzato."""
    if av is None:
        return False
    if bv is None:
        return True
    try:
        return av > bv
    except TypeError:
        return str(av) > str(bv)

_TIME_START_FIELDS = ("start", "started_at", "taken_at_iso", "mtime_iso",
                       "fired_at", "ts")
_TIME_END_FIELDS = ("end", "finished_at", "taken_at_iso", "mtime_iso",
                     "fired_at", "ts")


def _to_epoch(v):
    """Coerce ISO string / epoch number to float epoch. None se invalido."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            pass
        try:
            import datetime as _dt
            parsed = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed.timestamp()
        except Exception:
            return None
    return None


def _entry_time_window(e: dict):
    if not isinstance(e, dict):
        return None, None
    s_val = None
    for k in _TIME_START_FIELDS:
        if k in e and e[k] is not None:
            s_val = e[k]; break
    e_val = None
    for k in _TIME_END_FIELDS:
        if k in e and e[k] is not None:
            e_val = e[k]; break
    s = _to_epoch(s_val)
    en = _to_epoch(e_val) if e_val is not None else s
    if s is None:
        return None, None
    if en is None or en < s:
        en = s
    return s, en


def _entry_key(e: dict, on_keys: list[str]):
    if not isinstance(e, dict):
        return None
    parts = []
    for k in on_keys:
        v = e.get(k)
        if v is None:
            return None
        parts.append(v)
    return tuple(parts)


def _entry_label(e: dict) -> str:
    for k in ("summary", "name", "title", "id", "path"):
        v = e.get(k)
        if v:
            return str(v)[:80]
    return "?"


def _normalize_list(arg):
    if arg is None:
        return []
    if isinstance(arg, list):
        return arg
    if isinstance(arg, dict):
        return [arg]
    return []


def invoke(args: dict) -> dict:
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args"}
    op = args.get("op")
    if not op or op not in _VALID_OPS:
        return {"ok": False,
                "error": _msg("ERR_ARG_ENUM", arg="op", allowed=", ".join(sorted(_VALID_OPS))),
                "error_class": "invalid_args"}
    entries_a = _normalize_list(args.get("entries"))
    entries_b = _normalize_list(args.get("entries_b"))

    if op == "overlap":
        # Auto-detect temporal window. Niente on_keys richiesto.
        other_windows = []
        for o in entries_b:
            s, en = _entry_time_window(o)
            if s is not None:
                other_windows.append((s, en, _entry_label(o)))
        kept = []
        for e in entries_a:
            e_s, e_en = _entry_time_window(e)
            if e_s is None:
                continue
            hits = []
            for o_s, o_en, lab in other_windows:
                if e_s <= o_en and o_s <= e_en:
                    hits.append(lab)
            if hits:
                new_e = dict(e)
                new_e["_overlap_with"] = hits[:3]
                kept.append(new_e)
        return {
            "ok": True, "op": op, "entries": kept,
            "metadata": {
                "count_a": len(entries_a),
                "count_b": len(entries_b),
                "count_out": len(kept),
                "auto_detect_temporal": True,
            },
        }

    # intersect / union / difference / symdiff: richiedono on_keys
    on_keys = args.get("on_keys")
    if isinstance(on_keys, str):
        on_keys = [on_keys]
    if not isinstance(on_keys, list) or not on_keys:
        return {"ok": False,
                "error": (
                    f"op={op!r} requires `on_keys` (list of field "
                    "names to match on). E.g. on_keys=['path'] or "
                    "on_keys=['lat','lon']"
                ),
                "error_class": "invalid_args"}
    on_keys = [str(k) for k in on_keys]

    keys_a = []
    a_by_key: dict = {}
    for e in entries_a:
        k = _entry_key(e, on_keys)
        if k is not None:
            keys_a.append(k)
            a_by_key.setdefault(k, e)
    keys_b = set()
    b_by_key: dict = {}
    for e in entries_b:
        k = _entry_key(e, on_keys)
        if k is not None:
            keys_b.add(k)
            b_by_key.setdefault(k, e)

    keys_a_set = set(keys_a)
    ordered_a = list(dict.fromkeys(keys_a))
    ordered_b = list(b_by_key)
    if op == "intersect":
        out_keys = [k for k in ordered_a if k in keys_b]
        entries_out = [a_by_key[k] for k in out_keys]
    elif op == "difference":
        out_keys = [k for k in ordered_a if k not in keys_b]
        entries_out = [a_by_key[k] for k in out_keys]
    elif op == "union":
        entries_out = list(a_by_key.values())
        for k in ordered_b:
            if k in keys_a_set:
                continue
            entries_out.append(b_by_key[k])
    elif op == "symdiff":
        only_a = [k for k in ordered_a if k not in keys_b]
        only_b = [k for k in ordered_b if k not in keys_a_set]
        entries_out = [a_by_key[k] for k in only_a]
        entries_out.extend(b_by_key[k] for k in only_b)
    elif op == "delta":
        # DELTA universale per monitor (§7.9 deterministico): da A (snapshot
        # corrente) ritorna ciò che e' NUOVO o CAMBIATO rispetto a B (baseline/
        # stato salvato). NUOVO = chiave in A non in B. CAMBIATO = chiave in
        # entrambe ma A[delta_field] > B[delta_field] (watermark avanzato, es.
        # updated_at). Senza delta_field = solo nuovi (come difference).
        # Risolve dedup anti-duplicato di QUALSIASI ciclo di monitoraggio
        # (github/mail/rss): "processa solo ciò che ha attività nuova dal
        # watermark"; copre anche la RIAPERTURA (replica utente → updated_at
        # avanza → torna nel delta).
        delta_field = args.get("delta_field")
        out_keys = []
        for k in keys_a:
            if k in out_keys:
                continue
            if k not in keys_b:
                out_keys.append(k)               # nuovo
            elif delta_field and _is_advanced(
                    a_by_key[k].get(delta_field), b_by_key[k].get(delta_field)):
                out_keys.append(k)               # cambiato dal watermark
        entries_out = [a_by_key[k] for k in out_keys]
    else:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="op", reason=repr(op)),
                "error_class": "invalid_args"}
    return {
        "ok": True, "op": op, "entries": entries_out,
        "metadata": {
            "count_a": len(entries_a),
            "count_b": len(entries_b),
            "count_out": len(entries_out),
            "on_keys": on_keys,
        },
    }


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
