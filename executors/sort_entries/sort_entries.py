#!/usr/bin/env python3
"""sort_entries — order a list of entries by one field, optionally keep the first ones.

- `entries` is a list of flat records; `by` names the field to order by.
- Numbers compare as numbers, text as text; a field that holds dates compares
  as instants whatever their form (`record_dates`), so «the most recent» is
  the latest in time, not the last in the alphabet.
- `by="date"` (or the person's word for it) is the date of the record: its
  last change, else its making, else the date it carries. Records that carry
  no date are refused, never answered with the first row.
- Entries without the field, or with a value that cannot be compared, go last
  whatever the direction.
- `top` keeps the first N; with `group_by` the first N of each group of rows
  sharing that field's value («for each sender»), one when no number is given.

Pure compute, no external I/O.

    find_files → sort_entries(by="size", desc=True, top=5)
    read_messages → sort_entries(by="date", desc=True, top=1, group_by="from")

Contract:
    stdin:  JSON {entries, by, desc?, top?, group_by?, value_type?}
    stdout: JSON {ok, entries, count, total_input, sorted_by, desc, grouped_by?}
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
from record_dates import date_fields, holds_dates, instant  # noqa: E402


def invoke(args):
    entries = args.get("entries")
    by = args.get("by")
    desc = bool(args.get("desc", False))
    top = args.get("top")
    value_type = args.get("value_type") or "auto"
    group_by = args.get("group_by")

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
    if group_by is not None and (not isinstance(group_by, str) or not group_by.strip()):
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "invalid_group_by",
            "error": _msg("ERR_ARG_NOT_STRING", arg="group_by"),
        }
    if value_type not in {"auto", "date"}:
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "invalid_value_type",
            "error": _msg("ERR_ARG_ENUM", arg="value_type", allowed="auto, date"),
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
            from ordering_clause import is_date_term, resolve_field as _rf
            _resolved = _rf(by, entries)
            _date_asked = is_date_term(by)
        except Exception:
            _resolved, _date_asked = None, False
        if _resolved:
            by = _resolved
        elif _date_asked:
            # A date was asked for and these records carry none, or several
            # that no producer documents: choosing the first row as «the most
            # recent» would be an answer nobody computed.
            return {
                "ok": False,
                "error_class": "invalid_input",
                "error_code": "no_record_date",
                "error": _msg(
                    "ERR_UNKNOWN_FIELDS", unknown=by,
                    supported=", ".join(
                        [name for name, _role in date_fields(entries)]
                        or sorted({str(key) for entry in entries[:50]
                                   if isinstance(entry, dict) for key in entry
                                   if not str(key).startswith("_")}))),
            }

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
    # A field that holds dates is compared as instants whatever their form
    # (epoch number, ISO with or without a zone, a mail-style date): as text
    # «Fri, 13 Nov» sorts before «Mon, 02 Nov», and 23:30+00:00 before
    # 00:10+01:00 of the day after.
    dated_field = value_type == "date" or holds_dates(entries, by)
    if dated_field:
        keyed = [(instant(_val(entry)), entry) for entry in have]
        dated = [pair for pair in keyed if pair[0] is not None]
        unparsed = [entry for moment, entry in keyed if moment is None]
        dated.sort(key=lambda pair: pair[0], reverse=desc)
        unparsed.sort(key=lambda entry: str(_val(entry)).casefold(), reverse=desc)
        unparsed_count = len(unparsed)
        sorted_entries = [entry for _moment, entry in dated] + unparsed + missing
    else:
        have.sort(key=_key, reverse=desc)
        sorted_entries = have + missing
    total_input = len(entries)
    truncated = False
    group_count = None
    requested_group = group_by
    if group_by:
        # «For each …»: the same order inside every group of rows that share
        # the value of this field, and `top` counted group by group: one for
        # each when no number is given (0 keeps them all, as everywhere).
        # Groups keep the order in which the sorted list first meets them;
        # rows without the value form one group of their own.
        if entries and not any(isinstance(e, dict) and group_by in e for e in entries):
            try:
                from ordering_clause import resolve_field as _rf
                _group_field = _rf(group_by, entries)
            except Exception:
                _group_field = None
            if not _group_field:
                return {
                    "ok": False,
                    "error_class": "invalid_input",
                    "error_code": "group_field_not_found",
                    "error": _msg(
                        "ERR_UNKNOWN_FIELDS", unknown=group_by,
                        supported=", ".join(sorted(
                            {str(key) for entry in entries[:50]
                             if isinstance(entry, dict) for key in entry
                             if not str(key).startswith("_")}))),
                }
            group_by = _group_field

        def _group(entry):
            value = entry.get(group_by) if isinstance(entry, dict) else None
            if isinstance(value, str):
                return ("text", " ".join(value.split()).casefold())
            if value is None or isinstance(value, (bool, int, float)):
                return ("value", value)
            return ("json", json.dumps(value, sort_keys=True, default=str))

        groups: dict = {}
        for entry in sorted_entries:
            groups.setdefault(_group(entry), []).append(entry)
        group_count = len(groups)
        per_group = 1 if top is None else top
        kept = [rows if not per_group else rows[:per_group] for rows in groups.values()]
        grouped = [entry for rows in kept for entry in rows]
        truncated = len(grouped) < len(sorted_entries)
        sorted_entries = grouped
    elif top is not None and 0 < top < len(sorted_entries):
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
    if dated_field:
        out["value_type"] = "date"
        out["unparsed_count"] = unparsed_count
    if group_by:
        out["grouped_by"] = group_by
        out["group_count"] = group_count
        if group_by != requested_group:
            out["requested_group_by"] = requested_group
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
    # Ordering and taking a subset leave source field meanings unchanged.
    if isinstance(args.get("field_roles"), list):
        out["entry_field_roles"] = args["field_roles"]
    return out


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
