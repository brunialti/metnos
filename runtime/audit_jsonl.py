# SPDX-License-Identifier: AGPL-3.0-only
"""audit_jsonl.py — scrittura append-only JSONL per i log di audit (§7.2: una
sola definizione del primitivo, era duplicato ~6 volte in synt/promoter/i18n/
change-intent/verifier/review).

§2.8 (no silent failure): `fsync=True` di DEFAULT — un record di audit scritto
DEVE sopravvivere a un crash, altrimenti l'audit mentirebbe ("ho loggato" ma la
riga è persa). Chi ha un motivo di performance per non-durabilità passa
`fsync=False` esplicitamente.

`json.dumps` canonico: `ensure_ascii=False` (UTF-8 leggibile), `sort_keys=True`
(deterministico §7.9), `default=str` (un valore non serializzabile diventa
stringa invece di sollevare e far perdere la riga — sempre §2.8).

`'a'` + fsync su POSIX è atomico per linee < PIPE_BUF (~4KB): le righe di audit
sono brevi, quindi safe-by-construction (niente interleaving fra processi).
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def append_jsonl(path, records, *, fsync: bool = True) -> Path:
    """Appende uno o più record (dict o lista di dict) come righe JSONL a `path`.

    Crea la dir se manca. Ritorna il Path scritto. NON cattura le OSError: il
    chiamante decide se l'audit è best-effort (try/except attorno) o meno.
    """
    if isinstance(records, dict):
        records = (records,)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True,
                               default=str) + "\n")
        if fsync:
            f.flush()
            os.fsync(f.fileno())
    return p


def append_bounded_jsonl(
    path,
    records,
    *,
    max_bytes: int,
    backup_count: int,
    mode: int = 0o600,
    fsync: bool = True,
) -> Path:
    """Append JSONL with cross-process, size-bounded rotation.

    A stable sidecar lock serializes the size check, rename and append across
    processes.  At most ``backup_count`` complete generations are retained;
    the current file may exceed ``max_bytes`` only by one indivisible record.
    This is for telemetry/audit history, never for state stores whose readers
    require every historical row in one file.
    """
    if max_bytes < 1 or backup_count < 1:
        raise ValueError("max_bytes and backup_count must be positive")
    if isinstance(records, dict):
        records = (records,)
    payload = b"".join(
        (json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
         + "\n").encode("utf-8")
        for rec in records
    )
    if not payload:
        return Path(path)

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_path = p.with_name(f".{p.name}.rotation.lock")
    lock_fd = os.open(
        lock_path, os.O_CREAT | os.O_RDWR | _NOFOLLOW, mode)
    try:
        os.fchmod(lock_fd, mode)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        current_size = p.stat().st_size if p.exists() else 0
        if current_size and current_size + len(payload) > max_bytes:
            oldest = p.with_name(f"{p.name}.{backup_count}")
            try:
                oldest.unlink()
            except FileNotFoundError:
                pass
            for index in range(backup_count - 1, 0, -1):
                source = p.with_name(f"{p.name}.{index}")
                if source.exists():
                    os.replace(source, p.with_name(f"{p.name}.{index + 1}"))
            os.replace(p, p.with_name(f"{p.name}.1"))

        out_fd = os.open(
            p, os.O_CREAT | os.O_APPEND | os.O_WRONLY | _NOFOLLOW, mode)
        try:
            os.fchmod(out_fd, mode)
            view = memoryview(payload)
            while view:
                written = os.write(out_fd, view)
                if written < 1:
                    raise OSError("short write while appending audit JSONL")
                view = view[written:]
            if fsync:
                os.fsync(out_fd)
        finally:
            os.close(out_fd)
        if fsync:
            _fsync_directory(p.parent)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
    return p
