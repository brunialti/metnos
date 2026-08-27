"""The single door through which one executor may load another's code.

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
import os
import stat
import tomllib
from pathlib import Path
from types import ModuleType

ADMITTED_MODULE_NAME_V1 = "_metnos_admitted_executor"
MAXIMUM_CODE_FILE_BYTES_V1 = 4 * 1024 * 1024


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


def _declared_code_files_v1(manifest_path: Path) -> tuple[str, ...]:
    try:
        manifest = tomllib.loads(
            _read_exact_file_v1(manifest_path).decode("utf-8")
        )
    except (AdmittedModuleError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise AdmittedModuleError("admitted_module_manifest_unreadable") from exc
    section = manifest.get("code")
    files = section.get("files") if isinstance(section, dict) else None
    if not isinstance(files, list) or not files or not all(
        isinstance(item, str) and item for item in files
    ):
        raise AdmittedModuleError("admitted_module_files_undeclared")
    return tuple(files)


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

    declared = _declared_code_files_v1(manifest_path)
    payloads = [_read_exact_file_v1(directory / name) for name in declared]
    if entry != (directory / declared[0]).resolve():
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

    module = ModuleType(ADMITTED_MODULE_NAME_V1)
    module.__file__ = str(entry)
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
