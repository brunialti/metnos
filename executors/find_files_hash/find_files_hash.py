#!/usr/bin/env python3
"""find_files_hash — trova duplicati esatti senza materializzare il corpus.

La ricerca e' completa per default. ``max_results`` limita soltanto le
relazioni restituite alla chat; non limita mai l'insieme confrontato.

Per evitare I/O inutile, i file vengono prima raggruppati per dimensione,
poi confrontati tramite una piccola impronta campionata. SHA-256 viene
calcolato soltanto per i file che superano entrambi i filtri. L'impronta
campionata non decide mai che due file sono uguali: puo' soltanto escludere
un confronto, quindi il risultato finale resta esatto.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(parent / "runtime") for parent in Path(__file__).resolve().parents
    if (parent / "runtime" / "config.py").is_file()))

from executor_helpers import run_stdio  # noqa: E402
from executor_workers import (  # noqa: E402
    assigned_workers,
    map_ordered,
    worker_budget,
)
from messages import get as _msg  # noqa: E402
from parallel_walk import parallel_walk  # noqa: E402
from path_alias import resolve_path_with_alias  # noqa: E402


_HASH_CHUNK_BYTES = 1024 * 1024
_SAMPLE_CHUNK_BYTES = 16 * 1024
_MAX_IO_WORKERS = 32
_CACHE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class _FileStamp:
    """Identita' osservata durante la scansione, usata per la cache sicura."""

    path: Path
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int

    @property
    def cache_key(self) -> str:
        lexical = os.path.normcase(os.path.abspath(str(self.path)))
        return hashlib.sha256(os.fsencode(lexical)).hexdigest()

    @property
    def signature(self) -> tuple[int, int, int, int, int]:
        return (self.size, self.mtime_ns, self.ctime_ns,
                self.device, self.inode)


def _cache_scope(args: dict) -> str:
    """Namespace opaco e stabile: nessuna identita' utente finisce su disco."""
    email = str(args.get("_actor_email") or "").strip().casefold()
    actor = str(args.get("_actor") or "").strip().casefold()
    identity = email or actor or "local"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _cache_path(scope: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME")
                or Path.home() / ".cache")
    root = Path(os.environ.get("METNOS_USER_CACHE") or (base / "metnos"))
    return root / "file_hashes" / f"file_hashes-{scope}.sqlite"


def _open_cache(scope: str) -> sqlite3.Connection | None:
    """Apre la cache best-effort; l'assenza non riduce la correttezza."""
    if os.environ.get("METNOS_FIND_HASH_CACHE", "1") == "0":
        return None
    try:
        path = _cache_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS file_digests (
                path_key TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                ctime_ns INTEGER NOT NULL,
                device INTEGER NOT NULL,
                inode INTEGER NOT NULL,
                sample_version INTEGER NOT NULL,
                sample_digest TEXT,
                sha256 TEXT,
                updated_at INTEGER NOT NULL
            )
        """)
        return connection
    except (OSError, sqlite3.Error):
        return None


def _cache_rows(connection: sqlite3.Connection | None) -> dict[str, tuple]:
    if connection is None:
        return {}
    try:
        return {
            row[0]: tuple(row[1:])
            for row in connection.execute(
                "SELECT path_key,size,mtime_ns,ctime_ns,device,inode,"
                "sample_version,sample_digest,sha256 FROM file_digests"
            )
        }
    except sqlite3.Error:
        return {}


def _cached_digests(
        stamp: _FileStamp, rows: dict[str, tuple]) -> tuple[str | None, str | None]:
    row = rows.get(stamp.cache_key)
    if row is None:
        return None, None
    signature = tuple(int(value) for value in row[:5])
    if signature != stamp.signature or int(row[5]) != _CACHE_SCHEMA_VERSION:
        return None, None
    return row[6], row[7]


def _cache_store(
        connection: sqlite3.Connection | None,
        stamps: list[_FileStamp],
        samples: dict[str, str],
        hashes: dict[str, str],
) -> None:
    """Aggiorna in una transazione; nessun path in chiaro viene persistito."""
    if connection is None:
        return
    now = int(time.time())
    rows = []
    for stamp in stamps:
        key = stamp.cache_key
        sample = samples.get(key)
        digest = hashes.get(key)
        if sample is None and digest is None:
            continue
        rows.append((
            key, *stamp.signature, _CACHE_SCHEMA_VERSION,
            sample, digest, now,
        ))
    if not rows:
        return
    try:
        connection.executemany("""
            INSERT INTO file_digests (
                path_key,size,mtime_ns,ctime_ns,device,inode,
                sample_version,sample_digest,sha256,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(path_key) DO UPDATE SET
                size=excluded.size,
                mtime_ns=excluded.mtime_ns,
                ctime_ns=excluded.ctime_ns,
                device=excluded.device,
                inode=excluded.inode,
                sample_version=excluded.sample_version,
                sample_digest=excluded.sample_digest,
                sha256=excluded.sha256,
                updated_at=excluded.updated_at
        """, rows)
        connection.commit()
    except sqlite3.Error:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass


def _parse_patterns(value) -> list[str]:
    """Normalizza stringa/lista e separatori naturali in glob atomici."""
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_parse_patterns(item))
        return out
    if not isinstance(value, str):
        return []
    return [part.strip() for part in re.split(r"[,|]", value)
            if part.strip()]


def _nonnegative_int(args: dict, key: str, default: int) -> tuple[int | None, dict | None]:
    raw = args.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = -1
    if isinstance(raw, bool) or value < 0:
        return None, {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "arg_invalid",
            "error": _msg("ERR_ARG_NOT_NONNEGATIVE_INT", arg=key),
        }
    return value, None


def _matches(name: str, patterns: list[str], case_sensitive: bool) -> bool:
    if case_sensitive:
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
    folded = name.casefold()
    return any(fnmatch.fnmatchcase(folded, pattern.casefold())
               for pattern in patterns)


def _sha256_stable(path: Path, expected_size: int) -> tuple[str | None, str | None]:
    """Calcola SHA-256 e rifiuta un file cambiato durante la lettura."""
    try:
        before = path.stat()
        if int(before.st_size) != expected_size:
            return None, "file_changed_before_hash"
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
        after = path.stat()
        if (int(after.st_size) != expected_size
                or after.st_mtime_ns != before.st_mtime_ns):
            return None, "file_changed_during_hash"
        return digest.hexdigest(), None
    except PermissionError:
        return None, "permission_denied"
    except OSError as exc:
        return None, str(exc) or type(exc).__name__


def _sample_digest_stable(
        path: Path, expected_size: int) -> tuple[str | None, str | None]:
    """Impronta economica di inizio, centro e fine del file.

    E' soltanto un filtro negativo: file con impronte diverse non possono
    essere identici; quelli con la stessa impronta vengono comunque letti per
    intero da ``_sha256_stable``. Una collisione qui costa quindi I/O in piu',
    ma non puo' generare un falso duplicato.
    """
    try:
        before = path.stat()
        if int(before.st_size) != expected_size:
            return None, "file_changed_before_sample"

        positions = [0]
        if expected_size > 2 * _SAMPLE_CHUNK_BYTES:
            positions.append(max(
                0, expected_size // 2 - _SAMPLE_CHUNK_BYTES // 2))
        if expected_size > _SAMPLE_CHUNK_BYTES:
            positions.append(expected_size - _SAMPLE_CHUNK_BYTES)
        positions = sorted(set(positions))

        digest = hashlib.blake2b(digest_size=16)
        digest.update(expected_size.to_bytes(8, "big", signed=False))
        with path.open("rb") as handle:
            for position in positions:
                handle.seek(position)
                expected_read = min(
                    _SAMPLE_CHUNK_BYTES, expected_size - position)
                chunk = handle.read(expected_read)
                if len(chunk) != expected_read:
                    return None, "file_changed_during_sample"
                digest.update(position.to_bytes(8, "big", signed=False))
                digest.update(chunk)

        after = path.stat()
        if (int(after.st_size) != expected_size
                or after.st_mtime_ns != before.st_mtime_ns):
            return None, "file_changed_during_sample"
        return digest.hexdigest(), None
    except PermissionError:
        return None, "permission_denied"
    except OSError as exc:
        return None, str(exc) or type(exc).__name__


def _digest_jobs(jobs, digest_fn, *, workers: int | None = None):
    """Esegue letture indipendenti in parallelo, preservando l'ordine."""
    if not jobs:
        return []
    central_budget = assigned_workers(item_count=len(jobs))
    requested = central_budget if workers is None else max(1, int(workers))
    worker_count = min(central_budget, requested, _MAX_IO_WORKERS, len(jobs))
    with worker_budget(worker_count):
        completed, skipped = map_ordered(
            lambda job: digest_fn(job[0], job[1]), jobs)
    if skipped:
        raise RuntimeError("hash map unexpectedly skipped work")
    return [value for _index, value in completed]


def _stream_worker_count(jobs) -> int:
    """Adatta lo streaming completo alla dimensione media dei file.

    Le impronte brevi beneficiano di molta concorrenza. Per file grandi,
    invece, pochi stream saturano gia' un NAS e altri thread aggiungono seek e
    contesa. L'override d'ambiente consente tuning specifico dell'installazione.
    """
    explicit = os.environ.get("METNOS_FIND_HASH_STREAM_WORKERS")
    if explicit:
        try:
            return max(1, min(
                assigned_workers(item_count=len(jobs)),
                _MAX_IO_WORKERS,
                int(explicit),
            ))
        except ValueError:
            pass
    if not jobs:
        return 1
    average_size = sum(size for _path, size in jobs) / len(jobs)
    if average_size >= 1024 * 1024:
        return min(assigned_workers(item_count=len(jobs)), 8)
    if average_size >= 256 * 1024:
        return min(assigned_workers(item_count=len(jobs)), 16)
    return min(assigned_workers(item_count=len(jobs)), _MAX_IO_WORKERS)


def _failure(path: Path, reason: str) -> dict:
    if reason == "permission_denied":
        message = _msg("ERR_PERMISSION_DENIED")
        code = "ERR_PERMISSION_DENIED"
    else:
        message = _msg("ERR_FILE_READ_FAILED", path=str(path))
        code = "ERR_FILE_READ_FAILED"
    return {
        "path": str(path),
        "error_class": "io_error",
        "error_code": code,
        "error": message,
        "detail": reason,
    }


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "args_not_object",
            "error": _msg("ERR_ARGS_NOT_OBJECT"),
        }

    base_path = args.get("base_path")
    if not isinstance(base_path, str) or not base_path.strip():
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "arg_missing",
            "error": _msg("ERR_ARG_MISSING", arg="base_path"),
        }

    raw_pattern = args.get("pattern")
    raw_patterns = args.get("patterns")
    patterns_valid = (
        (raw_pattern is None or isinstance(raw_pattern, str))
        and (raw_patterns is None or (
            isinstance(raw_patterns, list)
            and all(isinstance(item, str) for item in raw_patterns)
        ))
    )
    if not patterns_valid:
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "arg_invalid",
            "error": _msg("ERR_ARG_NOT_LIST_OF", arg="patterns", of="strings"),
        }
    patterns = (_parse_patterns(raw_pattern)
                + _parse_patterns(raw_patterns))
    if not patterns:
        patterns = ["*"]
    if any(not isinstance(pattern, str) or not pattern for pattern in patterns):
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "arg_invalid",
            "error": _msg("ERR_ARG_NOT_LIST_OF", arg="patterns", of="strings"),
        }

    max_depth, error = _nonnegative_int(args, "max_depth", 0)
    if error:
        return error
    max_files, error = _nonnegative_int(args, "max_files", 0)
    if error:
        return error
    max_results, error = _nonnegative_int(args, "max_results", 500)
    if error:
        return error

    base, alias_note = resolve_path_with_alias(base_path)
    if not base.exists():
        return {
            "ok": False,
            "error_class": "not_found",
            "error_code": "ERR_PATH_NOT_FOUND",
            "error": _msg("ERR_PATH_NOT_FOUND", path=str(base)),
        }
    if not base.is_dir():
        return {
            "ok": False,
            "error_class": "wrong_type",
            "error_code": "ERR_PATH_WRONG_TYPE",
            "error": _msg("ERR_PATH_WRONG_TYPE", expected="directory",
                          actual="file", path=str(base)),
        }

    recursive = bool(args.get("recursive", True))
    case_sensitive = bool(args.get("case_sensitive", False))
    by_size: dict[int, list[_FileStamp]] = defaultdict(list)
    failed: list[dict] = []
    def _to_stamp(path, _kind, _depth, directory_entry):
        stat = directory_entry.stat(follow_symlinks=False)
        return _FileStamp(
            path=path,
            size=int(stat.st_size),
            mtime_ns=int(stat.st_mtime_ns),
            ctime_ns=int(stat.st_ctime_ns),
            device=int(stat.st_dev),
            inode=int(stat.st_ino),
        )

    walk = parallel_walk(
        base,
        accept=lambda path, kind, _depth: (
            kind == "file"
            and _matches(path.name, patterns, case_sensitive)
        ),
        transform=_to_stamp,
        recursive=recursive,
        max_depth=None if max_depth == 0 else max_depth,
        max_items=max_files,
    )
    stamps = walk.items
    scan_truncated = walk.truncated
    for walk_error in walk.errors:
        failed.append(_failure(walk_error.path, walk_error.reason))
    for stamp in stamps:
        by_size[stamp.size].append(stamp)
    scanned_files = len(stamps)

    same_size_candidates = sum(
        len(stamps) for stamps in by_size.values() if len(stamps) > 1)
    cache_connection = _open_cache(_cache_scope(args))
    cache_rows = _cache_rows(cache_connection)
    samples_by_key: dict[str, str] = {}
    hashes_by_key: dict[str, str] = {}
    sample_jobs = [
        stamp
        for size in sorted(by_size)
        if len(by_size[size]) > 1
        for stamp in sorted(
            by_size[size], key=lambda item: str(item.path).casefold())
    ]
    by_sample: dict[tuple[int, str], list[Path]] = defaultdict(list)
    sampled_files = 0
    sample_cache_hits = 0
    missing_sample_jobs: list[_FileStamp] = []
    for stamp in sample_jobs:
        sample, full_hash = _cached_digests(stamp, cache_rows)
        if sample is None:
            missing_sample_jobs.append(stamp)
            continue
        sample_cache_hits += 1
        sampled_files += 1
        samples_by_key[stamp.cache_key] = sample
        if full_hash is not None:
            hashes_by_key[stamp.cache_key] = full_hash
        by_sample[(stamp.size, sample)].append(stamp.path)

    raw_sample_jobs = [
        (stamp.path, stamp.size) for stamp in missing_sample_jobs]
    for stamp, (digest, reason) in zip(
            missing_sample_jobs,
            _digest_jobs(raw_sample_jobs, _sample_digest_stable)):
        if digest is None:
            failed.append(_failure(stamp.path, reason or "sample_failed"))
            continue
        sampled_files += 1
        samples_by_key[stamp.cache_key] = digest
        by_sample[(stamp.size, digest)].append(stamp.path)

    hash_jobs = [
        next(stamp for stamp in by_size[size] if stamp.path == path)
        for (size, _sample), paths in sorted(by_sample.items())
        if len(paths) > 1
        for path in paths
    ]
    full_hash_candidates = len(hash_jobs)
    by_hash: dict[tuple[int, str], list[Path]] = defaultdict(list)
    hashed_files = 0
    hash_cache_hits = 0
    missing_hash_jobs: list[_FileStamp] = []
    for stamp in hash_jobs:
        digest = hashes_by_key.get(stamp.cache_key)
        if digest is None:
            missing_hash_jobs.append(stamp)
            continue
        hash_cache_hits += 1
        hashed_files += 1
        by_hash[(stamp.size, digest)].append(stamp.path)

    raw_hash_jobs = [(stamp.path, stamp.size) for stamp in missing_hash_jobs]
    hash_workers = _stream_worker_count(raw_hash_jobs)
    for stamp, (digest, reason) in zip(
            missing_hash_jobs,
            _digest_jobs(
                raw_hash_jobs, _sha256_stable, workers=hash_workers)):
        if digest is None:
            failed.append(_failure(stamp.path, reason or "hash_failed"))
            continue
        hashed_files += 1
        hashes_by_key[stamp.cache_key] = digest
        by_hash[(stamp.size, digest)].append(stamp.path)

    _cache_store(
        cache_connection, sample_jobs, samples_by_key, hashes_by_key)
    if cache_connection is not None:
        try:
            cache_connection.close()
        except sqlite3.Error:
            pass

    groups: list[tuple[int, str, list[Path]]] = []
    for (size, digest), paths in by_hash.items():
        if len(paths) > 1:
            groups.append((size, digest, sorted(paths, key=lambda p: str(p).casefold())))
    groups.sort(key=lambda group: (-group[0], group[1], str(group[2][0]).casefold()))

    relations: list[dict] = []
    duplicate_files_count = 0
    redundant_bytes = 0
    for size, digest, paths in groups:
        original = paths[0]
        group_count = len(paths)
        duplicate_files_count += group_count
        redundant_bytes += size * (group_count - 1)
        for duplicate in paths[1:]:
            relations.append({
                "path": str(duplicate),
                "duplicate_of": str(original),
                "size": size,
                "sha256": digest,
                "group_count": group_count,
            })

    redundant_files_count = len(relations)
    results_truncated = bool(max_results and len(relations) > max_results)
    entries = relations if not max_results else relations[:max_results]
    out = {
        "ok": not failed,
        "ok_count": len(entries),
        "fail_count": len(failed),
        "entries": entries,
        "failed": failed,
        "scanned_files": scanned_files,
        "same_size_candidates": same_size_candidates,
        "sampled_files": sampled_files,
        "sample_cache_hits": sample_cache_hits,
        "full_hash_candidates": full_hash_candidates,
        "hashed_files": hashed_files,
        "hash_cache_hits": hash_cache_hits,
        "hash_workers": hash_workers,
        "cache_enabled": cache_connection is not None,
        "duplicate_groups_count": len(groups),
        "duplicate_files_count": duplicate_files_count,
        "redundant_files_count": redundant_files_count,
        "redundant_bytes": redundant_bytes,
        "source_complete": not scan_truncated and not failed,
        "scan_truncated": scan_truncated,
        "results_truncated": results_truncated,
        "walk_workers": walk.workers,
        "visited_dirs": walk.visited_dirs,
        "metadata": {
            "base_path": str(base),
            "patterns": patterns,
            "recursive": recursive,
            "case_sensitive": case_sensitive,
            **({"alias_resolved": alias_note} if alias_note else {}),
        },
    }
    if failed:
        out["error"] = failed[0]["error"]
        if scanned_files:
            out["partial"] = True
    if scan_truncated:
        out.update({
            "truncated": True,
            "truncated_what": "source_files",
            "used": scanned_files,
            "cap_field": "max_files",
            "cap_value": max_files,
            "truncated_intentional": True,
        })
    elif results_truncated:
        out.update({
            "truncated": True,
            "truncated_what": "duplicate_files",
            "used": len(entries),
            "available_total": redundant_files_count,
            "cap_field": "max_results",
            "cap_value": max_results,
        })
    return out


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
