#!/usr/bin/env python3
"""compute_lists — operatori logici fra 2+ liste di entries (ADR 15/5/2026).

Pattern §7.3 general-purpose per pipeline "due liste, intersezione/
unione/differenza" o "una lista, aggregazione/cardinalita'". Modella
i set ops e gli aggregati standard come una singola operazione `op`
parametrica su una o piu' chiavi `on_keys`.

ESEMPI:
  compute_lists(op="intersect", from_step=2, with_step=3, on_keys=["path"])
    → entries di step 2 che hanno path = qualche entry di step 3
  compute_lists(op="overlap", from_step=2, with_step=3)
    → entries di step 2 con [start,end] sovrapposto a entry di step 3
    (auto-detect campi temporali: start/end, started_at/finished_at,
    taken_at_iso, mtime)
  compute_lists(op="difference", from_step=2, with_step=3, on_keys=["id"])
    → entries di step 2 con id non in step 3
  compute_lists(op="union", from_step=2, with_step=3, on_keys=["sha256"])
    → unione deduplicata
  compute_lists(op="count", from_step=2) → {value: N}
  compute_lists(op="sum", from_step=2, field="size") → {value: total}

Set ops (intersect/union/difference/symdiff) richiedono with_step.
Overlap e' alias-shortcut: intersect su time window auto-detect.
Aggregati (sum/prod/avg/min/max/count) operano su 1 sola lista.

Determinismo §7.9: zero LLM, zero I/O. Pure compute in memoria.
"""
from __future__ import annotations

import json
import sys


_SET_OPS = {"intersect", "union", "difference", "symdiff", "overlap"}
_AGG_OPS = {"sum", "prod", "avg", "min", "max", "count"}
_VALID_OPS = _SET_OPS | _AGG_OPS

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
            return _dt.datetime.fromisoformat(
                s.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            return None
    return None


def _entry_time_window(e: dict):
    """Auto-detect (start_epoch, end_epoch) da entry. Se solo start
    (istante: foto/file) end=start. Returns (None, None) se no info."""
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
    """Estrae tupla-chiave da entry. None se qualche key manca (entry skipped)."""
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
    """Accetta lista o singolo dict (→ lista len-1) o None (→ [])."""
    if arg is None:
        return []
    if isinstance(arg, list):
        return arg
    if isinstance(arg, dict):
        return [arg]
    return []


def invoke(args: dict) -> dict:
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object",
                "error_class": "invalid_args"}
    op = args.get("op")
    if not op or op not in _VALID_OPS:
        return {"ok": False,
                "error": f"op must be one of {sorted(_VALID_OPS)}, got {op!r}",
                "error_class": "invalid_args"}
    entries_a = _normalize_list(args.get("entries"))
    entries_b = _normalize_list(args.get("entries_b"))

    # SET OPS (binary, richiedono entries_b)
    if op in _SET_OPS:
        if not entries_b and op != "overlap":
            # overlap puo' essere alias di "intersect con se stessa" se b
            # vuota → ritorna lista vuota (no overlap senza B)
            pass
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

        # Build key sets
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
        if op == "intersect":
            out_keys = keys_a_set & keys_b
            entries_out = [a_by_key[k] for k in out_keys]
        elif op == "difference":
            out_keys = keys_a_set - keys_b
            entries_out = [a_by_key[k] for k in out_keys]
        elif op == "union":
            # Dedupe: tengo A first poi aggiungo B-only
            entries_out = list(a_by_key.values())
            for k in keys_b - keys_a_set:
                entries_out.append(b_by_key[k])
        elif op == "symdiff":
            only_a = keys_a_set - keys_b
            only_b = keys_b - keys_a_set
            entries_out = [a_by_key[k] for k in only_a]
            entries_out.extend(b_by_key[k] for k in only_b)
        else:
            return {"ok": False, "error": f"unimplemented set op {op!r}",
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

    # AGGREGATE OPS (unary, una sola lista)
    if op == "count":
        return {"ok": True, "op": op, "value": len(entries_a),
                "metadata": {"count_a": len(entries_a)}}
    field = args.get("field")
    if not field:
        return {"ok": False,
                "error": f"op={op!r} requires `field` to aggregate on",
                "error_class": "invalid_args"}
    values: list[float] = []
    for e in entries_a:
        if not isinstance(e, dict):
            continue
        v = e.get(field)
        if v is None:
            continue
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue
    if not values:
        return {"ok": True, "op": op, "value": 0, "count": 0,
                "metadata": {"count_a": len(entries_a), "field": field,
                              "_note": "no numeric values"}}
    if op == "sum":
        val = sum(values)
    elif op == "prod":
        v = 1.0
        for x in values:
            v *= x
        val = v
    elif op == "avg":
        val = sum(values) / len(values)
    elif op == "min":
        val = min(values)
    elif op == "max":
        val = max(values)
    else:
        return {"ok": False, "error": f"unimplemented agg op {op!r}",
                "error_class": "invalid_args"}
    return {"ok": True, "op": op, "value": val, "count": len(values),
            "metadata": {"count_a": len(entries_a), "field": field}}


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
