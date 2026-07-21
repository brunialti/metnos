#!/usr/bin/env python3
"""
filter_entries — executor di Metnos v1.1.

Filtra una lista generica di "entries" (dict) in base a criteri. Pensato
per essere chained dopo fs_list / fs_find / qualunque executor che produca
una lista di oggetti con campi prevedibili (es. {name, kind, size, mime}).

E' un executor PURO: nessun I/O, nessuna capability speciale richiesta.

Tipi di filtro supportati (cumulativi: AND fra criteri diversi, OR dentro
liste di valori):

  - kind             = "image"  oppure ["image","video"]   match esatto su entry.kind
  - type             = "file"  oppure ["file","dir"]       match esatto su entry.type
  - mime_prefix      = "image/"                            entry.mime.startswith(...)
  - name_glob        = "*.jpg" oppure "*.jpg,*.png"        glob case-insensitive su entry.name
  - name_regex       = "^IMG_.*\\.(jpe?g|png)$"             regex case-insensitive su entry.name
  - size_min         = 1024                                 byte min (incluso)
  - size_max         = 10485760                             byte max (incluso)
  - mtime_after      = "2026-01-01"                         ISO date/datetime
  - mtime_before     = "2026-12-31"                         ISO date/datetime
  - where_field      = "relevance"   nome di un campo qualunque della entry
  - where_in         = ["high","medium"]   keep se entry[where_field] e' in lista
  - where_not_in     = ["junk","low"]      keep se entry[where_field] NON e' in lista
  - where_value      = "high"           shortcut per where_in=["high"]

Contratto:
    stdin:  JSON con args (entries: list[dict], + criteri)
    stdout: JSON {ok, entries, metadata: {count_in, count_out, dropped}}
"""
import datetime as _dt
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402


def _ensure_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x is not None]
    return [v]


def _parse_compound_pattern(value):
    """str con virgole/pipe -> list[str]; lista -> lista; None -> []."""
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, list):
        for v in value:
            out.extend(_parse_compound_pattern(v))
        return out
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return out
        parts = re.split(r"[,|]", s) if any(sep in s for sep in (",", "|")) else [s]
        for p in parts:
            p = p.strip()
            if p:
                out.append(p)
    return out


def _parse_iso_to_epoch(s):
    if not s:
        return None
    try:
        # date pure ('2026-01-01') o datetime ('2026-01-01T10:00:00')
        if "T" not in s and " " not in s:
            d = _dt.date.fromisoformat(s)
            return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc).timestamp()
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_TIME_START_FIELDS = ("start", "started_at", "taken_at_iso", "mtime_iso",
                       "fired_at", "ts")
_TIME_END_FIELDS = ("end", "finished_at", "taken_at_iso", "mtime_iso",
                     "fired_at", "ts")


def _to_epoch(v):
    """Coerce ISO string / epoch number to float epoch. Return None se invalid."""
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


def _entry_window(e, field_start: str | None = None,
                    field_end: str | None = None):
    """Estrae (start_epoch, end_epoch) da una entry. Field-name esplicito
    o autodetect tra `_TIME_START_FIELDS`/`_TIME_END_FIELDS`. Se solo
    start (foto/file istantanei), end=start."""
    if not isinstance(e, dict):
        return None, None
    s_val = None
    if field_start:
        s_val = e.get(field_start)
    else:
        for k in _TIME_START_FIELDS:
            if k in e and e[k] is not None:
                s_val = e[k]; break
    e_val = None
    if field_end:
        e_val = e.get(field_end)
    else:
        for k in _TIME_END_FIELDS:
            if k in e and e[k] is not None:
                e_val = e[k]; break
    s_epoch = _to_epoch(s_val)
    e_epoch = _to_epoch(e_val) if e_val is not None else s_epoch
    if s_epoch is None:
        return None, None
    if e_epoch is None or e_epoch < s_epoch:
        e_epoch = s_epoch
    return s_epoch, e_epoch


def _extract_time_windows(entries: list, field_start: str | None = None,
                            field_end: str | None = None):
    """Per ogni entry estrae (start_epoch, end_epoch, label). Saltate
    quelle senza time info."""
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        s, en = _entry_window(e, field_start, field_end)
        if s is None:
            continue
        label = (e.get("summary") or e.get("name")
                 or e.get("title") or e.get("id") or "?")
        out.append((s, en, str(label)[:80]))
    return out


def invoke(args):
    entries = args.get("entries")
    if not isinstance(entries, list):
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "entries_not_list",
            "error": _msg("ERR_ARG_NOT_LIST_OF", arg="entries", of="dicts"),
        }

    kinds = _ensure_list(args.get("kind"))
    types = _ensure_list(args.get("type"))
    mime_prefix = args.get("mime_prefix")
    name_globs = _parse_compound_pattern(args.get("name_glob"))
    name_regex_str = args.get("name_regex")
    name_regex = None
    if name_regex_str:
        try:
            name_regex = re.compile(name_regex_str, re.IGNORECASE)
        except re.error as e:
            return {
                "ok": False,
                "error_class": "invalid_input",
                "error_code": "invalid_name_regex",
                "error": _msg("ERR_ARG_INVALID", arg="name_regex", reason=str(e)),
            }
    size_min = args.get("size_min")
    size_max = args.get("size_max")
    mtime_after = _parse_iso_to_epoch(args.get("mtime_after"))
    mtime_before = _parse_iso_to_epoch(args.get("mtime_before"))

    # Filtro generico per campo arbitrario. where_field nomina la chiave da
    # consultare sulla entry; where_in/where_not_in/where_value sono i valori
    # ammessi/esclusi. Pensato per filtrare su campi arricchiti da executor
    # upstream (es. relevance prodotta da classify_entries).
    where_field = args.get("where_field")
    where_in = _ensure_list(args.get("where_in"))
    where_not_in = _ensure_list(args.get("where_not_in"))
    if not where_in and args.get("where_value") is not None:
        where_in = [args.get("where_value")]
    # Operatori di stringa su where_field arbitrario (15/5/2026):
    # bug live "trova appuntamenti HLT" → LLM passava where_in=["HLT*"]
    # pensando wildcard; where_in e' match esatto. Aggiunto starts_with,
    # contains, glob, regex per coprire i pattern naturali. Case-insensitive
    # di default (coerente con name_glob/name_regex).
    where_starts_with = args.get("where_starts_with")
    where_contains = args.get("where_contains")
    where_glob = args.get("where_glob")
    where_regex_str = args.get("where_regex")
    where_regex_re = None
    if where_regex_str:
        try:
            where_regex_re = re.compile(where_regex_str, re.IGNORECASE)
        except re.error as e:
            return {
                "ok": False,
                "error_class": "invalid_input",
                "error_code": "invalid_where_regex",
                "error": _msg("ERR_ARG_INVALID", arg="where_regex", reason=str(e)),
            }
    _has_where_str_op = any(x is not None for x in (
        where_starts_with, where_contains, where_glob, where_regex_str))
    if (where_in or where_not_in or _has_where_str_op) and not where_field:
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "missing_where_field",
            "error": _msg("ERR_FILTER_WHERE_FIELD"),
        }

    def keep(e):
        if not isinstance(e, dict):
            return False
        if kinds and e.get("kind") not in kinds:
            return False
        if types and e.get("type") not in types:
            return False
        if mime_prefix and not (e.get("mime") or "").startswith(mime_prefix):
            return False
        name = e.get("name") or ""
        if name_globs:
            nlower = name.lower()
            if not any(fnmatch.fnmatchcase(nlower, g.lower()) for g in name_globs):
                return False
        if name_regex and not name_regex.search(name):
            return False
        size = e.get("size")
        if size_min is not None and (size is None or size < size_min):
            return False
        if size_max is not None and (size is None or size > size_max):
            return False
        # I producer filesystem canonici non erano coerenti sul nome:
        # `list_dirs` espone mtime_epoch, `find_files` espone mtime. Il filtro
        # temporale deve consumare entrambi senza costringere il planner a una
        # trasformazione artificiale (turn live 67d22e8c). ISO resta ammesso
        # per producer esterni; valori non interpretabili vengono esclusi in
        # modo deterministico quando un bound è richiesto.
        mt = e.get("mtime_epoch")
        if mt is None:
            mt = e.get("mtime")
        if mt is not None and not isinstance(mt, (int, float)):
            try:
                mt = float(mt)
            except (TypeError, ValueError):
                mt = _parse_iso_to_epoch(mt)
        if mtime_after is not None and (mt is None or mt < mtime_after):
            return False
        if mtime_before is not None and (mt is None or mt > mtime_before):
            return False
        if where_field is not None:
            v = e.get(where_field)
            # where_in / where_not_in: match esatto, MA tollera wildcard glob
            # quando un valore contiene '*' o '?' (15/5/2026 §7.3). Bug live:
            # LLM passa where_in=["HLT*"] pensando wildcard; il match esatto
            # falliva. Applichiamo fnmatch case-insensitive sul valore stringa.
            v_str = str(v) if v is not None else ""
            v_lower = v_str.lower()
            # Campo-LISTA (es. category_hints=['list','bulk'], tags, labels):
            # match per INTERSEZIONE — keep se UN elemento della lista matcha un
            # pattern. Generale §7.3 (non solo mail). Scalare: comportamento
            # invariato (match esatto / glob su str(v)).
            def _matches_any(patterns):
                if isinstance(v, list):
                    vl = [str(x).lower() for x in v if x is not None]
                    for pat in patterns:
                        p = str(pat).lower()
                        glob = ("*" in p or "?" in p)
                        for x in vl:
                            if fnmatch.fnmatchcase(x, p) if glob else x == p:
                                return True
                    return False
                for pat in patterns:
                    p = str(pat)
                    if "*" in p or "?" in p:
                        if fnmatch.fnmatchcase(v_lower, p.lower()):
                            return True
                    elif v == pat or v_str == p:
                        return True
                return False
            if where_in and not _matches_any(where_in):
                return False
            if where_not_in and _matches_any(where_not_in):
                return False
            # Operatori di stringa su where_field (15/5/2026).
            # Coerce a string lowercase per case-insensitive matching.
            v_str = str(v) if v is not None else ""
            v_lower = v_str.lower()
            if where_starts_with is not None:
                if not v_lower.startswith(str(where_starts_with).lower()):
                    return False
            if where_contains is not None:
                if str(where_contains).lower() not in v_lower:
                    return False
            if where_glob is not None:
                if not fnmatch.fnmatchcase(v_lower, str(where_glob).lower()):
                    return False
            if where_regex_re is not None and not where_regex_re.search(v_str):
                return False
        return True

    filtered = [e for e in entries if keep(e)]

    # Temporal overlap filter (15/5/2026 §7.3): AND fra `filtered` e
    # `overlap_entries` su finestra temporale di ogni entry. Pattern
    # general-purpose per pipeline "due liste filtrate, intersezione".
    # Esempio: read_events → filter HLT (lista A) + filter MNM (lista B)
    # → filter overlap A vs B → eventi A che si sovrappongono con B.
    #
    # Time window di una entry: (start, end). Field estratti in ordine:
    # 'start'/'end' (events), 'started_at'/'finished_at' (history task),
    # 'taken_at_iso' (foto: punto, end=start), 'mtime' (file: punto).
    # Parsing tollerante (ISO, epoch).
    overlap_entries = args.get("overlap_entries")
    overlap_field_start = args.get("overlap_field_start") or None
    overlap_field_end = args.get("overlap_field_end") or None
    n_temporal_dropped = 0
    if isinstance(overlap_entries, list) and overlap_entries:
        other_windows = _extract_time_windows(
            overlap_entries, overlap_field_start, overlap_field_end,
        )
        if other_windows:
            kept_temporal: list[dict] = []
            for e in filtered:
                e_start, e_end = _entry_window(
                    e, overlap_field_start, overlap_field_end,
                )
                if e_start is None:
                    continue  # no temporal info → exclude (deterministic)
                hits = []
                for o_start, o_end, o_label in other_windows:
                    if e_start <= o_end and o_start <= e_end:
                        hits.append(o_label)
                if hits:
                    e_copy = dict(e)
                    e_copy["_overlap_with"] = hits[:3]
                    kept_temporal.append(e_copy)
            n_temporal_dropped = len(filtered) - len(kept_temporal)
            filtered = kept_temporal

    return {
        "ok": True,
        "entries": filtered,
        "metadata": {
            "count_in": len(entries),
            "count_out": len(filtered),
            "dropped": len(entries) - len(filtered),
            "criteria": {
                "kind": kinds or None,
                "type": types or None,
                "mime_prefix": mime_prefix,
                "name_glob": name_globs or None,
                "name_regex": name_regex_str,
                "size_min": size_min,
                "size_max": size_max,
                "mtime_after": args.get("mtime_after"),
                "mtime_before": args.get("mtime_before"),
                "where_field": where_field,
                "where_in": where_in or None,
                "where_not_in": where_not_in or None,
                "where_starts_with": where_starts_with,
                "where_contains": where_contains,
                "where_glob": where_glob,
                "where_regex": where_regex_str,
                "overlap_dropped": n_temporal_dropped,
            },
        },
    }


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
