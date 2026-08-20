"""Fail-closed local inventory sealing for durable-plan v1."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .schema import INVENTORY_SCHEMA_VERSION, inventory_digest, validate_inventory


class InventorySealError(ValueError):
    """A source set cannot be sealed without weakening its guarantees."""


@dataclass(frozen=True, slots=True)
class InventoryLimits:
    max_sources: int
    max_total_bytes: int
    max_depth: int

    def __post_init__(self) -> None:
        for name, value in (
            ("max_sources", self.max_sources),
            ("max_total_bytes", self.max_total_bytes),
            ("max_depth", self.max_depth),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InventorySealError(f"{name} must be a non-negative integer")
        if self.max_sources > 1_000_000:
            raise InventorySealError("max_sources exceeds the v1 limit")
        if self.max_depth > 128:
            raise InventorySealError("max_depth exceeds the v1 limit")


@dataclass(frozen=True, slots=True)
class _Candidate:
    path: Path
    locator: str


def _path_key(path: Path) -> bytes:
    return os.fsencode(str(path))


def _validate_root(raw: str | os.PathLike[str]) -> Path:
    text = os.fspath(raw)
    if not isinstance(text, str) or not text or "\x00" in text:
        raise InventorySealError("inventory roots must be non-empty local paths")
    parsed = urlsplit(text)
    if parsed.scheme:
        raise InventorySealError("URI and device-style inventory roots are not allowed")
    if text.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\")):
        raise InventorySealError("network and device-style paths are not allowed")
    path = Path(text)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InventorySealError("inventory root is missing or unreadable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise InventorySealError("an inventory root cannot be a symbolic link")
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
        raise InventorySealError("inventory roots must be regular files or directories")
    return path


def _walk_directory(root: Path, *, root_index: int, max_depth: int) -> Iterable[_Candidate]:
    stack: list[tuple[Path, int, tuple[str, ...]]] = [(root, 0, ())]
    while stack:
        directory, depth, relative_parts = stack.pop()
        if depth > max_depth:
            raise InventorySealError("inventory directory depth exceeds the admitted maximum")
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: os.fsencode(entry.name))
        except OSError as exc:
            raise InventorySealError("inventory directory changed or became unreadable") from exc
        child_directories: list[tuple[Path, int, tuple[str, ...]]] = []
        for entry in entries:
            parts = (*relative_parts, entry.name)
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth >= max_depth:
                        raise InventorySealError(
                            "inventory directory depth exceeds the admitted maximum"
                        )
                    child_directories.append((Path(entry.path), depth + 1, parts))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise InventorySealError(
                        "inventory contains a non-regular filesystem entry"
                    )
            except OSError as exc:
                raise InventorySealError("inventory entry changed during discovery") from exc
            yield _Candidate(
                path=Path(entry.path),
                locator=f"root-{root_index:04d}/{'/'.join(parts)}",
            )
        stack.extend(reversed(child_directories))


def _discover(roots: Sequence[str | os.PathLike[str]], limits: InventoryLimits) -> tuple[_Candidate, ...]:
    if isinstance(roots, (str, bytes, os.PathLike)):
        raise InventorySealError("inventory roots must be an array of paths")
    normalized = tuple(_validate_root(raw) for raw in roots)
    if not normalized:
        return ()
    absolute_keys = [os.path.abspath(os.fspath(path)) for path in normalized]
    if len(absolute_keys) != len(set(absolute_keys)):
        raise InventorySealError("inventory roots must be unique")

    candidates: list[_Candidate] = []
    for root_index, root in enumerate(normalized):
        metadata = root.lstat()
        if stat.S_ISREG(metadata.st_mode):
            candidates.append(_Candidate(root, f"root-{root_index:04d}/{root.name}"))
        else:
            candidates.extend(
                _walk_directory(root, root_index=root_index, max_depth=limits.max_depth)
            )
        if len(candidates) > limits.max_sources:
            raise InventorySealError("inventory source count exceeds the admitted maximum")
    return tuple(sorted(candidates, key=lambda item: item.locator.encode("utf-8")))


def _stable_file_digest(
    path: Path,
    *,
    chunk_bytes: int,
    before_final_stat: Callable[[Path], None] | None,
) -> tuple[str, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InventorySealError("inventory source cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise InventorySealError("inventory source is not a regular file")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, chunk_bytes)
            if not block:
                break
            digest.update(block)
        if before_final_stat is not None:
            before_final_stat(path)
        final_descriptor = os.fstat(descriptor)
        try:
            final_path = path.lstat()
        except OSError as exc:
            raise InventorySealError("inventory source disappeared while sealing") from exc
    finally:
        os.close(descriptor)

    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if identity(opened) != identity(final_descriptor) or identity(opened) != identity(final_path):
        raise InventorySealError("inventory source changed while its digest was calculated")
    return f"sha256:{digest.hexdigest()}", final_descriptor


def seal_local_inventory(
    roots: Sequence[str | os.PathLike[str]],
    *,
    device_id: str,
    limits: InventoryLimits,
    chunk_bytes: int = 1_048_576,
    before_final_stat: Callable[[Path], None] | None = None,
) -> dict[str, Any]:
    """Discover and seal regular local files without persisting absolute paths."""

    if not isinstance(device_id, str) or not device_id or len(device_id) > 128:
        raise InventorySealError("device_id must be a non-empty bounded string")
    if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes < 4096:
        raise InventorySealError("chunk_bytes must be an integer of at least 4096")

    candidates = _discover(roots, limits)
    sources: list[dict[str, Any]] = []
    total_bytes = 0
    for ordinal, candidate in enumerate(candidates):
        digest, metadata = _stable_file_digest(
            candidate.path,
            chunk_bytes=chunk_bytes,
            before_final_stat=before_final_stat,
        )
        total_bytes += int(metadata.st_size)
        if total_bytes > limits.max_total_bytes:
            raise InventorySealError("inventory byte size exceeds the admitted maximum")
        source_id = hashlib.sha256(
            f"metnos:durable-source:1\x00{device_id}\x00{candidate.locator}\x00{digest}".encode(
                "utf-8"
            )
        ).hexdigest()
        sources.append({
            "source_id": f"source_{source_id}",
            "ordinal": ordinal,
            "device_id": device_id,
            "locator_redacted": candidate.locator,
            "kind": "file",
            "size_bytes": int(metadata.st_size),
            "mtime_ns": int(metadata.st_mtime_ns),
            "content_digest": digest,
            "state": "ready",
            "accounted": True,
        })
    payload = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "sealed": True,
        "digest": inventory_digest(sources),
        "sources": sources,
    }
    validate_inventory(payload)
    return payload
