"""Portable codecs and private assembly core for RM-0008 distributions.

The public surface contains only inert, standard-library codecs.  Filesystem,
authority and deployment operations remain private and are added in vertical
increments; importing this module never performs one of those operations.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Mapping


RECEIVED_SOURCE_DESCRIPTOR_BASENAME_V1 = "received-source-v1.json"
RECEIVED_SOURCE_FILE_HASH_DOMAIN_V1 = (
    b"metnos.executor-birth.received-source-file/v1\0"
)
RECEIVED_SOURCE_ID_DOMAIN_V1 = b"metnos.executor-birth.received-source/v1\0"
MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1 = 16 * 1024 * 1024
MAX_RECEIVED_SOURCE_FILES_V1 = 20_000
MAX_RECEIVED_SOURCE_DIRECTORIES_V1 = 20_000
MAX_RECEIVED_SOURCE_PATH_DEPTH_V1 = 32
MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1 = 2 * 1024 * 1024 * 1024
DEPLOYMENT_DESCRIPTOR_PATH_V1 = (
    "deployment/executor-birth-deployment-v1.json"
)
DEPLOYMENT_DESCRIPTOR_ID_DOMAIN_V1 = (
    b"metnos.executor-birth.deployment-descriptor/v1\0"
)
PREDECESSOR_DESCRIPTOR_ID_DOMAIN_V1 = (
    b"metnos.executor-birth.predecessor-descriptor/v1\0"
)
STARTUP_PREREQUISITE_ID_DOMAIN_V1 = (
    b"metnos.executor-birth.startup-prerequisite/v1\0"
)
DEFAULT_OWNERSHIP_ROOT_TEXT_V1 = "/var/lib/metnos/executor-birth"
DEFAULT_RELEASE_ROOT_TEXT_V1 = (
    DEFAULT_OWNERSHIP_ROOT_TEXT_V1 + "/releases-v1"
)
DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1 = "/usr/libexec/metnos/executor-birth-v1"
DEFAULT_SYSTEM_UNIT_ROOT_TEXT_V1 = "/etc/systemd/system"
MAX_DEPLOYMENT_DESCRIPTOR_BYTES_V1 = 1024 * 1024
MAX_PREDECESSOR_DESCRIPTOR_BYTES_V1 = 16 * 1024 * 1024
MAX_STARTUP_PREREQUISITE_BYTES_V1 = 256 * 1024
MAX_DEPLOYMENT_ARTIFACTS_V1 = 20_000
MAX_PREDECESSOR_FILES_V1 = 20_000
MAX_SERVICE_COMMANDS_V1 = 20_000

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_DOCUMENT_KEYS = frozenset({
    "schema_version", "source_id", "service_user", "files",
})
_FILE_KEYS = frozenset({"path", "size", "content_hash", "mode"})
_FILE_MODES = frozenset({0o644, 0o755})
_DEPLOYMENT_KEYS = frozenset({
    "schema_version", "descriptor_id", "release_sequence",
    "installation_root", "service_user", "service_uid", "service_gid",
    "service_supplementary_gids", "service_home", "service_shell",
    "administrative_root", "system_unit_root", "artifacts",
    "service_catalog_id", "service_coverage_hash", "python_executable",
    "openssl_executable", "systemctl_executable",
    "systemd_analyze_executable",
})
_DEPLOYMENT_ARTIFACT_KEYS = frozenset({
    "source_path", "destination_path", "kind", "install_phase", "size",
    "content_hash", "mode", "uid", "gid",
})
_PREDECESSOR_KEYS = frozenset({
    "schema_version", "predecessor_id", "transaction_id",
    "installation_root", "files", "service_commands",
    "administrative_bundle_hash", "service_catalog_id",
    "service_coverage_hash",
})
_PREDECESSOR_FILE_KEYS = frozenset({"path", "size", "content_hash"})
_SERVICE_COMMAND_KEYS = frozenset({
    "entry_id", "execution_kind", "target_executable",
    "target_executable_hash", "python_module", "target_args",
    "target_working_directory", "target_environment",
})
_ENVIRONMENT_KEYS = frozenset({"name", "value"})
_STARTUP_PREREQUISITE_KEYS = frozenset({
    "schema_version", "prerequisite_id", "request_id", "closed_build_id",
    "release_sequence", "deployment_descriptor_id", "predecessor_id",
    "administrative_bundle_hash", "python_binary_hash",
    "openssl_binary_hash", "openssl_tcb_hash", "systemctl_binary_hash",
    "systemd_analyze_binary_hash", "service_catalog_id",
    "service_coverage_hash", "systemd_manager_version",
    "candidate_units_hash", "effective_units_hash",
})
_ARTIFACT_KINDS = frozenset({
    "administrative_program", "service_unit", "timer_unit", "target_unit",
    "stop_only_unit",
})
_EXECUTION_KINDS = frozenset({
    "none", "python_module", "native_executable", "systemctl_stop",
})
_ENTRY_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_MODULE_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,31}\Z"
)
_ENVIRONMENT_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
_UNIT_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.@-]*\.(?:service|timer|target)\Z"
)
_SYSTEMD_VERSION_RE = re.compile(
    r"(?P<major>[0-9]{3})(?:\.[0-9]+)*(?:[-+~.][A-Za-z0-9]+)*\Z"
)
_SUPPORTED_SYSTEMD_MAJOR_VERSIONS_V1 = frozenset({"255"})
_FORBIDDEN_ENVIRONMENT_NAMES = frozenset({
    "PATH", "HOME", "SHELL", "VIRTUAL_ENV", "METNOS_INSTALL_ROOT",
    "METNOS_VENV", "METNOS_CONFIG", "METNOS_OWNERSHIP_ROOT",
    "METNOS_EXECUTOR_BIRTH_ROOT",
})


class DistributionAssemblerError(RuntimeError):
    """Stable failure raised by a portable distribution-material codec."""

    def __init__(
        self, code: str = "birth_ownership_distribution_invalid",
        detail: str = "",
    ) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class ReceivedSourceFileV1:
    path: str
    size: int
    content_hash: str
    mode: int


@dataclass(frozen=True, slots=True)
class ReceivedSourceV1:
    source_id: str
    service_user: str
    files: tuple[ReceivedSourceFileV1, ...]


@dataclass(frozen=True, slots=True)
class DeploymentArtifactV1:
    source_path: str
    destination_path: str
    kind: str
    install_phase: str
    size: int
    content_hash: str
    mode: int
    uid: int
    gid: int


@dataclass(frozen=True, slots=True)
class DeploymentDescriptorV1:
    descriptor_id: str
    release_sequence: int
    installation_root: str
    service_user: str
    service_uid: int
    service_gid: int
    service_supplementary_gids: tuple[int, ...]
    service_home: str
    service_shell: str
    administrative_root: str
    system_unit_root: str
    artifacts: tuple[DeploymentArtifactV1, ...]
    service_catalog_id: str
    service_coverage_hash: str
    python_executable: str
    openssl_executable: str
    systemctl_executable: str
    systemd_analyze_executable: str


@dataclass(frozen=True, slots=True)
class PredecessorFileV1:
    path: str
    size: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class ServiceCommandEnvironmentV1:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class PredecessorServiceCommandV1:
    entry_id: str
    execution_kind: str
    target_executable: str | None
    target_executable_hash: str | None
    python_module: str | None
    target_args: tuple[str, ...]
    target_working_directory: str | None
    target_environment: tuple[ServiceCommandEnvironmentV1, ...]


@dataclass(frozen=True, slots=True)
class PredecessorDescriptorV1:
    predecessor_id: str
    transaction_id: str
    installation_root: str
    files: tuple[PredecessorFileV1, ...]
    service_commands: tuple[PredecessorServiceCommandV1, ...]
    administrative_bundle_hash: str
    service_catalog_id: str
    service_coverage_hash: str


@dataclass(frozen=True, slots=True)
class StartupPrerequisiteV1:
    prerequisite_id: str
    request_id: str
    closed_build_id: str
    release_sequence: int
    deployment_descriptor_id: str
    predecessor_id: str
    administrative_bundle_hash: str
    python_binary_hash: str
    openssl_binary_hash: str
    openssl_tcb_hash: str
    systemctl_binary_hash: str
    systemd_analyze_binary_hash: str
    service_catalog_id: str
    service_coverage_hash: str
    systemd_manager_version: str
    candidate_units_hash: str
    effective_units_hash: str


def _invalid(detail: str) -> DistributionAssemblerError:
    return DistributionAssemblerError(
        "birth_ownership_distribution_invalid", detail,
    )


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _invalid("json") from exc


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise _invalid("duplicate key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> object:
    raise _invalid("json constant")


def _relative_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or unicodedata.normalize("NFC", value) != value
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
    ):
        raise _invalid("file path")
    parts = value.split("/")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or len(parts) > MAX_RECEIVED_SOURCE_PATH_DEPTH_V1
        or PurePosixPath(value).as_posix() != value
        or parts[0] == RECEIVED_SOURCE_DESCRIPTOR_BASENAME_V1
    ):
        raise _invalid("file path")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid("file path") from exc
    return value


def _absolute_path(
    value: object, detail: str, *, allow_root: bool = False,
) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or "\x00" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise _invalid(detail)
    if value == "/":
        if allow_root:
            return value
        raise _invalid(detail)
    parts = value.split("/")[1:]
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(value).as_posix() != value
    ):
        raise _invalid(detail)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(detail) from exc
    return value


def _bounded_nonnegative(value: object, detail: str) -> int:
    if type(value) is not int or not 0 <= value <= 2 ** 63 - 1:
        raise _invalid(detail)
    return value


def _positive_identity(value: object, detail: str) -> int:
    if type(value) is not int or not 0 < value < 2 ** 31:
        raise _invalid(detail)
    return value


def _release_sequence(value: object) -> int:
    if type(value) is not int or not 0 < value <= 2 ** 63 - 1:
        raise _invalid("release sequence")
    return value


def _identifier(value: object, detail: str) -> str:
    if type(value) is not str or _ENTRY_ID_RE.fullmatch(value) is None:
        raise _invalid(detail)
    return value


def _text(
    value: object, detail: str, *, maximum: int, allow_empty: bool = False,
) -> str:
    if type(value) is not str or "\x00" in value:
        raise _invalid(detail)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(detail) from exc
    if (not allow_empty and not encoded) or len(encoded) > maximum:
        raise _invalid(detail)
    return value


def _document_id(domain: bytes, document: Mapping[str, object], field: str) -> str:
    return "sha256:" + hashlib.sha256(
        domain + _canonical({
            key: item for key, item in document.items() if key != field
        })
    ).hexdigest()


def _load_document(
    encoded: bytes, *, maximum: int, keys: frozenset[str], detail: str,
) -> dict[str, object]:
    if type(encoded) is not bytes or not encoded or len(encoded) > maximum:
        raise _invalid(f"{detail} size")
    try:
        value = json.loads(
            encoded.decode("ascii"), object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except DistributionAssemblerError:
        raise
    except (
        UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError,
        RecursionError,
    ) as exc:
        raise _invalid(f"{detail} json") from exc
    if type(value) is not dict or frozenset(value) != keys:
        raise _invalid(f"{detail} keys")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise _invalid(f"{detail} schema version")
    if _canonical(value) != encoded:
        raise _invalid(f"{detail} canonical json")
    return value


def _service_user(value: object) -> str:
    if type(value) is not str or _ACCOUNT_RE.fullmatch(value) is None:
        raise _invalid("service user")
    return value


def _digest(value: object, detail: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise _invalid(detail)
    return value


def _u64be(value: int) -> bytes:
    return value.to_bytes(8, "big", signed=False)


def received_source_file_hash_v1(
    path: str, size: int, chunks: Iterable[bytes],
) -> str:
    """Hash one file without materializing its contents.

    ``size`` is authoritative framing, but the iterable must yield exactly
    that many bytes.  A short or overlong stream is invalid.
    """

    canonical_path = _relative_path(path)
    if (
        type(size) is not int
        or size < 0
        or size > MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1
    ):
        raise _invalid("file size")
    path_bytes = canonical_path.encode("utf-8")
    digest = hashlib.sha256()
    digest.update(RECEIVED_SOURCE_FILE_HASH_DOMAIN_V1)
    digest.update(_u64be(len(path_bytes)))
    digest.update(path_bytes)
    digest.update(_u64be(size))
    observed = 0
    try:
        iterator = iter(chunks)
    except TypeError as exc:
        raise _invalid("file chunks") from exc
    while True:
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        except Exception as exc:
            raise _invalid("file chunks") from exc
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise _invalid("file chunk")
        try:
            view = memoryview(chunk)
            chunk_size = view.nbytes
        except (TypeError, ValueError) as exc:
            raise _invalid("file chunk") from exc
        if chunk_size == 0:
            raise _invalid("file chunk")
        observed += chunk_size
        if observed > size:
            raise _invalid("file size")
        try:
            digest.update(view)
        except (BufferError, TypeError, ValueError) as exc:
            raise _invalid("file chunk") from exc
    if observed != size:
        raise _invalid("file size")
    return "sha256:" + digest.hexdigest()


def _file_document(item: ReceivedSourceFileV1) -> dict[str, object]:
    return {
        "path": item.path,
        "size": item.size,
        "content_hash": item.content_hash,
        "mode": item.mode,
    }


def _validated_files(
    values: Iterable[ReceivedSourceFileV1], *, sort: bool,
) -> tuple[ReceivedSourceFileV1, ...]:
    items: list[ReceivedSourceFileV1] = []
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise _invalid("files") from exc
    for value in iterator:
        if type(value) is not ReceivedSourceFileV1:
            raise _invalid("file entry")
        path = _relative_path(value.path)
        if (
            type(value.size) is not int
            or value.size < 0
            or value.size > MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1
        ):
            raise _invalid("file size")
        content_hash = _digest(value.content_hash, "file hash")
        if type(value.mode) is not int or value.mode not in _FILE_MODES:
            raise _invalid("file mode")
        items.append(ReceivedSourceFileV1(
            path, value.size, content_hash, value.mode,
        ))
        if len(items) > MAX_RECEIVED_SOURCE_FILES_V1:
            raise _invalid("file count")
    if not items:
        raise _invalid("file count")
    if sort:
        items.sort(key=lambda item: item.path.encode("utf-8"))
    total = 0
    previous_key: bytes | None = None
    seen: set[str] = set()
    directories: set[str] = set()
    for item in items:
        key = item.path.encode("utf-8")
        if previous_key is not None and key <= previous_key:
            raise _invalid("file order")
        previous_key = key
        parts = item.path.split("/")
        parents = tuple(
            "/".join(parts[:index]) for index in range(1, len(parts))
        )
        if any(parent in seen for parent in parents):
            raise _invalid("file path collision")
        if item.path in seen or item.path in directories:
            raise _invalid("file path collision")
        seen.add(item.path)
        directories.update(parents)
        if len(directories) > MAX_RECEIVED_SOURCE_DIRECTORIES_V1:
            raise _invalid("directory count")
        total += item.size
        if total > MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1:
            raise _invalid("total size")
    return tuple(items)


def _source_id(value_without_id: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(
        RECEIVED_SOURCE_ID_DOMAIN_V1 + _canonical(value_without_id)
    ).hexdigest()


def build_received_source_v1(
    service_user: str, files: tuple[ReceivedSourceFileV1, ...],
) -> ReceivedSourceV1:
    """Build the sole canonical in-memory record and mint its source ID."""

    if type(files) is not tuple:
        raise _invalid("files")
    user = _service_user(service_user)
    canonical_files = _validated_files(files, sort=True)
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "service_user": user,
        "files": [_file_document(item) for item in canonical_files],
    }
    source_id = _source_id(unsigned)
    if len(_canonical({**unsigned, "source_id": source_id})) > (
        MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1
    ):
        raise _invalid("descriptor size")
    return ReceivedSourceV1(source_id, user, canonical_files)


def encode_received_source_v1(record: ReceivedSourceV1) -> bytes:
    """Validate and encode one canonical received-source record."""

    if type(record) is not ReceivedSourceV1:
        raise _invalid("received source")
    rebuilt = build_received_source_v1(record.service_user, record.files)
    if rebuilt != record:
        raise _invalid("source id")
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "service_user": rebuilt.service_user,
        "files": [_file_document(item) for item in rebuilt.files],
    }
    document: dict[str, object] = {
        **unsigned,
        "source_id": rebuilt.source_id,
    }
    encoded = _canonical(document)
    if len(encoded) > MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1:
        raise _invalid("descriptor size")
    # Keep a single validation path for emitted and received documents.
    decode_received_source_v1(encoded)
    return encoded


def decode_received_source_v1(encoded: bytes) -> ReceivedSourceV1:
    """Strictly decode and authenticate the content-addressed descriptor."""

    if (
        type(encoded) is not bytes
        or not encoded
        or len(encoded) > MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1
    ):
        raise _invalid("descriptor size")
    try:
        value = json.loads(
            encoded.decode("ascii"), object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except DistributionAssemblerError:
        raise
    except (
        UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError,
        RecursionError,
    ) as exc:
        raise _invalid("json") from exc
    if type(value) is not dict or frozenset(value) != _DOCUMENT_KEYS:
        raise _invalid("document keys")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _invalid("schema version")
    source_id = _digest(value["source_id"], "source id")
    user = _service_user(value["service_user"])
    raw_files = value["files"]
    if type(raw_files) is not list:
        raise _invalid("files")
    parsed: list[ReceivedSourceFileV1] = []
    for raw in raw_files:
        if type(raw) is not dict or frozenset(raw) != _FILE_KEYS:
            raise _invalid("file keys")
        parsed.append(ReceivedSourceFileV1(
            raw["path"], raw["size"], raw["content_hash"], raw["mode"],
        ))
    files = _validated_files(parsed, sort=False)
    record = build_received_source_v1(user, files)
    if source_id != record.source_id:
        raise _invalid("source id")
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "service_user": record.service_user,
        "files": [_file_document(item) for item in record.files],
    }
    canonical = _canonical({**unsigned, "source_id": record.source_id})
    if encoded != canonical:
        raise _invalid("canonical json")
    return record


def _deployment_artifact_document(
    value: DeploymentArtifactV1,
) -> dict[str, object]:
    return {
        "source_path": value.source_path,
        "destination_path": value.destination_path,
        "kind": value.kind,
        "install_phase": value.install_phase,
        "size": value.size,
        "content_hash": value.content_hash,
        "mode": value.mode,
        "uid": value.uid,
        "gid": value.gid,
    }


def _validated_deployment_artifacts(
    values: Iterable[DeploymentArtifactV1], *, sort: bool,
) -> tuple[DeploymentArtifactV1, ...]:
    artifacts: list[DeploymentArtifactV1] = []
    for value in values:
        if type(value) is not DeploymentArtifactV1:
            raise _invalid("deployment artifact")
        source = _relative_path(value.source_path)
        destination = _absolute_path(
            value.destination_path, "artifact destination",
        )
        if source == DEPLOYMENT_DESCRIPTOR_PATH_V1:
            raise _invalid("descriptor self reference")
        if type(value.kind) is not str or value.kind not in _ARTIFACT_KINDS:
            raise _invalid("artifact kind")
        if value.kind == "administrative_program":
            phase = "group6_admin"
            mode = 0o755
            prefix = DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1
            relative = destination.removeprefix(prefix + "/")
            if (
                relative != "preflight.py"
                or source != "deployment/admin/preflight.py"
            ):
                raise _invalid("administrative artifact binding")
        else:
            phase = "group7_cutover"
            mode = 0o644
            prefix = DEFAULT_SYSTEM_UNIT_ROOT_TEXT_V1
            relative = destination.removeprefix(prefix + "/")
            if (
                relative == destination
                or "/" in relative
                or source != f"deployment/systemd/{relative}"
                or len(relative.encode("utf-8")) > 192
                or _UNIT_RE.fullmatch(relative) is None
            ):
                raise _invalid("unit artifact binding")
            expected_suffix = {
                "service_unit": ".service",
                "timer_unit": ".timer",
                "target_unit": ".target",
                "stop_only_unit": ".service",
            }[value.kind]
            if not relative.endswith(expected_suffix):
                raise _invalid("unit artifact kind")
        if value.install_phase != phase:
            raise _invalid("artifact phase")
        size = _bounded_nonnegative(value.size, "artifact size")
        if size > 512 * 1024 * 1024:
            raise _invalid("artifact size")
        content_hash = _digest(value.content_hash, "artifact hash")
        if type(value.mode) is not int or value.mode != mode:
            raise _invalid("artifact mode")
        if type(value.uid) is not int or value.uid != 0:
            raise _invalid("artifact uid")
        if type(value.gid) is not int or value.gid != 0:
            raise _invalid("artifact gid")
        artifacts.append(DeploymentArtifactV1(
            source, destination, value.kind, phase, size, content_hash,
            mode, 0, 0,
        ))
        if len(artifacts) > MAX_DEPLOYMENT_ARTIFACTS_V1:
            raise _invalid("artifact count")
    if not artifacts:
        raise _invalid("artifact count")
    if sum(
        item.kind == "administrative_program" for item in artifacts
    ) != 1 or not any(
        item.kind != "administrative_program" for item in artifacts
    ):
        raise _invalid("artifact coverage")
    if sort:
        artifacts.sort(key=lambda item: item.destination_path.encode("utf-8"))
    destinations = [item.destination_path for item in artifacts]
    sources = [item.source_path for item in artifacts]
    if (
        destinations != sorted(destinations, key=lambda item: item.encode("utf-8"))
        or len(destinations) != len(set(destinations))
        or len(sources) != len(set(sources))
    ):
        raise _invalid("artifact order")
    return tuple(artifacts)


def _deployment_document(
    *, descriptor_id: str | None, release_sequence: int,
    installation_root: str, service_user: str, service_uid: int,
    service_gid: int, service_supplementary_gids: tuple[int, ...],
    service_home: str, service_shell: str, administrative_root: str,
    system_unit_root: str, artifacts: tuple[DeploymentArtifactV1, ...],
    service_catalog_id: str, service_coverage_hash: str,
    python_executable: str, openssl_executable: str,
    systemctl_executable: str, systemd_analyze_executable: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "descriptor_id": descriptor_id,
        "release_sequence": release_sequence,
        "installation_root": installation_root,
        "service_user": service_user,
        "service_uid": service_uid,
        "service_gid": service_gid,
        "service_supplementary_gids": list(service_supplementary_gids),
        "service_home": service_home,
        "service_shell": service_shell,
        "administrative_root": administrative_root,
        "system_unit_root": system_unit_root,
        "artifacts": [_deployment_artifact_document(item) for item in artifacts],
        "service_catalog_id": service_catalog_id,
        "service_coverage_hash": service_coverage_hash,
        "python_executable": python_executable,
        "openssl_executable": openssl_executable,
        "systemctl_executable": systemctl_executable,
        "systemd_analyze_executable": systemd_analyze_executable,
    }


def build_deployment_descriptor_v1(
    *, release_sequence: int, service_user: str, service_uid: int,
    service_gid: int, service_supplementary_gids: tuple[int, ...],
    service_home: str, service_shell: str,
    artifacts: tuple[DeploymentArtifactV1, ...], service_catalog_id: str,
    service_coverage_hash: str, python_executable: str,
    openssl_executable: str, systemctl_executable: str,
    systemd_analyze_executable: str,
) -> DeploymentDescriptorV1:
    sequence = _release_sequence(release_sequence)
    user = _service_user(service_user)
    uid = _positive_identity(service_uid, "service uid")
    gid = _positive_identity(service_gid, "service gid")
    if type(service_supplementary_gids) is not tuple:
        raise _invalid("service supplementary gids")
    supplementary = tuple(
        _positive_identity(item, "service supplementary gid")
        for item in service_supplementary_gids
    )
    if (
        supplementary != tuple(sorted(set(supplementary)))
        or gid not in supplementary
    ):
        raise _invalid("service supplementary gids")
    home = _absolute_path(service_home, "service home")
    shell = _absolute_path(service_shell, "service shell")
    if PurePosixPath(shell).name not in {"nologin", "false"}:
        raise _invalid("service shell")
    compiled_artifacts = _validated_deployment_artifacts(artifacts, sort=True)
    catalog_id = _digest(service_catalog_id, "service catalog id")
    coverage_hash = _digest(service_coverage_hash, "service coverage hash")
    executables = tuple(
        _absolute_path(value, detail) for value, detail in (
            (python_executable, "python executable"),
            (openssl_executable, "openssl executable"),
            (systemctl_executable, "systemctl executable"),
            (systemd_analyze_executable, "systemd analyze executable"),
        )
    )
    installation_root = f"{DEFAULT_RELEASE_ROOT_TEXT_V1}/{sequence:020d}"
    unsigned = _deployment_document(
        descriptor_id=None, release_sequence=sequence,
        installation_root=installation_root, service_user=user,
        service_uid=uid, service_gid=gid,
        service_supplementary_gids=supplementary, service_home=home,
        service_shell=shell,
        administrative_root=DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1,
        system_unit_root=DEFAULT_SYSTEM_UNIT_ROOT_TEXT_V1,
        artifacts=compiled_artifacts, service_catalog_id=catalog_id,
        service_coverage_hash=coverage_hash, python_executable=executables[0],
        openssl_executable=executables[1], systemctl_executable=executables[2],
        systemd_analyze_executable=executables[3],
    )
    descriptor_id = _document_id(
        DEPLOYMENT_DESCRIPTOR_ID_DOMAIN_V1, unsigned, "descriptor_id",
    )
    result = DeploymentDescriptorV1(
        descriptor_id, sequence, installation_root, user, uid, gid,
        supplementary, home, shell, DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1,
        DEFAULT_SYSTEM_UNIT_ROOT_TEXT_V1, compiled_artifacts, catalog_id,
        coverage_hash, *executables,
    )
    unsigned["descriptor_id"] = descriptor_id
    if len(_canonical(unsigned)) > MAX_DEPLOYMENT_DESCRIPTOR_BYTES_V1:
        raise _invalid("deployment descriptor size")
    return result


def encode_deployment_descriptor_v1(record: DeploymentDescriptorV1) -> bytes:
    if type(record) is not DeploymentDescriptorV1:
        raise _invalid("deployment descriptor")
    rebuilt = build_deployment_descriptor_v1(
        release_sequence=record.release_sequence, service_user=record.service_user,
        service_uid=record.service_uid, service_gid=record.service_gid,
        service_supplementary_gids=record.service_supplementary_gids,
        service_home=record.service_home, service_shell=record.service_shell,
        artifacts=record.artifacts, service_catalog_id=record.service_catalog_id,
        service_coverage_hash=record.service_coverage_hash,
        python_executable=record.python_executable,
        openssl_executable=record.openssl_executable,
        systemctl_executable=record.systemctl_executable,
        systemd_analyze_executable=record.systemd_analyze_executable,
    )
    if rebuilt != record:
        raise _invalid("deployment descriptor id")
    document = _deployment_document(
        descriptor_id=rebuilt.descriptor_id,
        release_sequence=rebuilt.release_sequence,
        installation_root=rebuilt.installation_root,
        service_user=rebuilt.service_user, service_uid=rebuilt.service_uid,
        service_gid=rebuilt.service_gid,
        service_supplementary_gids=rebuilt.service_supplementary_gids,
        service_home=rebuilt.service_home, service_shell=rebuilt.service_shell,
        administrative_root=rebuilt.administrative_root,
        system_unit_root=rebuilt.system_unit_root, artifacts=rebuilt.artifacts,
        service_catalog_id=rebuilt.service_catalog_id,
        service_coverage_hash=rebuilt.service_coverage_hash,
        python_executable=rebuilt.python_executable,
        openssl_executable=rebuilt.openssl_executable,
        systemctl_executable=rebuilt.systemctl_executable,
        systemd_analyze_executable=rebuilt.systemd_analyze_executable,
    )
    encoded = _canonical(document)
    if len(encoded) > MAX_DEPLOYMENT_DESCRIPTOR_BYTES_V1:
        raise _invalid("deployment descriptor size")
    return encoded


def decode_deployment_descriptor_v1(encoded: bytes) -> DeploymentDescriptorV1:
    value = _load_document(
        encoded, maximum=MAX_DEPLOYMENT_DESCRIPTOR_BYTES_V1,
        keys=_DEPLOYMENT_KEYS, detail="deployment descriptor",
    )
    raw_artifacts = value["artifacts"]
    if type(raw_artifacts) is not list:
        raise _invalid("deployment artifacts")
    parsed = []
    for raw in raw_artifacts:
        if type(raw) is not dict or frozenset(raw) != _DEPLOYMENT_ARTIFACT_KEYS:
            raise _invalid("deployment artifact keys")
        parsed.append(DeploymentArtifactV1(
            raw["source_path"], raw["destination_path"], raw["kind"],
            raw["install_phase"], raw["size"], raw["content_hash"],
            raw["mode"], raw["uid"], raw["gid"],
        ))
    supplementary = value["service_supplementary_gids"]
    if type(supplementary) is not list:
        raise _invalid("service supplementary gids")
    result = build_deployment_descriptor_v1(
        release_sequence=value["release_sequence"],
        service_user=value["service_user"], service_uid=value["service_uid"],
        service_gid=value["service_gid"],
        service_supplementary_gids=tuple(supplementary),
        service_home=value["service_home"], service_shell=value["service_shell"],
        artifacts=tuple(parsed), service_catalog_id=value["service_catalog_id"],
        service_coverage_hash=value["service_coverage_hash"],
        python_executable=value["python_executable"],
        openssl_executable=value["openssl_executable"],
        systemctl_executable=value["systemctl_executable"],
        systemd_analyze_executable=value["systemd_analyze_executable"],
    )
    if (
        value["descriptor_id"] != result.descriptor_id
        or value["installation_root"] != result.installation_root
        or value["administrative_root"] != result.administrative_root
        or value["system_unit_root"] != result.system_unit_root
        or encode_deployment_descriptor_v1(result) != encoded
    ):
        raise _invalid("deployment descriptor binding")
    return result


def _predecessor_file_document(value: PredecessorFileV1) -> dict[str, object]:
    return {
        "path": value.path,
        "size": value.size,
        "content_hash": value.content_hash,
    }


def _validated_predecessor_files(
    values: Iterable[PredecessorFileV1], *, sort: bool,
) -> tuple[PredecessorFileV1, ...]:
    files: list[PredecessorFileV1] = []
    for value in values:
        if type(value) is not PredecessorFileV1:
            raise _invalid("predecessor file")
        files.append(PredecessorFileV1(
            _relative_path(value.path),
            _bounded_nonnegative(value.size, "predecessor file size"),
            _digest(value.content_hash, "predecessor file hash"),
        ))
        if len(files) > MAX_PREDECESSOR_FILES_V1:
            raise _invalid("predecessor file count")
    if not files:
        raise _invalid("predecessor file count")
    if sort:
        files.sort(key=lambda item: item.path.encode("utf-8"))
    paths = [item.path for item in files]
    if (
        paths != sorted(paths, key=lambda item: item.encode("utf-8"))
        or len(paths) != len(set(paths))
    ):
        raise _invalid("predecessor file order")
    return tuple(files)


def _environment_document(
    value: ServiceCommandEnvironmentV1,
) -> dict[str, object]:
    return {"name": value.name, "value": value.value}


def _validated_command_environment(
    values: Iterable[ServiceCommandEnvironmentV1],
) -> tuple[ServiceCommandEnvironmentV1, ...]:
    environment: list[ServiceCommandEnvironmentV1] = []
    for value in values:
        if len(environment) >= 256:
            raise _invalid("service command environment count")
        if type(value) is not ServiceCommandEnvironmentV1:
            raise _invalid("service command environment")
        if (
            type(value.name) is not str
            or _ENVIRONMENT_RE.fullmatch(value.name) is None
            or value.name in _FORBIDDEN_ENVIRONMENT_NAMES
            or value.name.startswith(("PYTHON", "LD_", "DYLD_", "OPENSSL_"))
        ):
            raise _invalid("service command environment name")
        environment.append(ServiceCommandEnvironmentV1(
            value.name,
            _text(
                value.value, "service command environment value",
                maximum=16 * 1024, allow_empty=True,
            ),
        ))
    names = [item.name for item in environment]
    if names != sorted(names) or len(names) != len(set(names)):
        raise _invalid("service command environment order")
    return tuple(environment)


def _service_command_document(
    value: PredecessorServiceCommandV1,
) -> dict[str, object]:
    return {
        "entry_id": value.entry_id,
        "execution_kind": value.execution_kind,
        "target_executable": value.target_executable,
        "target_executable_hash": value.target_executable_hash,
        "python_module": value.python_module,
        "target_args": list(value.target_args),
        "target_working_directory": value.target_working_directory,
        "target_environment": [
            _environment_document(item) for item in value.target_environment
        ],
    }


def _validated_service_commands(
    values: Iterable[PredecessorServiceCommandV1], *, sort: bool,
) -> tuple[PredecessorServiceCommandV1, ...]:
    commands: list[PredecessorServiceCommandV1] = []
    for value in values:
        if type(value) is not PredecessorServiceCommandV1:
            raise _invalid("predecessor service command")
        entry_id = _identifier(value.entry_id, "service command entry id")
        if (
            type(value.execution_kind) is not str
            or value.execution_kind not in _EXECUTION_KINDS
        ):
            raise _invalid("service command execution kind")
        kind = value.execution_kind
        if type(value.target_args) is not tuple or len(value.target_args) > 28:
            raise _invalid("service command target arguments")
        arguments = tuple(
            _text(
                item, "service command target argument", maximum=4096,
                allow_empty=True,
            )
            for item in value.target_args
        )
        if type(value.target_environment) is not tuple:
            raise _invalid("service command environment")
        environment = _validated_command_environment(value.target_environment)
        if kind == "none":
            if any(item is not None for item in (
                value.target_executable, value.target_executable_hash,
                value.python_module, value.target_working_directory,
            )) or arguments or environment:
                raise _invalid("empty service command binding")
            executable = executable_hash = module = working_directory = None
        else:
            executable = _absolute_path(
                value.target_executable, "service command executable",
            )
            executable_hash = _digest(
                value.target_executable_hash, "service command executable hash",
            )
            working_directory = _absolute_path(
                value.target_working_directory,
                "service command working directory", allow_root=True,
            )
            if kind == "python_module":
                if (
                    type(value.python_module) is not str
                    or len(value.python_module.encode("utf-8")) > 255
                    or _MODULE_RE.fullmatch(value.python_module) is None
                ):
                    raise _invalid("service command python module")
                module = value.python_module
            else:
                if value.python_module is not None:
                    raise _invalid("service command python module")
                module = None
            if kind == "systemctl_stop":
                if (
                    working_directory != "/"
                    or len(arguments) < 2
                    or arguments[0] != "stop"
                    or any(_UNIT_RE.fullmatch(item) is None for item in arguments[1:])
                    or arguments[1:] != tuple(sorted(set(arguments[1:])))
                    or environment
                ):
                    raise _invalid("service command systemctl stop")
        commands.append(PredecessorServiceCommandV1(
            entry_id, kind, executable, executable_hash, module, arguments,
            working_directory, environment,
        ))
        if len(commands) > MAX_SERVICE_COMMANDS_V1:
            raise _invalid("service command count")
    if not commands:
        raise _invalid("service command count")
    if sort:
        commands.sort(key=lambda item: item.entry_id.encode("ascii"))
    entry_ids = [item.entry_id for item in commands]
    if entry_ids != sorted(entry_ids) or len(entry_ids) != len(set(entry_ids)):
        raise _invalid("service command order")
    return tuple(commands)


def _predecessor_document(
    *, predecessor_id: str | None, transaction_id: str,
    installation_root: str, files: tuple[PredecessorFileV1, ...],
    service_commands: tuple[PredecessorServiceCommandV1, ...],
    administrative_bundle_hash: str, service_catalog_id: str,
    service_coverage_hash: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "predecessor_id": predecessor_id,
        "transaction_id": transaction_id,
        "installation_root": installation_root,
        "files": [_predecessor_file_document(item) for item in files],
        "service_commands": [
            _service_command_document(item) for item in service_commands
        ],
        "administrative_bundle_hash": administrative_bundle_hash,
        "service_catalog_id": service_catalog_id,
        "service_coverage_hash": service_coverage_hash,
    }


def build_predecessor_descriptor_v1(
    *, transaction_id: str, installation_root: str,
    files: tuple[PredecessorFileV1, ...],
    service_commands: tuple[PredecessorServiceCommandV1, ...],
    administrative_bundle_hash: str, service_catalog_id: str,
    service_coverage_hash: str,
) -> PredecessorDescriptorV1:
    if type(files) is not tuple or type(service_commands) is not tuple:
        raise _invalid("predecessor descriptor sequences")
    transaction = _digest(transaction_id, "predecessor transaction id")
    root = _absolute_path(installation_root, "predecessor installation root")
    compiled_files = _validated_predecessor_files(files, sort=True)
    compiled_commands = _validated_service_commands(service_commands, sort=True)
    bundle_hash = _digest(
        administrative_bundle_hash, "predecessor administrative bundle hash",
    )
    catalog_id = _digest(service_catalog_id, "predecessor service catalog id")
    coverage_hash = _digest(
        service_coverage_hash, "predecessor service coverage hash",
    )
    document = _predecessor_document(
        predecessor_id=None, transaction_id=transaction,
        installation_root=root, files=compiled_files,
        service_commands=compiled_commands,
        administrative_bundle_hash=bundle_hash, service_catalog_id=catalog_id,
        service_coverage_hash=coverage_hash,
    )
    predecessor_id = _document_id(
        PREDECESSOR_DESCRIPTOR_ID_DOMAIN_V1, document, "predecessor_id",
    )
    result = PredecessorDescriptorV1(
        predecessor_id, transaction, root, compiled_files, compiled_commands,
        bundle_hash, catalog_id, coverage_hash,
    )
    document["predecessor_id"] = predecessor_id
    if len(_canonical(document)) > MAX_PREDECESSOR_DESCRIPTOR_BYTES_V1:
        raise _invalid("predecessor descriptor size")
    return result


def encode_predecessor_descriptor_v1(record: PredecessorDescriptorV1) -> bytes:
    if type(record) is not PredecessorDescriptorV1:
        raise _invalid("predecessor descriptor")
    rebuilt = build_predecessor_descriptor_v1(
        transaction_id=record.transaction_id,
        installation_root=record.installation_root, files=record.files,
        service_commands=record.service_commands,
        administrative_bundle_hash=record.administrative_bundle_hash,
        service_catalog_id=record.service_catalog_id,
        service_coverage_hash=record.service_coverage_hash,
    )
    if rebuilt != record:
        raise _invalid("predecessor descriptor id")
    encoded = _canonical(_predecessor_document(
        predecessor_id=rebuilt.predecessor_id,
        transaction_id=rebuilt.transaction_id,
        installation_root=rebuilt.installation_root, files=rebuilt.files,
        service_commands=rebuilt.service_commands,
        administrative_bundle_hash=rebuilt.administrative_bundle_hash,
        service_catalog_id=rebuilt.service_catalog_id,
        service_coverage_hash=rebuilt.service_coverage_hash,
    ))
    if len(encoded) > MAX_PREDECESSOR_DESCRIPTOR_BYTES_V1:
        raise _invalid("predecessor descriptor size")
    return encoded


def decode_predecessor_descriptor_v1(encoded: bytes) -> PredecessorDescriptorV1:
    value = _load_document(
        encoded, maximum=MAX_PREDECESSOR_DESCRIPTOR_BYTES_V1,
        keys=_PREDECESSOR_KEYS, detail="predecessor descriptor",
    )
    raw_files = value["files"]
    if type(raw_files) is not list:
        raise _invalid("predecessor files")
    files: list[PredecessorFileV1] = []
    for raw in raw_files:
        if type(raw) is not dict or frozenset(raw) != _PREDECESSOR_FILE_KEYS:
            raise _invalid("predecessor file keys")
        files.append(PredecessorFileV1(
            raw["path"], raw["size"], raw["content_hash"],
        ))
    raw_commands = value["service_commands"]
    if type(raw_commands) is not list:
        raise _invalid("predecessor service commands")
    commands: list[PredecessorServiceCommandV1] = []
    for raw in raw_commands:
        if type(raw) is not dict or frozenset(raw) != _SERVICE_COMMAND_KEYS:
            raise _invalid("service command keys")
        raw_args = raw["target_args"]
        raw_environment = raw["target_environment"]
        if type(raw_args) is not list or type(raw_environment) is not list:
            raise _invalid("service command sequences")
        environment: list[ServiceCommandEnvironmentV1] = []
        for item in raw_environment:
            if type(item) is not dict or frozenset(item) != _ENVIRONMENT_KEYS:
                raise _invalid("service command environment keys")
            environment.append(ServiceCommandEnvironmentV1(
                item["name"], item["value"],
            ))
        commands.append(PredecessorServiceCommandV1(
            raw["entry_id"], raw["execution_kind"], raw["target_executable"],
            raw["target_executable_hash"], raw["python_module"],
            tuple(raw_args), raw["target_working_directory"],
            tuple(environment),
        ))
    result = build_predecessor_descriptor_v1(
        transaction_id=value["transaction_id"],
        installation_root=value["installation_root"], files=tuple(files),
        service_commands=tuple(commands),
        administrative_bundle_hash=value["administrative_bundle_hash"],
        service_catalog_id=value["service_catalog_id"],
        service_coverage_hash=value["service_coverage_hash"],
    )
    if (
        value["predecessor_id"] != result.predecessor_id
        or encode_predecessor_descriptor_v1(result) != encoded
    ):
        raise _invalid("predecessor descriptor binding")
    return result


def _startup_prerequisite_document(
    *, prerequisite_id: str | None, request_id: str, closed_build_id: str,
    release_sequence: int, deployment_descriptor_id: str,
    predecessor_id: str, administrative_bundle_hash: str,
    python_binary_hash: str, openssl_binary_hash: str,
    openssl_tcb_hash: str, systemctl_binary_hash: str,
    systemd_analyze_binary_hash: str, service_catalog_id: str,
    service_coverage_hash: str, systemd_manager_version: str,
    candidate_units_hash: str, effective_units_hash: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "prerequisite_id": prerequisite_id,
        "request_id": request_id,
        "closed_build_id": closed_build_id,
        "release_sequence": release_sequence,
        "deployment_descriptor_id": deployment_descriptor_id,
        "predecessor_id": predecessor_id,
        "administrative_bundle_hash": administrative_bundle_hash,
        "python_binary_hash": python_binary_hash,
        "openssl_binary_hash": openssl_binary_hash,
        "openssl_tcb_hash": openssl_tcb_hash,
        "systemctl_binary_hash": systemctl_binary_hash,
        "systemd_analyze_binary_hash": systemd_analyze_binary_hash,
        "service_catalog_id": service_catalog_id,
        "service_coverage_hash": service_coverage_hash,
        "systemd_manager_version": systemd_manager_version,
        "candidate_units_hash": candidate_units_hash,
        "effective_units_hash": effective_units_hash,
    }


def build_startup_prerequisite_v1(
    *, request_id: str, closed_build_id: str, release_sequence: int,
    deployment_descriptor_id: str, predecessor_id: str,
    administrative_bundle_hash: str, python_binary_hash: str,
    openssl_binary_hash: str, openssl_tcb_hash: str,
    systemctl_binary_hash: str, systemd_analyze_binary_hash: str,
    service_catalog_id: str, service_coverage_hash: str,
    systemd_manager_version: str, candidate_units_hash: str,
    effective_units_hash: str,
) -> StartupPrerequisiteV1:
    version = _text(
        systemd_manager_version, "systemd manager version", maximum=128,
    )
    match = _SYSTEMD_VERSION_RE.fullmatch(version)
    if (
        match is None
        or match.group("major") not in _SUPPORTED_SYSTEMD_MAJOR_VERSIONS_V1
    ):
        raise _invalid("systemd manager version")
    values = (
        _digest(request_id, "startup request id"),
        _digest(closed_build_id, "startup closed build id"),
        _release_sequence(release_sequence),
        _digest(deployment_descriptor_id, "startup deployment descriptor id"),
        _digest(predecessor_id, "startup predecessor id"),
        _digest(administrative_bundle_hash, "startup administrative bundle hash"),
        _digest(python_binary_hash, "startup python binary hash"),
        _digest(openssl_binary_hash, "startup openssl binary hash"),
        _digest(openssl_tcb_hash, "startup openssl tcb hash"),
        _digest(systemctl_binary_hash, "startup systemctl binary hash"),
        _digest(
            systemd_analyze_binary_hash, "startup systemd analyze binary hash",
        ),
        _digest(service_catalog_id, "startup service catalog id"),
        _digest(service_coverage_hash, "startup service coverage hash"),
        version,
        _digest(candidate_units_hash, "startup candidate units hash"),
        _digest(effective_units_hash, "startup effective units hash"),
    )
    document = _startup_prerequisite_document(
        prerequisite_id=None, request_id=values[0], closed_build_id=values[1],
        release_sequence=values[2], deployment_descriptor_id=values[3],
        predecessor_id=values[4], administrative_bundle_hash=values[5],
        python_binary_hash=values[6], openssl_binary_hash=values[7],
        openssl_tcb_hash=values[8], systemctl_binary_hash=values[9],
        systemd_analyze_binary_hash=values[10], service_catalog_id=values[11],
        service_coverage_hash=values[12], systemd_manager_version=values[13],
        candidate_units_hash=values[14], effective_units_hash=values[15],
    )
    prerequisite_id = _document_id(
        STARTUP_PREREQUISITE_ID_DOMAIN_V1, document, "prerequisite_id",
    )
    result = StartupPrerequisiteV1(prerequisite_id, *values)
    document["prerequisite_id"] = prerequisite_id
    if len(_canonical(document)) > MAX_STARTUP_PREREQUISITE_BYTES_V1:
        raise _invalid("startup prerequisite size")
    return result


def encode_startup_prerequisite_v1(record: StartupPrerequisiteV1) -> bytes:
    if type(record) is not StartupPrerequisiteV1:
        raise _invalid("startup prerequisite")
    rebuilt = build_startup_prerequisite_v1(
        request_id=record.request_id, closed_build_id=record.closed_build_id,
        release_sequence=record.release_sequence,
        deployment_descriptor_id=record.deployment_descriptor_id,
        predecessor_id=record.predecessor_id,
        administrative_bundle_hash=record.administrative_bundle_hash,
        python_binary_hash=record.python_binary_hash,
        openssl_binary_hash=record.openssl_binary_hash,
        openssl_tcb_hash=record.openssl_tcb_hash,
        systemctl_binary_hash=record.systemctl_binary_hash,
        systemd_analyze_binary_hash=record.systemd_analyze_binary_hash,
        service_catalog_id=record.service_catalog_id,
        service_coverage_hash=record.service_coverage_hash,
        systemd_manager_version=record.systemd_manager_version,
        candidate_units_hash=record.candidate_units_hash,
        effective_units_hash=record.effective_units_hash,
    )
    if rebuilt != record:
        raise _invalid("startup prerequisite id")
    encoded = _canonical(_startup_prerequisite_document(
        prerequisite_id=rebuilt.prerequisite_id,
        request_id=rebuilt.request_id, closed_build_id=rebuilt.closed_build_id,
        release_sequence=rebuilt.release_sequence,
        deployment_descriptor_id=rebuilt.deployment_descriptor_id,
        predecessor_id=rebuilt.predecessor_id,
        administrative_bundle_hash=rebuilt.administrative_bundle_hash,
        python_binary_hash=rebuilt.python_binary_hash,
        openssl_binary_hash=rebuilt.openssl_binary_hash,
        openssl_tcb_hash=rebuilt.openssl_tcb_hash,
        systemctl_binary_hash=rebuilt.systemctl_binary_hash,
        systemd_analyze_binary_hash=rebuilt.systemd_analyze_binary_hash,
        service_catalog_id=rebuilt.service_catalog_id,
        service_coverage_hash=rebuilt.service_coverage_hash,
        systemd_manager_version=rebuilt.systemd_manager_version,
        candidate_units_hash=rebuilt.candidate_units_hash,
        effective_units_hash=rebuilt.effective_units_hash,
    ))
    if len(encoded) > MAX_STARTUP_PREREQUISITE_BYTES_V1:
        raise _invalid("startup prerequisite size")
    return encoded


def decode_startup_prerequisite_v1(encoded: bytes) -> StartupPrerequisiteV1:
    value = _load_document(
        encoded, maximum=MAX_STARTUP_PREREQUISITE_BYTES_V1,
        keys=_STARTUP_PREREQUISITE_KEYS, detail="startup prerequisite",
    )
    result = build_startup_prerequisite_v1(
        request_id=value["request_id"], closed_build_id=value["closed_build_id"],
        release_sequence=value["release_sequence"],
        deployment_descriptor_id=value["deployment_descriptor_id"],
        predecessor_id=value["predecessor_id"],
        administrative_bundle_hash=value["administrative_bundle_hash"],
        python_binary_hash=value["python_binary_hash"],
        openssl_binary_hash=value["openssl_binary_hash"],
        openssl_tcb_hash=value["openssl_tcb_hash"],
        systemctl_binary_hash=value["systemctl_binary_hash"],
        systemd_analyze_binary_hash=value["systemd_analyze_binary_hash"],
        service_catalog_id=value["service_catalog_id"],
        service_coverage_hash=value["service_coverage_hash"],
        systemd_manager_version=value["systemd_manager_version"],
        candidate_units_hash=value["candidate_units_hash"],
        effective_units_hash=value["effective_units_hash"],
    )
    if (
        value["prerequisite_id"] != result.prerequisite_id
        or encode_startup_prerequisite_v1(result) != encoded
    ):
        raise _invalid("startup prerequisite binding")
    return result


__all__ = [
    "DEPLOYMENT_DESCRIPTOR_PATH_V1",
    "DeploymentArtifactV1",
    "DeploymentDescriptorV1",
    "DistributionAssemblerError",
    "MAX_DEPLOYMENT_DESCRIPTOR_BYTES_V1",
    "MAX_PREDECESSOR_DESCRIPTOR_BYTES_V1",
    "MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1",
    "MAX_RECEIVED_SOURCE_DIRECTORIES_V1",
    "MAX_RECEIVED_SOURCE_FILES_V1",
    "MAX_RECEIVED_SOURCE_PATH_DEPTH_V1",
    "MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1",
    "MAX_STARTUP_PREREQUISITE_BYTES_V1",
    "PredecessorDescriptorV1",
    "PredecessorFileV1",
    "PredecessorServiceCommandV1",
    "RECEIVED_SOURCE_DESCRIPTOR_BASENAME_V1",
    "ReceivedSourceFileV1",
    "ReceivedSourceV1",
    "ServiceCommandEnvironmentV1",
    "StartupPrerequisiteV1",
    "build_deployment_descriptor_v1",
    "build_predecessor_descriptor_v1",
    "build_received_source_v1",
    "build_startup_prerequisite_v1",
    "decode_deployment_descriptor_v1",
    "decode_predecessor_descriptor_v1",
    "decode_received_source_v1",
    "decode_startup_prerequisite_v1",
    "encode_deployment_descriptor_v1",
    "encode_predecessor_descriptor_v1",
    "encode_received_source_v1",
    "encode_startup_prerequisite_v1",
    "received_source_file_hash_v1",
]
