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
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402


def invoke(args):
    entries = args.get("entries")
    by = args.get("by")
    desc = bool(args.get("desc", False))
    top = args.get("top")
    value_type = args.get("value_type") or "auto"

    if not isinstance(entries, list):
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "entries_not_list",
            "error": _msg("ERR_ARG_NOT_LIST", arg="entries"),
        }
    if not isinstance(by, str) or not by:
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "missing_sort_field",
            "error": _msg("ERR_ARG_MISSING", arg="by"),
        }
    if top is not None:
        if not isinstance(top, int) or top < 0:
            return {
                "ok": False,
                "error_class": "invalid_input",
                "error_code": "invalid_top",
                "error": _msg("ERR_ARG_NOT_INT", arg="top"),
            }
    if value_type not in {"auto", "date"}:
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "invalid_value_type",
            "error": "value_type must be 'auto' or 'date'",
        }

    # §2.4 robustezza al confine NL→determinismo: `by` arriva spesso come
    # TERMINE UTENTE («mailbox», «mittente», «dimensione») e non come campo
    # reale delle entries («account», «from», «size»). Se `by` non esiste in
    # NESSUNA entry, risoluzione deterministica condivisa
    # (runtime/ordering_clause.resolve_field: esatto > famiglie sinonimi
    # chiuse > substring). Fallisce → comportamento invariato (tutte le
    # entry "missing" in coda, §2.8 onesto via sorted_by nel risultato).
    requested_by = by
    if entries and not any(isinstance(e, dict) and by in e for e in entries):
        try:
            from ordering_clause import resolve_field as _rf
            _resolved = _rf(by, entries)
        except Exception:
            _resolved = None
        if _resolved:
            by = _resolved

    # Chiave di ordinamento. Le entry senza il campo / con valore non
    # comparabile finiscono SEMPRE IN CODA, a prescindere da desc (CLAUDE.md
    # §2.4). Per ottenerlo NON usiamo reverse= sull'intera lista (invertirebbe
    # anche il sentinel, portandolo in testa con desc=True): partizioniamo
    # have/missing, ordiniamo solo `have` e accodiamo `missing`. Il bucket di
    # tipo (numeri vs stringhe) evita il TypeError su colonne a tipo misto
    # (int vs str non sono confrontabili in Python 3).
    def _val(e):
        if not isinstance(e, dict):
            return None
        v = e.get(by)
        if isinstance(v, str) and not v.strip():
            return None
        if isinstance(v, (int, float, str)):  # bool e' sottotipo int: ok 0/1
            return v
        return None

    def _key(e):
        v = _val(e)
        if isinstance(v, (int, float)):
            return (0, v)        # bucket numeri
        return (1, v)            # bucket stringhe (nessun confronto cross-tipo)

    have = [e for e in entries if _val(e) is not None]
    missing = [e for e in entries if _val(e) is None]
    unparsed_count = 0
    if value_type == "date":
        def _date_key(entry):
            value = _val(entry)
            if not isinstance(value, str):
                return None
            raw = value.strip().replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return None

        dated = [entry for entry in have if _date_key(entry) is not None]
        unparsed = [entry for entry in have if _date_key(entry) is None]
        # ISO inputs may mix offset-aware datetimes and date-only values.
        # Their normalized textual prefix is chronologically sortable and
        # avoids comparing aware and naive datetime objects.
        dated.sort(key=lambda entry: str(_val(entry)), reverse=desc)
        unparsed.sort(key=lambda entry: str(_val(entry)).casefold(), reverse=desc)
        unparsed_count = len(unparsed)
        sorted_entries = dated + unparsed + missing
    else:
        have.sort(key=_key, reverse=desc)
        sorted_entries = have + missing
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
    if value_type == "date":
        out["value_type"] = "date"
        out["unparsed_count"] = unparsed_count
    if by != requested_by:
        # Trasparenza §2.8: la chiave utente è stata risolta su un campo
        # reale diverso (es. «mailbox» → account).
        out["requested_by"] = requested_by
    if truncated:
        # 2.7 truncation visibility: dichiarare il taglio come fatto oggettivo.
        # `truncated_intentional` segnala al runtime che il cap e' user-richiesto
        # (top esplicito) e quindi NON deve prepended cap-expand prompt.
        out["truncated"] = True
        out["truncated_what"] = _msg("MSG_OBJECT_ENTRIES")
        out["used"] = len(sorted_entries)
        out["available_total"] = total_input
        out["truncated_intentional"] = True
    return out


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
