"""Portable verifier for the signed RM-0008 F4 closed-build manifest.

The verifier is the sole production bridge from release bytes to the sealed
``ClosedBuildIdentity`` consumed by the ownership startup preflight.  Storage
of append-only build/head records is intentionally owned by a later block.
"""
from __future__ import annotations

import hashlib
import ast
import json
import os
import platform as _platform
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Mapping, NamedTuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from executor_birth_ownership_preflight import (
    ClosedBuildIdentity, _BUILD_AUTHORITY_SEAL,
)
from contract_boundary_guard import (
    BIRTH_CLOSED_COORDINATOR_STORE_OWNERS, BIRTH_CLOSED_EXCEPTION_SCOPES,
    BIRTH_CLOSED_GUARD_VERSION,
    BIRTH_CLOSED_OWNER, BIRTH_CLOSED_SCHEMA, BIRTH_CLOSED_SEALED_MODULES,
    SCAN_ROOTS, SCHEMA as BOUNDARY_INVENTORY_SCHEMA,
    birth_closed_findings, discover,
)


SIGNATURE_DOMAIN = b"metnos.executor-birth.closed-build/v1\0"
BUILD_ID_DOMAIN = b"metnos.executor-birth.closed-build-id/v1\0"
FILE_HASH_DOMAIN = b"metnos.executor-birth.closed-build-file/v1\0"
BOUNDARY_INVENTORY_DOMAIN = b"metnos.executor-birth.boundary-inventory/v1\0"
PURPOSE = "closed_distribution_v1"
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
DEFAULT_RELEASE_DIRECTORY_V1 = Path(
    "/var/lib/metnos/executor-birth/releases-v1"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_KEY_ID_RE = re.compile(r"distribution-ed25519-v1-sha256-[0-9a-f]{64}\Z")
_SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_PAYLOAD_KEYS = frozenset({
    "schema_version", "closed_build_id", "previous_closed_build_id",
    "release_sequence", "product_version", "platform", "architecture",
    "signing_key_id", "installation_root", "certificate_directory",
    "boundary_inventory_path", "boundary_inventory_hash",
    "boundary_guard_version", "preflight_entrypoint", "files",
})
_FILE_KEYS = frozenset({"path", "size", "content_hash", "role"})
_ROLES = frozenset({
    "runtime_code", "preflight", "boundary_guard", "boundary_inventory",
    "service_unit", "product_version", "dependency_lock",
})
_PLATFORMS = frozenset({"linux", "windows"})
_ARCHITECTURES = frozenset({"x86_64", "aarch64"})
_REQUIRED_PATH_ROLES = MappingProxyType({
    "runtime/contract_store.py": "runtime_code",
    "runtime/sign.py": "runtime_code",
    "runtime/contract_boundary_guard.py": "boundary_guard",
    "runtime/executor_birth.py": "runtime_code",
    "runtime/executor_birth_ownership_preflight.py": "preflight",
    "runtime/executor_birth_distribution_manifest.py": "preflight",
    "runtime/__version__.py": "product_version",
})


class DistributionManifestError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class DistributionKey:
    key_id: str
    public_key: Ed25519PublicKey
    purposes: frozenset[str]
    first_release_sequence: int = 1
    last_release_sequence: int | None = None

    def __post_init__(self) -> None:
        raw = (
            self.public_key.public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw,
            ) if isinstance(self.public_key, Ed25519PublicKey) else b""
        )
        expected = "distribution-ed25519-v1-sha256-" + hashlib.sha256(raw).hexdigest()
        if (
            _KEY_ID_RE.fullmatch(self.key_id or "") is None
            or self.key_id != expected
            or not isinstance(self.purposes, frozenset)
            or any(not isinstance(item, str) or not item for item in self.purposes)
            or isinstance(self.first_release_sequence, bool)
            or not isinstance(self.first_release_sequence, int)
            or self.first_release_sequence < 1
            or (
                self.last_release_sequence is not None
                and (
                    isinstance(self.last_release_sequence, bool)
                    or not isinstance(self.last_release_sequence, int)
                    or self.last_release_sequence < self.first_release_sequence
                )
            )
        ):
            raise DistributionManifestError(
                "birth_ownership_distribution_invalid", "key registry",
            )


@dataclass(frozen=True, slots=True)
class DistributionRegistry:
    keys: Mapping[str, DistributionKey]

    def __post_init__(self) -> None:
        values = dict(self.keys)
        if not values or any(
            key != entry.key_id or not isinstance(entry, DistributionKey)
            for key, entry in values.items()
        ):
            raise DistributionManifestError(
                "birth_ownership_distribution_invalid", "key registry",
            )
        object.__setattr__(self, "keys", MappingProxyType(values))


@dataclass(frozen=True, slots=True)
class DistributionFile:
    path: str
    size: int
    content_hash: str
    role: str


@dataclass(frozen=True, slots=True)
class AuthenticatedDistributionRecordV1:
    """Signed historical manifest; it makes no claim about live files."""

    closed_build_id: str
    previous_closed_build_id: str | None
    release_sequence: int
    product_version: str
    platform: str
    architecture: str
    signing_key_id: str
    installation_root: str
    certificate_directory: str
    boundary_inventory_path: str
    boundary_inventory_hash: str
    boundary_guard_version: str
    preflight_entrypoint: str
    files: tuple[DistributionFile, ...]
    encoded: bytes
    signature: bytes
    _artifact_binding: bytes
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _AUTHENTICATED_DISTRIBUTION_SEAL
            or not isinstance(self.encoded, bytes)
            or not isinstance(self.signature, bytes)
            or len(self.signature) != 64
            or self._artifact_binding != _authenticated_artifact_binding(
                self.encoded, self.signature,
            )
        ):
            raise DistributionManifestError(
                "birth_ownership_distribution_invalid", "authenticated artifact",
            )


@dataclass(frozen=True, slots=True)
class _AuthenticatedDistributionRecordForTestV1:
    """Nominally separate test result; productive verification rejects it."""

    closed_build_id: str
    previous_closed_build_id: str | None
    release_sequence: int
    product_version: str
    platform: str
    architecture: str
    signing_key_id: str
    installation_root: str
    certificate_directory: str
    boundary_inventory_path: str
    boundary_inventory_hash: str
    boundary_guard_version: str
    preflight_entrypoint: str
    files: tuple[DistributionFile, ...]
    encoded: bytes
    signature: bytes
    _artifact_binding: bytes
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _TEST_AUTHENTICATED_DISTRIBUTION_SEAL
            or not isinstance(self.encoded, bytes)
            or not isinstance(self.signature, bytes)
            or len(self.signature) != 64
            or self._artifact_binding != _authenticated_artifact_binding(
                self.encoded, self.signature,
            )
        ):
            raise DistributionManifestError(
                "birth_ownership_distribution_invalid", "test authenticated artifact",
            )


class _AuthenticatedDistributionMaterialV1(NamedTuple):
    closed_build_id: str
    previous_closed_build_id: str | None
    release_sequence: int
    product_version: str
    platform: str
    architecture: str
    signing_key_id: str
    installation_root: str
    certificate_directory: str
    boundary_inventory_path: str
    boundary_inventory_hash: str
    boundary_guard_version: str
    preflight_entrypoint: str
    files: tuple[DistributionFile, ...]
    encoded: bytes
    signature: bytes
    artifact_binding: bytes


@dataclass(frozen=True, slots=True)
class VerifiedDistribution:
    """Authenticated result; its sealed identity cannot be caller-populated."""

    identity: ClosedBuildIdentity
    previous_closed_build_id: str | None
    release_sequence: int
    product_version: str
    platform: str
    architecture: str
    installation_root: str
    certificate_directory: str
    preflight_entrypoint: str
    files: tuple[DistributionFile, ...]
    encoded: bytes
    signature: bytes
    _artifact_binding: bytes
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _VERIFIED_DISTRIBUTION_SEAL
            or not isinstance(self.encoded, bytes)
            or not isinstance(self.signature, bytes)
            or len(self.signature) != 64
            or self._artifact_binding != _distribution_artifact_binding(
                self.encoded, self.signature,
            )
        ):
            raise DistributionManifestError(
                "birth_ownership_distribution_invalid", "verified artifact",
            )


_AUTHENTICATED_DISTRIBUTION_SEAL = object()
_TEST_AUTHENTICATED_DISTRIBUTION_SEAL = object()
_VERIFIED_DISTRIBUTION_SEAL = object()


def _authenticated_artifact_binding(encoded: bytes, signature: bytes) -> bytes:
    return hashlib.sha256(
        b"metnos.executor-birth.authenticated-distribution-record/v1\0"
        + len(encoded).to_bytes(8, "big") + encoded + signature
    ).digest()


def _distribution_artifact_binding(encoded: bytes, signature: bytes) -> bytes:
    return hashlib.sha256(
        b"metnos.executor-birth.verified-distribution/v1\0"
        + len(encoded).to_bytes(8, "big") + encoded + signature
    ).digest()


@dataclass(frozen=True, slots=True)
class _VerificationEnvironment:
    platform: str
    architecture: str
    installation_root: Path
    claimed_installation_root: str
    require_administrative_metadata: bool
    verify_static_boundary: bool
    _seal: object


_ENVIRONMENT_SEAL = object()


def _environment_for_test(
    platform: str, architecture: str, installation_root: Path, *,
    claimed_installation_root: str | None = None,
    verify_static_boundary: bool = False,
) -> _VerificationEnvironment:
    """Portable-test seam; deliberately absent from the public API."""
    return _VerificationEnvironment(
        platform, architecture, Path(installation_root),
        claimed_installation_root or str(installation_root), False,
        verify_static_boundary, _ENVIRONMENT_SEAL,
    )


def distribution_key_id(public_key: Ed25519PublicKey) -> str:
    if not isinstance(public_key, Ed25519PublicKey):
        raise DistributionManifestError("birth_ownership_distribution_invalid", "key")
    raw = public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    return "distribution-ed25519-v1-sha256-" + hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise DistributionManifestError(
            "birth_ownership_distribution_invalid", "json",
        ) from exc


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise DistributionManifestError(
                "birth_ownership_distribution_invalid", "duplicate key",
            )
        value[key] = item
    return value


def _digest(value: object, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise DistributionManifestError(
            "birth_ownership_distribution_invalid", field,
        )
    return value


def _relative_path(value: object, field: str = "path") -> str:
    if not isinstance(value, str) or not value or unicodedata.normalize("NFC", value) != value:
        raise DistributionManifestError("birth_ownership_distribution_invalid", field)
    if "\\" in value or "\x00" in value or value.startswith("/"):
        raise DistributionManifestError("birth_ownership_distribution_invalid", field)
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise DistributionManifestError("birth_ownership_distribution_invalid", field)
    if PurePosixPath(value).as_posix() != value:
        raise DistributionManifestError("birth_ownership_distribution_invalid", field)
    return value


def _absolute_path(value: object, target: str, field: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise DistributionManifestError("birth_ownership_distribution_invalid", field)
    if target == "linux":
        candidate = PurePosixPath(value)
        if (
            not candidate.is_absolute() or candidate.as_posix() != value
            or any(part in {".", "..", ""} for part in candidate.parts[1:])
        ):
            raise DistributionManifestError("birth_ownership_distribution_invalid", field)
    else:
        if (
            value.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\"))
            or "/" in value or value.count(":") != 1
        ):
            raise DistributionManifestError("birth_ownership_distribution_invalid", field)
        candidate = PureWindowsPath(value)
        if (
            not candidate.is_absolute() or candidate.drive.startswith("\\")
            or any(part in {".", "..", ""} for part in candidate.parts[1:])
            or str(candidate) != value
        ):
            raise DistributionManifestError("birth_ownership_distribution_invalid", field)
    return value


def file_content_hash(path: str, content: bytes) -> str:
    normalized = _relative_path(path)
    if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
        raise DistributionManifestError("birth_ownership_distribution_invalid", "file size")
    encoded = normalized.encode("utf-8")
    material = (
        FILE_HASH_DOMAIN + len(encoded).to_bytes(8, "big") + encoded
        + len(content).to_bytes(8, "big") + content
    )
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _build_id(value: Mapping[str, object]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "closed_build_id"}
    return "sha256:" + hashlib.sha256(BUILD_ID_DOMAIN + _canonical(unsigned)).hexdigest()


def _runtime_environment() -> _VerificationEnvironment:
    if sys.platform.startswith("linux"):
        target = "linux"
    elif os.name == "nt":
        target = "windows"
    else:
        raise DistributionManifestError(
            "birth_ownership_distribution_platform_mismatch", "platform",
        )
    machine = _platform.machine().lower()
    architecture = {
        "amd64": "x86_64", "x86_64": "x86_64",
        "arm64": "aarch64", "aarch64": "aarch64",
    }.get(machine)
    if architecture is None:
        raise DistributionManifestError(
            "birth_ownership_distribution_platform_mismatch", "architecture",
        )
    # The installed module is the authority for the installation root.  Do not
    # pin a deployment path here: Metnos supports a configured/relocated root,
    # while the signed manifest below must still bind to this exact resolved
    # directory.
    root = Path(__file__).resolve().parents[1]
    return _VerificationEnvironment(
        target, architecture, root, str(root), True, True, _ENVIRONMENT_SEAL,
    )


def _require_product_release_metadata_v1(root: Path) -> None:
    absolute = Path(os.path.abspath(root))
    for component in reversed((absolute, *absolute.parents)):
        try:
            info = component.lstat()
        except OSError as exc:
            raise DistributionManifestError(
                "birth_ownership_distribution_file_mismatch", "release metadata",
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & 0x400)
            or info.st_uid != 0 or info.st_gid != 0
            or info.st_mode & 0o022
        ):
            raise DistributionManifestError(
                "birth_ownership_distribution_file_mismatch", "release metadata",
            )


def _parse(encoded: bytes) -> tuple[dict[str, object], tuple[DistributionFile, ...]]:
    if not isinstance(encoded, bytes) or not encoded or len(encoded) > MAX_PAYLOAD_BYTES:
        raise DistributionManifestError("birth_ownership_distribution_invalid", "payload size")
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=_pairs)
    except DistributionManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionManifestError(
            "birth_ownership_distribution_invalid", "json",
        ) from exc
    if not isinstance(value, dict) or set(value) != _PAYLOAD_KEYS or _canonical(value) != encoded:
        raise DistributionManifestError("birth_ownership_distribution_invalid", "schema")
    sequence = value.get("release_sequence")
    if (
        value.get("schema_version") != 1 or isinstance(sequence, bool)
        or not isinstance(sequence, int) or sequence < 1
        or not isinstance(value.get("product_version"), str)
        or _SEMVER_RE.fullmatch(str(value.get("product_version"))) is None
        or value.get("platform") not in _PLATFORMS
        or value.get("architecture") not in _ARCHITECTURES
    ):
        raise DistributionManifestError("birth_ownership_distribution_invalid", "header")
    previous = _digest(value.get("previous_closed_build_id"), "previous", nullable=True)
    if (sequence == 1) != (previous is None):
        raise DistributionManifestError("birth_ownership_distribution_chain_invalid")
    _digest(value.get("closed_build_id"), "closed_build_id")
    _digest(value.get("boundary_inventory_hash"), "boundary_inventory_hash")
    if value.get("closed_build_id") != _build_id(value):
        raise DistributionManifestError("birth_ownership_distribution_invalid", "closed_build_id")
    target = str(value["platform"])
    _absolute_path(value.get("installation_root"), target, "installation_root")
    _absolute_path(value.get("certificate_directory"), target, "certificate_directory")
    if target == "linux" and value.get("certificate_directory") != "/var/lib/metnos/executor-birth":
        raise DistributionManifestError(
            "birth_ownership_distribution_invalid", "certificate_directory",
        )
    inventory_path = _relative_path(value.get("boundary_inventory_path"), "inventory path")
    entrypoint = _relative_path(value.get("preflight_entrypoint"), "preflight entrypoint")
    guard = value.get("boundary_guard_version")
    if not isinstance(guard, str) or not guard or "\x00" in guard or len(guard.encode()) > 128:
        raise DistributionManifestError("birth_ownership_distribution_invalid", "guard")
    key_id = value.get("signing_key_id")
    if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
        raise DistributionManifestError("birth_ownership_distribution_invalid", "signing_key_id")
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise DistributionManifestError("birth_ownership_distribution_invalid", "files")
    files: list[DistributionFile] = []
    for raw in raw_files:
        if not isinstance(raw, dict) or set(raw) != _FILE_KEYS:
            raise DistributionManifestError("birth_ownership_distribution_invalid", "file schema")
        path = _relative_path(raw.get("path"))
        size = raw.get("size")
        role = raw.get("role")
        content_hash = _digest(raw.get("content_hash"), "content_hash")
        if (
            isinstance(size, bool) or not isinstance(size, int)
            or size < 0 or size > MAX_FILE_BYTES or role not in _ROLES
        ):
            raise DistributionManifestError("birth_ownership_distribution_invalid", "file")
        files.append(DistributionFile(path, size, str(content_hash), str(role)))
    paths = [item.path for item in files]
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")) or len(paths) != len(set(paths)):
        raise DistributionManifestError("birth_ownership_distribution_invalid", "file order")
    by_path = {item.path: item for item in files}
    if any(by_path.get(path) is None or by_path[path].role != role
           for path, role in _REQUIRED_PATH_ROLES.items()):
        raise DistributionManifestError("birth_ownership_distribution_invalid", "required files")
    for role in ("boundary_inventory", "service_unit", "dependency_lock"):
        if sum(item.role == role for item in files) != 1:
            raise DistributionManifestError("birth_ownership_distribution_invalid", role)
    if inventory_path not in by_path or by_path[inventory_path].role != "boundary_inventory":
        raise DistributionManifestError("birth_ownership_distribution_invalid", "inventory binding")
    if entrypoint not in by_path or by_path[entrypoint].role != "preflight":
        raise DistributionManifestError("birth_ownership_distribution_invalid", "entrypoint binding")
    return value, tuple(files)


def _secure_read(root: Path, item: DistributionFile, *, administrative: bool) -> bytes:
    path = root.joinpath(*item.path.split("/"))
    current = root
    for segment in item.path.split("/")[:-1]:
        current /= segment
        try:
            parent = current.lstat()
        except OSError as exc:
            raise DistributionManifestError(
                "birth_ownership_distribution_file_mismatch", item.path,
            ) from exc
        if (
            not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
            or bool(getattr(parent, "st_file_attributes", 0) & 0x400)
            or (administrative and os.name != "nt" and (
                parent.st_uid != 0 or parent.st_gid != 0 or parent.st_mode & 0o022
            ))
        ):
            raise DistributionManifestError(
                "birth_ownership_distribution_file_mismatch", item.path,
            )
    try:
        before = path.lstat()
    except OSError as exc:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", item.path,
        ) from exc
    reparse = bool(getattr(before, "st_file_attributes", 0) & 0x400)
    if (
        not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
        or reparse or before.st_nlink != 1
        or (administrative and os.name != "nt" and (
            before.st_uid != 0 or before.st_gid != 0 or before.st_mode & 0o022
        ))
    ):
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", item.path,
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise DistributionManifestError(
                    "birth_ownership_distribution_file_mismatch", item.path,
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, MAX_FILE_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > item.size or total > MAX_FILE_BYTES:
                    raise DistributionManifestError(
                        "birth_ownership_distribution_file_mismatch", item.path,
                    )
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except DistributionManifestError:
        raise
    except OSError as exc:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", item.path,
        ) from exc
    content = b"".join(chunks)
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
        value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    )
    if identity(before) != identity(after) or len(content) != item.size:
        raise DistributionManifestError("birth_ownership_distribution_file_mismatch", item.path)
    return content


def _secure_read_windows(root: Path, item: DistributionFile) -> bytes:
    """Use the already-certified Win32 handle reader after parent validation."""
    from executor_birth_semantic_authority import _secure_file_bytes

    path = root.joinpath(*item.path.split("/"))
    current = root
    try:
        root_final = os.path.normcase(os.path.abspath(root))
        for segment in item.path.split("/")[:-1]:
            current /= segment
            info = current.lstat()
            if (
                not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                or os.path.commonpath((root_final, os.path.normcase(os.path.abspath(current))))
                != root_final
            ):
                raise ValueError("unsafe parent")
        return _secure_file_bytes(path, maximum=item.size, error="unsafe distribution file")
    except Exception as exc:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", item.path,
        ) from exc


def _product_version_from_source(content: bytes) -> str:
    try:
        tree = ast.parse(content.decode("utf-8"), filename="runtime/__version__.py")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", "product version",
        ) from exc
    stores = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "__version__"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    values: list[str] = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__"
               for target in statement.targets):
            if (
                len(statement.targets) != 1
                or not isinstance(statement.targets[0], ast.Name)
                or not isinstance(statement.value, ast.Constant)
                or not isinstance(statement.value.value, str)
            ):
                raise DistributionManifestError(
                    "birth_ownership_distribution_file_mismatch", "product version",
                )
            values.append(statement.value.value)
    if len(stores) != 1 or len(values) != 1 or _SEMVER_RE.fullmatch(values[0]) is None:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", "product version",
        )
    return values[0]


def _canonical_inventory(content: bytes) -> dict[str, object]:
    try:
        value = json.loads(content.decode("ascii"), object_pairs_hook=_pairs)
    except DistributionManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", "boundary inventory",
        ) from exc
    expected_policy = {
        "schema": BIRTH_CLOSED_SCHEMA,
        "guard_version": BIRTH_CLOSED_GUARD_VERSION,
        "owner": BIRTH_CLOSED_OWNER,
        "coordinator_store_owners": sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS),
        "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
        "exceptions": [
            {"scope": scope, "exception": exception}
            for scope, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items())
        ],
    }
    if (
        not isinstance(value, dict) or _canonical(value) != content
        or value.get("schema") != BOUNDARY_INVENTORY_SCHEMA
        or value.get("scan_roots") != list(SCAN_ROOTS)
        or not isinstance(value.get("entries"), list)
        or value.get("birth_closed") != expected_policy
    ):
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", "boundary inventory",
        )
    return value


def _verify_local_import_closure(
    root: Path, files: tuple[DistributionFile, ...],
    content: Mapping[str, bytes],
) -> None:
    declared = {item.path for item in files}

    def local_candidates(module: str, source: str, level: int) -> tuple[str, ...]:
        pieces = [piece for piece in module.split(".") if piece]
        if level:
            parent = source.split("/")[:-1]
            if level > len(parent):
                return ()
            pieces = parent[:len(parent) - level + 1] + pieces
        alternatives = []
        for prefix in ([], ["runtime"]):
            path = "/".join(prefix + pieces)
            if path:
                alternatives.extend((path + ".py", path + "/__init__.py"))
        return tuple(dict.fromkeys(alternatives))

    for item in files:
        if not item.path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content[item.path].decode("utf-8"), filename=item.path)
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise DistributionManifestError(
                "birth_ownership_distribution_file_mismatch", "python source",
            ) from exc
        modules: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend((alias.name, 0) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append((node.module or "", node.level))
                modules.extend(
                    (".".join(filter(None, (node.module or "", alias.name))), node.level)
                    for alias in node.names if alias.name != "*"
                )
            elif isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "__import__"
                or isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
            ):
                raise DistributionManifestError(
                    "birth_ownership_distribution_extra_file", "dynamic import",
                )
        for module, level in modules:
            candidates = local_candidates(module, item.path, level)
            existing = [candidate for candidate in candidates
                        if root.joinpath(*candidate.split("/")).exists()]
            if existing and (len(existing) != 1 or existing[0] not in declared):
                raise DistributionManifestError(
                    "birth_ownership_distribution_extra_file", "uncovered local import",
                )


def _authenticated_distribution_material_with_registry(
    encoded: bytes, signature: bytes, *, registry: DistributionRegistry,
) -> _AuthenticatedDistributionMaterialV1:
    """Authenticate immutable manifest facts without looking at live files."""
    value, files = _parse(encoded)
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise DistributionManifestError("birth_ownership_distribution_invalid", "signature")
    if not isinstance(registry, DistributionRegistry):
        raise DistributionManifestError("birth_ownership_distribution_invalid", "registry")
    key_id = str(value["signing_key_id"])
    key = registry.keys.get(key_id)
    sequence = int(value["release_sequence"])
    if (
        key is None or key.purposes != frozenset({PURPOSE})
        or sequence < key.first_release_sequence
        or (key.last_release_sequence is not None and sequence > key.last_release_sequence)
    ):
        raise DistributionManifestError("birth_ownership_distribution_key_unauthorized")
    try:
        key.public_key.verify(signature, SIGNATURE_DOMAIN + encoded)
    except InvalidSignature as exc:
        raise DistributionManifestError("birth_ownership_distribution_invalid", "signature") from exc

    return _AuthenticatedDistributionMaterialV1(
        str(value["closed_build_id"]), value["previous_closed_build_id"],
        sequence, str(value["product_version"]), str(value["platform"]),
        str(value["architecture"]), key_id, str(value["installation_root"]),
        str(value["certificate_directory"]),
        str(value["boundary_inventory_path"]),
        str(value["boundary_inventory_hash"]),
        str(value["boundary_guard_version"]),
        str(value["preflight_entrypoint"]), files, bytes(encoded),
        bytes(signature), _authenticated_artifact_binding(encoded, signature),
    )


def authenticate_distribution_record_v1(
    encoded: bytes, signature: bytes,
) -> AuthenticatedDistributionRecordV1:
    """Cold-authenticate one record using only the fixed public trust store."""
    from executor_birth_ownership_authorities import (
        _load_fixed_ownership_public_snapshot_v1,
    )

    return _authenticate_distribution_record_from_fixed_snapshot_v1(
        encoded, signature, _load_fixed_ownership_public_snapshot_v1(),
    )


def _authenticate_distribution_record_from_fixed_snapshot_v1(
    encoded: bytes, signature: bytes, snapshot,
) -> AuthenticatedDistributionRecordV1:
    from executor_birth_ownership_authorities import (
        _FIXED_PUBLIC_SNAPSHOT_SEAL, _FixedOwnershipPublicSnapshotV1,
    )

    if (
        type(snapshot) is not _FixedOwnershipPublicSnapshotV1
        or snapshot._seal is not _FIXED_PUBLIC_SNAPSHOT_SEAL
    ):
        raise DistributionManifestError(
            "birth_ownership_distribution_invalid", "authority snapshot",
        )
    material = _authenticated_distribution_material_with_registry(
        encoded, signature,
        registry=snapshot.public.distribution,
    )
    return AuthenticatedDistributionRecordV1(
        *material, _AUTHENTICATED_DISTRIBUTION_SEAL,
    )


def _authenticate_distribution_record_for_test(
    encoded: bytes, signature: bytes, *, registry: DistributionRegistry,
) -> _AuthenticatedDistributionRecordForTestV1:
    """Portable seam with a nominal result rejected by production APIs."""
    material = _authenticated_distribution_material_with_registry(
        encoded, signature, registry=registry,
    )
    return _AuthenticatedDistributionRecordForTestV1(
        *material, _TEST_AUTHENTICATED_DISTRIBUTION_SEAL,
    )


def _is_authenticated_distribution_record_v1(
    value: object, *, for_test: bool = False,
) -> bool:
    expected_type = (
        _AuthenticatedDistributionRecordForTestV1
        if for_test else AuthenticatedDistributionRecordV1
    )
    expected_seal = (
        _TEST_AUTHENTICATED_DISTRIBUTION_SEAL
        if for_test else _AUTHENTICATED_DISTRIBUTION_SEAL
    )
    return (
        type(value) is expected_type
        and value._seal is expected_seal
        and value._artifact_binding == _authenticated_artifact_binding(
            value.encoded, value.signature,
        )
    )


def _record_matches_parsed_value(
    record: AuthenticatedDistributionRecordV1 | _AuthenticatedDistributionRecordForTestV1,
    value: Mapping[str, object], files: tuple[DistributionFile, ...],
) -> bool:
    return (
        record.closed_build_id == value["closed_build_id"]
        and record.previous_closed_build_id == value["previous_closed_build_id"]
        and record.release_sequence == value["release_sequence"]
        and record.product_version == value["product_version"]
        and record.platform == value["platform"]
        and record.architecture == value["architecture"]
        and record.signing_key_id == value["signing_key_id"]
        and record.installation_root == value["installation_root"]
        and record.certificate_directory == value["certificate_directory"]
        and record.boundary_inventory_path == value["boundary_inventory_path"]
        and record.boundary_inventory_hash == value["boundary_inventory_hash"]
        and record.boundary_guard_version == value["boundary_guard_version"]
        and record.preflight_entrypoint == value["preflight_entrypoint"]
        and record.files == files
    )


def _verify_authenticated_distribution_record(
    record: AuthenticatedDistributionRecordV1 | _AuthenticatedDistributionRecordForTestV1,
    environment: _VerificationEnvironment, *, for_test: bool,
) -> VerifiedDistribution:
    expected_type = (
        _AuthenticatedDistributionRecordForTestV1
        if for_test else AuthenticatedDistributionRecordV1
    )
    expected_seal = (
        _TEST_AUTHENTICATED_DISTRIBUTION_SEAL
        if for_test else _AUTHENTICATED_DISTRIBUTION_SEAL
    )
    if (
        type(record) is not expected_type
        or record._seal is not expected_seal
        or record._artifact_binding != _authenticated_artifact_binding(
            record.encoded, record.signature,
        )
    ):
        raise DistributionManifestError(
            "birth_ownership_distribution_invalid", "authenticated artifact",
        )
    value, files = _parse(record.encoded)
    if not _record_matches_parsed_value(record, value, files):
        raise DistributionManifestError(
            "birth_ownership_distribution_invalid", "record binding",
        )
    sequence = record.release_sequence
    if not isinstance(environment, _VerificationEnvironment) or environment._seal is not _ENVIRONMENT_SEAL:
        raise DistributionManifestError("birth_ownership_distribution_platform_mismatch")
    if (value["platform"], value["architecture"]) != (
        environment.platform, environment.architecture,
    ):
        raise DistributionManifestError("birth_ownership_distribution_platform_mismatch")
    if str(value["installation_root"]) != environment.claimed_installation_root:
        raise DistributionManifestError("birth_ownership_distribution_platform_mismatch", "root")
    try:
        root_info = environment.installation_root.lstat()
    except OSError as exc:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", "installation root",
        ) from exc
    if (
        not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
        or bool(getattr(root_info, "st_file_attributes", 0) & 0x400)
        or (environment.require_administrative_metadata and os.name != "nt" and (
            root_info.st_uid != 0 or root_info.st_gid != 0
            or root_info.st_mode & 0o022
        ))
    ):
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", "installation root",
        )
    by_path = {item.path: item for item in files}
    verified_content: dict[str, bytes] = {}
    for item in files:
        content = (
            _secure_read_windows(environment.installation_root, item)
            if environment.platform == "windows"
            else _secure_read(
                environment.installation_root, item,
                administrative=environment.require_administrative_metadata,
            )
        )
        if file_content_hash(item.path, content) != item.content_hash:
            raise DistributionManifestError(
                "birth_ownership_distribution_file_mismatch", item.path,
            )
        verified_content[item.path] = content
    inventory_path = str(value["boundary_inventory_path"])
    inventory_hash = "sha256:" + hashlib.sha256(
        BOUNDARY_INVENTORY_DOMAIN + verified_content[inventory_path]
    ).hexdigest()
    if inventory_hash != value["boundary_inventory_hash"]:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", "boundary inventory",
        )
    inventory = _canonical_inventory(verified_content[inventory_path])
    if value["boundary_guard_version"] != BIRTH_CLOSED_GUARD_VERSION:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", "boundary guard version",
        )
    if environment.verify_static_boundary:
        try:
            findings = birth_closed_findings(
                discover(environment.installation_root), inventory,
            )
        except Exception as exc:
            raise DistributionManifestError(
                "birth_ownership_distribution_file_mismatch", "boundary inventory",
            ) from exc
        if findings:
            raise DistributionManifestError(
                "birth_ownership_distribution_file_mismatch", "boundary inventory",
            )
    if _product_version_from_source(
        verified_content["runtime/__version__.py"]
    ) != value["product_version"]:
        raise DistributionManifestError(
            "birth_ownership_distribution_file_mismatch", "product version",
        )
    _verify_local_import_closure(
        environment.installation_root, files, verified_content,
    )
    runtime_dir = environment.installation_root / "runtime"
    if runtime_dir.is_dir():
        declared = set(by_path)
        for path in runtime_dir.glob("executor_birth*.py"):
            relative = path.relative_to(environment.installation_root).as_posix()
            if relative not in declared:
                raise DistributionManifestError(
                    "birth_ownership_distribution_extra_file", relative,
                )
        if any(runtime_dir.rglob("__pycache__")) or any(runtime_dir.rglob("*.pyc")):
            raise DistributionManifestError("birth_ownership_distribution_extra_file", "bytecode")
    identity = ClosedBuildIdentity(
        str(value["closed_build_id"]), str(value["boundary_inventory_hash"]),
        str(value["boundary_guard_version"]), _BUILD_AUTHORITY_SEAL,
    )
    return VerifiedDistribution(
        identity, value["previous_closed_build_id"], sequence,
        str(value["product_version"]), str(value["platform"]),
        str(value["architecture"]), str(value["installation_root"]),
        str(value["certificate_directory"]), str(value["preflight_entrypoint"]),
        files, bytes(record.encoded), bytes(record.signature),
        _distribution_artifact_binding(record.encoded, record.signature),
        _VERIFIED_DISTRIBUTION_SEAL,
    )


def verify_installed_distribution_record_v1(
    record: AuthenticatedDistributionRecordV1,
) -> VerifiedDistribution:
    """Verify the one live release selected solely by its signed sequence."""
    if not sys.platform.startswith("linux"):
        raise DistributionManifestError("birth_ownership_platform_unsupported")
    if (
        type(record) is not AuthenticatedDistributionRecordV1
        or record._seal is not _AUTHENTICATED_DISTRIBUTION_SEAL
    ):
        raise DistributionManifestError(
            "birth_ownership_distribution_invalid", "authenticated artifact",
        )
    expected_root = (
        DEFAULT_RELEASE_DIRECTORY_V1 / f"{record.release_sequence:020d}"
    )
    if record.installation_root != expected_root.as_posix():
        raise DistributionManifestError(
            "birth_ownership_distribution_platform_mismatch", "root",
        )
    _require_product_release_metadata_v1(expected_root)
    observed = _runtime_environment()
    environment = _VerificationEnvironment(
        observed.platform, observed.architecture, expected_root,
        expected_root.as_posix(), True, True, _ENVIRONMENT_SEAL,
    )
    return _verify_authenticated_distribution_record(
        record, environment, for_test=False,
    )


def verify_current_installation_distribution_v1(
    encoded: bytes, signature: bytes,
) -> VerifiedDistribution:
    """Reverify the G5 installation using fixed trust and the runtime root."""
    record = authenticate_distribution_record_v1(encoded, signature)
    environment = _runtime_environment()
    _require_product_release_metadata_v1(environment.installation_root)
    return _verify_authenticated_distribution_record(
        record, environment, for_test=False,
    )


def _verify_authenticated_distribution_record_for_test(
    record: _AuthenticatedDistributionRecordForTestV1,
    *, environment: _VerificationEnvironment,
) -> VerifiedDistribution:
    return _verify_authenticated_distribution_record(
        record, environment, for_test=True,
    )


def _verify_distribution_manifest_for_test(
    encoded: bytes, signature: bytes, *, registry: DistributionRegistry,
    _environment: _VerificationEnvironment | None = None,
) -> VerifiedDistribution:
    """Compatibility verifier; productive cold paths use the fixed trust store."""
    record = _authenticate_distribution_record_for_test(
        encoded, signature, registry=registry,
    )
    return _verify_authenticated_distribution_record_for_test(
        record, environment=_environment or _runtime_environment(),
    )


def is_verified_distribution(value: object) -> bool:
    """Recognize only an artifact emitted after full manifest verification."""
    return (
        isinstance(value, VerifiedDistribution)
        and value._seal is _VERIFIED_DISTRIBUTION_SEAL
    )


def _verified_distribution_for_test(
    identity: ClosedBuildIdentity, *, previous_closed_build_id: str | None,
    release_sequence: int, encoded: bytes, signature: bytes,
) -> VerifiedDistribution:
    """Narrow test seam; production artifacts come only from the verifier."""
    return VerifiedDistribution(
        identity, previous_closed_build_id, release_sequence, "1.0.0",
        "linux", "x86_64", "/opt/metnos",
        "/var/lib/metnos/executor-birth", "runtime/preflight.py", (),
        bytes(encoded), bytes(signature),
        _distribution_artifact_binding(encoded, signature),
        _VERIFIED_DISTRIBUTION_SEAL,
    )


__all__ = [
    "BOUNDARY_INVENTORY_DOMAIN", "BUILD_ID_DOMAIN", "FILE_HASH_DOMAIN",
    "DEFAULT_RELEASE_DIRECTORY_V1", "MAX_PAYLOAD_BYTES", "PURPOSE",
    "SIGNATURE_DOMAIN", "AuthenticatedDistributionRecordV1",
    "DistributionFile", "DistributionKey",
    "DistributionManifestError", "DistributionRegistry", "VerifiedDistribution",
    "authenticate_distribution_record_v1", "distribution_key_id",
    "file_content_hash", "is_verified_distribution",
    "verify_current_installation_distribution_v1",
    "verify_installed_distribution_record_v1",
]
