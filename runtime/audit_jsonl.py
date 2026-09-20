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

import json
import os
import stat
import time
from pathlib import Path
from typing import Mapping

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_REPLACE_TIMEOUT = 2.0


class AuditPathError(RuntimeError):
    """Stable fail-closed error for a redirected audit path."""

    code = "audit_path_invalid"

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"{self.code}: {path}: {detail}")


def _absolute(path: Path | str) -> Path:
    """Normalize dot segments without resolving links or reparse points."""
    return Path(os.path.abspath(os.fspath(path)))


def _is_link_like(path: Path, status: os.stat_result) -> bool:
    """Recognize POSIX links and Windows junction/reparse entries."""
    if stat.S_ISLNK(status.st_mode):
        return True
    if (
        getattr(status, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        return True
    try:
        return bool(hasattr(path, "is_junction") and path.is_junction())
    except OSError:
        return True


def _directory_chain(directory: Path) -> tuple[Path, ...]:
    absolute = _absolute(directory)
    return tuple(reversed((absolute, *absolute.parents)))


def _validate_parent(
    directory: Path,
    *,
    create: bool,
) -> tuple[Path, os.stat_result]:
    """Validate every lexical parent and return the immediate identity.

    Parent directories are created one component at a time so an existing
    link, junction, or other reparse point is never accepted as a directory.
    A second full validation after creation catches a concurrent replacement
    before any audit bytes are opened or written.
    """
    chain = _directory_chain(directory)
    for component in chain:
        try:
            current = component.lstat()
        except FileNotFoundError:
            if not create:
                raise AuditPathError(component, "parent missing")
            try:
                component.mkdir()
            except FileExistsError:
                pass
            except OSError as exc:
                raise AuditPathError(component, f"parent create failed: {exc}") from exc
            try:
                current = component.lstat()
            except OSError as exc:
                raise AuditPathError(component, f"parent unavailable: {exc}") from exc
        except OSError as exc:
            raise AuditPathError(component, f"parent unavailable: {exc}") from exc
        if _is_link_like(component, current):
            raise AuditPathError(component, "parent is a link or reparse point")
        if not stat.S_ISDIR(current.st_mode):
            raise AuditPathError(component, "parent is not a directory")

    # Do not rely on identities collected before child creation: re-observe the
    # complete chain, then retain the immediate parent for pre/open/post checks.
    immediate: os.stat_result | None = None
    for component in chain:
        try:
            current = component.lstat()
        except OSError as exc:
            raise AuditPathError(component, f"parent changed: {exc}") from exc
        if _is_link_like(component, current) or not stat.S_ISDIR(current.st_mode):
            raise AuditPathError(component, "parent changed or redirected")
        if component == chain[-1]:
            immediate = current
    assert immediate is not None
    return chain[-1], immediate


def _plain_file_status(
    path: Path,
    *,
    allow_missing: bool,
) -> os.stat_result | None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise AuditPathError(path, "file missing")
    except OSError as exc:
        raise AuditPathError(path, f"file unavailable: {exc}") from exc
    if _is_link_like(path, current):
        raise AuditPathError(path, "file is a link or reparse point")
    if not stat.S_ISREG(current.st_mode):
        raise AuditPathError(path, "file is not regular")
    return current


def _assert_parent_identity(
    parent: Path,
    expected: os.stat_result,
) -> None:
    observed_parent, current = _validate_parent(parent, create=False)
    if observed_parent != parent or not os.path.samestat(expected, current):
        raise AuditPathError(parent, "parent identity changed")


class _VerifiedOpen:
    __slots__ = ("descriptor", "path", "status", "parent", "parent_status")

    def __init__(
        self,
        descriptor: int,
        path: Path,
        status: os.stat_result,
        parent: Path,
        parent_status: os.stat_result,
    ) -> None:
        self.descriptor = descriptor
        self.path = path
        self.status = status
        self.parent = parent
        self.parent_status = parent_status


def _open_plain_file(
    path: Path | str,
    flags: int,
    mode: int,
) -> _VerifiedOpen:
    """Open one regular file and prove path identity pre/open/post."""
    target = _absolute(path)
    parent, parent_before = _validate_parent(target.parent, create=True)
    before = _plain_file_status(
        target,
        allow_missing=bool(flags & os.O_CREAT),
    )
    descriptor = -1
    try:
        descriptor = os.open(target, flags | _NOFOLLOW, mode)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise AuditPathError(target, "opened object is not regular")
        if before is not None and not os.path.samestat(before, opened):
            raise AuditPathError(target, "file identity changed while opening")
        current = _plain_file_status(target, allow_missing=False)
        if current is None or not os.path.samestat(current, opened):
            raise AuditPathError(target, "opened file is not the named file")
        _assert_parent_identity(parent, parent_before)
        return _VerifiedOpen(
            descriptor=descriptor,
            path=target,
            status=opened,
            parent=parent,
            parent_status=parent_before,
        )
    except AuditPathError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise AuditPathError(target, f"open failed: {exc}") from exc


def _assert_open_identity(opened: _VerifiedOpen) -> None:
    try:
        current_open = os.fstat(opened.descriptor)
    except OSError as exc:
        raise AuditPathError(opened.path, f"open file unavailable: {exc}") from exc
    if (
        not stat.S_ISREG(current_open.st_mode)
        or not os.path.samestat(opened.status, current_open)
    ):
        raise AuditPathError(opened.path, "open file identity changed")
    current_path = _plain_file_status(opened.path, allow_missing=False)
    if current_path is None or not os.path.samestat(opened.status, current_path):
        raise AuditPathError(opened.path, "named file identity changed")
    _assert_parent_identity(opened.parent, opened.parent_status)


def _set_file_mode(opened: _VerifiedOpen, mode: int) -> None:
    """Apply the best portable owner mode to a verified open file."""
    _assert_open_identity(opened)
    if hasattr(os, "fchmod"):
        os.fchmod(opened.descriptor, mode)
    else:  # pragma: no cover - exercised on Windows
        os.chmod(opened.path, mode)
    _assert_open_identity(opened)


def _prepare_lock_file(opened: _VerifiedOpen, mode: int) -> None:
    """Make a one-byte, owner-only lock target on every supported OS.

    ``msvcrt.locking`` locks byte ranges and therefore needs a stable byte at
    offset zero.  POSIX ``flock`` does not, but using the same file shape keeps
    the protocol and its recovery checks identical across platforms.
    """
    _assert_open_identity(opened)
    if os.fstat(opened.descriptor).st_size == 0:
        os.lseek(opened.descriptor, 0, os.SEEK_SET)
        os.write(opened.descriptor, b"\0")
    _set_file_mode(opened, mode)


def _lock_exclusive(descriptor: int) -> None:
    """Acquire one blocking cross-process lock without POSIX-only imports."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _write_all(descriptor: int, payload: bytes, *, operation: str) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written < 1:
            raise OSError(f"short write while {operation}")
        view = view[written:]


def _confirmed_windows_sharing_violation(exc: OSError) -> bool:
    """Retry only documented sharing/lock violations, never access denied."""
    return getattr(exc, "winerror", None) in {32, 33}


def _replace_with_sharing_retry(source: Path, destination: Path) -> None:
    deadline = time.monotonic() + _REPLACE_TIMEOUT
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if (
                os.name != "nt"
                or not _confirmed_windows_sharing_violation(exc)
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))


def _replace_plain_file(source: Path, destination: Path) -> None:
    """Rotate one verified regular entry inside a stable plain parent."""
    source = _absolute(source)
    destination = _absolute(destination)
    if source.parent != destination.parent:
        raise AuditPathError(destination, "rotation crosses parent directories")
    parent, parent_before = _validate_parent(source.parent, create=False)
    source_before = _plain_file_status(source, allow_missing=False)
    assert source_before is not None
    _plain_file_status(destination, allow_missing=True)
    try:
        _replace_with_sharing_retry(source, destination)
    except OSError as exc:
        raise AuditPathError(destination, f"rotation replace failed: {exc}") from exc
    try:
        source.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise AuditPathError(source, f"rotation source check failed: {exc}") from exc
    else:
        raise AuditPathError(source, "rotation source still exists")
    destination_after = _plain_file_status(destination, allow_missing=False)
    if (
        destination_after is None
        or not os.path.samestat(source_before, destination_after)
    ):
        raise AuditPathError(destination, "rotation changed file identity")
    _assert_parent_identity(parent, parent_before)


def _unlink_plain_file(path: Path) -> None:
    """Remove one validated rotation generation, never a redirected entry."""
    path = _absolute(path)
    parent, parent_before = _validate_parent(path.parent, create=False)
    before = _plain_file_status(path, allow_missing=True)
    if before is None:
        return
    try:
        path.unlink()
    except OSError as exc:
        raise AuditPathError(path, f"rotation unlink failed: {exc}") from exc
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise AuditPathError(path, f"rotation unlink check failed: {exc}") from exc
    else:
        raise AuditPathError(path, "rotation unlink did not remove entry")
    _assert_parent_identity(parent, parent_before)


def append_jsonl(path, records, *, fsync: bool = True) -> Path:
    """Appende uno o più record (dict o lista di dict) come righe JSONL a `path`.

    Crea la dir se manca. Ritorna il Path scritto. NON cattura le OSError: il
    chiamante decide se l'audit è best-effort (try/except attorno) o meno.
    """
    if isinstance(records, dict):
        records = (records,)
    payload = b"".join(
        (json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
         + "\n").encode("utf-8")
        for rec in records
    )
    if not payload:
        return Path(path)
    p = _absolute(path)
    opened = _open_plain_file(
        p,
        os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        _set_file_mode(opened, 0o600)
        _write_all(opened.descriptor, payload, operation="appending audit JSONL")
        if fsync:
            os.fsync(opened.descriptor)
            _fsync_directory(opened.parent)
        _assert_open_identity(opened)
    finally:
        os.close(opened.descriptor)
    return p


def append_unique_jsonl(
    path,
    record: Mapping[str, object],
    *,
    unique_field: str = "event_id",
    mode: int = 0o600,
    fsync: bool = True,
) -> Path:
    """Durably append one record at most once by a stable field.

    Rare authorization events need a stronger contract than ordinary
    telemetry: a process can stop after the audit fsync and before the state
    commit, so the safe retry must recognize the already durable event.  A
    cross-process lock makes the scan-and-append one operation.  Malformed
    existing rows fail closed instead of silently weakening deduplication.
    The same ID is idempotent only for an identical canonical record; reuse
    with different content is a collision/corruption error.
    """
    if not isinstance(record, Mapping):
        raise TypeError("record must be a mapping")
    unique_value = record.get(unique_field)
    if not isinstance(unique_value, str) or not unique_value:
        raise ValueError(f"{unique_field} must be non-empty text")
    payload = (
        json.dumps(
            dict(record),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ) + "\n"
    ).encode("utf-8")
    p = _absolute(path)
    lock_path = p.with_name(f".{p.name}.unique.lock")
    lock = _open_plain_file(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
        mode,
    )
    locked = False
    try:
        _prepare_lock_file(lock, mode)
        _lock_exclusive(lock.descriptor)
        locked = True
        _assert_open_identity(lock)
        audit = _open_plain_file(
            p,
            os.O_CREAT | os.O_RDWR | os.O_APPEND | getattr(os, "O_BINARY", 0),
            mode,
        )
        try:
            _set_file_mode(audit, mode)
            os.lseek(audit.descriptor, 0, os.SEEK_SET)
            existing = bytearray()
            while True:
                chunk = os.read(audit.descriptor, 64 * 1024)
                if not chunk:
                    break
                existing.extend(chunk)
            for line_number, line in enumerate(bytes(existing).splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    prior = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"malformed audit row {line_number}: {exc}",
                    ) from exc
                if not isinstance(prior, dict):
                    raise ValueError(
                        f"malformed audit row {line_number}: object required",
                    )
                if prior.get(unique_field) == unique_value:
                    prior_payload = (
                        json.dumps(
                            prior,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ) + "\n"
                    ).encode("utf-8")
                    if prior_payload != payload:
                        raise ValueError(
                            f"audit {unique_field} collision at row "
                            f"{line_number}: canonical record differs"
                        )
                    _assert_open_identity(audit)
                    _assert_open_identity(lock)
                    return p
            _write_all(
                audit.descriptor,
                payload,
                operation="appending unique audit JSONL",
            )
            if fsync:
                os.fsync(audit.descriptor)
                _fsync_directory(audit.parent)
            _assert_open_identity(audit)
            _assert_open_identity(lock)
        finally:
            os.close(audit.descriptor)
    finally:
        try:
            if locked:
                _unlock(lock.descriptor)
        finally:
            os.close(lock.descriptor)
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

    p = _absolute(path)
    lock_path = p.with_name(f".{p.name}.rotation.lock")
    lock = _open_plain_file(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
        mode,
    )
    locked = False
    try:
        _prepare_lock_file(lock, mode)
        _lock_exclusive(lock.descriptor)
        locked = True
        _assert_open_identity(lock)
        current = _plain_file_status(p, allow_missing=True)
        current_size = current.st_size if current is not None else 0
        if current_size and current_size + len(payload) > max_bytes:
            # Validate the complete mutable namespace before the first rename.
            for index in range(1, backup_count + 1):
                _plain_file_status(
                    p.with_name(f"{p.name}.{index}"),
                    allow_missing=True,
                )
            oldest = p.with_name(f"{p.name}.{backup_count}")
            _unlink_plain_file(oldest)
            for index in range(backup_count - 1, 0, -1):
                source = p.with_name(f"{p.name}.{index}")
                if _plain_file_status(source, allow_missing=True) is not None:
                    _replace_plain_file(
                        source,
                        p.with_name(f"{p.name}.{index + 1}"),
                    )
            _replace_plain_file(p, p.with_name(f"{p.name}.1"))
            _assert_open_identity(lock)

        out = _open_plain_file(
            p,
            os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            mode,
        )
        try:
            _set_file_mode(out, mode)
            _write_all(out.descriptor, payload, operation="appending audit JSONL")
            if fsync:
                os.fsync(out.descriptor)
                _fsync_directory(out.parent)
            _assert_open_identity(out)
            _assert_open_identity(lock)
        finally:
            os.close(out.descriptor)
    finally:
        try:
            if locked:
                _unlock(lock.descriptor)
        finally:
            os.close(lock.descriptor)
    return p
