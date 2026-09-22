#!/usr/bin/env python3
"""Organize regular files through a frozen, reversible transaction.

The public executor has two phases. ``preview`` scans and enriches the input,
evaluates a closed policy grammar, stores the complete plan in the turn history
and returns its content digest. ``apply`` loads that exact plan, verifies that
the relevant filesystem state is unchanged and executes only the recorded
actions. It never evaluates the policy again.

Mutation is deliberately serial and protected by advisory locks on every
source and destination root. Directory walking, metadata extraction and
hashing use only the worker budget assigned by the central runtime.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import datetime as dt
import errno
import base64
import ctypes
import fcntl
import fnmatch
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator


sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(parent / "runtime") for parent in Path(__file__).resolve().parents
    if (parent / "runtime" / "config.py").is_file()))

import config as C  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from messages import get as _msg  # noqa: E402
from parallel_walk import parallel_map_ordered, parallel_walk  # noqa: E402


_SCHEMA = 1
_HASH_CHUNK = 1024 * 1024
_MAX_CONDITION_DEPTH = 8
_MAX_CONDITION_NODES = 128
_MAX_PATTERN = 256
_MAX_FIELD_TEXT = 4096
_MAX_FILES_CEILING = 100_000
_MAX_BYTES_CEILING = 10 * 1024**4
_MAX_PREVIEW_CEILING = 500
_MAX_TTL_SECONDS = 3600
_MAX_ROOTS = 128
_MAX_OPERATIONS = 128
_MAX_ENTRY_METADATA_BYTES = 64 * 1024
_PLAN_SUFFIX = ".frozen-plan.json"
_RECEIPT_SUFFIX = ".organize-receipt.json"
_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDER_RE = re.compile(
    r"\{([A-Za-z][A-Za-z0-9_.-]{0,127})(?:\|([a-z]+))?\}")
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)"),
    re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)"),
)
_IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff",
    ".webp", ".bmp", ".gif", ".avif",
})
_AV_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".avi", ".mkv",
    ".webm", ".mts", ".m2ts", ".mpg", ".mpeg", ".mp3", ".m4a",
    ".aac", ".flac", ".wav", ".ogg", ".opus",
})
_OPERATORS = frozenset({
    "equals", "in", "exists", "glob", "range", "before", "after",
})
_TRANSFORMS = frozenset({"lower", "upper", "slug", "year"})


class OrganizeError(RuntimeError):
    def __init__(
        self,
        code: str,
        message_key: str,
        *,
        error_class: str = "operation_failed",
        detail: str = "",
        message_args: dict[str, object] | None = None,
    ) -> None:
        super().__init__(detail or code)
        self.code = code
        self.message_key = message_key
        self.error_class = error_class
        self.detail = detail
        self.message_args = message_args or {}


def _failure(exc: OrganizeError, **extra: object) -> dict:
    out = {
        "ok": False,
        "ok_count": 0,
        "fail_count": 0,
        "results": [],
        "failed": [],
        "error_code": exc.code,
        "error_class": exc.error_class,
        "error": _msg(exc.message_key, **exc.message_args),
    }
    if exc.detail:
        out["diagnostic"] = exc.detail[:1000]
    out.update(extra)
    return out


def _invalid(detail: str) -> OrganizeError:
    return OrganizeError(
        "ERR_ORGANIZE_POLICY",
        "ERR_ORGANIZE_POLICY",
        error_class="invalid_args",
        detail=detail,
    )


def _identity(info: os.stat_result) -> dict[str, int]:
    return {
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "ctime_ns": int(info.st_ctime_ns),
        "mode": stat.S_IMODE(info.st_mode),
        "atime_ns": int(info.st_atime_ns),
        "uid": int(info.st_uid),
        "gid": int(info.st_gid),
        "nlink": int(info.st_nlink),
    }


def _identity_tuple(value: dict) -> tuple[int, int, int, int, int]:
    return tuple(int(value[key]) for key in (
        "device", "inode", "size", "mtime_ns", "ctime_ns"))


def _same_file_identity(actual: dict | None, expected: dict) -> bool:
    """Match a planned file without trusting content equality alone.

    Rename changes ctime, so recovery binds the stable inode and metadata
    fields which must survive a same-filesystem move.  A same-byte
    replacement is therefore a conflict, never mistaken for our effect.
    """
    if not isinstance(actual, dict):
        return False
    return all(int(actual.get(key, -1)) == int(expected.get(key, -2)) for key in (
        "device", "inode", "size", "mtime_ns", "mode", "uid", "gid",
        "nlink",
    ))


def _fault(point: str) -> None:
    """Process-death fault injection, inert outside explicitly marked tests."""
    if (os.environ.get("METNOS_TESTING") == "1"
            and os.environ.get("METNOS_ORGANIZE_FAULT") == point):
        os.kill(os.getpid(), signal.SIGKILL)


def _lstat_regular(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise OrganizeError(
            "ERR_PATH_NOT_FOUND", "ERR_PATH_NOT_FOUND",
            error_class="not_found", detail=str(path),
            message_args={"path": str(path)},
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        actual = "symlink" if stat.S_ISLNK(info.st_mode) else "non_regular"
        raise OrganizeError(
            "ERR_PATH_WRONG_TYPE", "ERR_PATH_WRONG_TYPE",
            error_class="wrong_type", detail=f"{path}: {actual}",
            message_args={"expected": "regular file", "actual": actual,
                          "path": str(path)},
        )
    return info


def _stable_sha256(
    path: Path,
    expected: tuple[int, int, int, int, int] | None = None,
) -> tuple[str, os.stat_result]:
    before = _lstat_regular(path)
    if expected is not None and _identity_tuple(_identity(before)) != expected:
        raise OrganizeError(
            "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
            error_class="stale_plan", detail=f"identity changed: {path}")
    digest = hashlib.sha256()
    flags = (os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NOATIME", 0))
    try:
        descriptor = os.open(path, flags)
    except PermissionError as exc:
        raise OrganizeError(
            "ERR_PERMISSION_DENIED", "ERR_PERMISSION_DENIED",
            error_class="permission_denied",
            detail=f"cannot hash without changing access time: {path}",
            message_args={"path": str(path)},
        ) from exc
    opened = os.fstat(descriptor)
    if _identity_tuple(_identity(opened)) != _identity_tuple(_identity(before)):
        os.close(descriptor)
        raise OrganizeError(
            "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
            error_class="stale_plan", detail=f"path changed while opening: {path}")
    with os.fdopen(descriptor, "rb") as stream:
        for block in iter(lambda: stream.read(_HASH_CHUNK), b""):
            digest.update(block)
        opened_after = os.fstat(stream.fileno())
    after = _lstat_regular(path)
    if (_identity_tuple(_identity(opened_after)) != _identity_tuple(_identity(opened))
            or _identity_tuple(_identity(after)) != _identity_tuple(_identity(before))):
        raise OrganizeError(
            "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
            error_class="stale_plan", detail=f"changed while hashing: {path}")
    return digest.hexdigest(), after


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")


def _bounded_int(
    raw: object,
    *,
    name: str,
    default: int,
    maximum: int,
    minimum: int = 1,
) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise _invalid(f"{name}: boolean is not an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise _invalid(f"{name}: integer required") from exc
    if value == 0:
        return maximum
    if not minimum <= value <= maximum:
        raise _invalid(f"{name}: expected {minimum}..{maximum}")
    return value


def _history_blob_dir() -> Path:
    turn_id = os.environ.get("METNOS_TURN_ID") or ""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", turn_id):
        raise OrganizeError(
            "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
            error_class="invalid_context", detail="invalid turn id")
    history = Path(os.environ.get("METNOS_HISTORY_DIR") or (
        C.PATH_USER_DATA / "_history"))
    return history.expanduser() / turn_id / "blob"


def _history_root() -> Path:
    return Path(os.environ.get("METNOS_HISTORY_DIR") or (
        C.PATH_USER_DATA / "_history")).expanduser().resolve(strict=True)


def _actor_binding() -> dict[str, str]:
    binding = {
        "actor": str(os.environ.get("METNOS_ACTOR") or ""),
        "owner_user_id": str(os.environ.get("METNOS_OWNER_USER_ID") or ""),
        "channel": str(os.environ.get("METNOS_CHANNEL") or ""),
        "turn_id": str(os.environ.get("METNOS_TURN_ID") or ""),
        "host": str(os.environ.get("METNOS_DEVICE_ID") or os.uname().nodename),
    }
    if not all(binding.values()):
        raise OrganizeError(
            "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
            error_class="invalid_context", detail="incomplete runtime binding")
    return binding


def _directory_identity(path: Path) -> dict[str, int]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OrganizeError(
            "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
            error_class="unsafe_target", detail=str(path))
    return {"device": int(info.st_dev), "inode": int(info.st_ino)}


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _resolve_without_symlinks(value: str, *, expected: str) -> Path:
    """Resolve an existing path while rejecting symlinks in every component."""
    lexical = Path(os.path.expanduser(value))
    if not lexical.is_absolute():
        lexical = Path.cwd() / lexical
    lexical = Path(os.path.abspath(lexical))
    current = Path(lexical.anchor)
    try:
        for part in lexical.parts[1:]:
            current /= part
            if stat.S_ISLNK(current.lstat().st_mode):
                raise OrganizeError(
                    "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                    error_class="unsafe_target", detail=f"symlink component: {current}")
    except FileNotFoundError as exc:
        raise OrganizeError(
            "ERR_PATH_NOT_FOUND", "ERR_PATH_NOT_FOUND",
            error_class="not_found", detail=str(current),
            message_args={"path": str(current)},
        ) from exc
    resolved = lexical.resolve(strict=True)
    info = resolved.lstat()
    valid = stat.S_ISDIR(info.st_mode) if expected == "directory" else stat.S_ISREG(info.st_mode)
    if not valid:
        raise OrganizeError(
            "ERR_PATH_WRONG_TYPE", "ERR_PATH_WRONG_TYPE",
            error_class="wrong_type", detail=str(resolved),
            message_args={"expected": expected, "actual": "other", "path": str(resolved)},
        )
    return resolved


def _write_json_atomic(path: Path, value: dict) -> str:
    payload = _canonical_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(payload).hexdigest()


def _read_json_verified(path: Path, expected_sha256: str | None = None) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OrganizeError(
            "ERR_ORGANIZE_PLAN_NOT_FOUND", "ERR_ORGANIZE_PLAN_NOT_FOUND",
            error_class="not_found", detail=str(path)) from exc
    if expected_sha256 and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise OrganizeError(
            "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
            error_class="integrity_error", detail=str(path))
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrganizeError(
            "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
            error_class="integrity_error", detail=str(path)) from exc
    if not isinstance(value, dict) or value.get("schema") != _SCHEMA:
        raise OrganizeError(
            "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
            error_class="integrity_error", detail=str(path))
    return value


def _normalize_path_list(
    raw: object,
    *,
    name: str,
    required: bool = False,
) -> list[str]:
    if not isinstance(raw, list) or (required and not raw) or len(raw) > _MAX_ROOTS:
        qualifier = "non-empty " if required else ""
        raise _invalid(f"{name} must be a {qualifier}array of directory paths")
    paths: set[str] = set()
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip():
            raise _invalid(f"{name}[{index}] must be a non-empty string")
        paths.add(str(_resolve_without_symlinks(value, expected="directory")))
    return sorted(paths, key=lambda item: (os.path.normcase(item).casefold(), item))


def _validate_condition(
    value: object,
    *,
    depth: int = 0,
    counter: list[int] | None = None,
) -> None:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > _MAX_CONDITION_NODES or depth > _MAX_CONDITION_DEPTH:
        raise _invalid("condition tree exceeds safety bounds")
    if not isinstance(value, dict) or not value:
        raise _invalid("condition must be a non-empty object")
    branches = [key for key in ("all", "any", "not") if key in value]
    has_leaf = "field" in value or "operator" in value
    if len(branches) + int(has_leaf) != 1:
        raise _invalid("condition must contain exactly one branch or one leaf")
    if branches:
        branch = branches[0]
        if set(value) != {branch}:
            raise _invalid(f"condition.{branch}: unexpected keys")
        child = value[branch]
        if branch in {"all", "any"}:
            if not isinstance(child, list) or not child:
                raise _invalid(f"condition.{branch}: non-empty list required")
            for item in child:
                _validate_condition(item, depth=depth + 1, counter=counter)
        else:
            _validate_condition(child, depth=depth + 1, counter=counter)
        return
    allowed = {"field", "operator", "value"}
    if set(value) - allowed:
        raise _invalid("condition leaf contains unknown keys")
    field = value.get("field")
    operator = value.get("operator")
    if not isinstance(field, str) or not _FIELD_RE.fullmatch(field):
        raise _invalid("condition field is invalid")
    if operator not in _OPERATORS:
        raise _invalid("condition operator is invalid")
    if operator != "exists" and "value" not in value:
        raise _invalid(f"condition {operator} requires value")
    if operator == "exists" and "value" in value \
            and not isinstance(value.get("value"), bool):
        raise _invalid("condition exists value must be a boolean")
    if operator == "in" and not isinstance(value.get("value"), list):
        raise _invalid("condition in requires an array value")
    if operator == "range":
        bounds = value.get("value")
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise _invalid("condition range requires [minimum, maximum]")
    if operator == "glob":
        pattern = value.get("value")
        if not isinstance(pattern, str) or not pattern or len(pattern) > _MAX_PATTERN:
            raise _invalid(f"condition {operator} pattern is invalid")


def _validate_template(template: object) -> str:
    if not isinstance(template, str) or not template or len(template) > 512:
        raise _invalid("path_template must be a string of at most 512 characters")
    if Path(template).is_absolute() or "\x00" in template:
        raise _invalid("path_template must be relative")
    cursor = 0
    for match in _PLACEHOLDER_RE.finditer(template):
        if "{" in template[cursor:match.start()] or "}" in template[cursor:match.start()]:
            raise _invalid("path_template contains an invalid placeholder")
        transform = match.group(2)
        if transform and transform not in _TRANSFORMS:
            raise _invalid("path_template transform is not allowed")
        cursor = match.end()
    if "{" in template[cursor:] or "}" in template[cursor:]:
        raise _invalid("path_template contains an invalid placeholder")
    parts = Path(template).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise _invalid("path_template contains an unsafe path segment")
    return template


def _normalize_policy(args: dict) -> dict:
    allowed_args = {
        "client", "mode", "source_paths", "compare_paths", "destination_roots",
        "recursive", "max_depth", "entries", "select",
        "operations", "max_files", "max_bytes", "max_preview",
        "plan_ttl_seconds",
    }
    if set(args) - allowed_args:
        raise _invalid("preview contains unexpected arguments")
    source_paths = _normalize_path_list(
        args.get("source_paths"), name="source_paths", required=True)
    compare_paths = _normalize_path_list(
        args.get("compare_paths", []), name="compare_paths")
    destination_roots = _normalize_path_list(
        args.get("destination_roots", []), name="destination_roots")
    recursive = args.get("recursive", True)
    if not isinstance(recursive, bool):
        raise _invalid("recursive must be a boolean")
    max_depth = args.get("max_depth", 0)
    if (isinstance(max_depth, bool) or not isinstance(max_depth, int)
            or not 0 <= max_depth <= 64):
        raise _invalid("max_depth must be between 0 and 64")
    sources = [
        {"path": path, "recursive": recursive, "max_depth": max_depth}
        for path in source_paths
    ]
    compare_with = [
        {"path": path, "recursive": recursive, "max_depth": max_depth}
        for path in compare_paths
    ]

    entries_raw = args.get("entries")
    entries: list[dict] | None = None
    if entries_raw is not None:
        if not isinstance(entries_raw, list):
            raise _invalid("entries must be an array")
        if len(entries_raw) > _MAX_FILES_CEILING:
            raise _invalid("entries exceeds the hard file limit")
        entries = []
        for index, item in enumerate(entries_raw):
            if not isinstance(item, dict):
                raise _invalid(f"entries[{index}] must be an object")
            path = item.get("path") or item.get("src")
            if not isinstance(path, str) or not path:
                raise _invalid(f"entries[{index}].path is required")
            if len(_canonical_bytes(item)) > _MAX_ENTRY_METADATA_BYTES:
                raise _invalid(f"entries[{index}] metadata is too large")
            resolved = _resolve_without_symlinks(path, expected="regular file")
            if not any(resolved == root or root in resolved.parents
                       for root in map(Path, source_paths)):
                raise _invalid(f"entries[{index}] is outside source_paths")
            copied = dict(item)
            copied["path"] = str(resolved)
            copied.pop("src", None)
            entries.append(copied)

    selection = args.get("select")
    if selection is not None:
        _validate_condition(selection)

    operations_raw = args.get("operations")
    if (not isinstance(operations_raw, list) or not operations_raw
            or len(operations_raw) > _MAX_OPERATIONS):
        raise _invalid("operations must be a non-empty array")
    operations: list[dict] = []
    seen_move = False
    deduplicate_count = 0
    for index, raw in enumerate(operations_raw):
        if not isinstance(raw, dict):
            raise _invalid(f"operations[{index}] must be an object")
        operation_type = raw.get("type")
        if operation_type == "deduplicate":
            if set(raw) - {"type", "match", "keep"}:
                raise _invalid(f"operations[{index}]: unexpected keys")
            if seen_move:
                raise _invalid("deduplicate operations must precede move operations")
            deduplicate_count += 1
            if deduplicate_count > 1:
                raise _invalid("only one deduplicate operation is allowed")
            if raw.get("match", "sha256") != "sha256":
                raise _invalid("deduplicate.match must be sha256")
            keep = raw.get("keep", "outside_sources_or_first")
            if keep not in {"outside_sources", "first_source", "outside_sources_or_first"}:
                raise _invalid("deduplicate.keep is invalid")
            if keep == "outside_sources" and not compare_with:
                raise _invalid("outside_sources requires compare_with")
            operations.append({"type": "deduplicate", "match": "sha256", "keep": keep})
            continue
        if operation_type != "move":
            raise _invalid(f"operations[{index}].type is invalid")
        if set(raw) - {
            "type", "when", "destination_root", "path_template",
            "on_missing", "on_conflict",
        }:
            raise _invalid(f"operations[{index}]: unexpected keys")
        seen_move = True
        when = raw.get("when")
        if when is not None:
            _validate_condition(when)
        root_value = raw.get("destination_root")
        if not isinstance(root_value, str) or not root_value:
            raise _invalid(f"operations[{index}].destination_root is required")
        destination_root = _resolve_without_symlinks(root_value, expected="directory")
        if str(destination_root) not in destination_roots:
            raise _invalid(
                f"operations[{index}].destination_root must be declared "
                "in destination_roots")
        on_missing = raw.get("on_missing", "next")
        on_conflict = raw.get("on_conflict", "fail")
        if on_missing not in {"next", "leave", "fail"}:
            raise _invalid("move.on_missing is invalid")
        if on_conflict not in {"fail", "hash_suffix"}:
            raise _invalid("move.on_conflict is invalid")
        operations.append({
            "type": "move",
            "when": when,
            "destination_root": str(destination_root),
            "path_template": _validate_template(raw.get("path_template")),
            "on_missing": on_missing,
            "on_conflict": on_conflict,
        })
    if not seen_move and not deduplicate_count:
        raise _invalid("policy contains no material operation")

    source_roots = [Path(spec["path"]) for spec in sources]
    for operation in operations:
        root_value = operation.get("destination_root")
        if not root_value:
            continue
        destination_root = Path(root_value)
        for source_root in source_roots:
            try:
                destination_root.relative_to(source_root)
            except ValueError:
                continue
            raise _invalid("destination_root must not be inside a source root")

    return {
        "sources": sources,
        "entries": entries,
        "compare_with": compare_with,
        "destination_roots": destination_roots,
        "select": selection,
        "operations": operations,
        "max_files": _bounded_int(
            args.get("max_files"), name="max_files", default=10_000,
            maximum=_MAX_FILES_CEILING),
        "max_bytes": _bounded_int(
            args.get("max_bytes"), name="max_bytes", default=100 * 1024**3,
            maximum=_MAX_BYTES_CEILING),
        "max_preview": _bounded_int(
            args.get("max_preview"), name="max_preview", default=100,
            maximum=_MAX_PREVIEW_CEILING),
        "plan_ttl_seconds": _bounded_int(
            args.get("plan_ttl_seconds"), name="plan_ttl_seconds", default=900,
            maximum=_MAX_TTL_SECONDS, minimum=60),
    }


def _scan_specs(specs: list[dict], *, max_files: int) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    failures: list[dict] = []
    seen: set[str] = set()
    for spec in specs:
        remaining = max_files - len(records)
        if remaining <= 0:
            raise OrganizeError(
                "ERR_ORGANIZE_LIMIT", "ERR_ORGANIZE_LIMIT",
                error_class="limit_exceeded", detail="max_files",
                message_args={"limit": max_files})
        root = Path(spec["path"])
        result = parallel_walk(
            root,
            accept=lambda path, kind, _depth: (
                kind == "file" and not path.name.startswith(".metnos-organize-")),
            transform=lambda path, _kind, _depth, entry: {
                "path": str(path), **_identity(entry.stat(follow_symlinks=False)),
            },
            recursive=bool(spec["recursive"]),
            max_depth=None if int(spec["max_depth"]) == 0 else int(spec["max_depth"]),
            max_items=remaining + 1,
        )
        if result.truncated or len(result.items) > remaining:
            raise OrganizeError(
                "ERR_ORGANIZE_LIMIT", "ERR_ORGANIZE_LIMIT",
                error_class="limit_exceeded", detail="max_files",
                message_args={"limit": max_files})
        for item in result.items:
            if item["path"] in seen:
                continue
            seen.add(item["path"])
            records.append(item)
        failures.extend({
            "path": str(error.path),
            "error_code": "ERR_PERMISSION_DENIED" if error.reason == "permission_denied"
            else "ERR_FILE_READ_FAILED",
            "diagnostic": error.reason,
        } for error in result.errors)
    records.sort(key=lambda item: (os.path.normcase(item["path"]).casefold(), item["path"]))
    failures.sort(key=lambda item: item["path"])
    return records, failures


def _inventory(records: list[dict]) -> list[dict]:
    keys = ("path", "device", "inode", "size", "mtime_ns", "ctime_ns")
    return [{key: record[key] for key in keys} for record in records]


def _root_and_relative(path: str, roots: list[str]) -> tuple[str, str]:
    candidate = Path(path)
    matches = [Path(root) for root in roots
               if candidate != Path(root) and Path(root) in candidate.parents]
    if not matches:
        raise _invalid(f"path is outside declared roots: {path}")
    root = max(matches, key=lambda item: len(item.parts))
    relative = str(candidate.relative_to(root))
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise _invalid(f"unsafe relative path: {path}")
    return str(root), relative


def _filename_date(path: Path) -> dt.datetime | None:
    found: set[dt.datetime] = set()
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(path.stem):
            try:
                found.add(dt.datetime(
                    int(match.group(1)), int(match.group(2)), int(match.group(3))))
            except ValueError:
                continue
    return next(iter(found)) if len(found) == 1 else None


def _parse_media_date(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    clean = value.strip().split("\x00", 1)[0]
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(clean[:19], fmt)
        except ValueError:
            continue
    try:
        parsed = dt.datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _capture_date(path: Path) -> dict | None:
    extension = path.suffix.casefold()
    parsed: dt.datetime | None = None
    source = ""
    if extension in _IMAGE_EXTENSIONS:
        try:
            from PIL import ExifTags, Image
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                exif = image.getexif()
                nested = exif.get_ifd(ExifTags.IFD.Exif) if hasattr(ExifTags, "IFD") else {}
                for tag in (36867, 36868, 306):
                    parsed = _parse_media_date(exif.get(tag) or nested.get(tag))
                    if parsed is not None:
                        source = "embedded"
                        break
        except Exception:
            parsed = None
    elif extension in _AV_EXTENSIONS:
        command = [
            "ffprobe", "-v", "error", "-show_entries",
            "format_tags=creation_time:stream_tags=creation_time",
            "-of", "json", "--", str(path),
        ]
        try:
            completed = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=10)
            payload = json.loads(completed.stdout or "{}") if completed.returncode == 0 else {}
            values: list[object] = []
            format_tags = ((payload.get("format") or {}).get("tags") or {})
            if isinstance(format_tags, dict):
                values.extend(value for key, value in format_tags.items()
                              if key.casefold() == "creation_time")
            for stream in payload.get("streams") or []:
                tags = (stream or {}).get("tags") or {}
                if isinstance(tags, dict):
                    values.extend(value for key, value in tags.items()
                                  if key.casefold() == "creation_time")
            parsed_values = {item for value in values
                             if (item := _parse_media_date(value)) is not None}
            if len(parsed_values) == 1:
                parsed = next(iter(parsed_values))
                source = "embedded"
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            parsed = None
    if parsed is None:
        parsed = _filename_date(path)
        source = "filename" if parsed is not None else ""
    if parsed is None or not 1900 <= parsed.year <= dt.datetime.now().year + 1:
        return None
    return {
        "iso": parsed.isoformat(timespec="seconds"),
        "year": parsed.year,
        "month": parsed.month,
        "day": parsed.day,
        "source": source,
    }


def _field_names(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        field = value.get("field")
        if isinstance(field, str):
            found.add(field)
        for child in value.values():
            found.update(_field_names(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_field_names(child))
    elif isinstance(value, str):
        found.update(match.group(1) for match in _PLACEHOLDER_RE.finditer(value))
    return found


def _enrich_one(item: tuple[dict, bool]) -> dict:
    record, need_capture_date = item
    path = Path(record["path"])
    out = dict(record)
    out.update({
        "name": path.name,
        "stem": path.stem,
        "extension": path.suffix.casefold().lstrip("."),
        "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "modified_time": dt.datetime.fromtimestamp(
            int(record["mtime_ns"]) / 1_000_000_000,
            tz=dt.timezone.utc).isoformat(),
    })
    if need_capture_date and not isinstance(out.get("capture_date"), dict):
        out["capture_date"] = _capture_date(path)
    return out


def _lookup(entry: dict, field: str) -> tuple[bool, object]:
    value: object = entry
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return value is not None, value


def _comparable(value: object) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return float(stripped)
        except ValueError:
            pass
        try:
            return dt.datetime.fromisoformat(stripped.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return stripped.casefold()
    return str(value).casefold()


def _evaluate(condition: dict, entry: dict) -> bool | None:
    if "all" in condition:
        values = [_evaluate(child, entry) for child in condition["all"]]
        return False if False in values else None if None in values else True
    if "any" in condition:
        values = [_evaluate(child, entry) for child in condition["any"]]
        return True if True in values else None if None in values else False
    if "not" in condition:
        value = _evaluate(condition["not"], entry)
        return None if value is None else not value
    exists, actual = _lookup(entry, condition["field"])
    operator = condition["operator"]
    if operator == "exists":
        expected = condition.get("value", True)
        return exists if expected is not False else not exists
    if not exists:
        return None
    expected = condition.get("value")
    if operator == "equals":
        return _comparable(actual) == _comparable(expected)
    if operator == "in":
        return any(_comparable(actual) == _comparable(item) for item in expected)
    text = str(actual)[:_MAX_FIELD_TEXT]
    if operator == "glob":
        return fnmatch.fnmatchcase(text.casefold(), str(expected).casefold())
    if operator == "range":
        value = _comparable(actual)
        low, high = (_comparable(item) if item is not None else None for item in expected)
        try:
            return (low is None or value >= low) and (high is None or value <= high)
        except TypeError:
            return False
    left, right = _comparable(actual), _comparable(expected)
    try:
        return left < right if operator == "before" else left > right
    except TypeError:
        return False


def _transform_value(value: object, transform: str | None) -> str:
    if transform == "year":
        if isinstance(value, dict):
            value = value.get("year")
        elif isinstance(value, str):
            value = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).year
    rendered = str(value)
    if transform == "lower":
        rendered = rendered.lower()
    elif transform == "upper":
        rendered = rendered.upper()
    elif transform == "slug":
        rendered = re.sub(r"[^A-Za-z0-9._-]+", "-", rendered).strip("-._")
    if (not rendered or rendered in {".", ".."} or "\x00" in rendered
            or "/" in rendered or "\\" in rendered):
        raise KeyError("unsafe template value")
    return rendered


def _render_destination(operation: dict, entry: dict) -> Path:
    template = operation["path_template"]

    def replace(match: re.Match) -> str:
        exists, value = _lookup(entry, match.group(1))
        if not exists:
            raise KeyError(match.group(1))
        return _transform_value(value, match.group(2))

    relative = _PLACEHOLDER_RE.sub(replace, template)
    root = Path(operation["destination_root"])
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise KeyError("destination escaped root") from exc
    return candidate


def _hash_suffix(path: Path, digest: str, reserved: set[str]) -> Path:
    stem, suffix = path.stem, path.suffix
    serial = 1
    while True:
        marker = f"__{digest[:12]}" if serial == 1 else f"__{digest[:12]}_{serial}"
        candidate = path.with_name(f"{stem}{marker}{suffix}")
        if str(candidate) not in reserved and not os.path.lexists(candidate):
            return candidate
        serial += 1


def _hash_records(records: list[dict]) -> tuple[list[dict], list[dict]]:
    def work(record: dict) -> dict:
        try:
            digest, _info = _stable_sha256(
                Path(record["path"]), _identity_tuple(record))
            return {"ok": True, "path": record["path"], "sha256": digest}
        except Exception as exc:
            return {"ok": False, "path": record["path"],
                    "diagnostic": f"{type(exc).__name__}: {exc}"}

    values = parallel_map_ordered(records, work)
    by_path = {item["path"]: item for item in values}
    failed = [item for item in values if not item["ok"]]
    output = []
    for record in records:
        item = by_path[record["path"]]
        if not item["ok"]:
            continue
        copied = dict(record)
        copied["sha256"] = item["sha256"]
        output.append(copied)
    return output, failed


def _plan_digest(plan: dict) -> str:
    material = {key: value for key, value in plan.items()
                if key not in {"token", "plan_path", "plan_sha256"}}
    return hashlib.sha256(_canonical_bytes(material)).hexdigest()


def _build_plan(policy: dict) -> dict:
    scanned_source_records, scan_failures = _scan_specs(
        policy["sources"], max_files=policy["max_files"])
    source_records = scanned_source_records
    if policy["entries"] is not None:
        scanned_by_path = {item["path"]: item for item in scanned_source_records}
        source_records = []
        seen_entries: set[str] = set()
        for entry in policy["entries"]:
            path = entry["path"]
            if path in seen_entries:
                raise _invalid(f"duplicate entry path: {path}")
            seen_entries.add(path)
            scanned = scanned_by_path.get(path)
            if scanned is None:
                raise OrganizeError(
                    "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                    error_class="stale_plan", detail=f"entry not in scan: {path}")
            enriched_entry = dict(entry)
            enriched_entry.update(scanned)
            source_records.append(enriched_entry)
        source_records.sort(
            key=lambda item: (os.path.normcase(item["path"]).casefold(), item["path"]))
    compare_records, compare_failures = _scan_specs(
        policy["compare_with"], max_files=policy["max_files"])
    source_paths = {record["path"] for record in source_records}
    compare_records = [record for record in compare_records
                       if record["path"] not in source_paths]
    failures = scan_failures + compare_failures

    referenced_fields = _field_names(policy["select"]) | _field_names(policy["operations"])
    need_capture_date = any(
        field == "capture_date" or field.startswith("capture_date.")
        for field in referenced_fields)
    enriched = parallel_map_ordered(
        [(record, need_capture_date) for record in source_records], _enrich_one)
    selected = [record for record in enriched
                if policy["select"] is None or _evaluate(policy["select"], record) is True]

    deduplicate = next((op for op in policy["operations"]
                        if op["type"] == "deduplicate"), None)
    move_operations = [op for op in policy["operations"] if op["type"] == "move"]
    # Every moved file needs a content hash for apply-time verification and
    # byte-exact undo, even when the policy itself does not mention hashes.
    need_hash = bool(deduplicate or move_operations)
    if any(field == "sha256" or field.startswith("sha256.")
           for field in referenced_fields):
        need_hash = True

    hash_failures: list[dict] = []
    if need_hash:
        selected_sizes = {int(record["size"]) for record in selected}
        compare_candidates = [record for record in compare_records
                              if int(record["size"]) in selected_sizes]
        hash_bytes = sum(int(record["size"]) for record in selected + compare_candidates)
        if hash_bytes > policy["max_bytes"]:
            raise OrganizeError(
                "ERR_ORGANIZE_LIMIT", "ERR_ORGANIZE_LIMIT",
                error_class="limit_exceeded", detail="max_bytes",
                message_args={"limit": policy["max_bytes"]})
        selected, failed_source_hashes = _hash_records(selected)
        compare_candidates, failed_compare_hashes = _hash_records(compare_candidates)
        hash_failures = failed_source_hashes + failed_compare_hashes
    else:
        compare_candidates = []
        source_bytes = sum(int(record["size"]) for record in selected)
        if source_bytes > policy["max_bytes"]:
            raise OrganizeError(
                "ERR_ORGANIZE_LIMIT", "ERR_ORGANIZE_LIMIT",
                error_class="limit_exceeded", detail="max_bytes",
                message_args={"limit": policy["max_bytes"]})
    failures.extend(hash_failures)

    actions: list[dict] = []
    deleted_paths: set[str] = set()
    if deduplicate:
        source_by_hash: dict[str, list[dict]] = {}
        compare_by_hash: dict[str, list[dict]] = {}
        for record in selected:
            source_by_hash.setdefault(record["sha256"], []).append(record)
        for record in compare_candidates:
            compare_by_hash.setdefault(record["sha256"], []).append(record)
        for digest, group in sorted(source_by_hash.items()):
            group.sort(key=lambda item: item["path"].casefold())
            external = sorted(compare_by_hash.get(digest, []),
                              key=lambda item: item["path"].casefold())
            keep = deduplicate["keep"]
            if external and keep in {"outside_sources", "outside_sources_or_first"}:
                reference = external[0]
                duplicates = group
            elif keep in {"first_source", "outside_sources_or_first"} and len(group) > 1:
                reference = group[0]
                duplicates = group[1:]
            else:
                continue
            for record in duplicates:
                deleted_paths.add(record["path"])
                actions.append({
                    "action": "delete_duplicate",
                    "source": record["path"],
                    "source_identity": {key: record[key] for key in (
                        "device", "inode", "size", "mtime_ns", "ctime_ns",
                        "mode", "atime_ns", "uid", "gid", "nlink")},
                    "sha256": digest,
                    "reference": reference["path"],
                    "reference_identity": {key: reference[key] for key in (
                        "device", "inode", "size", "mtime_ns", "ctime_ns")},
                    "reference_sha256": digest,
                })

    reserved: set[str] = set()
    move_by_source: dict[str, str] = {}
    blocked = False
    for record in selected:
        if record["path"] in deleted_paths:
            continue
        decided = False
        for operation in move_operations:
            verdict = True if operation["when"] is None else _evaluate(operation["when"], record)
            if verdict is False:
                continue
            if verdict is None:
                if operation["on_missing"] == "next":
                    continue
                action = "leave_unresolved" if operation["on_missing"] == "leave" else "blocked_missing"
                actions.append({"action": action, "source": record["path"]})
                blocked = blocked or action.startswith("blocked")
                decided = True
                break
            try:
                destination = _render_destination(operation, record)
            except (KeyError, ValueError):
                if operation["on_missing"] == "next":
                    continue
                action = "leave_unresolved" if operation["on_missing"] == "leave" else "blocked_missing"
                actions.append({"action": action, "source": record["path"]})
                blocked = blocked or action.startswith("blocked")
                decided = True
                break
            if destination == Path(record["path"]):
                actions.append({"action": "leave_unchanged", "source": record["path"]})
                decided = True
                break
            collision = str(destination) in reserved or os.path.lexists(destination)
            if collision:
                if operation["on_conflict"] == "fail":
                    actions.append({
                        "action": "blocked_conflict", "source": record["path"],
                        "destination": str(destination),
                    })
                    blocked = True
                    decided = True
                    break
                digest = record.get("sha256")
                if not isinstance(digest, str):
                    raise _invalid("hash_suffix requires sha256")
                destination = _hash_suffix(destination, digest, reserved)
            reserved.add(str(destination))
            move_by_source[record["path"]] = str(destination)
            actions.append({
                "action": "move",
                "source": record["path"],
                "destination": str(destination),
                "destination_root": operation["destination_root"],
                "source_identity": {key: record[key] for key in (
                    "device", "inode", "size", "mtime_ns", "ctime_ns",
                    "mode", "atime_ns", "uid", "gid", "nlink")},
                "sha256": record.get("sha256"),
            })
            decided = True
            break
        if not decided:
            actions.append({"action": "leave_unmatched", "source": record["path"]})

    source_roots = [item["path"] for item in policy["sources"]]
    readable_roots = source_roots + [item["path"] for item in policy["compare_with"]]
    root_identities = {
        path: _directory_identity(Path(path))
        for path in sorted(set(readable_roots + policy["destination_roots"]))
    }
    for action in actions:
        if action["action"] not in {"move", "delete_duplicate"}:
            continue
        source_root, source_relative = _root_and_relative(
            action["source"], source_roots)
        action.update(source_root=source_root, source_relative=source_relative,
                      source_root_identity=root_identities[source_root])
        if action["action"] == "move":
            destination_root, destination_relative = _root_and_relative(
                action["destination"], policy["destination_roots"])
            action.update(destination_root=destination_root,
                          destination_relative=destination_relative,
                          destination_root_identity=root_identities[destination_root])
        else:
            reference_root, reference_relative = _root_and_relative(
                action["reference"], readable_roots)
            action.update(reference_root=reference_root,
                          reference_relative=reference_relative,
                          reference_root_identity=root_identities[reference_root])
    move_actions = {item["source"]: item for item in actions
                    if item["action"] == "move"}
    for action in actions:
        if action["action"] != "delete_duplicate":
            continue
        moved_reference = move_actions.get(action["reference"])
        if moved_reference:
            action.update(
                reference_after_apply=moved_reference["destination"],
                reference_after_root=moved_reference["destination_root"],
                reference_after_relative=moved_reference["destination_relative"],
                reference_after_root_identity=moved_reference["destination_root_identity"],
            )
        else:
            action.update(
                reference_after_apply=action["reference"],
                reference_after_root=action["reference_root"],
                reference_after_relative=action["reference_relative"],
                reference_after_root_identity=action["reference_root_identity"],
            )
    actions.sort(key=lambda item: item["source"].casefold())
    now = int(time.time())
    plan = {
        "schema": _SCHEMA,
        "created_at": now,
        "expires_at": now + policy["plan_ttl_seconds"],
        "binding": _actor_binding(),
        "root_identities": root_identities,
        "policy": policy,
        "source_inventory": _inventory(scanned_source_records),
        "compare_inventory": _inventory(compare_records),
        "actions": actions,
        "scan_complete": not failures,
        "failures": failures,
        "blocked": blocked,
    }
    plan["token"] = _plan_digest(plan)
    return plan


def _action_preview(action: dict) -> dict:
    return {key: action[key] for key in (
        "action", "source", "destination", "reference") if key in action}


def _preview(args: dict) -> dict:
    policy = _normalize_policy(args)
    plan = _build_plan(policy)
    mutations = [item for item in plan["actions"]
                 if item["action"] in {"move", "delete_duplicate"}]
    # Anything that cannot be executed losslessly is a blocked preview, not a
    # consent form that can only fail after approval.
    for action in mutations:
        try:
            if action["action"] == "move":
                _assert_move_preflight(action)
            elif int(action["source_identity"].get("nlink") or 0) != 1:
                raise OrganizeError(
                    "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                    error_class="unsupported_metadata", detail="hardlinked duplicate")
        except (OrganizeError, OSError) as exc:
            plan["blocked"] = True
            plan["failures"].append({
                "path": action.get("source"),
                "diagnostic": f"{type(exc).__name__}: {exc}",
            })
    apply_ready = bool(plan["scan_complete"] and not plan["blocked"])
    if apply_ready:
        blob_dir = _history_blob_dir()
        plan_path = blob_dir / f"{plan['token']}{_PLAN_SUFFIX}"
        _write_json_atomic(plan_path, plan)
    shown = [_action_preview(item) for item in plan["actions"][:policy["max_preview"]]]
    counts: dict[str, int] = {}
    for item in plan["actions"]:
        counts[item["action"]] = counts.get(item["action"], 0) + 1
    out = {
        "ok": apply_ready,
        "mode": "preview",
        "apply_ready": apply_ready,
        "scan_complete": plan["scan_complete"],
        "confirmation_token": plan["token"] if apply_ready else None,
        "expires_at": plan["expires_at"] if apply_ready else None,
        "source_count": len(plan["source_inventory"]),
        "compare_count": len(plan["compare_inventory"]),
        "move_count": counts.get("move", 0),
        "duplicate_count": counts.get("delete_duplicate", 0),
        "unresolved_count": sum(value for key, value in counts.items()
                                if key.startswith("leave_") or key.startswith("blocked_")),
        "source_paths": [item["path"] for item in policy["sources"]],
        "compare_paths": [item["path"] for item in policy["compare_with"]],
        "destination_roots": policy["destination_roots"],
        "results": shown,
        "failed": plan["failures"],
        "fail_count": len(plan["failures"]),
        "ok_count": 0,
        "_undo": {"outcome": "no_effect"},
    }
    if len(shown) < len(plan["actions"]):
        out.update({
            "truncated": True, "truncated_what": "planned_actions",
            "used": len(shown), "available_total": len(plan["actions"]),
            "cap_field": "max_preview", "cap_value": policy["max_preview"],
        })
    if not plan["scan_complete"]:
        out.update({
            "error_code": "ERR_ORGANIZE_SCAN_INCOMPLETE",
            "error_class": "io_error",
            "error": _msg("ERR_ORGANIZE_SCAN_INCOMPLETE"),
        })
    elif plan["blocked"]:
        out.update({
            "error_code": "ERR_ORGANIZE_PLAN_BLOCKED",
            "error_class": "conflict",
            "error": _msg("ERR_ORGANIZE_PLAN_BLOCKED"),
        })
    elif mutations:
        out.update({"decision": "needs_confirmation", "requires_confirmation": True})
    return out


def _plan_path(token: str) -> Path:
    return _history_blob_dir() / f"{token}{_PLAN_SUFFIX}"


def _load_plan(token: object, *, allow_expired: bool = False) -> dict:
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise OrganizeError(
            "ERR_ORGANIZE_CONFIRMATION_REQUIRED",
            "ERR_ORGANIZE_CONFIRMATION_REQUIRED",
            error_class="needs_confirmation")
    plan = _read_json_verified(_plan_path(token))
    if plan.get("token") != token or _plan_digest(plan) != token:
        raise OrganizeError(
            "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
            error_class="integrity_error", detail="plan digest mismatch")
    if plan.get("binding") != _actor_binding():
        raise OrganizeError(
            "ERR_ORGANIZE_CONFIRMATION_REQUIRED",
            "ERR_ORGANIZE_CONFIRMATION_REQUIRED",
            error_class="permission_denied", detail="actor binding mismatch")
    if not allow_expired and int(plan.get("expires_at") or 0) < int(time.time()):
        raise OrganizeError(
            "ERR_ORGANIZE_PLAN_EXPIRED", "ERR_ORGANIZE_PLAN_EXPIRED",
            error_class="stale_plan")
    if not plan.get("scan_complete") or plan.get("blocked"):
        raise OrganizeError(
            "ERR_ORGANIZE_PLAN_BLOCKED", "ERR_ORGANIZE_PLAN_BLOCKED",
            error_class="conflict")
    return plan


def _assert_apply_scope(args: dict, plan: dict) -> None:
    supplied = {
        "source_paths": _normalize_path_list(
            args.get("source_paths"), name="source_paths", required=True),
        "compare_paths": _normalize_path_list(
            args.get("compare_paths", []), name="compare_paths"),
        "destination_roots": _normalize_path_list(
            args.get("destination_roots", []), name="destination_roots"),
    }
    expected = {
        "source_paths": [item["path"] for item in plan["policy"]["sources"]],
        "compare_paths": [item["path"] for item in plan["policy"]["compare_with"]],
        "destination_roots": plan["policy"]["destination_roots"],
    }
    if supplied != expected:
        raise OrganizeError(
            "ERR_ORGANIZE_CONFIRMATION_REQUIRED",
            "ERR_ORGANIZE_CONFIRMATION_REQUIRED",
            error_class="permission_denied", detail="apply scope differs from preview")


def _assert_inventory(plan: dict) -> None:
    policy = plan["policy"]
    for path, expected in (plan.get("root_identities") or {}).items():
        if _directory_identity(Path(path)) != expected:
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail=f"root identity changed: {path}")
    current_sources, source_failures = _scan_specs(
        policy["sources"], max_files=policy["max_files"])
    current_compare, compare_failures = _scan_specs(
        policy["compare_with"], max_files=policy["max_files"])
    source_paths = {record["path"] for record in current_sources}
    current_compare = [record for record in current_compare
                       if record["path"] not in source_paths]
    if source_failures or compare_failures \
            or _inventory(current_sources) != plan["source_inventory"] \
            or _inventory(current_compare) != plan["compare_inventory"]:
        raise OrganizeError(
            "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
            error_class="stale_plan")


def _lock_roots(plan_or_receipt: dict) -> list[Path]:
    roots: set[Path] = set()
    policy = plan_or_receipt.get("policy") or {}
    for spec in (policy.get("sources") or []) + (policy.get("compare_with") or []):
        roots.add(Path(spec["path"]))
    for entry in policy.get("entries") or []:
        roots.add(Path(entry["path"]).parent)
    for operation in policy.get("operations") or []:
        if operation.get("destination_root"):
            roots.add(Path(operation["destination_root"]))
    return sorted(roots, key=lambda path: str(path).casefold())


@contextmanager
def _exclusive_roots(roots: list[Path]) -> Iterator[None]:
    with ExitStack() as stack:
        descriptors: list[int] = []
        for root in roots:
            existing = root
            while not existing.exists() and existing.parent != existing:
                existing = existing.parent
            descriptor = os.open(existing, os.O_RDONLY | os.O_DIRECTORY)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            descriptors.append(descriptor)
        try:
            yield
        finally:
            for descriptor in reversed(descriptors):
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


def _open_root_fd(path: str, expected: dict) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OrganizeError(
            "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
            error_class="stale_plan", detail=f"root unavailable: {path}") from exc
    info = os.fstat(descriptor)
    if (not stat.S_ISDIR(info.st_mode)
            or int(info.st_dev) != int(expected["device"])
            or int(info.st_ino) != int(expected["inode"])):
        os.close(descriptor)
        raise OrganizeError(
            "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
            error_class="stale_plan", detail=f"root identity changed: {path}")
    return descriptor


def _open_parent_at(root_fd: int, relative: str) -> tuple[int, str]:
    parts = Path(relative).parts
    if (not parts or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in parts)):
        raise OrganizeError(
            "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
            error_class="unsafe_target", detail=relative)
    current = os.dup(root_fd)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        for part in parts[:-1]:
            child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = child
        return current, parts[-1]
    except BaseException:
        os.close(current)
        raise


def _open_regular_at(root_fd: int, relative: str, *, expected: dict | None = None,
                     writable: bool = False) -> tuple[int, int, str]:
    parent_fd, name = _open_parent_at(root_fd, relative)
    flags = ((os.O_RDWR if writable else os.O_RDONLY) | os.O_CLOEXEC
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NOATIME", 0))
    try:
        try:
            descriptor = os.open(name, flags, dir_fd=parent_fd)
        except PermissionError:
            flags &= ~getattr(os, "O_NOATIME", 0)
            descriptor = os.open(name, flags, dir_fd=parent_fd)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OrganizeError(
                "ERR_PATH_WRONG_TYPE", "ERR_PATH_WRONG_TYPE",
                error_class="wrong_type", detail=relative,
                message_args={"expected": "regular file", "actual": "other",
                              "path": relative})
        if expected is not None and _identity_tuple(_identity(info)) != _identity_tuple(expected):
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail=relative)
        return descriptor, parent_fd, name
    except BaseException:
        os.close(parent_fd)
        raise


def _hash_fd(descriptor: int) -> str:
    before = os.fstat(descriptor)
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        block = os.read(descriptor, _HASH_CHUNK)
        if not block:
            break
        digest.update(block)
    after = os.fstat(descriptor)
    if _identity_tuple(_identity(before)) != _identity_tuple(_identity(after)):
        raise OrganizeError(
            "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
            error_class="stale_plan", detail="file changed while hashing")
    return digest.hexdigest()


def _write_all(descriptor: int, data: bytes) -> None:
    """Write one block completely, retrying an interrupted syscall."""
    remaining = memoryview(data)
    while remaining:
        try:
            written = os.write(descriptor, remaining)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError(errno.EIO, "short write made no progress")
        remaining = remaining[written:]


def _metadata_fd(descriptor: int) -> dict:
    info = os.fstat(descriptor)
    attributes = []
    try:
        for name in sorted(os.listxattr(descriptor)):
            value = os.getxattr(descriptor, name)
            attributes.append({"name": name, "value_b64": base64.b64encode(value).decode("ascii")})
    except OSError as exc:
        raise OrganizeError(
            "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
            error_class="permission_denied", detail=f"xattr read failed: {exc}") from exc
    return {
        "mode": stat.S_IMODE(info.st_mode), "uid": int(info.st_uid),
        "gid": int(info.st_gid), "atime_ns": int(info.st_atime_ns),
        "mtime_ns": int(info.st_mtime_ns), "nlink": int(info.st_nlink),
        "xattrs": attributes,
    }


def _capture_restore_metadata(action: dict) -> dict:
    """Read and bind all metadata required for a byte-exact restore."""
    root_fd = _open_root_fd(action["source_root"], action["source_root_identity"])
    descriptor = parent_fd = -1
    try:
        descriptor, parent_fd, _name = _open_regular_at(
            root_fd, action["source_relative"], expected=action["source_identity"])
        if _hash_fd(descriptor) != action["sha256"]:
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail=action["source"])
        current = os.fstat(descriptor)
        if int(current.st_nlink) != 1:
            raise OrganizeError(
                "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                error_class="unsupported_metadata", detail="hardlinked duplicate")
        metadata = _metadata_fd(descriptor)
        if os.geteuid() != 0 and (
                metadata["uid"] != os.geteuid()
                or metadata["gid"] not in set(os.getgroups()) | {os.getegid()}):
            raise OrganizeError(
                "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                error_class="unsupported_metadata", detail="ownership is not restorable")
        return metadata
    finally:
        for value in (descriptor, parent_fd, root_fd):
            if value >= 0:
                try:
                    os.close(value)
                except OSError:
                    pass


def _verify_metadata_roundtrip(item: dict) -> None:
    """Prove restore metadata on the exact source parent or fail closed."""
    metadata = item["metadata"]
    root_fd = _open_root_fd(item["source_root"], item["source_root_identity"])
    parent_fd = descriptor = -1
    name = f".metnos-metadata-probe-{secrets.token_hex(16)}"
    try:
        parent_fd, _target_name = _open_parent_at(
            root_fd, item["source_relative"])
        descriptor = os.open(
            name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
        os.write(descriptor, b"metnos-metadata-probe")
        current = os.fstat(descriptor)
        if (int(current.st_uid), int(current.st_gid)) != (
                int(metadata["uid"]), int(metadata["gid"])):
            os.fchown(descriptor, int(metadata["uid"]), int(metadata["gid"]))
        os.fchmod(descriptor, int(metadata["mode"]))
        for attribute in metadata.get("xattrs") or []:
            os.setxattr(
                descriptor, str(attribute["name"]),
                base64.b64decode(str(attribute["value_b64"]), validate=True))
        os.utime(descriptor, ns=(
            int(metadata["atime_ns"]), int(metadata["mtime_ns"])))
        os.fsync(descriptor)
        observed = _metadata_fd(descriptor)
        expected_xattrs = metadata.get("xattrs") or []
        if (any(int(observed[key]) != int(metadata[key]) for key in (
                "mode", "uid", "gid", "atime_ns", "mtime_ns"))
                or observed.get("xattrs") != expected_xattrs):
            raise OrganizeError(
                "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                error_class="unsupported_metadata",
                detail="metadata roundtrip mismatch")
    except OrganizeError:
        raise
    except (OSError, ValueError, KeyError) as exc:
        raise OrganizeError(
            "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
            error_class="unsupported_metadata",
            detail=f"metadata roundtrip failed: {exc}") from exc
    finally:
        if parent_fd >= 0 and descriptor >= 0:
            try:
                candidate = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                opened = os.fstat(descriptor)
                if ((int(candidate.st_dev), int(candidate.st_ino))
                        == (int(opened.st_dev), int(opened.st_ino))):
                    os.unlink(name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            except OSError:
                pass
        for value in (descriptor, parent_fd, root_fd):
            if value >= 0:
                try:
                    os.close(value)
                except OSError:
                    pass


def _copy_backup(source: Path, destination: Path, action: dict) -> None:
    expected = _identity_tuple(action["source_identity"])
    root_fd = _open_root_fd(action["source_root"], action["source_root_identity"])
    descriptor = parent_fd = -1
    try:
        descriptor, parent_fd, _name = _open_regular_at(
            root_fd, action["source_relative"], expected=action["source_identity"])
        before = os.fstat(descriptor)
        if int(before.st_nlink) != 1:
            raise OrganizeError(
                "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                error_class="unsupported_metadata", detail="hardlinked duplicate")
        metadata = _metadata_fd(descriptor)
        expected_metadata = action.get("metadata") or action.get("restore_metadata")
        if metadata != expected_metadata:
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail="metadata changed before backup")
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_fd >= 0:
            os.close(parent_fd)
        os.close(root_fd)
        raise
    if destination.exists():
        digest, _info = _stable_sha256(destination)
        if digest != action["sha256"]:
            raise OrganizeError(
                "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                error_class="integrity_error", detail=str(destination))
        os.close(descriptor)
        os.close(parent_fd)
        os.close(root_fd)
        return
    try:
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp",
            dir=str(destination.parent))
    except BaseException:
        os.close(descriptor)
        os.close(parent_fd)
        os.close(root_fd)
        raise
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    fd = temporary_fd
    temporary_identity = os.fstat(fd)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(fd, "wb", closefd=False) as outgoing:
            for block in iter(lambda: os.read(descriptor, _HASH_CHUNK), b""):
                digest.update(block)
                outgoing.write(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        after = os.fstat(descriptor)
        if _identity_tuple(_identity(after)) != expected or digest.hexdigest() != action["sha256"]:
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail=str(source))
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            saved, _info = _stable_sha256(destination)
            if saved != action["sha256"]:
                raise OrganizeError(
                    "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                    error_class="integrity_error", detail=str(destination))
        _fsync_directory(destination.parent)
        saved, _info = _stable_sha256(destination)
        if saved != action["sha256"]:
            raise OrganizeError(
                "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                error_class="integrity_error", detail=str(destination))
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            candidate = temporary.lstat()
            if ((int(candidate.st_dev), int(candidate.st_ino))
                    == (int(temporary_identity.st_dev),
                        int(temporary_identity.st_ino))):
                temporary.unlink()
        except FileNotFoundError:
            pass
        os.close(descriptor)
        os.close(parent_fd)
        os.close(root_fd)


def _rename_noreplace(source_parent_fd: int, source_name: str,
                      destination_parent_fd: int, destination_name: str) -> None:
    """Atomic Linux rename constrained to two already-open directories."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OrganizeError(
            "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
            error_class="capability_unavailable", detail="renameat2 unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p,
                          ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_parent_fd, os.fsencode(source_name),
        destination_parent_fd, os.fsencode(destination_name), 1)
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise OrganizeError(
            "ERR_DST_EXISTS", "ERR_DST_EXISTS", error_class="conflict",
            detail=destination_name, message_args={"path": destination_name})
    if error == errno.EXDEV:
        raise OrganizeError(
            "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
            error_class="cross_device_unsupported", detail=destination_name)
    raise OSError(error, os.strerror(error), source_name)


def _move_noreplace(source: Path, destination: Path, action: dict) -> list[dict]:
    source_root_fd = _open_root_fd(
        action["source_root"], action["source_root_identity"])
    destination_root_fd = _open_root_fd(
        action["destination_root"], action["destination_root_identity"])
    source_fd = source_parent_fd = destination_parent_fd = -1
    try:
        if int(os.fstat(source_root_fd).st_dev) != int(os.fstat(destination_root_fd).st_dev):
            raise OrganizeError(
                "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                error_class="cross_device_unsupported", detail=str(destination))
        source_fd, source_parent_fd, source_name = _open_regular_at(
            source_root_fd, action["source_relative"],
            expected=action["source_identity"])
        digest = _hash_fd(source_fd)
        if action.get("sha256") and digest != action["sha256"]:
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail=str(source))
        destination_parent_fd, destination_name = _open_parent_at(
            destination_root_fd, action["destination_relative"])
        source_info = os.fstat(source_fd)
        named_source = os.stat(
            source_name, dir_fd=source_parent_fd, follow_symlinks=False)
        if ((int(named_source.st_dev), int(named_source.st_ino))
                != (int(source_info.st_dev), int(source_info.st_ino))):
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail=str(source))
        _rename_noreplace(
            source_parent_fd, source_name,
            destination_parent_fd, destination_name)
        destination_info = os.stat(
            destination_name, dir_fd=destination_parent_fd,
            follow_symlinks=False)
        if ((int(destination_info.st_dev), int(destination_info.st_ino))
                != (int(source_info.st_dev), int(source_info.st_ino))):
            raise OrganizeError(
                "ERR_ORGANIZE_APPLY", "ERR_ORGANIZE_APPLY",
                error_class="integrity_error", detail=str(destination))
        os.fsync(destination_parent_fd)
        if source_parent_fd != destination_parent_fd:
            os.fsync(source_parent_fd)
        return []
    finally:
        for descriptor in (source_fd, source_parent_fd, destination_parent_fd,
                           source_root_fd, destination_root_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _relative_file_state(root: str, root_identity: dict, relative: str,
                         expected_hash: str) -> tuple[str, dict | None]:
    root_fd = _open_root_fd(root, root_identity)
    parent_fd = descriptor = -1
    try:
        try:
            descriptor, parent_fd, _name = _open_regular_at(root_fd, relative)
        except (FileNotFoundError, NotADirectoryError):
            return "absent", None
        info = _identity(os.fstat(descriptor))
        digest = _hash_fd(descriptor)
        return ("expected", info) if digest == expected_hash else ("changed", info)
    finally:
        for value in (descriptor, parent_fd, root_fd):
            if value >= 0:
                try:
                    os.close(value)
                except OSError:
                    pass


def _restore_blob(item: dict, *, journal: dict,
                  receipt_path: Path) -> bool:
    expected = item["blob_sha256"]
    target = Path(item["path"])
    state, _info = _relative_file_state(
        item["source_root"], item["source_root_identity"],
        item["source_relative"], expected)
    if state == "expected":
        accepted = (item.get("restored_identity")
                    or item.get("restore_intent_identity"))
        if accepted and _same_file_identity(_info, accepted):
            item["restored_identity"] = _info
            return False
        raise OrganizeError(
            "ERR_DST_EXISTS", "ERR_DST_EXISTS", error_class="conflict",
            detail=str(target), message_args={"path": str(target)})
    if state != "absent":
        raise OrganizeError(
            "ERR_DST_EXISTS", "ERR_DST_EXISTS", error_class="conflict",
            detail=str(target), message_args={"path": str(target)})
    pending_identity = item.get("restore_intent_identity")
    pending_name = item.get("restore_temp_name")
    if pending_identity or pending_name:
        if (not isinstance(pending_identity, dict)
                or not isinstance(pending_name, str)
                or not re.fullmatch(
                    r"\.metnos-restore-[0-9a-f]{16}-[0-9a-f]{24}",
                    pending_name)):
            raise OrganizeError(
                "ERR_ORGANIZE_RECEIPT_INVALID",
                "ERR_ORGANIZE_RECEIPT_INVALID",
                error_class="integrity_error",
                detail="invalid pending restore intent")
        root_fd = _open_root_fd(
            item["source_root"], item["source_root_identity"])
        descriptor = parent_fd = -1
        try:
            target_relative = Path(item["source_relative"])
            temp_relative = str(target_relative.parent / pending_name)
            descriptor, parent_fd, temp_name = _open_regular_at(
                root_fd, temp_relative, expected=pending_identity)
            if (_hash_fd(descriptor) != expected
                    or _metadata_fd(descriptor) != (item.get("metadata") or {})):
                raise OrganizeError(
                    "ERR_ORGANIZE_RECEIPT_INVALID",
                    "ERR_ORGANIZE_RECEIPT_INVALID",
                    error_class="integrity_error",
                    detail="pending restore temporary changed")
            _rename_noreplace(
                parent_fd, temp_name, parent_fd, target_relative.name)
            item["restored_identity"] = _identity(os.fstat(descriptor))
            os.fsync(parent_fd)
            return True
        finally:
            for value in (descriptor, parent_fd, root_fd):
                if value >= 0:
                    try:
                        os.close(value)
                    except OSError:
                        pass
    blob = Path(item["blob_path"])
    digest, blob_info = _stable_sha256(blob)
    if digest != expected:
        raise OrganizeError(
            "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
            error_class="integrity_error", detail=str(blob))
    metadata = item.get("metadata") or {}
    root_fd = _open_root_fd(item["source_root"], item["source_root_identity"])
    parent_fd = -1
    temporary_name = (
        f".metnos-restore-{expected[:16]}-{secrets.token_hex(12)}")
    descriptor = -1
    try:
        parent_fd, target_name = _open_parent_at(
            root_fd, item["source_relative"])
        descriptor = os.open(
            temporary_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
        copied = hashlib.sha256()
        with blob.open("rb") as incoming:
            for block in iter(lambda: incoming.read(_HASH_CHUNK), b""):
                copied.update(block)
                _write_all(descriptor, block)
        written_info = os.fstat(descriptor)
        if (copied.hexdigest() != expected
                or int(written_info.st_size) != int(blob_info.st_size)
                or _hash_fd(descriptor) != expected):
            raise OrganizeError(
                "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
                error_class="integrity_error",
                detail=f"restored temporary differs from backup: {blob}")
        current = os.fstat(descriptor)
        uid, gid = int(metadata["uid"]), int(metadata["gid"])
        if (int(current.st_uid), int(current.st_gid)) != (uid, gid):
            os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, int(metadata["mode"]))
        for attribute in metadata.get("xattrs") or []:
            os.setxattr(
                descriptor, str(attribute["name"]),
                base64.b64decode(str(attribute["value_b64"]), validate=True))
        os.utime(descriptor, ns=(int(metadata["atime_ns"]), int(metadata["mtime_ns"])))
        os.fsync(descriptor)
        os.fsync(parent_fd)
        # Durable inverse intent: if the process dies after rename but before
        # the outer action journal, recovery can distinguish our inode from a
        # same-byte file created by another actor.
        item["restore_temp_name"] = temporary_name
        item["restore_intent_identity"] = _identity(os.fstat(descriptor))
        _write_journal(receipt_path, journal)
        _fault("after_restore_intent_before_rename")
        _rename_noreplace(
            parent_fd, temporary_name, parent_fd, target_name)
        item["restored_identity"] = _identity(os.fstat(descriptor))
        os.fsync(parent_fd)
        return True
    except BaseException:
        if parent_fd >= 0 and descriptor >= 0:
            try:
                candidate = os.stat(
                    temporary_name, dir_fd=parent_fd,
                    follow_symlinks=False)
                opened = os.fstat(descriptor)
                if ((int(candidate.st_dev), int(candidate.st_ino))
                        == (int(opened.st_dev), int(opened.st_ino))):
                    os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        for value in (descriptor, parent_fd, root_fd):
            if value >= 0:
                try:
                    os.close(value)
                except OSError:
                    pass


def _reverse_move(item: dict) -> bool:
    source_state, _source_info = _relative_file_state(
        item["source_root"], item["source_root_identity"],
        item["source_relative"], item["sha256"])
    destination_state, destination_info = _relative_file_state(
        item["destination_root"], item["destination_root_identity"],
        item["destination_relative"], item["sha256"])
    if (source_state == "expected"
            and _same_file_identity(_source_info, item["source_identity"])
            and destination_state == "absent"):
        return False
    if (source_state != "absent" or destination_state != "expected"
            or not _same_file_identity(
                destination_info, item["source_identity"])):
        raise OrganizeError(
            "ERR_ORGANIZE_UNDO", "ERR_ORGANIZE_UNDO",
            error_class="conflict", detail=item["destination"])
    action = {
        "source": item["destination"], "destination": item["source"],
        "source_root": item["destination_root"],
        "source_relative": item["destination_relative"],
        "source_root_identity": item["destination_root_identity"],
        "destination_root": item["source_root"],
        "destination_relative": item["source_relative"],
        "destination_root_identity": item["source_root_identity"],
        "source_identity": destination_info, "sha256": item["sha256"],
    }
    _move_noreplace(Path(item["destination"]), Path(item["source"]), action)
    return True


def _receipt_path(token: str) -> Path:
    return _history_blob_dir() / f"{token}{_RECEIPT_SUFFIX}"


def _write_journal(path: Path, journal: dict) -> str:
    journal["updated_at"] = int(time.time())
    return _write_json_atomic(path, journal)


def _receipt_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare_journal(plan: dict, mutations: list[dict], blob_dir: Path) -> dict:
    entries: list[dict] = []
    for action in mutations:
        item = dict(action)
        item["kind"] = "delete" if action["action"] == "delete_duplicate" else "move"
        item["state"] = "pending"
        if item["kind"] == "delete":
            item.update({
                "path": action["source"],
                "blob_path": str(blob_dir / f"{action['sha256']}.bin"),
                "blob_sha256": action["sha256"],
                "metadata": dict(action["restore_metadata"]),
            })
        entries.append(item)
    return {
        "schema": _SCHEMA,
        "kind": "organize_files_transaction",
        "status": "prepared",
        "created_at": int(time.time()),
        "binding": plan["binding"],
        "token": plan["token"],
        "policy": plan["policy"],
        "root_identities": plan["root_identities"],
        "actions": entries,
        "failure": None,
    }


def _ensure_backups(journal: dict, receipt_path: Path) -> None:
    """Finish every journaled delete backup before any user-file mutation."""
    for item in journal.get("actions") or []:
        if item.get("kind") != "delete" or item.get("backup_ready"):
            continue
        _verify_metadata_roundtrip(item)
        _copy_backup(Path(item["source"]), Path(item["blob_path"]), item)
        item["backup_ready"] = True
        _write_journal(receipt_path, journal)


def _move_state(item: dict) -> str:
    source_state, source_info = _relative_file_state(
        item["source_root"], item["source_root_identity"],
        item["source_relative"], item["sha256"])
    destination_state, destination_info = _relative_file_state(
        item["destination_root"], item["destination_root_identity"],
        item["destination_relative"], item["sha256"])
    source_owned = _same_file_identity(source_info, item["source_identity"])
    destination_owned = _same_file_identity(
        destination_info, item["source_identity"])
    if (source_state == "expected" and destination_state == "expected"
            and (source_info["device"], source_info["inode"]) == (
                destination_info["device"], destination_info["inode"])):
        # Atomic rename never leaves both names behind.  This is necessarily
        # a hardlink introduced outside our transaction, regardless of the
        # resulting nlink/ctime change on the shared inode.
        return "external_conflict"
    if source_state == "expected" and source_owned \
            and destination_state == "absent":
        return "original"
    if ((source_state == "expected" and not source_owned
            and destination_state == "absent")
            or (source_state == "expected" and source_owned
                and destination_state in {"expected", "changed"}
                and not destination_owned)):
        return "external_conflict"
    if source_state == "absent" and destination_state == "expected" \
            and destination_owned:
        return "applied"
    return "conflict"


def _delete_state(item: dict) -> str:
    state, info = _relative_file_state(
        item["source_root"], item["source_root_identity"],
        item["source_relative"], item["sha256"])
    accepted = item.get("restored_identity") or item["source_identity"]
    if state == "expected" and _same_file_identity(info, accepted):
        return "original"
    if state == "absent":
        return "applied"
    return "conflict"


def _reconcile_journal(journal: dict) -> list[str]:
    states = []
    for item in journal.get("actions") or []:
        current = _move_state(item) if item.get("kind") == "move" else _delete_state(item)
        item["observed_state"] = current
        states.append(current)
    return states


def _delete_anchored(item: dict) -> None:
    reference_state, _reference_info = _relative_file_state(
        item["reference_after_root"], item["reference_after_root_identity"],
        item["reference_after_relative"], item["reference_sha256"])
    if reference_state != "expected":
        raise OrganizeError(
            "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
            error_class="stale_plan", detail=item["reference_after_apply"])
    root_fd = _open_root_fd(item["source_root"], item["source_root_identity"])
    descriptor = parent_fd = -1
    try:
        descriptor, parent_fd, name = _open_regular_at(
            root_fd, item["source_relative"], expected=item["source_identity"])
        if _hash_fd(descriptor) != item["sha256"]:
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail=item["source"])
        # The hard-link topology is part of the frozen identity.  Re-check at
        # the last possible point: an added link between preflight and unlink
        # must fail closed rather than deleting a name from altered topology.
        current = _identity(os.fstat(descriptor))
        if (int(current["nlink"]) != 1
                or not _same_file_identity(current, item["source_identity"])):
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail=item["source"])
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        for value in (descriptor, parent_fd, root_fd):
            if value >= 0:
                try:
                    os.close(value)
                except OSError:
                    pass


def _validate_reverse_receipt(receipt: dict, receipt_path: Path) -> None:
    if (receipt.get("kind") != "organize_files_transaction"
            or receipt.get("status") not in {
                "committed", "partial", "undoing", "undone"}
            or not isinstance(receipt.get("actions"), list)):
        raise OrganizeError(
            "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
            error_class="integrity_error", detail=str(receipt_path))
    blob_root = receipt_path.parent
    for item in receipt["actions"]:
        if not isinstance(item, dict) or item.get("kind") not in {"move", "delete"}:
            raise _invalid("invalid receipt action")
        expected = item.get("sha256")
        required = {
            "source_root", "source_root_identity", "source_relative", "source",
        }
        if (not required.issubset(item) or not isinstance(expected, str)
                or not _TOKEN_RE.fullmatch(expected)):
            raise _invalid("invalid receipt action")
        if item["kind"] == "move":
            if not {"destination_root", "destination_root_identity",
                    "destination_relative", "destination"}.issubset(item):
                raise _invalid("invalid move receipt action")
            continue
        blob_value = item.get("blob_path")
        if not isinstance(blob_value, str):
            raise _invalid("invalid backup receipt action")
        blob = _resolve_without_symlinks(blob_value, expected="regular file")
        try:
            blob.relative_to(blob_root)
        except ValueError as exc:
            raise OrganizeError(
                "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
                error_class="integrity_error", detail=str(blob)) from exc
        if blob.name != f"{expected}.bin" or _stable_sha256(blob)[0] != expected:
            raise OrganizeError(
                "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
                error_class="integrity_error", detail=str(blob))


def _journal_output(journal: dict, receipt_path: Path, receipt_sha: str) -> dict:
    moved = [item for item in journal["actions"] if item["kind"] == "move"]
    deleted = [item for item in journal["actions"] if item["kind"] == "delete"]
    results = [
        {"kind": "move", "src": item["source"], "dst": item["destination"],
         "sha256": item["sha256"]} for item in moved
    ] + [
        {"kind": "delete", "path": item["source"],
         "reference": item["reference_after_apply"],
         "blob_sha256": item["blob_sha256"]} for item in deleted
    ]
    undo = ({"outcome": "no_effect"} if not results else {
        "outcome": "reversible", "reverse_pattern": "module.reverse",
        "receipt_path": str(receipt_path), "receipt_sha256": receipt_sha,
    })
    return {
        "ok": True, "mode": "apply", "ok_count": len(results), "fail_count": 0,
        "results": results, "failed": [], "move_count": len(moved),
        "duplicate_count": len(deleted), "confirmation_token": journal["token"],
        "receipt_path": str(receipt_path) if results else None,
        "receipt_sha256": receipt_sha if results else None, "_undo": undo,
    }


def _partial_failure(journal: dict, receipt_path: Path, detail: str,
                     failures: list[dict]) -> dict:
    states = _reconcile_journal(journal)
    has_effect = any(state in {"applied", "conflict"} for state in states)
    journal["status"] = "partial" if has_effect else "rolled_back"
    journal["failure"] = detail[:1000]
    journal["rollback_failures"] = failures
    receipt_sha = _write_journal(receipt_path, journal)
    error = OrganizeError(
        "ERR_ORGANIZE_ROLLBACK_PARTIAL" if has_effect else "ERR_ORGANIZE_APPLY",
        "ERR_ORGANIZE_ROLLBACK_PARTIAL" if has_effect else "ERR_ORGANIZE_APPLY",
        error_class="partial_failure" if has_effect else "operation_failed",
        detail=detail)
    extra: dict[str, object] = {
        "failed": failures, "fail_count": len(failures),
        "_undo": {"outcome": "no_effect"},
    }
    if has_effect:
        extra.update({"partial": True, "_undo": {
            "outcome": "reversible", "reverse_pattern": "module.reverse",
            "receipt_path": str(receipt_path), "receipt_sha256": receipt_sha,
        }})
    return _failure(error, **extra)


def _persisted_failure(journal: dict, receipt_path: Path) -> dict:
    states = _reconcile_journal(journal)
    has_effect = any(state in {"applied", "conflict"} for state in states)
    failures = journal.get("rollback_failures") or []
    error = OrganizeError(
        "ERR_ORGANIZE_ROLLBACK_PARTIAL" if has_effect else "ERR_ORGANIZE_APPLY",
        "ERR_ORGANIZE_ROLLBACK_PARTIAL" if has_effect else "ERR_ORGANIZE_APPLY",
        error_class="partial_failure" if has_effect else "operation_failed",
        detail=str(journal.get("failure") or "previous transaction did not commit"))
    extra: dict[str, object] = {
        "failed": failures, "fail_count": len(failures),
        "_undo": {"outcome": "no_effect"},
    }
    if has_effect:
        extra.update({"partial": True, "_undo": {
            "outcome": "reversible", "reverse_pattern": "module.reverse",
            "receipt_path": str(receipt_path),
            "receipt_sha256": _receipt_digest(receipt_path),
        }})
    return _failure(error, **extra)


def _rollback_journal(journal: dict, receipt_path: Path,
                      cause: BaseException) -> dict:
    failures: list[dict] = []
    for item in reversed(journal.get("actions") or []):
        try:
            state = _move_state(item) if item["kind"] == "move" else _delete_state(item)
            if item["kind"] == "delete" and state == "applied":
                _restore_blob(
                    item, journal=journal, receipt_path=receipt_path)
            elif item["kind"] == "move" and state == "applied":
                _reverse_move(item)
            elif state in {"conflict", "external_conflict"}:
                raise OrganizeError(
                    "ERR_ORGANIZE_UNDO", "ERR_ORGANIZE_UNDO",
                    error_class="conflict", detail=item.get("source", ""))
            item["state"] = "rolled_back"
        except Exception as exc:
            failures.append({
                "path": item.get("destination") or item.get("source"),
                "diagnostic": f"{type(exc).__name__}: {exc}",
            })
    return _partial_failure(
        journal, receipt_path, f"{type(cause).__name__}: {cause}", failures)


def _assert_move_preflight(action: dict) -> None:
    source_root_fd = _open_root_fd(action["source_root"], action["source_root_identity"])
    destination_root_fd = _open_root_fd(
        action["destination_root"], action["destination_root_identity"])
    source_fd = source_parent_fd = destination_parent_fd = -1
    try:
        if os.fstat(source_root_fd).st_dev != os.fstat(destination_root_fd).st_dev:
            raise OrganizeError(
                "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                error_class="cross_device_unsupported", detail=action["destination"])
        source_fd, source_parent_fd, _name = _open_regular_at(
            source_root_fd, action["source_relative"], expected=action["source_identity"])
        if _hash_fd(source_fd) != action["sha256"]:
            raise OrganizeError(
                "ERR_ORGANIZE_STALE_PLAN", "ERR_ORGANIZE_STALE_PLAN",
                error_class="stale_plan", detail=action["source"])
        destination_parent_fd, destination_name = _open_parent_at(
            destination_root_fd, action["destination_relative"])
        try:
            os.stat(destination_name, dir_fd=destination_parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise OrganizeError(
            "ERR_DST_EXISTS", "ERR_DST_EXISTS", error_class="conflict",
            detail=action["destination"], message_args={"path": action["destination"]})
    finally:
        for value in (source_fd, source_parent_fd, destination_parent_fd,
                      source_root_fd, destination_root_fd):
            if value >= 0:
                try:
                    os.close(value)
                except OSError:
                    pass


def _apply(args: dict) -> dict:
    if set(args) - {
        "client", "mode", "confirmation_token", "source_paths",
        "compare_paths", "destination_roots",
    }:
        raise _invalid("apply contains unexpected arguments")
    token = args.get("confirmation_token")
    authorization = os.environ.get("METNOS_FROZEN_PLAN_AUTHORIZATION") or ""
    if (not isinstance(token, str) or not authorization
            or not hmac.compare_digest(token, authorization)):
        raise OrganizeError(
            "ERR_ORGANIZE_CONFIRMATION_REQUIRED", "ERR_ORGANIZE_CONFIRMATION_REQUIRED",
            error_class="needs_confirmation", detail="runtime grant missing")
    receipt_path = _receipt_path(token)
    plan = _load_plan(token, allow_expired=receipt_path.exists())
    _assert_apply_scope(args, plan)
    with _exclusive_roots(_lock_roots(plan)):
        if receipt_path.exists():
            journal = _read_json_verified(receipt_path)
            if (journal.get("token") != token or journal.get("binding") != plan["binding"]
                    or journal.get("kind") != "organize_files_transaction"):
                raise OrganizeError(
                    "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
                    error_class="integrity_error", detail=str(receipt_path))
            if journal.get("status") in {"undoing", "undone"}:
                return _failure(OrganizeError(
                    "ERR_ORGANIZE_APPLY", "ERR_ORGANIZE_APPLY",
                    error_class="conflict",
                    detail="transaction already reversed or reverse in progress"),
                    _undo={"outcome": "no_effect"})
            states = _reconcile_journal(journal)
            if journal.get("status") == "committed" and all(
                    state == "applied" for state in states):
                return _journal_output(journal, receipt_path, _receipt_digest(receipt_path))
            if journal.get("status") in {"partial", "rolled_back"}:
                return _persisted_failure(journal, receipt_path)
            if states and all(state == "applied" for state in states):
                journal["status"] = "committed"
                journal["committed_at"] = int(time.time())
                receipt_sha = _write_journal(receipt_path, journal)
                return _journal_output(journal, receipt_path, receipt_sha)
            if any(state != "original" for state in states):
                return _rollback_journal(
                    journal, receipt_path, RuntimeError("recovering interrupted transaction"))
        else:
            _assert_inventory(plan)
            mutations = [item for item in plan["actions"]
                         if item["action"] in {"move", "delete_duplicate"}]
            blob_dir = _history_blob_dir()
            blob_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            deletes = [item for item in mutations if item["action"] == "delete_duplicate"]
            unique_backup_bytes = sum({
                item["sha256"]: int(item["source_identity"]["size"])
                for item in deletes}.values())
            quota = min(
                int(os.environ.get("METNOS_ORGANIZE_HISTORY_MAX_BYTES") or 100 * 1024**3),
                _MAX_BYTES_CEILING,
            )
            if unique_backup_bytes > quota \
                    or shutil.disk_usage(blob_dir).free < unique_backup_bytes + 64 * 1024**2:
                raise OrganizeError(
                    "ERR_ORGANIZE_PREFLIGHT", "ERR_ORGANIZE_PREFLIGHT",
                    error_class="resource_exhausted", detail="backup quota or space")
            for action in mutations:
                if action["action"] == "move":
                    _assert_move_preflight(action)
                else:
                    action["restore_metadata"] = _capture_restore_metadata(action)
            journal = _prepare_journal(plan, mutations, blob_dir)
            # Write-ahead barrier: no user-visible mutation precedes this fsync.
            _write_journal(receipt_path, journal)

        try:
            _fault("after_wal")
            _ensure_backups(journal, receipt_path)
            journal["status"] = "applying"
            for item in journal["actions"]:
                item["state"] = "intent"
                _write_journal(receipt_path, journal)
                if item["kind"] == "move":
                    _move_noreplace(Path(item["source"]), Path(item["destination"]), item)
                else:
                    _delete_anchored(item)
                _fault("after_effect_before_journal")
                item["state"] = "applied"
                _write_journal(receipt_path, journal)
            journal["status"] = "committed"
            journal["committed_at"] = int(time.time())
            receipt_sha = _write_journal(receipt_path, journal)
            return _journal_output(journal, receipt_path, receipt_sha)
        except BaseException as exc:
            return _rollback_journal(journal, receipt_path, exc)


def invoke(args: object) -> dict:
    if not isinstance(args, dict):
        return _failure(_invalid("args must be an object"),
                        _undo={"outcome": "no_effect"})
    client = args.get("client", "local")
    if client != "local":
        return _failure(OrganizeError(
            "ERR_NOT_APPLICABLE", "ERR_NOT_APPLICABLE",
            error_class="not_applicable", detail=f"client={client}",
            message_args={"what": f"client '{client}'"}),
            _undo={"outcome": "no_effect"})
    mode = args.get("mode", "preview")
    try:
        if mode == "preview":
            return _preview(args)
        if mode == "apply":
            return _apply(args)
        raise _invalid("mode must be preview or apply")
    except OrganizeError as exc:
        return _failure(exc, _undo={"outcome": "no_effect"})
    except PermissionError as exc:
        return _failure(OrganizeError(
            "ERR_PERMISSION_DENIED", "ERR_PERMISSION_DENIED",
            error_class="permission_denied", detail=str(exc),
            message_args={"path": str(exc.filename or "")}),
            _undo={"outcome": "no_effect"})
    except OSError as exc:
        return _failure(OrganizeError(
            "ERR_ORGANIZE_APPLY", "ERR_ORGANIZE_APPLY",
            error_class="io_error", detail=f"{type(exc).__name__}: {exc}"),
            _undo={"outcome": "no_effect"})


def reverse(_plan: dict, results: dict) -> dict:
    try:
        if not isinstance(results, dict):
            raise _invalid("results must be an object")
        undo = results.get("_undo")
        if not isinstance(undo, dict):
            raise OrganizeError(
                "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
                error_class="invalid_args")
        path_value = undo.get("receipt_path")
        digest = undo.get("receipt_sha256")
        if (not isinstance(path_value, str) or not Path(path_value).is_absolute()
                or not isinstance(digest, str) or not _TOKEN_RE.fullmatch(digest)):
            raise OrganizeError(
                "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
                error_class="invalid_args")
        receipt_path = _resolve_without_symlinks(
            path_value, expected="regular file")
        try:
            receipt_path.relative_to(_history_root())
        except ValueError as exc:
            raise OrganizeError(
                "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
                error_class="integrity_error", detail=str(receipt_path)) from exc
        if not receipt_path.name.endswith(_RECEIPT_SUFFIX):
            raise OrganizeError(
                "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
                error_class="integrity_error", detail=str(receipt_path))
        receipt = _read_json_verified(receipt_path)
        current_binding = _actor_binding()
        receipt_binding = receipt.get("binding") or {}
        if any(not hmac.compare_digest(
                str(receipt_binding.get(key) or ""), str(current_binding.get(key) or ""))
                for key in ("actor", "owner_user_id", "host")):
            raise OrganizeError(
                "ERR_ORGANIZE_RECEIPT_INVALID", "ERR_ORGANIZE_RECEIPT_INVALID",
                error_class="permission_denied", detail="actor binding mismatch")
        _validate_reverse_receipt(receipt, receipt_path)
        status = receipt.get("status")
        if status in {"undoing", "undone"}:
            if not hmac.compare_digest(
                    str(receipt.get("commit_receipt_sha256") or ""), digest):
                raise OrganizeError(
                    "ERR_ORGANIZE_RECEIPT_INVALID",
                    "ERR_ORGANIZE_RECEIPT_INVALID",
                    error_class="integrity_error", detail=str(receipt_path))
            if status == "undone":
                return {"ok": True, "ok_count": 0, "fail_count": 0,
                        "results": [], "failed": []}
        else:
            if not hmac.compare_digest(_receipt_digest(receipt_path), digest):
                raise OrganizeError(
                    "ERR_ORGANIZE_RECEIPT_INVALID",
                    "ERR_ORGANIZE_RECEIPT_INVALID",
                    error_class="integrity_error", detail=str(receipt_path))
            # Reverse is itself journaled before its first filesystem effect.
            # The original committed digest remains the stable broker key for
            # crash retry and repeated undo.
            receipt["commit_receipt_sha256"] = digest
            receipt["status"] = "undoing"
            receipt["undo_started_at"] = int(time.time())
            _write_journal(receipt_path, receipt)
        restored: list[dict] = []
        failed: list[dict] = []
        with _exclusive_roots(_lock_roots(receipt)):
            for item in reversed(receipt.get("actions") or []):
                try:
                    if item["kind"] == "delete":
                        changed = _restore_blob(
                            item, journal=receipt,
                            receipt_path=receipt_path)
                        action = "restore_deleted"
                        path = item["source"]
                    else:
                        changed = _reverse_move(item)
                        action = "move_back"
                        path = item["source"]
                    _fault("after_reverse_effect_before_journal")
                    if changed:
                        restored.append({"path": path, "action": action})
                    item["undo_state"] = "undone"
                    item.pop("undo_error", None)
                    _write_journal(receipt_path, receipt)
                except Exception as exc:
                    failure = {
                        "path": item.get("destination") or item.get("source"),
                        "diagnostic": f"{type(exc).__name__}: {exc}"}
                    item["undo_error"] = failure["diagnostic"]
                    failed.append(failure)
                    _write_journal(receipt_path, receipt)
        receipt["undo_failures"] = failed
        if failed:
            receipt["status"] = "undoing"
        else:
            receipt["status"] = "undone"
            receipt["undone_at"] = int(time.time())
        _write_journal(receipt_path, receipt)
        out = {
            "ok": not failed,
            "ok_count": len(restored),
            "fail_count": len(failed),
            "results": restored,
            "failed": failed,
        }
        if restored and failed:
            out["partial"] = True
        if failed:
            out.update({
                "error_code": "ERR_ORGANIZE_UNDO",
                "error_class": "operation_failed",
                "error": _msg("ERR_ORGANIZE_UNDO"),
            })
        return out
    except OrganizeError as exc:
        return _failure(exc)
    except Exception as exc:
        return _failure(OrganizeError(
            "ERR_ORGANIZE_UNDO", "ERR_ORGANIZE_UNDO",
            error_class="operation_failed", detail=f"{type(exc).__name__}: {exc}"))


def main() -> None:
    run_stdio(
        invoke,
        error_extra={
            "error_class": "invalid_args", "error_code": "ERR_ARG_INVALID",
            "ok_count": 0, "fail_count": 0, "results": [], "failed": [],
            "_undo": {"outcome": "no_effect"},
        },
    )


if __name__ == "__main__":
    main()
