"""Closed, private snapshots of executor birth candidates.

This module deliberately has no signing or publication dependency.  A producer
must provide a dedicated staging directory whose complete contents are the two
contract files and the files named by ``[code].files``.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Mapping

from executor_birth_secure_file import (
    SecureFileReadError, read_immutable_regular_file,
)


MANIFEST_FILE = "manifest.toml"
LANGUAGE_STATE_FILE = "manifest.lang_state.json"


class CandidateSnapshotError(RuntimeError):
    """Stable fail-closed error raised while acquiring a candidate."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    """Owned immutable bytes copied from one closed staging directory."""

    private_root: Path
    manifest_bytes: bytes
    language_state_bytes: bytes
    code_files: Mapping[str, bytes]

    def close(self) -> None:
        """Destroy the private copy; safe to call more than once."""
        _remove_private(self.private_root)

    def __enter__(self) -> "CandidateSnapshot":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def _link_like(path: Path, status: os.stat_result | None = None) -> bool:
    try:
        current = path.lstat() if status is None else status
        return (
            stat.S_ISLNK(current.st_mode)
            or bool(
                getattr(current, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            or (hasattr(path, "is_junction") and path.is_junction())
        )
    except OSError:
        return True


def _identity(status: os.stat_result) -> tuple[int, ...]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_nlink,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _portable_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CandidateSnapshotError("candidate_path_invalid", str(value))
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or "\\" in value
        or posix.as_posix() != value
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise CandidateSnapshotError("candidate_path_invalid", value)
    return value


def _declared_code_files(manifest_bytes: bytes) -> tuple[str, ...]:
    try:
        parsed = tomllib.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CandidateSnapshotError("candidate_manifest_invalid", str(exc)) from exc
    code = parsed.get("code")
    files = code.get("files") if isinstance(code, dict) else None
    if not isinstance(files, list) or not files:
        raise CandidateSnapshotError("candidate_file_missing", "code.files")
    paths = tuple(_portable_relative(item) for item in files)
    if len(set(paths)) != len(paths):
        raise CandidateSnapshotError("candidate_path_invalid", "duplicate code.files")
    folded: set[str] = set()
    for path in paths:
        key = path.casefold()
        if key in folded:
            raise CandidateSnapshotError("candidate_path_invalid", f"case collision: {path}")
        folded.add(key)
    if MANIFEST_FILE in paths or LANGUAGE_STATE_FILE in paths:
        raise CandidateSnapshotError("candidate_path_invalid", "reserved contract file")
    return paths


def _tree_state(root: Path) -> dict[str, tuple[str, tuple[int, ...]]]:
    state: dict[str, tuple[str, tuple[int, ...]]] = {}
    try:
        root_status = root.lstat()
    except OSError as exc:
        raise CandidateSnapshotError("snapshot_unavailable", str(root)) from exc
    if _link_like(root, root_status) or not stat.S_ISDIR(root_status.st_mode):
        raise CandidateSnapshotError("candidate_link_forbidden", str(root))
    state[""] = ("dir", _identity(root_status))
    try:
        entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    except OSError as exc:
        raise CandidateSnapshotError("snapshot_unavailable", str(root)) from exc
    for entry in entries:
        relative = entry.relative_to(root).as_posix()
        try:
            status = entry.lstat()
        except OSError as exc:
            raise CandidateSnapshotError("candidate_changed", relative) from exc
        if _link_like(entry, status):
            raise CandidateSnapshotError("candidate_link_forbidden", relative)
        if stat.S_ISDIR(status.st_mode):
            kind = "dir"
        elif stat.S_ISREG(status.st_mode):
            if status.st_nlink != 1:
                raise CandidateSnapshotError("candidate_link_forbidden", relative)
            kind = "file"
        else:
            raise CandidateSnapshotError("candidate_file_nonregular", relative)
        state[relative] = (kind, _identity(status))
    return state


def _expected_entries(code_files: tuple[str, ...]) -> dict[str, str]:
    expected = {MANIFEST_FILE: "file", LANGUAGE_STATE_FILE: "file"}
    for relative in code_files:
        expected[relative] = "file"
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            expected[parent.as_posix()] = "dir"
            parent = parent.parent
    return expected


def _check_closed_tree(state: Mapping[str, tuple[str, tuple[int, ...]]], expected: Mapping[str, str]) -> None:
    present = set(state) - {""}
    missing = sorted(set(expected) - present)
    if missing:
        raise CandidateSnapshotError("candidate_file_missing", missing[0])
    extra = sorted(present - set(expected))
    if extra:
        raise CandidateSnapshotError("candidate_file_extra", extra[0])
    for name, kind in expected.items():
        if state[name][0] != kind:
            raise CandidateSnapshotError("candidate_file_nonregular", name)


def _read_regular(root: Path, relative: str) -> bytes:
    """Read through no-follow directory descriptors where POSIX provides them."""
    if os.name == "nt":
        path = root.joinpath(*PurePosixPath(relative).parts)
        try:
            components = (root, *[
                root.joinpath(*PurePosixPath(relative).parts[:index])
                for index in range(1, len(PurePosixPath(relative).parts))
            ])
            before_components = tuple(
                _identity(component.lstat()) for component in components
            )
            if any(_link_like(component) for component in components):
                raise CandidateSnapshotError("candidate_link_forbidden", relative)
            before = path.lstat()
            if _link_like(path, before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise CandidateSnapshotError("candidate_link_forbidden", relative)
            # Path-based stat fields and CRT fstat fields are not a portable
            # cross-API identity on Windows.  The shared Win32 oracle pins the
            # entry against write/rename/delete, verifies its final path and
            # checks identity and shape twice on the same native handle.
            payload = read_immutable_regular_file(path, maximum=before.st_size)
            after = path.lstat()
            after_components = tuple(
                _identity(component.lstat()) for component in components
            )
            if (
                _identity(before) != _identity(after)
                or before_components != after_components
            ):
                raise CandidateSnapshotError("candidate_changed", relative)
            return payload
        except CandidateSnapshotError:
            raise
        except (OSError, SecureFileReadError) as exc:
            raise CandidateSnapshotError("candidate_changed", relative) from exc
    descriptors: list[int] = []
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        root_fd = os.open(root, directory_flags)
        descriptors.append(root_fd)
        parent_fd = root_fd
        parts = PurePosixPath(relative).parts
        for component in parts[:-1]:
            parent_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            descriptors.append(parent_fd)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(parts[-1], flags, dir_fd=parent_fd)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise CandidateSnapshotError("candidate_link_forbidden", relative)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after):
            raise CandidateSnapshotError("candidate_changed", relative)
        return b"".join(chunks)
    except CandidateSnapshotError:
        raise
    except OSError as exc:
        raise CandidateSnapshotError("candidate_changed", relative) from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_private(root: Path, relative: str, payload: bytes) -> None:
    destination = root.joinpath(*PurePosixPath(relative).parts)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_private(root: Path) -> None:
    if not root.exists():
        return
    try:
        root.chmod(0o700)
        for entry in root.rglob("*"):
            if entry.is_dir() and not entry.is_symlink():
                entry.chmod(0o700)
    except OSError:
        pass
    shutil.rmtree(root, ignore_errors=True)


def _acquire_snapshot(
    source_root: Path | str,
    *, private_parent: Path | str | None,
    fixed_auxiliary_files: tuple[str, ...],
) -> tuple[CandidateSnapshot, Mapping[str, bytes]]:
    """Copy one fixed envelope; auxiliary bytes never enter the candidate."""
    source = Path(source_root)
    private = Path(tempfile.mkdtemp(prefix="metnos-birth-", dir=private_parent))
    try:
        initial = _tree_state(source)
        # The manifest must be read before the exact envelope can be derived.
        if MANIFEST_FILE not in initial:
            raise CandidateSnapshotError("candidate_file_missing", MANIFEST_FILE)
        manifest_bytes = _read_regular(source, MANIFEST_FILE)
        code_paths = _declared_code_files(manifest_bytes)
        expected = _expected_entries(code_paths)
        source_expected = dict(expected)
        source_expected.update({name: "file" for name in fixed_auxiliary_files})
        _check_closed_tree(initial, source_expected)

        payloads: dict[str, bytes] = {MANIFEST_FILE: manifest_bytes}
        for relative in (LANGUAGE_STATE_FILE, *code_paths, *fixed_auxiliary_files):
            payloads[relative] = _read_regular(source, relative)
        final = _tree_state(source)
        if initial != final:
            raise CandidateSnapshotError("candidate_changed", str(source))

        for relative, payload in payloads.items():
            if relative in fixed_auxiliary_files:
                continue
            _write_private(private, relative, payload)
        copied = _tree_state(private)
        _check_closed_tree(copied, expected)
        for relative, payload in payloads.items():
            if relative in fixed_auxiliary_files:
                continue
            if _read_regular(private, relative) != payload:
                raise CandidateSnapshotError("candidate_changed", relative)

        for entry in sorted(private.rglob("*"), reverse=True):
            entry.chmod(0o500 if entry.is_dir() else 0o400)
        private.chmod(0o500)
        snapshot = CandidateSnapshot(
            private_root=private,
            manifest_bytes=manifest_bytes,
            language_state_bytes=payloads[LANGUAGE_STATE_FILE],
            code_files=MappingProxyType({path: payloads[path] for path in code_paths}),
        )
        auxiliary = MappingProxyType({
            name: payloads[name] for name in fixed_auxiliary_files
        })
        return snapshot, auxiliary
    except Exception:
        _remove_private(private)
        raise


def acquire_candidate_snapshot(
    source_root: Path | str,
    *,
    private_parent: Path | str | None = None,
) -> CandidateSnapshot:
    """Copy and return the exact closed candidate, or fail without a snapshot."""
    snapshot, _auxiliary = _acquire_snapshot(
        source_root, private_parent=private_parent, fixed_auxiliary_files=(),
    )
    return snapshot


def materialize_birth_candidate_from_authoring(
    source_root: Path | str,
    destination: Path | str,
) -> Path:
    """Create an exact Birth candidate from a signed authoring tree.

    The current signature is evidence for the installed source, not an input
    to a new admission.  This function captures the complete signed envelope,
    removes that derived evidence from the candidate, and derives the code
    digest from the same immutable bytes that it writes to staging.
    """
    from manifest_code_digest import prepare_manifest_digest_v1

    target = Path(destination)
    if os.path.lexists(target):
        raise CandidateSnapshotError("candidate_destination_invalid", str(target))
    snapshot, _signature = _acquire_authenticated_current_snapshot(source_root)
    try:
        manifest = prepare_manifest_digest_v1(
            snapshot.manifest_bytes, snapshot.code_files,
        )
        target.mkdir(mode=0o700)
        _write_private(target, MANIFEST_FILE, manifest)
        _write_private(
            target, LANGUAGE_STATE_FILE, snapshot.language_state_bytes,
        )
        for relative, payload in snapshot.code_files.items():
            _write_private(target, relative, payload)
        expected = _expected_entries(tuple(snapshot.code_files))
        _check_closed_tree(_tree_state(target), expected)
        return target
    except Exception:
        _remove_private(target)
        raise
    finally:
        snapshot.close()


def materialize_birth_candidate_from_manifest_ref(
    ref: object,
    destination: Path | str,
    *,
    timeout: float = 30.0,
) -> Path:
    """Create a Birth candidate from one versioned opaque authoring ref."""
    from executor_birth_authoring import (
        AuthoringInstallError, read_manifest_ref_tree_versioned,
    )
    from manifest_code_digest import prepare_manifest_digest_v1

    target = Path(destination)
    if os.path.lexists(target):
        raise CandidateSnapshotError("candidate_destination_invalid", str(target))
    try:
        payloads = read_manifest_ref_tree_versioned(ref, timeout=timeout)
    except AuthoringInstallError as exc:
        raise CandidateSnapshotError(exc.code, exc.detail) from exc
    manifest_bytes = payloads.get(MANIFEST_FILE)
    language_state_bytes = payloads.get(LANGUAGE_STATE_FILE)
    signature_bytes = payloads.get("manifest.toml.sig")
    if not all(isinstance(item, bytes) for item in (
        manifest_bytes, language_state_bytes, signature_bytes,
    )):
        raise CandidateSnapshotError("candidate_file_missing", str(target))
    code_paths = _declared_code_files(manifest_bytes)
    expected = {MANIFEST_FILE, LANGUAGE_STATE_FILE, "manifest.toml.sig", *code_paths}
    if set(payloads) != expected:
        raise CandidateSnapshotError("candidate_entry_unexpected", str(target))
    code_files = {name: payloads[name] for name in code_paths}
    try:
        manifest = prepare_manifest_digest_v1(manifest_bytes, code_files)
        target.mkdir(mode=0o700)
        _write_private(target, MANIFEST_FILE, manifest)
        _write_private(target, LANGUAGE_STATE_FILE, language_state_bytes)
        for relative, payload in code_files.items():
            _write_private(target, relative, payload)
        _check_closed_tree(_tree_state(target), _expected_entries(code_paths))
        return target
    except Exception:
        _remove_private(target)
        raise


def _acquire_authenticated_current_snapshot(
    source_root: Path | str,
    *, private_parent: Path | str | None = None,
) -> tuple[CandidateSnapshot, bytes]:
    """Copy the fixed signed-source envelope used by a current generation."""
    snapshot, auxiliary = _acquire_snapshot(
        source_root, private_parent=private_parent,
        fixed_auxiliary_files=("manifest.toml.sig",),
    )
    return snapshot, auxiliary["manifest.toml.sig"]
