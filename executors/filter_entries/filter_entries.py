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
import re
import sys


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


def invoke(args):
    entries = args.get("entries")
    if not isinstance(entries, list):
        return {"ok": False, "error": "missing or invalid arg 'entries' (must be a list of dicts)"}

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
            return {"ok": False, "error": f"invalid name_regex: {e}"}
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
    if (where_in or where_not_in) and not where_field:
        return {"ok": False,
                "error": "where_in/where_not_in/where_value richiedono where_field"}

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
        mt = e.get("mtime_epoch")
        if mtime_after is not None and (mt is None or mt < mtime_after):
            return False
        if mtime_before is not None and (mt is None or mt > mtime_before):
            return False
        if where_field is not None:
            v = e.get(where_field)
            if where_in and v not in where_in:
                return False
            if where_not_in and v in where_not_in:
                return False
        return True

    filtered = [e for e in entries if keep(e)]
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
            },
        },
    }


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
