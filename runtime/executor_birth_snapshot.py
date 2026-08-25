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
            with path.open("rb") as handle:
                payload = handle.read()
                after_handle = os.fstat(handle.fileno())
            after = path.lstat()
            after_components = tuple(
                _identity(component.lstat()) for component in components
            )
            if (
                _identity(before) != _identity(after_handle)
                or _identity(before) != _identity(after)
                or before_components != after_components
            ):
                raise CandidateSnapshotError("candidate_changed", relative)
            return payload
        except CandidateSnapshotError:
            raise
        except OSError as exc:
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


def acquire_candidate_snapshot(
    source_root: Path | str,
    *,
    private_parent: Path | str | None = None,
) -> CandidateSnapshot:
    """Copy and return the exact closed candidate, or fail without a snapshot."""
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
        _check_closed_tree(initial, expected)

        payloads: dict[str, bytes] = {MANIFEST_FILE: manifest_bytes}
        for relative in (LANGUAGE_STATE_FILE, *code_paths):
            payloads[relative] = _read_regular(source, relative)
        final = _tree_state(source)
        if initial != final:
            raise CandidateSnapshotError("candidate_changed", str(source))

        for relative, payload in payloads.items():
            _write_private(private, relative, payload)
        copied = _tree_state(private)
        _check_closed_tree(copied, expected)
        for relative, payload in payloads.items():
            if _read_regular(private, relative) != payload:
                raise CandidateSnapshotError("candidate_changed", relative)

        for entry in sorted(private.rglob("*"), reverse=True):
            entry.chmod(0o500 if entry.is_dir() else 0o400)
        private.chmod(0o500)
        return CandidateSnapshot(
            private_root=private,
            manifest_bytes=manifest_bytes,
            language_state_bytes=payloads[LANGUAGE_STATE_FILE],
            code_files=MappingProxyType({path: payloads[path] for path in code_paths}),
        )
    except Exception:
        _remove_private(private)
        raise
