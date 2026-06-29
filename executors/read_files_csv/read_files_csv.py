#!/usr/bin/env python3
"""read_files_csv — executor di Metnos v1.1.

Legge file CSV e ritorna le righe come lista di dict (header → valore).
Vettoriale: una sola call accetta una lista di paths.

Backend: stdlib `csv` (RFC 4180 compatibile, autodetect dialect).
Niente dipendenze esterne.

Contratto:
    stdin: JSON {paths: list[str], delimiter?: str (auto), encoding?: str = "utf-8",
                 has_header?: bool = true, max_rows?: int = 10000}
    stdout: JSON {ok, ok_count, fail_count, entries, failed}
            entries[i] = {path, headers, rows: list[dict|list], row_count}
"""
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from executor_helpers import coerce_cap  # noqa: E402


def _read_one(path_arg, delimiter, encoding, has_header, max_rows):
    path = Path(os.path.expanduser(path_arg)).resolve()
    if not path.exists():
        return None, "path does not exist"
    if not path.is_file():
        return None, "path is not a file"
    try:
        with open(path, "r", encoding=encoding, newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            if delimiter is None:
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    delim = dialect.delimiter
                except csv.Error:
                    delim = ","
            else:
                delim = delimiter
            reader = csv.reader(f, delimiter=delim)
            rows = []
            headers = None
            for i, raw in enumerate(reader):
                if i == 0 and has_header:
                    headers = [h.strip() for h in raw]
                    continue
                if has_header and headers is not None:
                    rows.append({headers[j] if j < len(headers) else f"col_{j}": v for j, v in enumerate(raw)})
                else:
                    rows.append(raw)
                if len(rows) >= max_rows:
                    break
            # Sondaggio post-cap per available_total reale (sufficiente
            # contare le righe rimanenti, non parsearle).
            truncated = False
            available_total = len(rows)
            try:
                tail = sum(1 for _ in reader)
                if tail > 0:
                    truncated = True
                    available_total = len(rows) + tail
            except Exception:
                pass
            return {
                "path": str(path),
                "headers": headers,
                "rows": rows,
                "row_count": len(rows),
                "delimiter": delim,
                "_truncated": truncated,
                "_available_total": available_total,
            }, None
    except UnicodeDecodeError as e:
        return None, f"encoding error: {e}"
    except OSError as e:
        return None, f"os error: {e}"
    except Exception as e:
        return None, f"csv parse error: {e}"


def invoke(args):
    paths = args.get("paths")
    delimiter = args.get("delimiter")
    encoding = args.get("encoding") or "utf-8"
    has_header = bool(args.get("has_header", True))
    max_rows = coerce_cap(args, "max_rows", 10000, maximum=1000000)

    if not isinstance(paths, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="paths")}

    entries, failed = [], []
    aggregate_truncated = False
    aggregate_used = 0
    aggregate_available = 0
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p:
            failed.append({"index": i, "path": p, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="path")})
            continue
        entry, err = _read_one(p, delimiter, encoding, has_header, max_rows)
        if err:
            failed.append({"index": i, "path": str(Path(os.path.expanduser(p)).resolve()), "error": err})
            continue
        if entry.pop("_truncated", False):
            aggregate_truncated = True
        aggregate_used += entry.get("row_count", 0)
        aggregate_available += entry.pop("_available_total", entry.get("row_count", 0))
        entries.append(entry)

    out = {
        "ok": len(failed) == 0,
        "ok_count": len(entries),
        "fail_count": len(failed),
        "entries": entries,
        "failed": failed,
    }
    if aggregate_truncated:
        out["truncated"] = True
        out["truncated_what"] = "riga"
        out["used"] = aggregate_used
        out["available_total"] = aggregate_available
        out["cap_field"] = "max_rows"
        out["cap_value"] = max_rows
    return out


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
