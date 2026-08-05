"""Bounded, loss-aware lifecycle helpers for Metnos file logs.

The live directories stay small; old data is gzip-compressed into a separate
archive.  A source is removed only after the compressed copy has been read
back and its SHA-256/length match the source.  Archives are bounded by both
age and total bytes, so moving growth elsewhere cannot create a second leak.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Iterable

_CHUNK = 1024 * 1024
_DATED_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _stream_digest(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while block := stream.read(_CHUNK):
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def _gzip_digest(path: Path) -> tuple[str, int]:
    with gzip.open(path, "rb") as stream:
        return _stream_digest(stream)


def _fsync_dir(path: Path) -> None:
    """Persist a rename/unlink where the filesystem supports directory fsync."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _archive_destination(source: Path, archive_dir: Path) -> Path:
    match = _DATED_NAME.match(source.name)
    if match:
        year, month = match.group(1), match.group(2)
    else:
        stamp = datetime.fromtimestamp(source.stat().st_mtime)
        year, month = stamp.strftime("%Y"), stamp.strftime("%m")
    return archive_dir / year / month / f"{source.name}.gz"


def _verified_gzip_move(source: Path, destination: Path) -> tuple[int, int]:
    """Compress and remove *source* only after an exact read-back check."""
    if source.is_symlink() or not source.is_file():
        raise ValueError("source must be a regular, non-symlink file")

    destination.parent.mkdir(parents=True, exist_ok=True)
    before = source.stat()

    # Recovery after a previous run archived successfully but failed to unlink.
    if destination.is_file():
        with source.open("rb") as stream:
            source_hash, source_size = _stream_digest(stream)
        try:
            archived_hash, archived_size = _gzip_digest(destination)
        except (OSError, EOFError, gzip.BadGzipFile):
            archived_hash, archived_size = "", -1
        after = source.stat()
        if ((before.st_ino, before.st_size, before.st_mtime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns)
                and (source_hash, source_size) ==
                (archived_hash, archived_size)):
            source.unlink()
            _fsync_dir(source.parent)
            return source_size, destination.stat().st_size

    temp = destination.with_name(
        f".{destination.name}.tmp-{os.getpid()}-{time.time_ns()}")
    digest = hashlib.sha256()
    source_size = 0
    try:
        with source.open("rb") as src, temp.open("xb") as raw:
            opened = os.fstat(src.fileno())
            with gzip.GzipFile(
                    filename=source.name, mode="wb", fileobj=raw,
                    compresslevel=6, mtime=0) as zipped:
                while block := src.read(_CHUNK):
                    digest.update(block)
                    source_size += len(block)
                    zipped.write(block)
            raw.flush()
            os.fsync(raw.fileno())
            closed = os.fstat(src.fileno())
        if ((opened.st_ino, opened.st_size, opened.st_mtime_ns) !=
                (closed.st_ino, closed.st_size, closed.st_mtime_ns)):
            raise RuntimeError("source changed while it was being archived")

        archived_hash, archived_size = _gzip_digest(temp)
        if (digest.hexdigest(), source_size) != (archived_hash, archived_size):
            raise RuntimeError("compressed archive failed read-back verification")

        os.replace(temp, destination)
        os.utime(destination, ns=(before.st_atime_ns, before.st_mtime_ns))
        _fsync_dir(destination.parent)

        # Re-check the pathname as well as the already-open descriptor before
        # unlinking: a writer must not be able to replace it under the reaper.
        current = source.stat()
        if ((before.st_ino, before.st_size, before.st_mtime_ns) !=
                (current.st_ino, current.st_size, current.st_mtime_ns)):
            raise RuntimeError("source changed before archive commit")
        source.unlink()
        _fsync_dir(source.parent)
        return source_size, destination.stat().st_size
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _archives(archive_dir: Path) -> list[Path]:
    if not archive_dir.exists():
        return []
    return sorted(
        (path for path in archive_dir.rglob("*.gz")
         if path.is_file() and not path.is_symlink()),
        key=lambda path: (path.stat().st_mtime, str(path)),
    )


def _prune_archives(
    archive_dir: Path,
    *,
    now: float,
    retention_days: int,
    max_bytes: int,
) -> tuple[int, int]:
    candidates = _archives(archive_dir)
    removed = 0
    removed_bytes = 0
    if retention_days > 0:
        cutoff = now - retention_days * 86400
        for path in list(candidates):
            if path.stat().st_mtime >= cutoff:
                continue
            size = path.stat().st_size
            path.unlink()
            removed += 1
            removed_bytes += size
            candidates.remove(path)

    if max_bytes > 0:
        total = sum(path.stat().st_size for path in candidates)
        for path in candidates:
            if total <= max_bytes:
                break
            size = path.stat().st_size
            path.unlink()
            total -= size
            removed += 1
            removed_bytes += size
    if removed:
        _fsync_dir(archive_dir)
    return removed, removed_bytes


def archive_daily_logs(
    live_dir: Path | str,
    archive_dir: Path | str,
    *,
    live_days: int = 60,
    backup_live_days: int = 7,
    archive_days: int = 365,
    max_archive_bytes: int = 2 * 1024 ** 3,
    now: float | None = None,
    patterns: Iterable[str] = ("*.jsonl", "*.jsonl.bak"),
) -> dict:
    """Move old daily logs out of the live directory into a bounded archive."""
    if (live_days < 1 or backup_live_days < 1
            or archive_days < 0 or max_archive_bytes < 0):
        raise ValueError("invalid log retention policy")
    live = Path(live_dir).expanduser().resolve()
    archive = Path(archive_dir).expanduser().resolve()
    if live == archive or live in archive.parents:
        raise ValueError("archive directory must be outside the live directory")
    instant = time.time() if now is None else now
    cutoff = instant - live_days * 86400
    report: dict = {
        "archived_files": 0,
        "archived_source_bytes": 0,
        "archive_bytes_written": 0,
        "pruned_archives": 0,
        "pruned_archive_bytes": 0,
        "failures": [],
        "live_retention_days": live_days,
        "backup_live_retention_days": backup_live_days,
        "archive_retention_days": archive_days,
        "archive_max_bytes": max_archive_bytes,
    }
    if live.exists():
        sources: set[Path] = set()
        for pattern in patterns:
            sources.update(live.glob(pattern))
        for source in sorted(sources):
            try:
                source_cutoff = (instant - backup_live_days * 86400
                                 if source.name.endswith(".bak") else cutoff)
                if (source.is_symlink() or not source.is_file()
                        or source.stat().st_mtime >= source_cutoff):
                    continue
                destination = _archive_destination(source, archive)
                source_bytes, archive_bytes = _verified_gzip_move(
                    source, destination)
                report["archived_files"] += 1
                report["archived_source_bytes"] += source_bytes
                report["archive_bytes_written"] += archive_bytes
            except (OSError, EOFError, ValueError, RuntimeError) as exc:
                report["failures"].append(
                    {"file": source.name, "error": repr(exc)})

    pruned, pruned_bytes = _prune_archives(
        archive, now=instant, retention_days=archive_days,
        max_bytes=max_archive_bytes)
    report["pruned_archives"] = pruned
    report["pruned_archive_bytes"] = pruned_bytes
    return report


def rotate_plain_log(
    path: Path | str,
    archive_dir: Path | str,
    *,
    max_bytes: int,
    keep: int = 6,
    now: float | None = None,
) -> dict:
    """Rotate an inactive plain log before its producer starts."""
    if max_bytes < 1 or keep < 1:
        raise ValueError("max_bytes and keep must be positive")
    source = Path(path).expanduser().resolve()
    archive = Path(archive_dir).expanduser().resolve()
    if not source.exists() or source.stat().st_size < max_bytes:
        return {"rotated": False, "reason": "below_limit_or_missing"}
    instant = time.time() if now is None else now
    stamp = datetime.fromtimestamp(instant).strftime("%Y%m%d-%H%M%S")
    destination = archive / f"{source.name}.{stamp}.gz"
    suffix = 1
    while destination.exists():
        destination = archive / f"{source.name}.{stamp}.{suffix}.gz"
        suffix += 1
    source_bytes, archive_bytes = _verified_gzip_move(source, destination)
    source.touch(mode=0o600, exist_ok=True)
    rotated = sorted(
        archive.glob(f"{source.name}.*.gz"),
        key=lambda item: (item.stat().st_mtime, item.name), reverse=True)
    removed = 0
    for old in rotated[keep:]:
        old.unlink()
        removed += 1
    return {
        "rotated": True,
        "source_bytes": source_bytes,
        "archive_bytes": archive_bytes,
        "pruned_archives": removed,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Metnos bounded log lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    rotate = subparsers.add_parser("rotate", help="rotate one inactive log")
    rotate.add_argument("--path", required=True)
    rotate.add_argument("--archive-dir", required=True)
    rotate.add_argument("--max-bytes", required=True, type=int)
    rotate.add_argument("--keep", type=int, default=6)
    args = parser.parse_args()
    if args.command == "rotate":
        result = rotate_plain_log(
            args.path, args.archive_dir,
            max_bytes=args.max_bytes, keep=args.keep)
        print(json.dumps(result, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
