#!/usr/bin/env python3
"""group_entries — merge entries da N step diversi in una lista unica.

Tipico in pipeline split read_html / read_pdf:
    1. find_urls -> entries con content_type misto
    2. filter_entries (text/html) -> read_urls_html
    3. filter_entries (application/pdf) -> read_urls_pdf
    4. group_entries(from_steps=[2,3]) -> entries unica deduplicata per url

Args:
    from_steps: list[int]   N step di provenienza, runtime espande
    entries_lists: list[list[dict]]  alternativa diretta
    dedup_key: str = "url"  campo per deduplicazione (None = no dedup)

Output: entries=[merged...], total_in:int, dedupes:int.
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


def invoke(args: dict) -> dict:
    # Il runtime espande from_steps[i] in args["entries_lists"][i]
    # SOLO se la convenzione e' supportata. Per ora supportiamo
    # `entries_lists` come argomento esplicito (lista di liste di dict),
    # `from_steps` come informazione di provenienza che il runtime
    # serializza preventivamente (estensione futura del runtime).
    entries_lists = args.get("entries_lists")
    if entries_lists is None:
        # Fallback: se passato `entries` (lista piatta), trattiamo come 1 lista.
        entries = args.get("entries")
        if isinstance(entries, list):
            entries_lists = [entries]
        else:
            entries_lists = []

    if not isinstance(entries_lists, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="entries_lists", of="lists")}

    dedup_key = args.get("dedup_key", "url")
    if dedup_key in ("", None, 0):
        dedup_key = None

    merged: list[dict] = []
    seen: set = set()
    total_in = 0
    dedupes = 0
    for sub in entries_lists:
        if not isinstance(sub, list):
            continue
        for item in sub:
            total_in += 1
            if not isinstance(item, dict):
                # tieni elementi non-dict come stringa
                merged.append({"value": item}); continue
            if dedup_key is None:
                merged.append(item)
                continue
            key = item.get(dedup_key)
            if key is None:
                # entry senza la chiave: la teniamo (no merge possibile)
                merged.append(item)
                continue
            if key in seen:
                dedupes += 1
                continue
            seen.add(key)
            merged.append(item)

    return {
        "ok": True,
        "ok_count": len(merged),
        "fail_count": 0,
        "entries": merged,
        "total_in": total_in,
        "dedupes": dedupes,
    }


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
