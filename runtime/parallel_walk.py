"""Visita ricorsiva parallela e deterministica del filesystem.

Il dominio viene diviso per directory. In modalita' completa le sottocartelle
scoperte entrano in una coda condivisa: ogni worker prende il prossimo task
disponibile, bilanciando automaticamente alberi sbilanciati. In modalita'
bounded si procede per livelli, cosi' il taglio resta indipendente dall'ordine
di completamento dei thread.

Il core non conosce file, immagini o codice sorgente. Il chiamante passa:

* ``accept(path, kind, depth)`` per scegliere gli elementi;
* ``transform(path, kind, depth, dir_entry)`` per produrre il risultato;
* ``descend(path, depth)`` per potare sottocartelle non pertinenti.

I callback sono eseguiti nei worker e devono quindi essere privi di stato
mutabile condiviso. L'ordine restituito e' sempre breadth/path per una visita
bounded e path globale per una visita completa.
"""
from __future__ import annotations

import os
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generic, TypeVar

from executor_workers import (
    assigned_workers,
    map_ordered,
    worker_budget,
)


T = TypeVar("T")
PathKey = tuple[str, str]
EntryKind = str  # "file" | "dir" | "symlink" | "other"
Accept = Callable[[Path, EntryKind, int], bool]
Descend = Callable[[Path, int], bool]
Transform = Callable[[Path, EntryKind, int, os.DirEntry], T]

@dataclass(frozen=True)
class WalkError:
    path: Path
    reason: str
    error_type: str


@dataclass
class WalkResult(Generic[T]):
    items: list[T]
    errors: list[WalkError]
    visited_dirs: int
    visited_entries: int
    truncated: bool
    workers: int

    @property
    def source_complete(self) -> bool:
        return not self.truncated and not self.errors


@dataclass
class _DirectoryResult(Generic[T]):
    items: list[tuple[PathKey, T]]
    children: list[Path]
    errors: list[WalkError]
    visited_entries: int


def _reason(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return "permission_denied"
    return str(exc) or type(exc).__name__


def _path_key(path: Path) -> tuple[str, str]:
    """Chiave stabile, indipendente dall'ordine di completamento dei worker."""
    raw = str(path)
    return os.path.normcase(raw).casefold(), raw


def _kind(entry: os.DirEntry) -> EntryKind:
    if entry.is_symlink():
        return "symlink"
    if entry.is_dir(follow_symlinks=False):
        return "dir"
    if entry.is_file(follow_symlinks=False):
        return "file"
    return "other"


def _scan_directory(
        directory: Path,
        directory_depth: int,
        *,
        recursive: bool,
        max_depth: int | None,
        accept: Accept,
        transform: Transform[T],
        descend: Descend,
) -> _DirectoryResult[T]:
    items: list[tuple[PathKey, T]] = []
    children: list[Path] = []
    errors: list[WalkError] = []
    try:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name.casefold())
    except OSError as exc:
        return _DirectoryResult(
            [], [], [WalkError(directory, _reason(exc), type(exc).__name__)], 0)

    entry_depth = directory_depth + 1
    for entry in entries:
        path = directory / entry.name
        try:
            kind = _kind(entry)
        except OSError as exc:
            errors.append(WalkError(path, _reason(exc), type(exc).__name__))
            continue

        if (kind == "dir" and recursive
                and (max_depth is None or entry_depth < max_depth)):
            try:
                if descend(path, entry_depth):
                    children.append(path)
            except Exception as exc:
                errors.append(WalkError(
                    path, _reason(exc), type(exc).__name__))

        try:
            selected = accept(path, kind, entry_depth)
        except Exception as exc:
            errors.append(WalkError(path, _reason(exc), type(exc).__name__))
            continue
        if not selected:
            continue
        try:
            item = transform(path, kind, entry_depth, entry)
        except Exception as exc:
            errors.append(WalkError(path, _reason(exc), type(exc).__name__))
            continue
        items.append((_path_key(path), item))

    children.sort(key=_path_key)
    return _DirectoryResult(items, children, errors, len(entries))


def _full_walk(
        root: Path,
        *,
        recursive: bool,
        max_depth: int | None,
        accept: Accept,
        transform: Transform[T],
        descend: Descend,
        workers: int,
) -> WalkResult[T]:
    mapped: list[tuple[PathKey, T]] = []
    errors: list[WalkError] = []
    visited_dirs = 0
    visited_entries = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        ready = deque([(root, 0)])
        pending = {}
        max_inflight = max(workers, workers * 4)
        while ready or pending:
            while ready and len(pending) < max_inflight:
                directory, depth = ready.popleft()
                pending[pool.submit(
                    _scan_directory,
                    directory,
                    depth,
                    recursive=recursive,
                    max_depth=max_depth,
                    accept=accept,
                    transform=transform,
                    descend=descend,
                )] = (directory, depth)
            if not pending:
                break
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            # Il completamento e' non deterministico; l'ordinamento finale
            # separa deliberatamente scheduling e semantica del risultato.
            for future in completed:
                directory, depth = pending.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # confine difensivo del worker
                    errors.append(WalkError(
                        directory, _reason(exc), type(exc).__name__))
                    continue
                visited_dirs += 1
                visited_entries += result.visited_entries
                mapped.extend(result.items)
                errors.extend(result.errors)
                for child in result.children:
                    ready.append((child, depth + 1))
    mapped.sort(key=lambda pair: pair[0])
    errors.sort(key=lambda error: _path_key(error.path))
    return WalkResult(
        items=[item for _, item in mapped],
        errors=errors,
        visited_dirs=visited_dirs,
        visited_entries=visited_entries,
        truncated=False,
        workers=workers,
    )


def _bounded_walk(
        root: Path,
        *,
        recursive: bool,
        max_depth: int | None,
        max_items: int,
        accept: Accept,
        transform: Transform[T],
        descend: Descend,
        workers: int,
) -> WalkResult[T]:
    mapped: list[tuple[PathKey, T]] = []
    errors: list[WalkError] = []
    visited_dirs = 0
    visited_entries = 0
    current: list[tuple[Path, int]] = [(root, 0)]
    truncated = False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        while current:
            current.sort(key=lambda item: _path_key(item[0]))
            next_level: list[tuple[Path, int]] = []
            level_items: list[tuple[PathKey, T]] = []
            batch_size = max(workers, workers * 4)
            for start in range(0, len(current), batch_size):
                batch = current[start:start + batch_size]
                futures = [pool.submit(
                    _scan_directory,
                    directory,
                    depth,
                    recursive=recursive,
                    max_depth=max_depth,
                    accept=accept,
                    transform=transform,
                    descend=descend,
                ) for directory, depth in batch]
                for (directory, depth), future in zip(batch, futures):
                    try:
                        result = future.result()
                    except Exception as exc:
                        errors.append(WalkError(
                            directory, _reason(exc), type(exc).__name__))
                        continue
                    visited_dirs += 1
                    visited_entries += result.visited_entries
                    level_items.extend(result.items)
                    errors.extend(result.errors)
                    next_level.extend((child, depth + 1)
                                      for child in result.children)
            level_items.sort(key=lambda pair: pair[0])
            remaining = max_items - len(mapped)
            if len(level_items) > remaining:
                mapped.extend(level_items[:remaining])
                truncated = True
                break
            mapped.extend(level_items)
            if len(mapped) == max_items:
                truncated = bool(next_level)
                break
            current = next_level

    errors.sort(key=lambda error: _path_key(error.path))
    return WalkResult(
        items=[item for _, item in mapped],
        errors=errors,
        visited_dirs=visited_dirs,
        visited_entries=visited_entries,
        truncated=truncated,
        workers=workers,
    )


def parallel_walk(
        root: str | os.PathLike,
        *,
        accept: Accept | None = None,
        transform: Transform[T] | None = None,
        descend: Descend | None = None,
        recursive: bool = True,
        max_depth: int | None = None,
        max_items: int = 0,
        workers: int | None = None,
) -> WalkResult[T | Path]:
    """Visita ``root`` applicando callback pure agli elementi trovati.

    ``max_depth=None`` significa profondita' illimitata; 0 restituisce zero
    elementi sotto la radice. ``max_items=0`` significa nessun limite e usa la
    coda dinamica. Un limite positivo abilita la variante breadth-first, il cui
    taglio e' riproducibile anche se i thread terminano in ordine diverso.
    I collegamenti simbolici possono essere restituiti ma non sono mai seguiti.
    """
    root_path = Path(root)
    if max_depth is not None and max_depth < 0:
        raise ValueError("max_depth must be >= 0 or None")
    if max_items < 0:
        raise ValueError("max_items must be >= 0")
    central_budget = assigned_workers()
    requested = central_budget if workers is None else max(1, int(workers))
    # Il chiamante puo' soltanto ridurre il budget assegnato dal runtime.
    worker_count = min(central_budget, requested)
    chosen_accept: Accept = accept or (lambda _path, _kind, _depth: True)
    chosen_descend: Descend = descend or (lambda _path, _depth: True)
    chosen_transform = transform or (
        lambda path, _kind, _depth, _entry: path)

    if max_depth == 0:
        return WalkResult([], [], 0, 0, False, worker_count)
    if max_items:
        return _bounded_walk(
            root_path,
            recursive=recursive,
            max_depth=max_depth,
            max_items=max_items,
            accept=chosen_accept,
            transform=chosen_transform,
            descend=chosen_descend,
            workers=worker_count,
        )
    return _full_walk(
        root_path,
        recursive=recursive,
        max_depth=max_depth,
        accept=chosen_accept,
        transform=chosen_transform,
        descend=chosen_descend,
        workers=worker_count,
    )


def parallel_map_ordered(items, function, *, workers: int | None = None):
    """Applica ``function`` in parallelo preservando l'ordine di ``items``.

    È il secondo stadio per operazioni che prima enumerano un albero e poi
    elaborano ogni risultato indipendentemente. Le eccezioni non vengono
    nascoste: il chiamante puo' catturarle nel proprio callback e produrre il
    contratto d'errore specifico del dominio.
    """
    materialized = list(items)
    if not materialized:
        return []
    central_budget = assigned_workers(item_count=len(materialized))
    requested = central_budget if workers is None else max(1, int(workers))
    worker_count = min(central_budget, requested, len(materialized))
    with worker_budget(worker_count):
        completed, skipped = map_ordered(function, materialized)
    if skipped:  # nessuna deadline e' usata: guardia contro drift del core.
        raise RuntimeError("parallel map unexpectedly skipped work")
    return [value for _index, value in completed]
