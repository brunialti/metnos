"""Portable, recoverable authoring-tree primitives for Executor Birth F4.

The module deliberately does not publish RM-0007 generations and does not own
any signing key.  It provides the closed tree identity, canonical journal and
version codecs, and the shared/exclusive token used by readers and the sole
Birth writer.  ``contract_store.commit_birth_snapshot`` composes these
primitives with the authenticated AdmissionReceipt and current pointer.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Iterator, Mapping, Protocol


TREE_DOMAIN = b"metnos.executor-birth.authoring-tree/v1\0"
JOURNAL_DOMAIN = b"metnos.executor-birth.authoring-journal/v1\0"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REQUEST_RE = re.compile(r"sha256:([0-9a-f]{64})\Z")


class AuthoringInstallError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class AuthoringManifestRef(Protocol):
    """Narrow structural input for the sole versioned authoring reader."""

    manifest_dir: Path
    contract_id: object


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _digest(domain: bytes, payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(domain + payload).hexdigest()


def _require_digest(value: object, field: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise AuthoringInstallError("authoring_journal_invalid", field)


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise AuthoringInstallError("authoring_tree_invalid", str(value))
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value or ".." in parsed.parts:
        raise AuthoringInstallError("authoring_tree_invalid", value)
    return value


def authoring_tree_id(files: Mapping[str, bytes]) -> str:
    """Return the normative identity of one exact, closed authoring tree."""
    records: list[dict[str, object]] = []
    folded: set[str] = set()
    for name in sorted(files, key=lambda item: item.encode("utf-8")):
        relative = _relative(name)
        if relative.casefold() in folded or not isinstance(files[name], bytes):
            raise AuthoringInstallError("authoring_tree_invalid", relative)
        folded.add(relative.casefold())
        payload = files[name]
        records.append({
            "relative_path": relative,
            "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        })
    if not records:
        raise AuthoringInstallError("authoring_tree_invalid", "empty")
    return _digest(TREE_DOMAIN, _canonical(records))


@dataclass(frozen=True, slots=True)
class AuthoringInstallJournalV1:
    request_id: str
    contract_id: str
    source_origin: str
    canonical_tree_id: str
    old_tree_id: str | None
    new_tree_id: str
    candidate_id: str
    semantic_core_id: str
    admission_context_id: str
    predecessor_generation_id: str | None
    new_generation_id: str
    staging_basename: str
    backup_basename: str
    recovery_action: str = "restore_old_until_new_pointer"
    state: str = "prepared"
    schema_version: int = 1

    def __post_init__(self) -> None:
        match = _REQUEST_RE.fullmatch(self.request_id)
        if match is None:
            raise AuthoringInstallError("authoring_journal_invalid", "request_id")
        for field in (
            "canonical_tree_id", "new_tree_id", "candidate_id",
            "semantic_core_id", "admission_context_id", "new_generation_id",
        ):
            _require_digest(getattr(self, field), field)
        _require_digest(self.old_tree_id, "old_tree_id", nullable=True)
        _require_digest(
            self.predecessor_generation_id,
            "predecessor_generation_id", nullable=True,
        )
        if not self.contract_id or not self.source_origin or "\0" in self.contract_id + self.source_origin:
            raise AuthoringInstallError("authoring_journal_invalid", "identity")
        suffix = match.group(1)
        if self.staging_basename != f".birth-stage-{suffix}":
            raise AuthoringInstallError("authoring_journal_invalid", "staging_basename")
        if self.backup_basename != f".birth-backup-{suffix}":
            raise AuthoringInstallError("authoring_journal_invalid", "backup_basename")
        if self.recovery_action != "restore_old_until_new_pointer" or self.state != "prepared":
            raise AuthoringInstallError("authoring_journal_invalid", "state")
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise AuthoringInstallError("authoring_journal_invalid", "schema_version")

    def as_dict(self) -> dict[str, object]:
        return {
            "admission_context_id": self.admission_context_id,
            "backup_basename": self.backup_basename,
            "candidate_id": self.candidate_id,
            "canonical_tree_id": self.canonical_tree_id,
            "contract_id": self.contract_id,
            "new_generation_id": self.new_generation_id,
            "new_tree_id": self.new_tree_id,
            "old_tree_id": self.old_tree_id,
            "predecessor_generation_id": self.predecessor_generation_id,
            "recovery_action": self.recovery_action,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "semantic_core_id": self.semantic_core_id,
            "source_origin": self.source_origin,
            "staging_basename": self.staging_basename,
            "state": self.state,
        }

    def encode(self) -> bytes:
        return _canonical(self.as_dict())

    @property
    def journal_hash(self) -> str:
        return _digest(JOURNAL_DOMAIN, self.encode())


def decode_journal(encoded: bytes) -> AuthoringInstallJournalV1:
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthoringInstallError("authoring_journal_invalid", "json") from exc
    if not isinstance(value, dict) or _canonical(value) != encoded:
        raise AuthoringInstallError("authoring_journal_invalid", "canonical")
    expected = set(AuthoringInstallJournalV1.__dataclass_fields__)
    if set(value) != expected:
        raise AuthoringInstallError("authoring_journal_invalid", "schema")
    try:
        return AuthoringInstallJournalV1(**value)
    except TypeError as exc:
        raise AuthoringInstallError("authoring_journal_invalid", "types") from exc


@dataclass(frozen=True, slots=True)
class AuthoringVersionV1:
    contract_id: str
    version: int
    tree_id: str
    schema_version: int = 1

    def encode(self) -> bytes:
        if (
            not self.contract_id or "\0" in self.contract_id
            or isinstance(self.version, bool) or self.version < 0
            or self.schema_version != 1
        ):
            raise AuthoringInstallError("authoring_version_invalid")
        _require_digest(self.tree_id, "tree_id")
        return _canonical({
            "contract_id": self.contract_id,
            "schema_version": 1,
            "tree_id": self.tree_id,
            "version": self.version,
        })


def decode_version(encoded: bytes) -> AuthoringVersionV1:
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthoringInstallError("authoring_version_invalid", "json") from exc
    if not isinstance(value, dict) or set(value) != {
        "contract_id", "schema_version", "tree_id", "version",
    } or _canonical(value) != encoded:
        raise AuthoringInstallError("authoring_version_invalid", "schema")
    try:
        result = AuthoringVersionV1(**value)
        result.encode()
        return result
    except (TypeError, AuthoringInstallError) as exc:
        if isinstance(exc, AuthoringInstallError):
            raise
        raise AuthoringInstallError("authoring_version_invalid", "types") from exc


class _LocalRWLock:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.readers = 0
        self.writer = False
        self.waiting_writers = 0

    def acquire(self, exclusive: bool, deadline: float) -> bool:
        with self.condition:
            if exclusive:
                self.waiting_writers += 1
                try:
                    while self.writer or self.readers:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not self.condition.wait(remaining):
                            return False
                    self.writer = True
                finally:
                    self.waiting_writers -= 1
            else:
                while self.writer or self.waiting_writers:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not self.condition.wait(remaining):
                        return False
                self.readers += 1
            return True

    def release(self, exclusive: bool) -> None:
        with self.condition:
            if exclusive:
                self.writer = False
            else:
                self.readers -= 1
            self.condition.notify_all()


_RW_GUARD = threading.Lock()
_RW_LOCKS: dict[str, _LocalRWLock] = {}


def _local_lock(path: Path) -> _LocalRWLock:
    key = os.path.normcase(os.path.abspath(path))
    with _RW_GUARD:
        return _RW_LOCKS.setdefault(key, _LocalRWLock())


def _try_os_lock(handle: object, exclusive: bool) -> bool:
    handle.seek(0)  # type: ignore[attr-defined]
    if os.name == "nt":
        import msvcrt
        mode = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK
        try:
            msvcrt.locking(handle.fileno(), mode, 1)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(
            handle.fileno(),  # type: ignore[attr-defined]
            (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB,
        )
        return True
    except OSError:
        return False


def _unlock_os(handle: object) -> None:
    handle.seek(0)  # type: ignore[attr-defined]
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextlib.contextmanager
def authoring_token(
    lock_path: Path, *, exclusive: bool, timeout: float,
) -> Iterator[None]:
    """Acquire the F4 reader/writer token with one finite deadline."""
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    deadline = time.monotonic() + timeout
    local = _local_lock(lock_path)
    if not local.acquire(exclusive, deadline):
        raise AuthoringInstallError("authoring_token_timeout")
    handle = None
    locked = False
    try:
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        status = os.fstat(handle.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise AuthoringInstallError("authoring_atomic_install_unsupported")
        if status.st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        elif status.st_size != 1:
            raise AuthoringInstallError("authoring_atomic_install_unsupported")
        while not _try_os_lock(handle, exclusive):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthoringInstallError("authoring_token_timeout")
            time.sleep(min(0.02, remaining))
        locked = True
        yield
    except AuthoringInstallError:
        raise
    except (OSError, ImportError) as exc:
        raise AuthoringInstallError("authoring_atomic_install_unsupported", str(exc)) from exc
    finally:
        try:
            if handle is not None:
                if locked:
                    _unlock_os(handle)
                handle.close()
        finally:
            local.release(exclusive)


def read_tree(root: Path, relative_paths: tuple[str, ...]) -> Mapping[str, bytes]:
    """Read a closed list and reject links, hard links and undeclared entries."""
    expected = {_relative(item) for item in relative_paths}
    expected_directories = {
        parent.as_posix()
        for name in expected
        for parent in PurePosixPath(name).parents
        if parent != PurePosixPath(".")
    }
    present: set[str] = set()
    present_directories: set[str] = set()
    payloads: dict[str, bytes] = {}
    try:
        root_status = root.lstat()
        if (
            not stat.S_ISDIR(root_status.st_mode)
            or stat.S_ISLNK(root_status.st_mode)
            or bool(getattr(root_status, "st_file_attributes", 0) & 0x400)
        ):
            raise AuthoringInstallError("authoring_tree_invalid", str(root))
        for entry in root.rglob("*"):
            relative = entry.relative_to(root).as_posix()
            status = entry.lstat()
            if stat.S_ISLNK(status.st_mode) or bool(
                getattr(status, "st_file_attributes", 0) & 0x400
            ):
                raise AuthoringInstallError("authoring_tree_invalid", relative)
            if stat.S_ISDIR(status.st_mode):
                if relative not in expected_directories:
                    raise AuthoringInstallError("authoring_tree_invalid", relative)
                present_directories.add(relative)
                continue
            if (
                relative not in expected or not stat.S_ISREG(status.st_mode)
                or status.st_nlink != 1
            ):
                raise AuthoringInstallError("authoring_tree_invalid", relative)
            present.add(relative)
            payloads[relative] = entry.read_bytes()
    except OSError as exc:
        raise AuthoringInstallError("authoring_tree_invalid", str(exc)) from exc
    if present != expected or present_directories != expected_directories:
        raise AuthoringInstallError("authoring_tree_invalid", "closed set")
    return MappingProxyType(payloads)


@dataclass(frozen=True, slots=True)
class AuthoringPaths:
    canonical: Path
    control: Path
    lock: Path
    version: Path
    journal: Path

    def transaction_paths(self, journal: AuthoringInstallJournalV1) -> tuple[Path, Path]:
        return (
            self.canonical.parent / journal.staging_basename,
            self.canonical.parent / journal.backup_basename,
        )


def authoring_paths(canonical: Path, contract_id: str) -> AuthoringPaths:
    """Derive every control path without accepting a journal-supplied path."""
    canonical = Path(os.path.abspath(canonical))
    if not contract_id or "\0" in contract_id or canonical.parent == canonical:
        raise AuthoringInstallError("authoring_tree_invalid", "canonical")
    key = hashlib.sha256(contract_id.encode("utf-8")).hexdigest()
    control = canonical.parent / f".{canonical.name}.birth-control-{key}"
    return AuthoringPaths(
        canonical, control, control / "authoring.lock",
        control / "version.json", control / "journal.json",
    )


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_file(path: Path, payload: bytes) -> None:
    """Create or atomically replace one control file and reread exact bytes."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
        if path.read_bytes() != payload:
            raise AuthoringInstallError("authoring_control_reread_mismatch", path.name)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def persist_prepared_journal(paths: AuthoringPaths, journal: AuthoringInstallJournalV1) -> None:
    staging, backup = paths.transaction_paths(journal)
    if staging.parent != paths.canonical.parent or backup.parent != paths.canonical.parent:
        raise AuthoringInstallError("authoring_journal_invalid", "transaction paths")
    _durable_file(paths.journal, journal.encode())


def load_prepared_journal(paths: AuthoringPaths) -> AuthoringInstallJournalV1 | None:
    if not paths.journal.exists():
        return None
    try:
        return decode_journal(paths.journal.read_bytes())
    except OSError as exc:
        raise AuthoringInstallError("authoring_journal_invalid", str(exc)) from exc


def read_version(paths: AuthoringPaths, contract_id: str) -> AuthoringVersionV1 | None:
    if not paths.version.exists():
        return None
    try:
        result = decode_version(paths.version.read_bytes())
    except OSError as exc:
        raise AuthoringInstallError("authoring_version_invalid", str(exc)) from exc
    if result.contract_id != contract_id:
        raise AuthoringInstallError("authoring_version_invalid", "contract_id")
    return result


def advance_version(paths: AuthoringPaths, contract_id: str, tree_id: str) -> AuthoringVersionV1:
    current = read_version(paths, contract_id)
    # Recovery may resume after the version file itself became durable but
    # before the prepared journal was removed.  Advancing the same tree a
    # second time would manufacture an observer-visible change that never
    # happened.  Treat the exact tree as the durable idempotency key.
    if current is not None and current.tree_id == tree_id:
        return current
    result = AuthoringVersionV1(
        contract_id=contract_id,
        version=0 if current is None else current.version + 1,
        tree_id=tree_id,
    )
    _durable_file(paths.version, result.encode())
    return result


def materialize_staging(
    paths: AuthoringPaths,
    journal: AuthoringInstallJournalV1,
    files: Mapping[str, bytes],
) -> Path:
    """Materialize and durably verify the exact tree named by the journal."""
    staging, backup = paths.transaction_paths(journal)
    if staging.exists() or backup.exists():
        raise AuthoringInstallError("authoring_recovery_required")
    if authoring_tree_id(files) != journal.new_tree_id:
        raise AuthoringInstallError("authoring_tree_invalid", "new_tree_id")
    try:
        staging.mkdir(mode=0o700)
        directories = sorted({
            parent
            for name in files
            for parent in PurePosixPath(name).parents
            if parent != PurePosixPath(".")
        }, key=lambda item: len(item.parts))
        for relative in directories:
            (staging / relative).mkdir(mode=0o700)
        for name, payload in files.items():
            destination = staging.joinpath(*PurePosixPath(_relative(name)).parts)
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        for directory in reversed([staging / item for item in directories]):
            _sync_directory(directory)
        _sync_directory(staging)
        _sync_directory(staging.parent)
        reread = read_tree(staging, tuple(files))
        if dict(reread) != dict(files) or authoring_tree_id(reread) != journal.new_tree_id:
            raise AuthoringInstallError("authoring_tree_reread_mismatch")
        return staging
    except Exception:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def replace_with_staging(paths: AuthoringPaths, journal: AuthoringInstallJournalV1) -> None:
    """Perform the two directory renames while the exclusive token is held."""
    staging, backup = paths.transaction_paths(journal)
    try:
        if journal.old_tree_id is None:
            if paths.canonical.exists():
                raise AuthoringInstallError("authoring_recovery_ambiguous", "unexpected old tree")
        else:
            old = read_tree(paths.canonical, tuple(_tree_file_names(paths.canonical)))
            if authoring_tree_id(old) != journal.old_tree_id:
                raise AuthoringInstallError("authoring_recovery_ambiguous", "old_tree_id")
            os.replace(paths.canonical, backup)
            _sync_directory(paths.canonical.parent)
        os.replace(staging, paths.canonical)
        _sync_directory(paths.canonical.parent)
    except AuthoringInstallError:
        raise
    except OSError as exc:
        raise AuthoringInstallError("authoring_atomic_install_unsupported", str(exc)) from exc


def _tree_file_names(root: Path) -> tuple[str, ...]:
    try:
        return tuple(sorted(
            entry.relative_to(root).as_posix()
            for entry in root.rglob("*") if entry.is_file() and not entry.is_symlink()
        ))
    except OSError as exc:
        raise AuthoringInstallError("authoring_tree_invalid", str(exc)) from exc


def observe_tree(root: Path) -> Mapping[str, bytes]:
    """Observe every file while still enforcing the exact closed-tree rules."""
    return read_tree(root, _tree_file_names(root))


def cleanup_transaction(paths: AuthoringPaths, journal: AuthoringInstallJournalV1) -> None:
    staging, backup = paths.transaction_paths(journal)
    for target in (staging, backup):
        if target.exists():
            if target.is_symlink() or not target.is_dir():
                raise AuthoringInstallError("authoring_recovery_ambiguous", target.name)
            shutil.rmtree(target)
    try:
        paths.journal.unlink()
    except FileNotFoundError:
        pass
    _sync_directory(paths.canonical.parent)
    _sync_directory(paths.control)


def rollback_prepared(paths: AuthoringPaths, journal: AuthoringInstallJournalV1) -> None:
    """Apply the closed old-pointer branch of the normative recovery matrix."""
    staging, backup = paths.transaction_paths(journal)
    canonical_id: str | None = None
    if paths.canonical.exists():
        current = read_tree(paths.canonical, _tree_file_names(paths.canonical))
        canonical_id = authoring_tree_id(current)
    allowed_canonical = {journal.old_tree_id, journal.new_tree_id}
    if backup.exists():
        allowed_canonical.add(None)
    if canonical_id not in allowed_canonical:
        raise AuthoringInstallError("authoring_recovery_ambiguous", "canonical")
    if backup.exists():
        old = read_tree(backup, _tree_file_names(backup))
        if authoring_tree_id(old) != journal.old_tree_id:
            raise AuthoringInstallError("authoring_recovery_ambiguous", "backup")
    if canonical_id == journal.new_tree_id:
        if journal.old_tree_id is None:
            shutil.rmtree(paths.canonical)
        elif backup.exists():
            shutil.rmtree(paths.canonical)
            os.replace(backup, paths.canonical)
        else:
            raise AuthoringInstallError("authoring_recovery_ambiguous", "backup missing")
    elif canonical_id is None and journal.old_tree_id is not None:
        if not backup.exists():
            raise AuthoringInstallError("authoring_recovery_ambiguous", "old tree missing")
        os.replace(backup, paths.canonical)
    if staging.exists():
        staged = read_tree(staging, _tree_file_names(staging))
        if authoring_tree_id(staged) != journal.new_tree_id:
            raise AuthoringInstallError("authoring_recovery_ambiguous", "staging")
        shutil.rmtree(staging)
    try:
        paths.journal.unlink()
    except FileNotFoundError:
        pass
    _sync_directory(paths.canonical.parent)
    _sync_directory(paths.control)


def _read_authoring_versioned(
    paths: AuthoringPaths,
    contract_id: str,
    reader: Callable[[], Mapping[str, bytes]],
    *,
    timeout: float,
) -> Mapping[str, bytes]:
    with authoring_token(paths.lock, exclusive=False, timeout=timeout):
        before = read_version(paths, contract_id)
        if before is None:
            raise AuthoringInstallError("authoring_version_invalid", "missing")
        payloads = reader()
        after = read_version(paths, contract_id)
        if before != after or authoring_tree_id(payloads) != before.tree_id:
            raise AuthoringInstallError("authoring_version_changed")
        return payloads


def read_authoring_versioned(
    paths: AuthoringPaths,
    contract_id: str,
    relative_paths: tuple[str, ...],
    *,
    timeout: float,
) -> Mapping[str, bytes]:
    """Read one all-or-nothing authoring view under the shared F4 token."""
    return _read_authoring_versioned(
        paths, contract_id,
        lambda: read_tree(paths.canonical, relative_paths),
        timeout=timeout,
    )


def read_manifest_ref_versioned(
    ref: AuthoringManifestRef,
    relative_paths: tuple[str, ...],
    *,
    timeout: float,
) -> Mapping[str, bytes]:
    """Read a ``ManifestRef`` without exposing its authoring locator.

    Ordinary callers must pass the reference as an opaque identity.  Resolving
    ``manifest_dir`` and deriving the control directory remain confined to this
    reader boundary, where the shared token and the before/after version check
    cannot accidentally be omitted.
    """
    contract_id = getattr(ref.contract_id, "value", ref.contract_id)
    if not isinstance(contract_id, str):
        raise AuthoringInstallError("authoring_tree_invalid", "contract_id")
    paths = authoring_paths(ref.manifest_dir, contract_id)
    return read_authoring_versioned(
        paths, contract_id, relative_paths, timeout=timeout,
    )


def read_manifest_ref_tree_versioned(
    ref: AuthoringManifestRef,
    *,
    timeout: float,
) -> Mapping[str, bytes]:
    """Read the exact closed authoring tree behind an opaque manifest ref."""
    contract_id = getattr(ref.contract_id, "value", ref.contract_id)
    if not isinstance(contract_id, str):
        raise AuthoringInstallError("authoring_tree_invalid", "contract_id")
    paths = authoring_paths(ref.manifest_dir, contract_id)
    return _read_authoring_versioned(
        paths, contract_id, lambda: observe_tree(paths.canonical),
        timeout=timeout,
    )
