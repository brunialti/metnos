#!/usr/bin/env python3
"""
get_file_dates — executor di Metnos v1.1.

Arricchisce una lista di entries (file) con `date_epoch` + `date_source`
scegliendo in priorita':
  1. EXIF DateTimeOriginal (DateTimeDigitized, DateTime) per file immagine
  2. birth time del filesystem (stat -c %W su Linux ext4)
  3. mtime (modification time del filesystem)

Vettoriale per costruzione: accetta sempre una lista di entries (anche di
un solo elemento, o vuota). Ogni entry deve avere almeno 'path'. Se la
entry e' priva di campi temporali utili, vengono ricavati dal filesystem.

Output: stesse entries con due campi aggiunti:
  - date_epoch: float (secondi unix)
  - date_source: 'exif' | 'birth' | 'mtime' | 'unknown'

Per integrarsi con move_files: i placeholder `{date_year}`, `{date_yymmdd}`,
`{date_yyyymmdd}` di dst_template usano questo campo.

Contratto:
    stdin:  JSON con args (entries: list[dict])
    stdout: JSON {ok, ok_count, fail_count, entries, failed}
"""
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402


def _exif_date_epoch(path):
    """Ritorna (epoch, 'exif') se EXIF DateTime* disponibile e parsabile."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
    except ImportError:
        return None
    try:
        with Image.open(path) as img:
            exif = img._getexif() or {}
    except Exception:
        return None
    if not exif:
        return None
    named = {TAGS.get(k, k): v for k, v in exif.items()}
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        v = named.get(key)
        if not v or not isinstance(v, str):
            continue
        try:
            dt = datetime.datetime.strptime(v, "%Y:%m:%d %H:%M:%S")
            return dt.timestamp()
        except ValueError:
            continue
    return None


def _birth_epoch(path):
    """Ritorna birth time epoch se disponibile dal filesystem (Linux ext4 / btrfs)."""
    try:
        out = subprocess.run(
            ["stat", "-c", "%W", str(path)],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    t = out.stdout.strip()
    if not t or not t.isdigit():
        return None
    epoch = int(t)
    return float(epoch) if epoch > 0 else None


def _pick_date(path, entry):
    """Sceglie il miglior timestamp per una entry. Ritorna (epoch, source)."""
    # 1) EXIF (solo per immagini, evita di tentare su tutto)
    mime = entry.get("mime") or ""
    kind = entry.get("kind") or ""
    if kind == "image" or mime.startswith("image/"):
        epoch = _exif_date_epoch(path)
        if epoch is not None:
            return epoch, "exif"
    # 2) birth time
    epoch = _birth_epoch(path)
    if epoch is not None:
        return epoch, "birth"
    # 3) mtime (da entry o da stat)
    me = entry.get("mtime_epoch")
    if me is not None:
        try:
            return float(me), "mtime"
        except (TypeError, ValueError):
            pass
    try:
        return float(path.stat().st_mtime), "mtime"
    except OSError:
        return None, "unknown"


def invoke(args):
    entries = args.get("entries")
    if entries is None or not isinstance(entries, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="entries")}

    out_entries = []
    failed = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            failed.append({"index": i, "error": _msg("ERR_ARG_NOT_DICT", arg="entry")})
            continue
        src_arg = entry.get("path") or entry.get("src")
        if not src_arg or not isinstance(src_arg, str):
            failed.append({"index": i, "error": _msg("ERR_ARG_MISSING", arg="path")})
            continue
        path = Path(os.path.expanduser(src_arg))
        epoch, source = _pick_date(path, entry)
        new_entry = dict(entry)
        if epoch is not None:
            new_entry["date_epoch"] = epoch
            new_entry["date_source"] = source
        else:
            new_entry["date_source"] = "unknown"
        out_entries.append(new_entry)

    return {
        "ok": len(failed) == 0,
        "ok_count": len(out_entries),
        "fail_count": len(failed),
        "entries": out_entries,
        "failed": failed,
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
