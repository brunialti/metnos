#!/usr/bin/env python3
"""sort_entries — ordina una lista di entries per un campo, opzionale top-K.

Spec:
- input deve essere una `entries: list`;
- ogni elemento e' un dizionario flat (chiavi top-level, niente path dotted);
- il sort agisce sul valore della chiave indicata da `by`;
- confronto: max/min per numeri (int, float), lessicografico per stringhe.
- entries senza il campo o con valore non comparabile (None, list, dict, ...)
  finiscono in coda (sort stable, no errore).

Pure compute, no I/O esterna.

Tipico uso in pipeline:
    find_files → sort_entries(by="size", desc=True, top=5) → final_answer
    read_messages → sort_entries(by="size", desc=True) → top-N final_answer
    find_dirs → sort_entries(by="file_count", desc=True, top=3) → final_answer

Contratto:
    stdin:  JSON {entries: list[dict], by: str, desc?: bool, top?: int}
    stdout: JSON {ok, entries: list[dict], sorted_by, desc, count, total_input}
"""
from __future__ import annotations

import json
import sys


def invoke(args):
    entries = args.get("entries")
    by = args.get("by")
    desc = bool(args.get("desc", False))
    top = args.get("top")

    if not isinstance(entries, list):
        return {"ok": False, "error": "missing or invalid 'entries' (must be a list)"}
    if not isinstance(by, str) or not by:
        return {"ok": False, "error": "missing required arg 'by' (string, name of field to sort by)"}
    if top is not None:
        if not isinstance(top, int) or top < 0:
            return {"ok": False, "error": "'top' must be a non-negative integer or null"}

    # Estrai chiave di ordinamento. Entries che non hanno il campo o
    # hanno valore non comparabile finiscono in coda (treated as -inf
    # for desc, +inf for asc) — coerente con CLAUDE.md 2.4 robustezza
    # NL→det: niente errore se una entry e' incompleta.
    def _key(e):
        if not isinstance(e, dict):
            return (1, 0)  # always last
        v = e.get(by)
        if v is None:
            return (1, 0)
        if isinstance(v, (int, float, str)):
            return (0, v)
        return (1, 0)

    sorted_entries = sorted(entries, key=_key, reverse=desc)
    total_input = len(entries)
    truncated = False
    if top is not None and 0 < top < len(sorted_entries):
        sorted_entries = sorted_entries[:top]
        truncated = True

    out = {
        "ok": True,
        "entries": sorted_entries,
        "count": len(sorted_entries),
        "total_input": total_input,
        "sorted_by": by,
        "desc": desc,
    }
    if truncated:
        # 2.7 truncation visibility: dichiarare il taglio come fatto oggettivo.
        # `truncated_intentional` segnala al runtime che il cap e' user-richiesto
        # (top esplicito) e quindi NON deve prepended cap-expand prompt.
        out["truncated"] = True
        out["truncated_what"] = "entries"
        out["used"] = len(sorted_entries)
        out["available_total"] = total_input
        out["truncated_intentional"] = True
    return out


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
