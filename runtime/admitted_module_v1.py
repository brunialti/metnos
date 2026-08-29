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

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from code_file_paths import PortableCodePathError, validate_portable_code_files

try:
    # The full runtime captures these before executor code runs.  Minimal
    # dependency subprocesses intentionally mount only this door and its path
    # validator; their sealed projected records never need the catalogue.
    from loader import (
        invalidate_catalog_cache as _invalidate_catalog_cache_at_start_v1,
        load_catalog as _load_catalog_at_start_v1,
    )
except ImportError:
    # The projected-record branch below does not reference these names.  If a
    # caller nevertheless attempts the ordinary branch in this minimal
    # process, the missing global raises NameError inside the fail-closed
    # catalogue exception boundary.
    pass

ADMITTED_MODULE_NAME_V1 = "_metnos_admitted_executor"
MAXIMUM_CODE_FILE_BYTES_V1 = 4 * 1024 * 1024
ADMITTED_EXECUTORS_ENV_V1 = "METNOS_ADMITTED_EXECUTORS_V1"
MAXIMUM_ADMITTED_ENV_BYTES_V1 = 64 * 1024
_PARENT_PROJECTION_SEAL_V1 = object()

# ``undo_last_turn`` is the one deliberately naked system broker.  Loading a
# custom reverse module executes its top-level code before ``reverse`` can be
# selected, so signature/catalogue admission alone is not sufficient: a new
# Birth contract must not silently extend that broker's authority.  The
# authenticated door therefore keeps the reviewed custom-reverse vocabulary
# byte-exact. Ordinary modules and declarative reverse patterns are unchanged.
_REVIEWED_MODULE_REVERSE_DIGESTS_V1 = {
    "create_images_indices": "sha256:5f5b94cf38d1a7a9bbde678e0397f4d9ae799f1f5898eb4a0148838e0030b845",
    "delete_dirs": "sha256:dd7106b098c107a0282b41a3ea37d363848ad78be509fffc5d7ba9c9e5c7d60d",
    "login_urls": "sha256:bcac469765b5e27ac0fe0a975e9b51b9d4dd6ef70ac577b66ccb4689d45d1c1f",
    "open_sites": "sha256:c63e58a07045ad7f2563a6f03e1fdccce98ff0ac384f47fece1c7ae8350f6877",
    "run_processes": "sha256:47eef328046d85d0e241af373e16bbff7064b9dbc2884ad3f24b975dabaa76b9",
    "set_messages": "sha256:6ad6030d29d86a7374dee06e9f7244f2e35719bfbd30160b3aa213378fe774c5",
    "set_signatures": "sha256:5e975030b99cfac4b61a8b35fe4410f2cf20bed0d4b4703b152da117bb69b173",
    "share_files": "sha256:1c17b6d6b2f7190e992bb04861b6dca661fc74c76426b6a3f24c7b7a3779b403",
    "write_files_doc": "sha256:ef3b7111bb6871052bef6e99ec8e5d38d022961ac3ef1f48a876bb037ac0eeb5",
}


def _trusted_keys_dir_at_start_v1() -> Path | None:
    """Freeze an absolute trust root or fail closed.

    A relative home/configuration value is caller-controlled search-path state,
    not an installed trust anchor.  In particular, never fall back to the
    current working directory when service-account home discovery fails.
    """
    configured = os.environ.get("METNOS_USER_CONFIG", "")
    if configured:
        candidate = Path(configured)
        if candidate.is_absolute():
            return candidate / "keys"
        return None
    try:
        home = Path.home()
    except (KeyError, RuntimeError):
        fallback = os.environ.get("USERPROFILE", "")
        if not fallback:
            fallback = (
                os.environ.get("HOMEDRIVE", "")
                + os.environ.get("HOMEPATH", "")
            )
        home = Path(fallback) if fallback else None
    if home is None or not home.is_absolute():
        return None
    return home / ".config" / "metnos" / "keys"


_TRUSTED_KEYS_DIR_V1 = _trusted_keys_dir_at_start_v1()


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
    _projection_seal: object


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
    from sign import KEYS_DIR

    for name in names:
        target = catalog.get(name)
        if target is None or getattr(target, "name", None) != name:
            raise AdmittedModuleError("admitted_module_dependency_unavailable")
        signer = getattr(target, "signed_by", None)
        if (
            not isinstance(signer, str)
            or not signer
            or not signer.isascii()
            or len(signer) > 128
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                   for character in signer)
        ):
            raise AdmittedModuleError("admitted_module_record_invalid")
        records.append(target)
        root = Path(getattr(target, "manifest_path", "") or "").parent
        if not root.is_absolute():
            raise AdmittedModuleError("admitted_module_record_invalid")
        if root not in roots:
            roots.append(root)
        # Expose only the public key that authenticated this exact catalogue
        # row, never the key directory or unrelated trust anchors.
        public_key = KEYS_DIR / f"{signer}_pub.bin"
        try:
            key_info = public_key.lstat()
        except OSError as exc:
            raise AdmittedModuleError(
                "admitted_module_dependency_unavailable",
            ) from exc
        if (
            not stat.S_ISREG(key_info.st_mode)
            or stat.S_ISLNK(key_info.st_mode)
            or key_info.st_nlink != 1
            or key_info.st_size != 32
            or bool(
                getattr(key_info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        ):
            raise AdmittedModuleError("admitted_module_dependency_unavailable")
        if public_key not in roots:
            roots.append(public_key)
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
        _projection_seal=_PARENT_PROJECTION_SEAL_V1,
    )


def _record_binding_v1(executor) -> tuple[str, Path, Path, str, tuple[str, ...]]:
    """Return the complete executable binding carried by one catalogue row."""
    name = getattr(executor, "name", None)
    manifest_path = getattr(executor, "manifest_path", None)
    code_path = getattr(executor, "code_path", None)
    digest = getattr(executor, "digest", None)
    if (
        not isinstance(name, str)
        or not name
        or not name.isascii()
        or len(name) > 128
        or manifest_path is None
        or code_path is None
        or not isinstance(digest, str)
    ):
        raise AdmittedModuleError("admitted_module_record_invalid")
    return (
        name,
        Path(manifest_path),
        Path(code_path),
        digest,
        _closed_code_files_v1(getattr(executor, "code_files", ())),
    )


def _trusted_public_keys_v1() -> tuple[Ed25519PublicKey, ...]:
    keys = []
    if _TRUSTED_KEYS_DIR_V1 is None:
        return ()
    try:
        paths = sorted(_TRUSTED_KEYS_DIR_V1.glob("*_pub.bin"))
    except OSError:
        return ()
    for path in paths:
        try:
            raw = _read_exact_file_v1(path)
            keys.append(Ed25519PublicKey.from_public_bytes(raw))
        except (AdmittedModuleError, TypeError, ValueError):
            continue
    return tuple(keys)


def _verify_projection_signature_v1(
    manifest_bytes: bytes, signature_bytes: bytes,
) -> None:
    keys = _trusted_public_keys_v1()
    for public_key in keys:
        try:
            public_key.verify(signature_bytes, manifest_bytes)
        except InvalidSignature:
            continue
        return
    raise AdmittedModuleError("admitted_module_projection_untrusted")


def _authenticate_parent_projection_v1(executor):
    """Authenticate a child projection against an installed trusted key."""
    import tomllib

    supplied = _record_binding_v1(executor)
    manifest_path = supplied[1]
    directory = manifest_path.parent
    if manifest_path.name != "manifest.toml":
        raise AdmittedModuleError("admitted_module_record_mismatch")
    canonical_manifest = _contained_file_v1(directory, "manifest.toml")
    try:
        if canonical_manifest.resolve(strict=True) != manifest_path.resolve(strict=True):
            raise AdmittedModuleError("admitted_module_record_mismatch")
        manifest_bytes = _read_exact_file_v1(canonical_manifest)
        signature_bytes = _read_exact_file_v1(
            _contained_file_v1(directory, "manifest.toml.sig"),
        )
        _verify_projection_signature_v1(manifest_bytes, signature_bytes)
        manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
    except AdmittedModuleError:
        raise
    except (
        OSError, TypeError, ValueError, UnicodeDecodeError,
        tomllib.TOMLDecodeError,
    ) as exc:
        raise AdmittedModuleError(
            "admitted_module_projection_untrusted",
        ) from exc
    code = manifest.get("code")
    if not isinstance(code, dict):
        raise AdmittedModuleError("admitted_module_record_mismatch")
    declared = _closed_code_files_v1(code.get("files"))
    signed_binding = (
        manifest.get("name"),
        canonical_manifest,
        _contained_file_v1(directory, declared[0]),
        code.get("digest"),
        declared,
    )
    if supplied != signed_binding:
        raise AdmittedModuleError("admitted_module_record_mismatch")
    return executor, manifest


def _current_verified_record_v1(executor):
    """Bind a supplied row to the currently verified catalogue snapshot.

    Paths and digests are evidence only when they came from the signed
    catalogue.  A caller-calculated digest for an arbitrary pathname is not
    authority, even if the bytes happen to match it.
    """
    supplied = _record_binding_v1(executor)
    if (
        isinstance(executor, AdmittedExecutorRecordV1)
        and executor._projection_seal is _PARENT_PROJECTION_SEAL_V1
    ):
        return _authenticate_parent_projection_v1(executor)
    try:
        # These callables are captured before executor code runs.  Rebinding
        # public attributes on ``loader`` cannot replace the authority used by
        # this door in the current interpreter.
        _invalidate_catalog_cache_at_start_v1()
        current = _load_catalog_at_start_v1().get(supplied[0])
    except Exception as exc:
        raise AdmittedModuleError(
            "admitted_module_catalog_unavailable",
        ) from exc
    if current is None:
        raise AdmittedModuleError("admitted_module_dependency_unavailable")
    verified = _record_binding_v1(current)
    if supplied != verified:
        raise AdmittedModuleError("admitted_module_record_mismatch")
    # A catalogue row is necessary for current visibility, but is not by
    # itself cryptographic authority.  Re-authenticate its exact manifest and
    # binding so even a compromised catalogue callable cannot admit arbitrary
    # caller-digested bytes.
    return _authenticate_parent_projection_v1(current)


def _require_reviewed_module_reverse_v1(executor, manifest: dict) -> None:
    """Keep executable custom reverse hooks a closed byte vocabulary."""
    pattern = manifest.get("reverse_pattern")
    patterns = [pattern] if isinstance(pattern, str) else pattern
    if not manifest.get("revertible") or not isinstance(patterns, list):
        return
    try:
        from reverse_patterns import PATTERNS
        unknown = [name for name in patterns if name not in PATTERNS]
    except (ImportError, TypeError):
        unknown = list(patterns)
    if not unknown:
        return
    name, _manifest, _code, digest, _files = _record_binding_v1(executor)
    if _REVIEWED_MODULE_REVERSE_DIGESTS_V1.get(name) != digest:
        raise AdmittedModuleError("admitted_module_reverse_unreviewed", name)


def load_admitted_module_v1(executor) -> ModuleType:
    """Return the module of a published executor, authenticated first.

    ``executor`` must match the current row of the verified catalogue.  The
    door then guarantees that the bytes on disk right now are still the ones
    covered by that row's signature.

    A record with no signed digest belongs to the installed distribution — its
    code changes only with a deployment — and is admitted only when it really
    lives under the distribution's own executor root.  Neither branch accepts a
    path chosen by the caller.
    """
    executor, signed_manifest = _current_verified_record_v1(executor)
    _require_reviewed_module_reverse_v1(executor, signed_manifest)
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
