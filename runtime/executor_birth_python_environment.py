"""Pure canonical model for a content-addressed production Python venv."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable


PYTHON_ENVIRONMENT_SCHEMA_V1 = "metnos.executor-birth.python-environment/1"
PYTHON_ENVIRONMENT_ROOT_V1 = "/var/lib/metnos/python-envs-v1"
PYTHON_ENVIRONMENT_LAUNCH_FLAGS_V1 = ("-I", "-B")
MAX_PYTHON_DEPENDENCY_LOCK_BYTES_V1 = 16 * 1024 * 1024
MAX_PYTHON_ENVIRONMENT_BYTES_V1 = 16 * 1024 * 1024
MAX_PYTHON_ENVIRONMENT_FILES_V1 = 20_000
MAX_PYTHON_ENVIRONMENT_FILE_BYTES_V1 = 512 * 1024 * 1024
MAX_PYTHON_ENVIRONMENT_TOTAL_BYTES_V1 = 2 * 1024 * 1024 * 1024
_LOCK_HASH_DOMAIN_V1 = b"metnos.executor-birth.python-dependency-lock/v1\0"
_FILE_HASH_DOMAIN_V1 = b"metnos.executor-birth.python-venv-file/v1\0"
_ENVIRONMENT_ID_DOMAIN_V1 = b"metnos.executor-birth.python-environment-id/v1\0"
_DIGEST_RE_V1 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FILE_KEYS_V1 = frozenset({"kind", "path", "size", "mode", "content_hash"})
_DOCUMENT_KEYS_V1 = frozenset({
    "schema", "environment_id", "profile", "platform", "architecture",
    "implementation", "python_version", "cache_tag", "soabi",
    "environment_root", "python_executable", "site_packages",
    "launcher_flags", "system_site_packages", "dependency_lock_hash", "files",
})


class PythonEnvironmentError(ValueError):
    """Closed public failure for invalid production-environment material."""

    def __init__(self, detail: str) -> None:
        self.code = "birth_ownership_python_environment_invalid"
        self.detail = detail
        super().__init__(self.code)


def _invalid(detail: str) -> PythonEnvironmentError:
    return PythonEnvironmentError(detail)


@dataclass(frozen=True, slots=True)
class PythonEnvironmentProfileV1:
    name: str
    platform: str
    architecture: str
    implementation: str
    python_version: str
    cache_tag: str
    soabi: str
    python_relative: str
    site_packages_relative: str


def _profile(name: str, architecture: str, soabi_arch: str) -> PythonEnvironmentProfileV1:
    return PythonEnvironmentProfileV1(
        name, "linux", architecture, "cpython", "3.12", "cpython-312",
        f"cpython-312-{soabi_arch}-linux-gnu", "bin/python",
        "lib/python3.12/site-packages",
    )


_PROFILES_V1 = MappingProxyType({
    item.name: item for item in (
        _profile("linux-aarch64-cpython-312", "aarch64", "aarch64"),
        _profile("linux-x86_64-cpython-312", "x86_64", "x86_64"),
    )
})
PYTHON_ENVIRONMENT_PROFILES_V1 = tuple(sorted(_PROFILES_V1))


def python_environment_profile_v1(value: object) -> PythonEnvironmentProfileV1:
    if type(value) is not str:
        raise _invalid("profile")
    try:
        return _PROFILES_V1[value]
    except KeyError as exc:
        raise _invalid("profile") from exc


def _digest_v1(value: object, detail: str) -> str:
    if type(value) is not str or _DIGEST_RE_V1.fullmatch(value) is None:
        raise _invalid(detail)
    return value


def python_environment_root_v1(dependency_lock_hash: object) -> str:
    digest = _digest_v1(dependency_lock_hash, "dependency lock hash")
    return f"{PYTHON_ENVIRONMENT_ROOT_V1}/{digest.removeprefix('sha256:')}"


def python_dependency_lock_hash_v1(profile: object, encoded: object) -> str:
    selected = python_environment_profile_v1(profile)
    if (
        type(encoded) is not bytes or not encoded
        or len(encoded) > MAX_PYTHON_DEPENDENCY_LOCK_BYTES_V1
    ):
        raise _invalid("dependency lock")
    name = selected.name.encode("ascii")
    digest = hashlib.sha256(_LOCK_HASH_DOMAIN_V1)
    digest.update(len(name).to_bytes(8, "big"))
    digest.update(name)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    return "sha256:" + digest.hexdigest()


def _environment_path_v1(value: object) -> str:
    if type(value) is not str or unicodedata.normalize("NFC", value) != value:
        raise _invalid("file path")
    encoded = value.encode("utf-8")
    parts = value.split("/")
    if (
        not encoded or len(encoded) > 4096 or value.startswith("/")
        or "\\" in value or len(parts) > 32
        or any(not item or item in {".", ".."} for item in parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _invalid("file path")
    folded = tuple(item.casefold() for item in parts)
    basename = folded[-1]
    if (
        ".venv" in folded or "__pycache__" in folded
        or basename.endswith((".pyc", ".pyo", ".pth"))
        or basename in {"sitecustomize.py", "usercustomize.py"}
    ):
        raise _invalid("forbidden venv file")
    allowed = value in {"bin/python", "pyvenv.cfg"} or value.startswith(
        "lib/python3.12/site-packages/"
    )
    if not allowed:
        raise _invalid("venv layout")
    return value


@dataclass(frozen=True, slots=True)
class PythonEnvironmentFileV1:
    path: str
    size: int
    mode: int
    content_hash: str

    def __post_init__(self) -> None:
        selected = _environment_path_v1(self.path)
        if (
            type(self.size) is not int or self.size < 0
            or self.size > MAX_PYTHON_ENVIRONMENT_FILE_BYTES_V1
            or type(self.mode) is not int or self.mode not in {0o644, 0o755}
            or (selected == "bin/python" and self.mode != 0o755)
            or (selected == "pyvenv.cfg" and self.mode != 0o644)
        ):
            raise _invalid("file metadata")
        _digest_v1(self.content_hash, "file hash")


def _file_document_v1(value: PythonEnvironmentFileV1) -> dict[str, object]:
    return {
        "kind": "regular", "path": value.path, "size": value.size,
        "mode": value.mode, "content_hash": value.content_hash,
    }


def _canonical_v1(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _invalid("canonical encoding") from exc


def _pairs_v1(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise _invalid("duplicate key")
        result[key] = value
    return result


def _reject_constant_v1(_value: str) -> object:
    raise _invalid("json constant")


def _files_v1(value: object) -> tuple[PythonEnvironmentFileV1, ...]:
    if (
        type(value) is not tuple or not value
        or len(value) > MAX_PYTHON_ENVIRONMENT_FILES_V1
        or any(type(item) is not PythonEnvironmentFileV1 for item in value)
    ):
        raise _invalid("files")
    paths = tuple(item.path for item in value)
    required = {"bin/python", "pyvenv.cfg"}
    if not required.issubset(paths) or not any(
        path.startswith("lib/python3.12/site-packages/") for path in paths
    ):
        raise _invalid("venv coverage")
    if paths != tuple(sorted(paths, key=lambda item: item.encode("utf-8"))):
        raise _invalid("file order")
    if len(paths) != len(set(paths)):
        raise _invalid("duplicate file")
    if sum(item.size for item in value) > MAX_PYTHON_ENVIRONMENT_TOTAL_BYTES_V1:
        raise _invalid("file total")
    return value


def _unsigned_document_v1(
    profile: PythonEnvironmentProfileV1, dependency_lock_hash: str,
    files: tuple[PythonEnvironmentFileV1, ...],
) -> dict[str, object]:
    root = python_environment_root_v1(dependency_lock_hash)
    return {
        "schema": PYTHON_ENVIRONMENT_SCHEMA_V1, "profile": profile.name,
        "platform": profile.platform, "architecture": profile.architecture,
        "implementation": profile.implementation,
        "python_version": profile.python_version, "cache_tag": profile.cache_tag,
        "soabi": profile.soabi, "environment_root": root,
        "python_executable": f"{root}/{profile.python_relative}",
        "site_packages": f"{root}/{profile.site_packages_relative}",
        "launcher_flags": list(PYTHON_ENVIRONMENT_LAUNCH_FLAGS_V1),
        "system_site_packages": False, "dependency_lock_hash": dependency_lock_hash,
        "files": [_file_document_v1(item) for item in files],
    }


def _environment_id_v1(document: dict[str, object]) -> str:
    digest = hashlib.sha256(_ENVIRONMENT_ID_DOMAIN_V1 + _canonical_v1(document))
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PythonEnvironmentV1:
    environment_id: str
    profile: PythonEnvironmentProfileV1
    dependency_lock_hash: str
    files: tuple[PythonEnvironmentFileV1, ...]

    def __post_init__(self) -> None:
        if type(self.profile) is not PythonEnvironmentProfileV1:
            raise _invalid("profile record")
        canonical_profile = python_environment_profile_v1(self.profile.name)
        if self.profile != canonical_profile:
            raise _invalid("profile record")
        lock_hash = _digest_v1(self.dependency_lock_hash, "dependency lock hash")
        selected_files = _files_v1(self.files)
        expected = _environment_id_v1(
            _unsigned_document_v1(canonical_profile, lock_hash, selected_files),
        )
        if self.environment_id != expected:
            raise _invalid("environment id")

    @property
    def environment_root(self) -> str:
        return python_environment_root_v1(self.dependency_lock_hash)

    @property
    def python_executable(self) -> str:
        return f"{self.environment_root}/{self.profile.python_relative}"

    @property
    def site_packages(self) -> str:
        return f"{self.environment_root}/{self.profile.site_packages_relative}"


def build_python_environment_v1(
    *, profile: object, dependency_lock_hash: object, files: object,
) -> PythonEnvironmentV1:
    selected = python_environment_profile_v1(profile)
    lock_hash = _digest_v1(dependency_lock_hash, "dependency lock hash")
    selected_files = _files_v1(files)
    identifier = _environment_id_v1(
        _unsigned_document_v1(selected, lock_hash, selected_files),
    )
    return PythonEnvironmentV1(identifier, selected, lock_hash, selected_files)


def encode_python_environment_v1(value: object) -> bytes:
    if type(value) is not PythonEnvironmentV1:
        raise _invalid("environment record")
    unsigned = _unsigned_document_v1(
        value.profile, value.dependency_lock_hash, value.files,
    )
    return _canonical_v1({**unsigned, "environment_id": value.environment_id})


def _file_from_document_v1(value: object) -> PythonEnvironmentFileV1:
    if (
        type(value) is not dict or frozenset(value) != _FILE_KEYS_V1
        or value.get("kind") != "regular"
    ):
        raise _invalid("file schema")
    return PythonEnvironmentFileV1(
        value.get("path"), value.get("size"), value.get("mode"),
        value.get("content_hash"),
    )


def _raw_files_v1(value: object) -> list[object]:
    if type(value) is not list or not value or len(value) > MAX_PYTHON_ENVIRONMENT_FILES_V1:
        raise _invalid("files")
    return value


def decode_python_environment_v1(encoded: object) -> PythonEnvironmentV1:
    value = _decode_document_v1(encoded)
    profile = python_environment_profile_v1(value.get("profile"))
    lock_hash = _digest_v1(value.get("dependency_lock_hash"), "dependency lock hash")
    files = tuple(_file_from_document_v1(item) for item in _raw_files_v1(value.get("files")))
    expected = _unsigned_document_v1(profile, lock_hash, files)
    observed = dict(value)
    environment_id = observed.pop("environment_id")
    if observed != expected:
        raise _invalid("profile metadata")
    return PythonEnvironmentV1(
        _digest_v1(environment_id, "environment id"), profile, lock_hash, files,
    )


def _decode_document_v1(encoded: object) -> dict[str, object]:
    if (
        type(encoded) is not bytes or not encoded
        or len(encoded) > MAX_PYTHON_ENVIRONMENT_BYTES_V1
    ):
        raise _invalid("document size")
    try:
        value = json.loads(
            encoded.decode("ascii"), object_pairs_hook=_pairs_v1,
            parse_constant=_reject_constant_v1,
        )
    except PythonEnvironmentError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid("document json") from exc
    if type(value) is not dict or frozenset(value) != _DOCUMENT_KEYS_V1:
        raise _invalid("document schema")
    if _canonical_v1(value) != encoded:
        raise _invalid("document representation")
    return value


def python_environment_file_hash_v1(
    path: object, size: object, chunks: Iterable[bytes],
) -> str:
    selected = _environment_path_v1(path)
    if type(size) is not int or size < 0 or size > MAX_PYTHON_ENVIRONMENT_FILE_BYTES_V1:
        raise _invalid("file size")
    path_bytes = selected.encode("utf-8")
    digest = hashlib.sha256(_FILE_HASH_DOMAIN_V1)
    digest.update(len(path_bytes).to_bytes(8, "big"))
    digest.update(path_bytes)
    digest.update(size.to_bytes(8, "big"))
    observed = 0
    try:
        for chunk in chunks:
            if type(chunk) is not bytes:
                raise _invalid("file chunk")
            observed += len(chunk)
            if observed > size:
                raise _invalid("file content size")
            digest.update(chunk)
    except PythonEnvironmentError:
        raise
    except Exception as exc:
        raise _invalid("file chunks") from exc
    if observed != size:
        raise _invalid("file content size")
    return "sha256:" + digest.hexdigest()


__all__ = [
    "MAX_PYTHON_DEPENDENCY_LOCK_BYTES_V1", "MAX_PYTHON_ENVIRONMENT_BYTES_V1",
    "MAX_PYTHON_ENVIRONMENT_FILE_BYTES_V1", "MAX_PYTHON_ENVIRONMENT_FILES_V1",
    "MAX_PYTHON_ENVIRONMENT_TOTAL_BYTES_V1",
    "PYTHON_ENVIRONMENT_LAUNCH_FLAGS_V1", "PYTHON_ENVIRONMENT_PROFILES_V1",
    "PYTHON_ENVIRONMENT_ROOT_V1", "PYTHON_ENVIRONMENT_SCHEMA_V1",
    "PythonEnvironmentError", "PythonEnvironmentFileV1",
    "PythonEnvironmentProfileV1", "PythonEnvironmentV1",
    "build_python_environment_v1", "decode_python_environment_v1",
    "encode_python_environment_v1", "python_dependency_lock_hash_v1",
    "python_environment_file_hash_v1", "python_environment_profile_v1",
    "python_environment_root_v1",
]
