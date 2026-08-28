"""The single door through which one executor may load another's code.

This module deliberately does **not** live in the ``executor_birth_*``
namespace.  Inside the gate, dynamic evaluation is forbidden outright, because
code evaluated there could rebuild the authority that writes to disk; the
acceptance base enforces that.  This door is the opposite kind of thing: it is
consumed by executors, never by the gate, and a cell proves no gate module
imports it.  Naming it as gate code would have been a claim that is not true.

Loading a module from a path is not forbidden and not granted by trust: it is
granted by *authentication*.  A candidate may not open a path and execute what
it finds there, but it may ask this door for the code of a published executor,
and the door hands back a module only after the bytes it is about to run match
the signature that admitted them.

That is stricter than what existed before.  The previous arrangement resolved a
path and executed whatever was at it, with no comparison of any kind: a builtin
was allowed to do it because it was a builtin, not because anything checked.
Here the bytes are read once, digested, compared, and then executed **from the
copy already in memory**, so nothing can be swapped between the check and the
run.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from code_file_paths import PortableCodePathError, validate_portable_code_files

ADMITTED_MODULE_NAME_V1 = "_metnos_admitted_executor"
MAXIMUM_CODE_FILE_BYTES_V1 = 4 * 1024 * 1024
ADMITTED_EXECUTORS_ENV_V1 = "METNOS_ADMITTED_EXECUTORS_V1"
MAXIMUM_ADMITTED_ENV_BYTES_V1 = 64 * 1024


class AdmittedModuleError(RuntimeError):
    """The code of a published executor cannot be authenticated."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _read_exact_file_v1(path: Path) -> bytes:
    """Read one regular file through a handle that follows no final link."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AdmittedModuleError("admitted_module_unreadable", path.name) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise AdmittedModuleError("admitted_module_not_regular", path.name)
        if info.st_size > MAXIMUM_CODE_FILE_BYTES_V1:
            raise AdmittedModuleError("admitted_module_too_large", path.name)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(MAXIMUM_CODE_FILE_BYTES_V1 + 1)
    finally:
        os.close(descriptor)


def code_digest_of_bytes_v1(payloads) -> str:
    """Digest the declared code files from bytes already held.

    The signer digests the same concatenation by reopening each path.  Doing it
    from the bytes that will actually run removes the gap between the two, and
    a cell proves the two agree.
    """
    digest = hashlib.sha256()
    for payload in payloads:
        digest.update(payload)
    return f"sha256:{digest.hexdigest()}"


def _closed_code_files_v1(value: object) -> tuple[str, ...]:
    try:
        return validate_portable_code_files(value)
    except PortableCodePathError as exc:
        raise AdmittedModuleError("admitted_module_files_undeclared") from exc


def _contained_file_v1(directory: Path, relative: str) -> Path:
    """Resolve one validated relative path without crossing a link."""
    try:
        root_info = directory.lstat()
    except OSError as exc:
        raise AdmittedModuleError("admitted_module_unreadable") from exc
    if (not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
            or bool(getattr(root_info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))):
        raise AdmittedModuleError("admitted_module_path_invalid")
    current = directory
    for component in relative.split("/"):
        current = current / component
        try:
            info = current.lstat()
        except OSError as exc:
            raise AdmittedModuleError(
                "admitted_module_unreadable", Path(relative).name,
            ) from exc
        if stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise AdmittedModuleError(
                "admitted_module_unreadable", Path(relative).name,
            )
    try:
        root = directory.resolve(strict=True)
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise AdmittedModuleError(
            "admitted_module_path_invalid", Path(relative).name,
        ) from exc
    return current


@dataclass(frozen=True, slots=True)
class AdmittedExecutorRecordV1:
    """Bounded record projected by the parent from its verified catalogue."""

    name: str
    manifest_path: Path
    code_path: Path
    digest: str
    code_files: tuple[str, ...]


def encode_admitted_executor_records_v1(executors) -> str:
    """Encode only fields already authenticated in the parent catalogue."""
    records = []
    names: set[str] = set()
    for executor in executors:
        name = str(getattr(executor, "name", "") or "")
        if (not name or not name.isascii() or len(name) > 128
                or name in names):
            raise AdmittedModuleError("admitted_module_record_invalid")
        names.add(name)
        code_files = _closed_code_files_v1(
            getattr(executor, "code_files", ()),
        )
        manifest_path = Path(getattr(executor, "manifest_path", "") or "")
        code_path = Path(getattr(executor, "code_path", "") or "")
        digest = str(getattr(executor, "digest", "") or "")
        if not manifest_path.name or not code_path.name:
            raise AdmittedModuleError("admitted_module_record_invalid")
        records.append({
            "code_files": list(code_files),
            "code_path": str(code_path),
            "digest": digest,
            "manifest_path": str(manifest_path),
            "name": name,
        })
    encoded = json.dumps(
        records, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > MAXIMUM_ADMITTED_ENV_BYTES_V1:
        raise AdmittedModuleError("admitted_module_record_invalid")
    return encoded


def admitted_code_dependency_projection_v1(
    executor, catalog,
) -> tuple[str, list[Path]]:
    """Project only signed dependency names from a verified catalogue."""
    names = getattr(executor, "code_dependencies", ()) or ()
    if (
        not isinstance(names, tuple)
        or len(names) > 32
        or len(names) != len(set(names))
        or any(
            not isinstance(name, str)
            or not name
            or not name.isascii()
            or len(name) > 128
            for name in names
        )
    ):
        raise AdmittedModuleError("admitted_module_record_invalid")
    if not names:
        return "", []
    records = []
    roots: list[Path] = []
    for name in names:
        target = catalog.get(name)
        if target is None or getattr(target, "name", None) != name:
            raise AdmittedModuleError("admitted_module_dependency_unavailable")
        records.append(target)
        root = Path(getattr(target, "manifest_path", "") or "").parent
        if not root.is_absolute():
            raise AdmittedModuleError("admitted_module_record_invalid")
        if root not in roots:
            roots.append(root)
    return encode_admitted_executor_records_v1(records), roots


def runtime_admitted_executor_v1(name: str) -> AdmittedExecutorRecordV1:
    """Read one parent-owned verified record made visible to this process."""
    encoded = os.environ.get(ADMITTED_EXECUTORS_ENV_V1, "")
    if not encoded or len(encoded.encode("utf-8")) > MAXIMUM_ADMITTED_ENV_BYTES_V1:
        raise AdmittedModuleError("admitted_module_dependency_unavailable")
    try:
        values = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise AdmittedModuleError("admitted_module_record_invalid") from exc
    if not isinstance(values, list) or len(values) > 32:
        raise AdmittedModuleError("admitted_module_record_invalid")
    matches = []
    expected = {"name", "manifest_path", "code_path", "digest", "code_files"}
    for value in values:
        if not isinstance(value, dict) or set(value) != expected:
            raise AdmittedModuleError("admitted_module_record_invalid")
        if value.get("name") == name:
            matches.append(value)
    if len(matches) != 1:
        raise AdmittedModuleError("admitted_module_dependency_unavailable")
    value = matches[0]
    if any(not isinstance(value[key], str) for key in (
            "name", "manifest_path", "code_path", "digest")):
        raise AdmittedModuleError("admitted_module_record_invalid")
    return AdmittedExecutorRecordV1(
        name=value["name"],
        manifest_path=Path(value["manifest_path"]),
        code_path=Path(value["code_path"]),
        digest=value["digest"],
        code_files=_closed_code_files_v1(value["code_files"]),
    )


def load_admitted_module_v1(executor) -> ModuleType:
    """Return the module of a published executor, authenticated first.

    ``executor`` is a loaded catalogue record: the loader already verified its
    signature, so what is added here is the guarantee that the bytes on disk
    right now are still the ones that signature covers.

    A record with no signed digest belongs to the installed distribution — its
    code changes only with a deployment — and is admitted only when it really
    lives under the distribution's own executor root.  Neither branch accepts a
    path chosen by the caller.
    """
    manifest_path = Path(getattr(executor, "manifest_path", "") or "")
    code_path = getattr(executor, "code_path", None)
    if not manifest_path.name or code_path is None:
        raise AdmittedModuleError("admitted_module_unpublished")
    directory = manifest_path.parent
    entry = Path(code_path).resolve()

    declared = _closed_code_files_v1(getattr(executor, "code_files", ()))
    paths = [_contained_file_v1(directory, name) for name in declared]
    payloads = [_read_exact_file_v1(path) for path in paths]
    if entry != paths[0].resolve(strict=True):
        raise AdmittedModuleError("admitted_module_entry_mismatch")

    signed = str(getattr(executor, "digest", "") or "")
    if signed:
        observed = code_digest_of_bytes_v1(payloads)
        if observed != signed:
            raise AdmittedModuleError("admitted_module_digest_mismatch")
    else:
        import config as C

        root = Path(C.PATH_EXECUTORS).resolve()
        if root not in entry.parents:
            raise AdmittedModuleError("admitted_module_outside_distribution")

    # Some standard decorators (notably ``dataclasses.dataclass``) resolve the
    # defining module through ``sys.modules`` while the class body executes.
    # Give this isolated load a unique identity. Executor subprocesses are
    # bounded; retaining the module for their lifetime preserves normal Python
    # reflection and pickling semantics without a cross-load name collision.
    module_name = f"{ADMITTED_MODULE_NAME_V1}_{id(payloads):x}"
    module = ModuleType(module_name)
    module.__file__ = str(entry)
    sys.modules[module_name] = module
    try:
        # Compiled from the very bytes that were authenticated a line above:
        # re-opening the path here would reintroduce the gap this door closes.
        compiled = compile(payloads[0], str(entry), "exec")
        exec(compiled, module.__dict__)
    except BaseException as exc:
        raise AdmittedModuleError(
            "admitted_module_unloadable", type(exc).__name__,
        ) from exc
    return module
