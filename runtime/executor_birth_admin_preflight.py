#!/usr/bin/python3
"""Root-only, standard-library startup preflight for executor Birth V1.

The installed copy is invoked with ``python3 -I -S``.  Consequently this file
must remain self contained: importing a Metnos module here would make the
component being authenticated part of its own trust path.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import platform
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, NamedTuple


SUPPORTED_SYSTEMD_VERSIONS = ("255.4-1ubuntu8.17",)
OWNERSHIP_ROOT = Path("/var/lib/metnos/executor-birth")
AUTHORITY_ROOT = OWNERSHIP_ROOT / "authorities-v1"
RELEASE_ROOT = OWNERSHIP_ROOT / "releases-v1"
RUNTIME_ROOT = Path("/run/metnos-executor-birth-v1")
OPENSSL_LINK = Path("/usr/bin/openssl")

MAX_REGISTRY_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_DISTRIBUTION_FILE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_FILES = 20_000
MAX_MANIFEST_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_OPENSSL_STREAM_BYTES = 4096
OPENSSL_TIMEOUT_SECONDS = 5.0
OPENSSL_TEARDOWN_TIMEOUT_SECONDS = 1.0
OPENSSL_TEMPORARY_PREFIX = ".verify-"
SIGNATURE_DOMAIN = b"metnos.executor-birth.closed-build/v1\0"
BUILD_ID_DOMAIN = b"metnos.executor-birth.closed-build-id/v1\0"
FILE_HASH_DOMAIN = b"metnos.executor-birth.closed-build-file/v1\0"
BOUNDARY_INVENTORY_DOMAIN = b"metnos.executor-birth.boundary-inventory/v1\0"

EXIT_MISSING = 20
EXIT_INVALID = 21
EXIT_HEAD_MISMATCH = 22
EXIT_PLATFORM = 23
EXIT_RECOVERY = 24

CODE_MISSING = "birth_ownership_preflight_missing"
CODE_INVALID = "birth_ownership_preflight_invalid"
CODE_HEAD_MISMATCH = "birth_ownership_preflight_head_mismatch"
CODE_PLATFORM = "birth_ownership_platform_unsupported"
CODE_RECOVERY = "birth_ownership_recovery_required"

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ENTRY_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_UNIT_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,191}\.(?:service|timer|target)\Z"
)
_OBSERVED_UNIT_RE = re.compile(
    r"(?:-|[A-Za-z0-9](?:[A-Za-z0-9_.:@-]|\\x[0-9A-Fa-f]{2}){0,246})\."
    r"(?:service|socket|target|device|mount|automount|swap|timer|path|slice|scope)\Z"
)
_INTEGER_RE = re.compile(r"0|[1-9][0-9]*\Z")
_DURATION_COMPONENT_RE = re.compile(
    r"(0|[1-9][0-9]*)(?:\.([0-9]{1,6}))?(us|ms|s|min|h|d|w)\Z"
)
_PROPERTY_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*\Z")
_DISTRIBUTION_KEY_RE = re.compile(
    r"distribution-ed25519-v1-sha256-[0-9a-f]{64}\Z"
)
_SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)

_REGISTRY_KEYS = frozenset({
    "schema_version", "authority", "key_id", "public_key", "purposes",
    "first_release_sequence", "last_release_sequence",
})
_MANIFEST_KEYS = frozenset({
    "schema_version", "closed_build_id", "previous_closed_build_id",
    "release_sequence", "product_version", "platform", "architecture",
    "signing_key_id", "installation_root", "certificate_directory",
    "boundary_inventory_path", "boundary_inventory_hash",
    "boundary_guard_version", "preflight_entrypoint", "files",
})
_MANIFEST_FILE_KEYS = frozenset({"path", "size", "content_hash", "role"})
_MANIFEST_ROLES = frozenset({
    "runtime_code", "preflight", "boundary_guard", "boundary_inventory",
    "service_unit", "service_catalog", "deployment_descriptor",
    "product_version", "dependency_lock",
})
_REQUIRED_MANIFEST_PATHS = {
    "deployment/admin/preflight.py": "preflight",
    "deployment/executor-birth-deployment-v1.json": "deployment_descriptor",
    "deployment/executor-birth-service-catalog-v1.json": "service_catalog",
    "runtime/contract_store.py": "runtime_code",
    "runtime/sign.py": "runtime_code",
    "runtime/contract_boundary_guard.py": "boundary_guard",
    "runtime/executor_birth.py": "runtime_code",
    "runtime/executor_birth_ownership_preflight.py": "preflight",
    "runtime/executor_birth_distribution_manifest.py": "preflight",
    "runtime/__version__.py": "product_version",
}

_BOUNDARY_INVENTORY_SCHEMA = "metnos.contract-boundary-inventory/2"
_BIRTH_CLOSED_SCHEMA = "metnos.contract-boundary-birth-closed/1"
_BIRTH_CLOSED_GUARD_VERSION = (
    "metnos.contract-boundary-inventory/2+birth-closed/1"
)
_BOUNDARY_SCAN_ROOTS = ("runtime", "install", "scripts", "executors")
_BIRTH_CLOSED_OWNER = "runtime/executor_birth_operational.py:birth_executor"
_BIRTH_CLOSED_SEALED_MODULES = (
    "runtime/contract_store.py",
    "runtime/executor_birth.py",
    "runtime/executor_birth_commit_publisher.py",
    "runtime/executor_birth_operational.py",
    "runtime/executor_birth_ownership_coordinator.py",
    "runtime/executor_birth_ownership_cutover.py",
    "runtime/executor_birth_reattestation.py",
    "runtime/sign.py",
)
_BIRTH_CLOSED_COORDINATOR_STORE_OWNERS = (
    "install/birth_ownership_authority_provisioner.py:_discard_temporary",
    "install/birth_ownership_authority_provisioner.py:_load_or_create_pair",
    "install/birth_ownership_authority_provisioner.py:_provision_ownership_authorities_at_v1",
    "install/birth_ownership_authority_provisioner.py:_provision_ownership_authorities_locked_v1",
    "install/birth_ownership_authority_provisioner.py:_provisioning_lock",
    "install/birth_ownership_authority_provisioner.py:_publish_no_replace",
    "install/birth_ownership_authority_provisioner.py:_sync_directory",
    "install/birth_ownership_authority_provisioner.py:_write_exclusive",
    "install/birth_ownership_authority_provisioner.py:provision_root_ownership_authorities_v1",
    "install/executor_birth_source_receiver.py:<module>",
    "install/executor_birth_source_receiver.py:_copy_source_file_v1",
    "install/executor_birth_source_receiver.py:_copy_source_file_v1.copied_chunks",
    "install/executor_birth_source_receiver.py:_create_private_directory_v1",
    "install/executor_birth_source_receiver.py:_create_source_directories_v1",
    "install/executor_birth_source_receiver.py:_ensure_child_directory_v1",
    "install/executor_birth_source_receiver.py:_open_received_tree_at_v1",
    "install/executor_birth_source_receiver.py:_receive_source_for_test_v1",
    "install/executor_birth_source_receiver.py:_receive_source_locked_core_v1",
    "install/executor_birth_source_receiver.py:_receive_source_v1",
    "install/executor_birth_source_receiver.py:_receive_source_with_product_session_v1",
    "install/executor_birth_source_receiver.py:_receive_source_with_test_session_v1",
    "install/executor_birth_source_receiver.py:_remove_owned_tree_at_v1",
    "install/executor_birth_source_receiver.py:_rename_no_replace_v1",
    "install/executor_birth_source_receiver.py:_seal_temporary_directories_v1",
    "install/executor_birth_source_receiver.py:_verify_received_tree_fd_v1",
    "install/executor_birth_source_receiver.py:_write_all_v1",
    "install/executor_birth_source_receiver.py:_write_descriptor_v1",
    "install/executor_birth_source_receiver.py:main",
    "runtime/executor_birth_commit_publisher.py:_BirthCommitPublisher._persist_current_reattestation",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore._append_pair",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore._update_required_head_locked",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.append_authenticated_build",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.append_cutover",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.append_head",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.initialize",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.update_required_head",
    "runtime/executor_birth_ownership_chain.py:_OwnershipChainStoreForTest._initialize_with_authorities",
    "runtime/executor_birth_ownership_chain.py:_ensure_exact_directory_v1",
    "runtime/executor_birth_ownership_chain.py:_ensure_product_directory_v1",
    "runtime/executor_birth_ownership_chain.py:_inspect_ownership_chain_state_core_v1",
    "runtime/executor_birth_ownership_chain.py:_inspect_ownership_chain_state_for_test_v1",
    "runtime/executor_birth_ownership_chain.py:_replace_required_pointer",
    "runtime/executor_birth_ownership_chain.py:_required_head_lock",
    "runtime/executor_birth_ownership_chain.py:inspect_ownership_chain_state_v1",
    "runtime/executor_birth_ownership_coordinator.py:OwnershipCoordinatorJournalV1.append",
    "runtime/executor_birth_ownership_coordinator.py:OwnershipCoordinatorJournalV1.load",
    "runtime/executor_birth_ownership_coordinator.py:_DeploymentLockLeaseV1",
    "runtime/executor_birth_ownership_coordinator.py:_LockedOwnershipCoordinatorGraphSnapshotV2",
    "runtime/executor_birth_ownership_coordinator.py:_append_coordinator_record_v1",
    "runtime/executor_birth_ownership_coordinator.py:_append_receipts_complete",
    "runtime/executor_birth_ownership_coordinator.py:_build_locked_coordinator_graph_registry_v2.require_issued",
    "runtime/executor_birth_ownership_coordinator.py:_build_locked_coordinator_graph_registry_v2.resolve_issued",
    "runtime/executor_birth_ownership_coordinator.py:_decode_record",
    "runtime/executor_birth_ownership_coordinator.py:_decode_record_v2",
    "runtime/executor_birth_ownership_coordinator.py:_deployment_lock_at_v1",
    "runtime/executor_birth_ownership_coordinator.py:_deployment_lock_for_test_v1",
    "runtime/executor_birth_ownership_coordinator.py:_deployment_lock_v1",
    "runtime/executor_birth_ownership_coordinator.py:_prepare_under_maintenance_v1",
    "runtime/executor_birth_ownership_coordinator.py:_proof_from_values",
    "runtime/executor_birth_ownership_coordinator.py:_publish_certificate_with_prerequisite_v1",
    "runtime/executor_birth_ownership_coordinator.py:_require_locked_coordinator_graph_snapshot_v2",
    "runtime/executor_birth_ownership_coordinator.py:_resolve_ownership_coordinator_locked_v2",
)
_BIRTH_CLOSED_EXCEPTION_SCOPES = (
    ("runtime/admin/manifest_refactor.py:<module>", "offline_nonproductive_authoring"),
    ("runtime/admin/manifest_refactor.py:main", "offline_nonproductive_authoring"),
    ("runtime/admin/manifest_refactor.py:refactor_manifest", "offline_nonproductive_authoring"),
    ("runtime/change_rollback.py:_rollback_create_executor", "retirement_only"),
    ("runtime/cli/skills_cli.py:_cmd_uninstall", "retirement_only"),
    ("runtime/i18n_pipeline.py:live_contract_context", "localization_only"),
    ("runtime/i18n_translator.py:<module>", "offline_nonproductive_authoring"),
    ("runtime/i18n_translator.py:_align_one_manifest", "offline_nonproductive_authoring"),
    ("runtime/i18n_translator.py:align_manifest_descriptions", "offline_nonproductive_authoring"),
    ("runtime/manifest_normalize.py:<module>", "offline_nonproductive_authoring"),
    ("runtime/manifest_normalize.py:apply_one", "offline_nonproductive_authoring"),
    ("runtime/manifest_normalize.py:main", "offline_nonproductive_authoring"),
    ("runtime/migrate_manifest_descriptions.py:<module>", "offline_nonproductive_authoring"),
    ("runtime/migrate_manifest_descriptions.py:main", "offline_nonproductive_authoring"),
    ("runtime/migrate_manifest_descriptions.py:migrate_dirs", "offline_nonproductive_authoring"),
    ("runtime/migrate_manifest_descriptions.py:migrate_one", "offline_nonproductive_authoring"),
)
_BOUNDARY_ENTRY_KEYS = frozenset({
    "path", "scope", "role", "capabilities", "destination", "phase",
})
_BOUNDARY_ROLES = frozenset({
    "administrative_tool", "birth_owner", "documentation", "live_reader",
    "migration_boundary", "offline_authoring", "operational_producer",
    "store_owner",
})

REPEATABLE_PROPERTIES = frozenset({
    "ExecStartPre", "ExecStartPreEx", "ExecStart", "ExecStartEx", "ExecStop",
    "ExecStopEx", "TimersMonotonic", "TimersCalendar",
})
_DURATION_FACTORS = {
    "us": 1, "ms": 1_000, "s": 1_000_000, "min": 60_000_000,
    "h": 3_600_000_000, "d": 86_400_000_000, "w": 604_800_000_000,
}


class PreflightError(RuntimeError):
    """One stable public denial class; detail is never written to stderr."""

    def __init__(self, code: str, exit_status: int, detail: str = "") -> None:
        self.code = code
        self.exit_status = exit_status
        self.detail = detail
        super().__init__(detail or code)


def _invalid(detail: str) -> PreflightError:
    return PreflightError(CODE_INVALID, EXIT_INVALID, detail)


def _recovery(detail: str) -> PreflightError:
    return PreflightError(CODE_RECOVERY, EXIT_RECOVERY, detail)


@dataclass(frozen=True, slots=True)
class DistributionFileV1:
    path: str
    size: int
    content_hash: str
    role: str


class DistributionFactsV1(NamedTuple):
    schema_version: int
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


@dataclass(frozen=True, slots=True)
class AuthenticatedDistributionV1:
    facts: DistributionFactsV1
    files: tuple[DistributionFileV1, ...]
    encoded: bytes
    signature: bytes
    artifact_binding: bytes

    def __post_init__(self) -> None:
        if (
            type(self.facts) is not DistributionFactsV1
            or not isinstance(self.files, tuple)
            or any(type(item) is not DistributionFileV1 for item in self.files)
            or type(self.encoded) is not bytes or type(self.signature) is not bytes
            or len(self.signature) != 64 or type(self.artifact_binding) is not bytes
            or self.artifact_binding != _distribution_artifact_binding_v1(
                self.encoded, self.signature,
            )
        ):
            raise _invalid("distribution authority")


@dataclass(frozen=True, slots=True)
class _AuthenticatedDistributionForTestV1:
    facts: DistributionFactsV1
    files: tuple[DistributionFileV1, ...]
    encoded: bytes
    signature: bytes
    artifact_binding: bytes

    def __post_init__(self) -> None:
        if (
            type(self.facts) is not DistributionFactsV1
            or not isinstance(self.files, tuple)
            or any(type(item) is not DistributionFileV1 for item in self.files)
            or type(self.encoded) is not bytes or type(self.signature) is not bytes
            or len(self.signature) != 64 or type(self.artifact_binding) is not bytes
            or self.artifact_binding != _distribution_artifact_binding_v1(
                self.encoded, self.signature,
            )
        ):
            raise _invalid("test distribution authority")


class CliCommandV1(NamedTuple):
    command: str
    entry_id: str | None


class DistributionPublicKeyV1(NamedTuple):
    key_id: str
    raw_public_key: bytes


class _DistributionSnapshotEntryV1(NamedTuple):
    path: str
    kind: str
    identity: tuple[int, ...]


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _invalid("non-canonical value") from exc


def _json_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise _invalid("duplicate JSON key")
        result[key] = value
    return result


def _reject_number(_: str) -> object:
    raise _invalid("non-integer JSON number")


def _parse_integer(raw: str) -> int:
    if len(raw) > 64:
        raise _invalid("JSON integer bound")
    try:
        return int(raw)
    except ValueError as exc:
        raise _invalid("JSON integer") from exc


def _require_json_depth_v1(value: object) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        # One maximum manifest has 20,000 file objects and about 100,000
        # value nodes.  Keep a closed margin for its top-level metadata.
        if depth > 64 or nodes > 120_000:
            raise _invalid("JSON structural bound")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def decode_canonical_json_v1(encoded: bytes, maximum: int) -> object:
    """Decode one size-bounded, duplicate-free canonical ASCII document."""
    if (
        type(encoded) is not bytes or type(maximum) is not int
        or maximum <= 0 or len(encoded) > maximum
    ):
        raise _invalid("JSON size")
    try:
        value = json.loads(
            encoded.decode("ascii"), object_pairs_hook=_json_pairs,
            parse_int=_parse_integer,
            parse_float=_reject_number, parse_constant=_reject_number,
        )
    except PreflightError:
        raise
    except (
        UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError,
    ) as exc:
        raise _invalid("JSON encoding") from exc
    _require_json_depth_v1(value)
    if _canonical_json(value) != encoded:
        raise _invalid("JSON canonicality")
    return value


def _digest(domain: bytes, payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(domain + payload).hexdigest()


def _distribution_artifact_binding_v1(encoded: bytes, signature: bytes) -> bytes:
    if type(encoded) is not bytes or type(signature) is not bytes:
        raise _invalid("distribution artifact binding")
    return hashlib.sha256(
        b"metnos.executor-birth.authenticated-distribution-record/v1\0"
        + len(encoded).to_bytes(8, "big") + encoded + signature
    ).digest()


def _distribution_facts_v1(value: dict[str, object]) -> DistributionFactsV1:
    return DistributionFactsV1(
        value["schema_version"], value["closed_build_id"],
        value["previous_closed_build_id"], value["release_sequence"],
        value["product_version"], value["platform"], value["architecture"],
        value["signing_key_id"], value["installation_root"],
        value["certificate_directory"], value["boundary_inventory_path"],
        value["boundary_inventory_hash"], value["boundary_guard_version"],
        value["preflight_entrypoint"],
    )


def _decode_distribution_registry_v1(encoded: bytes) -> DistributionPublicKeyV1:
    value = decode_canonical_json_v1(encoded, MAX_REGISTRY_BYTES)
    if (
        not isinstance(value, dict) or set(value) != _REGISTRY_KEYS
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("authority") != "distribution"
        or value.get("purposes") != ["closed_distribution_v1"]
        or type(value.get("first_release_sequence")) is not int
        or value.get("first_release_sequence") != 1
        or value.get("last_release_sequence") is not None
    ):
        raise _invalid("distribution registry schema")
    key_id = value.get("key_id")
    public_key = value.get("public_key")
    if (
        not isinstance(key_id, str) or _DISTRIBUTION_KEY_RE.fullmatch(key_id) is None
        or not isinstance(public_key, str)
    ):
        raise _invalid("distribution registry key")
    try:
        raw = base64.b64decode(public_key, validate=True)
    except (ValueError, TypeError) as exc:
        raise _invalid("distribution public key") from exc
    if (
        len(raw) != 32 or base64.b64encode(raw).decode("ascii") != public_key
        or key_id != "distribution-ed25519-v1-sha256-" + hashlib.sha256(raw).hexdigest()
    ):
        raise _invalid("distribution public key")
    return DistributionPublicKeyV1(key_id, raw)


def distribution_file_hash_v1(path: str, content: bytes) -> str:
    path = validate_relative_path_v1(path)
    if type(content) is not bytes or len(content) > MAX_DISTRIBUTION_FILE_BYTES:
        raise _invalid("distribution file")
    encoded_path = path.encode("utf-8")
    material = (
        len(encoded_path).to_bytes(8, "big") + encoded_path
        + len(content).to_bytes(8, "big") + content
    )
    return _digest(FILE_HASH_DOMAIN, material)


def _parse_distribution_manifest_v1(
    encoded: bytes,
) -> tuple[dict[str, object], tuple[DistributionFileV1, ...]]:
    value = decode_canonical_json_v1(encoded, MAX_MANIFEST_BYTES)
    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        raise _invalid("distribution manifest schema")
    sequence = value.get("release_sequence")
    previous = value.get("previous_closed_build_id")
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or type(sequence) is not int or sequence < 1
        or not isinstance(value.get("product_version"), str)
        or _SEMVER_RE.fullmatch(value["product_version"]) is None
        or value.get("platform") not in {"linux", "windows"}
        or value.get("architecture") not in {"x86_64", "aarch64"}
        or ((sequence == 1) != (previous is None))
    ):
        raise _invalid("distribution manifest header")
    if previous is not None:
        _require_digest(previous, "previous_closed_build_id")
    _require_digest(value.get("closed_build_id"), "closed_build_id")
    _require_digest(value.get("boundary_inventory_hash"), "boundary_inventory_hash")
    key_id = value.get("signing_key_id")
    if not isinstance(key_id, str) or _DISTRIBUTION_KEY_RE.fullmatch(key_id) is None:
        raise _invalid("distribution signing key")
    target_platform = value["platform"]
    installation_root = _validate_distribution_absolute_path_v1(
        value.get("installation_root"), target_platform,
    )
    certificate_directory = _validate_distribution_absolute_path_v1(
        value.get("certificate_directory"), target_platform,
    )
    if value.get("platform") == "linux" and certificate_directory != OWNERSHIP_ROOT.as_posix():
        raise _invalid("certificate directory")
    inventory_path = validate_relative_path_v1(value.get("boundary_inventory_path"))
    preflight_path = validate_relative_path_v1(value.get("preflight_entrypoint"))
    guard_version = value.get("boundary_guard_version")
    if (
        not isinstance(guard_version, str) or not guard_version
        or "\0" in guard_version or len(guard_version.encode("utf-8")) > 128
    ):
        raise _invalid("boundary guard version")
    raw_files = value.get("files")
    if (
        not isinstance(raw_files, list) or not raw_files
        or len(raw_files) > MAX_MANIFEST_FILES
    ):
        raise _invalid("distribution file list")
    files: list[DistributionFileV1] = []
    total_size = 0
    for raw in raw_files:
        if not isinstance(raw, dict) or set(raw) != _MANIFEST_FILE_KEYS:
            raise _invalid("distribution file schema")
        path = validate_relative_path_v1(raw.get("path"))
        size = raw.get("size")
        role = raw.get("role")
        if (
            type(size) is not int or size < 0 or size > MAX_DISTRIBUTION_FILE_BYTES
            or role not in _MANIFEST_ROLES
        ):
            raise _invalid("distribution file entry")
        total_size += size
        if total_size > MAX_MANIFEST_TOTAL_BYTES:
            raise _invalid("distribution total size")
        files.append(DistributionFileV1(
            path, size, _require_digest(raw.get("content_hash"), "content_hash"), role,
        ))
    paths = [item.path for item in files]
    if (
        paths != sorted(paths, key=lambda item: item.encode("utf-8"))
        or len(paths) != len(set(paths))
    ):
        raise _invalid("distribution file order")
    by_path = {item.path: item for item in files}
    if any(
        path not in by_path or by_path[path].role != role
        for path, role in _REQUIRED_MANIFEST_PATHS.items()
    ):
        raise _invalid("required distribution files")
    for role in (
        "boundary_inventory", "dependency_lock", "service_catalog",
        "deployment_descriptor",
    ):
        if sum(item.role == role for item in files) != 1:
            raise _invalid("distribution role cardinality")
    if not any(item.role == "service_unit" for item in files):
        raise _invalid("service unit cardinality")
    if (
        inventory_path not in by_path or by_path[inventory_path].role != "boundary_inventory"
        or preflight_path != "deployment/admin/preflight.py"
        or preflight_path not in by_path or by_path[preflight_path].role != "preflight"
    ):
        raise _invalid("manifest path binding")
    unsigned = dict(value)
    unsigned.pop("closed_build_id")
    if value["closed_build_id"] != _digest(BUILD_ID_DOMAIN, _canonical_json(unsigned)):
        raise _invalid("closed build id")
    expected_root = (RELEASE_ROOT / f"{sequence:020d}").as_posix()
    if value.get("platform") == "linux" and installation_root != expected_root:
        raise _invalid("installation root")
    return value, tuple(files)


def _validate_distribution_absolute_path_v1(value: object, target: str) -> str:
    if target == "linux":
        return validate_absolute_path_v1(value)
    if (
        not isinstance(value, str) or not value or "\0" in value
        or unicodedata.normalize("NFC", value) != value
        or value.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\"))
        or "/" in value or value.count(":") != 1
        or len(value.encode("utf-8")) > 4096
    ):
        raise _invalid("Windows absolute path")
    path = PureWindowsPath(value)
    if (
        not path.is_absolute() or path.drive.startswith("\\") or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise _invalid("Windows absolute path")
    return value


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise _invalid(field)
    return value


def validate_digest_v1(value: object) -> str:
    return _require_digest(value, "digest")


def validate_entry_id_v1(value: object) -> str:
    if not isinstance(value, str) or _ENTRY_ID_RE.fullmatch(value) is None:
        raise _invalid("entry_id")
    return value


def validate_unit_name_v1(value: object) -> str:
    if (
        not isinstance(value, str) or _UNIT_RE.fullmatch(value) is None
        or len(value.encode("utf-8")) > 192
    ):
        raise _invalid("unit name")
    return value


def validate_absolute_path_v1(value: object) -> str:
    if (
        not isinstance(value, str) or not value or "\0" in value
        or "\\" in value or unicodedata.normalize("NFC", value) != value
    ):
        raise _invalid("absolute path")
    try:
        encoded = value.encode("utf-8")
        path = PurePosixPath(value)
    except (UnicodeEncodeError, ValueError) as exc:
        raise _invalid("absolute path") from exc
    if (
        len(encoded) > 4096 or not path.is_absolute()
        or not value.startswith("/") or value.startswith("//")
        or str(path) != value or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise _invalid("absolute path")
    return value


def validate_relative_path_v1(value: object) -> str:
    if (
        not isinstance(value, str) or not value or "\0" in value
        or "\\" in value or unicodedata.normalize("NFC", value) != value
    ):
        raise _invalid("relative path")
    try:
        encoded = value.encode("utf-8")
        path = PurePosixPath(value)
    except (UnicodeEncodeError, ValueError) as exc:
        raise _invalid("relative path") from exc
    if (
        len(encoded) > 4096 or path.is_absolute() or value == "."
        or str(path) != value
        or len(path.parts) > 32 or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise _invalid("relative path")
    return value


def require_linux_before_io_v1() -> None:
    """First productive guard; reading ``sys.platform`` performs no I/O."""
    if sys.platform != "linux":
        raise PreflightError(CODE_PLATFORM, EXIT_PLATFORM, "platform")


def _missing(detail: str) -> PreflightError:
    return PreflightError(CODE_MISSING, EXIT_MISSING, detail)


def _metadata_identity_v1(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _require_safe_directory_chain_v1(
    directory: Path, *, uid: int, gid: int, stop: Path | None,
) -> None:
    if not directory.is_absolute():
        raise _invalid("directory chain")
    stop_value = stop.as_posix() if stop is not None else None
    current = directory
    checked_stop = stop is None
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            raise _missing("directory chain") from exc
        except OSError as exc:
            raise _invalid("directory chain") from exc
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o022
        ):
            raise _invalid("unsafe directory chain")
        if stop_value is not None and current.as_posix() == stop_value:
            checked_stop = True
            break
        if current.parent == current:
            break
        current = current.parent
    if not checked_stop:
        raise _invalid("directory outside trusted root")


def _read_bounded_regular_v1(
    path: Path, maximum: int, *, uid: int, gid: int,
    mode: int | None = None, chain_stop: Path | None = None,
) -> bytes:
    """Read one immutable regular file through a no-follow stable handle."""
    if (
        not isinstance(path, Path) or not path.is_absolute()
        or type(maximum) is not int or maximum < 0
        or type(uid) is not int or type(gid) is not int
        or (mode is not None and type(mode) is not int)
        or (
            chain_stop is not None and (
                not isinstance(chain_stop, Path) or not chain_stop.is_absolute()
                or not path.is_relative_to(chain_stop)
            )
        )
    ):
        raise _invalid("bounded read arguments")
    _require_safe_directory_chain_v1(path.parent, uid=uid, gid=gid, stop=chain_stop)
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise _missing("required file") from exc
    except OSError as exc:
        raise _invalid("required file") from exc
    if (
        not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
        or before.st_uid != uid or before.st_gid != gid or before.st_nlink != 1
        or before.st_mode & 0o022 or before.st_size > maximum
        or (mode is not None and stat.S_IMODE(before.st_mode) != mode)
    ):
        raise _invalid("unsafe regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _invalid("open regular file") from exc
    try:
        opened = os.fstat(descriptor)
        if _metadata_identity_v1(opened) != _metadata_identity_v1(before):
            raise _invalid("file replaced before read")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum:
                raise _invalid("file too large")
        after = os.fstat(descriptor)
    except OSError as exc:
        raise _invalid("read regular file") from exc
    finally:
        os.close(descriptor)
    if _metadata_identity_v1(after) != _metadata_identity_v1(before):
        raise _invalid("file changed during read")
    try:
        final_path = path.lstat()
    except OSError as exc:
        raise _invalid("file path changed during read") from exc
    if _metadata_identity_v1(final_path) != _metadata_identity_v1(before):
        raise _invalid("file path changed during read")
    return b"".join(chunks)


def _validate_snapshot_directory_v1(
    info: os.stat_result, *, uid: int, gid: int, device: int | None = None,
) -> None:
    if (
        not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o022
        or (device is not None and info.st_dev != device)
    ):
        raise _invalid("unsafe distribution directory")


def _distribution_trie_v1(
    files: tuple[DistributionFileV1, ...],
) -> dict[str, object]:
    root: dict[str, object] = {}
    for item in files:
        components = item.path.split("/")
        folded_components = tuple(component.casefold() for component in components)
        if (
            item.path.casefold().endswith((".pyc", ".pyo"))
            or "__pycache__" in folded_components
        ):
            raise _invalid("declared bytecode")
        node = root
        for index, component in enumerate(components):
            final = index == len(components) - 1
            present = node.get(component)
            if final:
                if present is not None:
                    raise _invalid("distribution path collision")
                node[component] = item
            else:
                if present is None:
                    child: dict[str, object] = {}
                    node[component] = child
                    node = child
                elif isinstance(present, dict):
                    node = present
                else:
                    raise _invalid("distribution path collision")
    return root


def _snapshot_expected_names_v1(
    descriptor: int, node: dict[str, object],
) -> tuple[str, ...]:
    try:
        observed_names = os.listdir(descriptor)
    except OSError as exc:
        raise _invalid("distribution directory listing") from exc
    if len(observed_names) != len(set(observed_names)):
        raise _invalid("duplicate distribution name")
    expected_names = frozenset(node)
    for name in observed_names:
        try:
            canonical_name = (
                isinstance(name, str) and name not in {"", ".", ".."}
                and "/" not in name and "\0" not in name
                and unicodedata.normalize("NFC", name) == name
                and name.encode("utf-8")
            )
        except UnicodeEncodeError as exc:
            raise _invalid("distribution name") from exc
        if not canonical_name or name not in expected_names:
            raise _invalid("extra distribution entry")
    if frozenset(observed_names) != expected_names:
        raise _invalid("missing distribution entry")
    return tuple(sorted(expected_names, key=lambda value: value.encode("utf-8")))


def _snapshot_open_flags_v1(directory: bool) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        return flags | getattr(os, "O_DIRECTORY", 0)
    return flags | getattr(os, "O_NONBLOCK", 0)


def _snapshot_distribution_inventory_v1(
    descriptor: int, node: dict[str, object], prefix: str, *,
    uid: int, gid: int, device: int,
    output: list[_DistributionSnapshotEntryV1],
) -> None:
    before = os.fstat(descriptor)
    _validate_snapshot_directory_v1(before, uid=uid, gid=gid, device=device)
    names = _snapshot_expected_names_v1(descriptor, node)
    for name in names:
        expected = node[name]
        relative = name if not prefix else prefix + "/" + name
        try:
            child_descriptor = os.open(
                name, _snapshot_open_flags_v1(isinstance(expected, dict)),
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise _invalid("distribution entry open") from exc
        try:
            child_before = os.fstat(child_descriptor)
            if isinstance(expected, dict):
                _validate_snapshot_directory_v1(
                    child_before, uid=uid, gid=gid, device=device,
                )
                if not expected:
                    raise _invalid("empty distribution directory")
                kind = "directory"
            else:
                if type(expected) is not DistributionFileV1:
                    raise _invalid("distribution trie")
                if (
                    not stat.S_ISREG(child_before.st_mode)
                    or stat.S_ISLNK(child_before.st_mode)
                    or child_before.st_uid != uid or child_before.st_gid != gid
                    or child_before.st_dev != device
                    or child_before.st_nlink != 1 or child_before.st_mode & 0o022
                    or child_before.st_size != expected.size
                ):
                    raise _invalid("unsafe distribution file")
                kind = "file"
            output.append(_DistributionSnapshotEntryV1(
                relative, kind, _metadata_identity_v1(child_before),
            ))
            if isinstance(expected, dict):
                _snapshot_distribution_inventory_v1(
                    child_descriptor, expected, relative, uid=uid, gid=gid,
                    device=device, output=output,
                )
            child_after = os.fstat(child_descriptor)
            try:
                live_after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                raise _invalid("distribution entry replaced") from exc
            if (
                _metadata_identity_v1(child_before)
                != _metadata_identity_v1(child_after)
                or _metadata_identity_v1(child_before)
                != _metadata_identity_v1(live_after)
            ):
                raise _invalid("distribution entry changed")
        finally:
            os.close(child_descriptor)
    after = os.fstat(descriptor)
    if _metadata_identity_v1(before) != _metadata_identity_v1(after):
        raise _invalid("distribution directory changed")


def _read_distribution_bytes_v1(
    descriptor: int, node: dict[str, object], prefix: str, *,
    uid: int, gid: int, device: int,
    snapshot: dict[str, _DistributionSnapshotEntryV1],
    capture_paths: frozenset[str], captured: dict[str, bytes],
) -> None:
    before = os.fstat(descriptor)
    _validate_snapshot_directory_v1(before, uid=uid, gid=gid, device=device)
    current_path = prefix
    current = snapshot.get(current_path)
    if (
        current is None or current.kind != "directory"
        or current.identity != _metadata_identity_v1(before)
    ):
        raise _invalid("distribution directory changed after snapshot A")
    names = _snapshot_expected_names_v1(descriptor, node)

    for name in names:
        expected = node[name]
        relative = name if not prefix else prefix + "/" + name
        try:
            child_descriptor = os.open(
                name, _snapshot_open_flags_v1(isinstance(expected, dict)),
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise _invalid("distribution entry open") from exc
        try:
            child_before = os.fstat(child_descriptor)
            expected_snapshot = snapshot.get(relative)
            expected_kind = "directory" if isinstance(expected, dict) else "file"
            if (
                expected_snapshot is None or expected_snapshot.kind != expected_kind
                or expected_snapshot.identity != _metadata_identity_v1(child_before)
            ):
                raise _invalid("distribution entry changed after snapshot A")
            if isinstance(expected, dict):
                _validate_snapshot_directory_v1(
                    child_before, uid=uid, gid=gid, device=device,
                )
                _read_distribution_bytes_v1(
                    child_descriptor, expected, relative, uid=uid, gid=gid,
                    device=device,
                    snapshot=snapshot, capture_paths=capture_paths,
                    captured=captured,
                )
            else:
                if type(expected) is not DistributionFileV1:
                    raise _invalid("distribution trie")
                if (
                    not stat.S_ISREG(child_before.st_mode)
                    or stat.S_ISLNK(child_before.st_mode)
                    or child_before.st_uid != uid or child_before.st_gid != gid
                    or child_before.st_dev != device
                    or child_before.st_nlink != 1 or child_before.st_mode & 0o022
                    or child_before.st_size != expected.size
                ):
                    raise _invalid("unsafe distribution file")
                path_bytes = relative.encode("utf-8")
                hasher = hashlib.sha256(
                    FILE_HASH_DOMAIN + len(path_bytes).to_bytes(8, "big")
                    + path_bytes + expected.size.to_bytes(8, "big")
                )
                output = bytearray() if relative in capture_paths else None
                total = 0
                while True:
                    chunk = os.read(
                        child_descriptor,
                        min(1024 * 1024, expected.size + 1 - total),
                    )
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > expected.size:
                        raise _invalid("distribution file grew")
                    hasher.update(chunk)
                    if output is not None:
                        output.extend(chunk)
                if total != expected.size:
                    raise _invalid("distribution file size")
                if "sha256:" + hasher.hexdigest() != expected.content_hash:
                    raise _invalid("distribution file hash")
                if output is not None:
                    captured[relative] = bytes(output)
            child_after = os.fstat(child_descriptor)
            try:
                live_after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                raise _invalid("distribution entry replaced") from exc
            if (
                _metadata_identity_v1(child_before)
                != _metadata_identity_v1(child_after)
                or _metadata_identity_v1(child_before)
                != _metadata_identity_v1(live_after)
            ):
                raise _invalid("distribution entry changed")
        finally:
            os.close(child_descriptor)
    after = os.fstat(descriptor)
    if (
        _metadata_identity_v1(before) != _metadata_identity_v1(after)
        or current.identity != _metadata_identity_v1(after)
    ):
        raise _invalid("distribution directory changed")


def _snapshot_exact_distribution_tree_v1(
    root: Path, files: tuple[DistributionFileV1, ...], *,
    uid: int, gid: int, chain_stop: Path | None,
    capture_paths: frozenset[str],
    semantic_check: Callable[[dict[str, bytes]], None] | None = None,
) -> dict[str, bytes]:
    trie = _distribution_trie_v1(files)
    if not trie:
        raise _invalid("empty distribution")
    _require_safe_directory_chain_v1(root, uid=uid, gid=gid, stop=chain_stop)
    try:
        path_before = root.lstat()
    except OSError as exc:
        raise _invalid("distribution root") from exc
    _validate_snapshot_directory_v1(path_before, uid=uid, gid=gid)
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise _invalid("distribution root open") from exc
    captured: dict[str, bytes] = {}
    try:
        opened = os.fstat(descriptor)
        if _metadata_identity_v1(path_before) != _metadata_identity_v1(opened):
            raise _invalid("distribution root replaced")
        snapshot_a_items = [_DistributionSnapshotEntryV1(
            "", "directory", _metadata_identity_v1(opened),
        )]
        _snapshot_distribution_inventory_v1(
            descriptor, trie, "", uid=uid, gid=gid, device=opened.st_dev,
            output=snapshot_a_items,
        )
        snapshot_a = {item.path: item for item in snapshot_a_items}
        if len(snapshot_a) != len(snapshot_a_items):
            raise _invalid("distribution snapshot A collision")
        _read_distribution_bytes_v1(
            descriptor, trie, "", uid=uid, gid=gid, device=opened.st_dev,
            snapshot=snapshot_a,
            capture_paths=capture_paths, captured=captured,
        )
        if semantic_check is not None:
            semantic_check(captured)
        snapshot_b_items = [_DistributionSnapshotEntryV1(
            "", "directory", _metadata_identity_v1(os.fstat(descriptor)),
        )]
        _snapshot_distribution_inventory_v1(
            descriptor, trie, "", uid=uid, gid=gid, device=opened.st_dev,
            output=snapshot_b_items,
        )
        if tuple(snapshot_a_items) != tuple(snapshot_b_items):
            raise _invalid("distribution snapshot A/B mismatch")
        final_descriptor = os.fstat(descriptor)
        try:
            path_after = root.lstat()
        except OSError as exc:
            raise _invalid("distribution root replaced") from exc
        if (
            _metadata_identity_v1(path_before)
            != _metadata_identity_v1(final_descriptor)
            or _metadata_identity_v1(path_before)
            != _metadata_identity_v1(path_after)
        ):
            raise _invalid("distribution root changed")
    finally:
        os.close(descriptor)
    if frozenset(captured) != capture_paths:
        raise _invalid("distribution capture")
    return captured


def _resolve_root_executable_v1(path: Path) -> Path:
    """Resolve at most eight root-owned links without PATH lookup."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise _invalid("executable path")
    current = path
    for _index in range(9):
        _require_safe_directory_chain_v1(current.parent, uid=0, gid=0, stop=None)
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            raise _missing("executable") from exc
        except OSError as exc:
            raise _invalid("executable") from exc
        if stat.S_ISLNK(info.st_mode):
            if info.st_uid != 0 or info.st_gid != 0 or _index == 8:
                raise _invalid("executable link")
            try:
                target = os.readlink(current)
            except OSError as exc:
                raise _invalid("executable link") from exc
            candidate = Path(target)
            if not candidate.is_absolute():
                candidate = current.parent / candidate
            candidate = Path(os.path.normpath(candidate))
            if not candidate.is_absolute():
                raise _invalid("executable link target")
            current = candidate
            continue
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or info.st_nlink != 1 or info.st_mode & 0o022
            or not info.st_mode & 0o111
        ):
            raise _invalid("unsafe executable")
        return current
    raise _invalid("executable link depth")


def _ed25519_public_pem_v1(raw: bytes) -> bytes:
    if type(raw) is not bytes or len(raw) != 32:
        raise _invalid("Ed25519 public key")
    der = bytes.fromhex("302a300506032b6570032100") + raw
    body = base64.b64encode(der)
    return b"-----BEGIN PUBLIC KEY-----\n" + body + b"\n-----END PUBLIC KEY-----\n"


def _write_private_temporary_v1(path: Path, content: bytes, uid: int, gid: int) -> None:
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("short temporary write")
            offset += written
        os.fsync(descriptor)
        info = os.fstat(descriptor)
    except OSError as exc:
        raise _invalid("OpenSSL temporary") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid
        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size != len(content)
    ):
        raise _invalid("OpenSSL temporary metadata")


def _require_no_openssl_residue_v1(temporary_root: Path) -> None:
    try:
        with os.scandir(temporary_root) as entries:
            for entry in entries:
                if entry.name.startswith(OPENSSL_TEMPORARY_PREFIX):
                    raise _recovery("OpenSSL temporary residue")
    except PreflightError:
        raise
    except OSError as exc:
        raise _invalid("OpenSSL temporary inventory") from exc


def _teardown_openssl_process_v1(process: subprocess.Popen[bytes]) -> None:
    failed = False
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        failed = True
    try:
        process.wait(timeout=OPENSSL_TEARDOWN_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        failed = True
    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            failed = True
    try:
        if process.poll() is None:
            failed = True
    except OSError:
        failed = True
    if failed:
        raise _recovery("OpenSSL process teardown")


def _run_openssl_bounded_v1(argv: tuple[str, ...]) -> tuple[int, bytes, bytes]:
    process: subprocess.Popen[bytes] | None = None
    output = bytearray()
    error = bytearray()
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env={"LC_ALL": "C"}, shell=False,
            close_fds=True,
        )
        if process.stdout is None or process.stderr is None:
            raise _invalid("OpenSSL pipes")
        for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        deadline = time.monotonic() + OPENSSL_TIMEOUT_SECONDS
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, OPENSSL_TIMEOUT_SECONDS)
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(argv, OPENSSL_TIMEOUT_SECONDS)
            for key, _mask in events:
                try:
                    chunk = os.read(
                        key.fileobj.fileno(), MAX_OPENSSL_STREAM_BYTES + 1,
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = output if key.data == "stdout" else error
                target.extend(chunk)
                if len(target) > MAX_OPENSSL_STREAM_BYTES:
                    raise _invalid("OpenSSL output bound")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, OPENSSL_TIMEOUT_SECONDS)
        returncode = process.wait(timeout=remaining)
        return returncode, bytes(output), bytes(error)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _invalid("OpenSSL execution") from exc
    finally:
        active_failure = sys.exception()
        teardown_failure: PreflightError | None = None
        try:
            selector.close()
        except OSError:
            teardown_failure = _recovery("OpenSSL selector teardown")
        if process is not None:
            try:
                _teardown_openssl_process_v1(process)
            except PreflightError as exc:
                teardown_failure = exc
        if teardown_failure is not None:
            if active_failure is not None:
                raise teardown_failure from active_failure
            raise teardown_failure


def _verify_ed25519_openssl_core_v1(
    raw_public_key: bytes, payload: bytes, signature: bytes, *,
    openssl_executable: Path, temporary_root: Path,
    temporary_uid: int, temporary_gid: int, chain_stop: Path | None,
) -> None:
    if (
        type(payload) is not bytes or type(signature) is not bytes
        or len(signature) != 64 or not isinstance(openssl_executable, Path)
        or not openssl_executable.is_absolute()
        or not isinstance(temporary_root, Path) or not temporary_root.is_absolute()
    ):
        raise _invalid("signature verification arguments")
    _require_safe_directory_chain_v1(
        temporary_root, uid=temporary_uid, gid=temporary_gid, stop=chain_stop,
    )
    _require_no_openssl_residue_v1(temporary_root)
    try:
        try:
            directory_name = tempfile.mkdtemp(prefix=".verify-", dir=temporary_root)
        except OSError as exc:
            raise _invalid("OpenSSL temporary directory") from exc
        directory = Path(directory_name)
        if (
            directory.parent != temporary_root
            or not directory.name.startswith(".verify-")
            or "/" in directory.name or "\0" in directory.name
        ):
            raise _invalid("OpenSSL temporary directory name")
        directory_info = directory.lstat()
        if (
            directory_info.st_uid != temporary_uid
            or directory_info.st_gid != temporary_gid
            or stat.S_IMODE(directory_info.st_mode) != 0o700
        ):
            raise _invalid("OpenSSL temporary directory")
        key_path = directory / "public-key.pem"
        payload_path = directory / "payload.bin"
        signature_path = directory / "signature.bin"
        _write_private_temporary_v1(
            key_path, _ed25519_public_pem_v1(raw_public_key),
            temporary_uid, temporary_gid,
        )
        _write_private_temporary_v1(payload_path, payload, temporary_uid, temporary_gid)
        _write_private_temporary_v1(
            signature_path, signature, temporary_uid, temporary_gid,
        )
        argv = (
            str(openssl_executable), "pkeyutl", "-config", "/dev/null",
            "-provider", "default", "-propquery", "provider=default",
            "-verify", "-pubin", "-inkey", str(key_path), "-rawin",
            "-in", str(payload_path), "-sigfile", str(signature_path),
        )
        returncode, stdout, stderr = _run_openssl_bounded_v1(argv)
        if (
            returncode != 0
            or stderr != b"Using configuration from /dev/null\n"
            or stdout != b"Signature Verified Successfully\n"
        ):
            raise _invalid("distribution signature")
    finally:
        active_failure = sys.exception()
        directory = locals().get("directory")
        if isinstance(directory, Path):
            cleanup_failed = False
            for name in ("public-key.pem", "payload.bin", "signature.bin"):
                try:
                    (directory / name).unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    cleanup_failed = True
            try:
                directory.rmdir()
            except OSError:
                cleanup_failed = True
            if cleanup_failed:
                cleanup_failure = _recovery("OpenSSL temporary residue")
                if active_failure is not None:
                    raise cleanup_failure from active_failure
                raise cleanup_failure


def _load_product_distribution_registry_v1() -> DistributionPublicKeyV1:
    require_linux_before_io_v1()
    encoded = _read_bounded_regular_v1(
        AUTHORITY_ROOT / "distribution-registry-v1.json", MAX_REGISTRY_BYTES,
        uid=0, gid=0, mode=0o644,
    )
    return _decode_distribution_registry_v1(encoded)


def authenticate_distribution_v1(
    encoded: bytes, signature: bytes,
) -> AuthenticatedDistributionV1:
    """Authenticate manifest bytes using only the fixed productive trust root."""
    require_linux_before_io_v1()
    value, files = _parse_distribution_manifest_v1(encoded)
    registry = _load_product_distribution_registry_v1()
    if value["signing_key_id"] != registry.key_id:
        raise _invalid("distribution signing key")
    openssl_executable = _resolve_root_executable_v1(OPENSSL_LINK)
    _verify_ed25519_openssl_core_v1(
        registry.raw_public_key, SIGNATURE_DOMAIN + encoded, signature,
        openssl_executable=openssl_executable, temporary_root=RUNTIME_ROOT,
        temporary_uid=0, temporary_gid=0, chain_stop=None,
    )
    return AuthenticatedDistributionV1(
        _distribution_facts_v1(value), files, bytes(encoded), bytes(signature),
        _distribution_artifact_binding_v1(encoded, signature),
    )


def _authenticate_distribution_for_test_v1(
    encoded: bytes, signature: bytes, registry_encoded: bytes, *,
    openssl_executable: Path, temporary_root: Path,
) -> _AuthenticatedDistributionForTestV1:
    """Nominally distinct seam; its result is rejected by productive loaders."""
    value, files = _parse_distribution_manifest_v1(encoded)
    registry = _decode_distribution_registry_v1(registry_encoded)
    if value["signing_key_id"] != registry.key_id:
        raise _invalid("distribution signing key")
    uid, gid = os.getuid(), os.getgid()
    _verify_ed25519_openssl_core_v1(
        registry.raw_public_key, SIGNATURE_DOMAIN + encoded, signature,
        openssl_executable=openssl_executable, temporary_root=temporary_root,
        temporary_uid=uid, temporary_gid=gid, chain_stop=temporary_root,
    )
    return _AuthenticatedDistributionForTestV1(
        _distribution_facts_v1(value), files, bytes(encoded), bytes(signature),
        _distribution_artifact_binding_v1(encoded, signature),
    )


def _validate_boundary_inventory_v1(content: bytes) -> dict[str, object]:
    value = decode_canonical_json_v1(content, MAX_DISTRIBUTION_FILE_BYTES)
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema", "source_census", "scan_roots", "entries", "birth_closed",
        }
        or value.get("schema") != _BOUNDARY_INVENTORY_SCHEMA
        or value.get("scan_roots") != list(_BOUNDARY_SCAN_ROOTS)
        or not isinstance(value.get("source_census"), str)
        or not value["source_census"] or "\0" in value["source_census"]
        or not isinstance(value.get("entries"), list)
    ):
        raise _invalid("boundary inventory")
    policy = value.get("birth_closed")
    if not isinstance(policy, dict) or set(policy) != {
        "schema", "guard_version", "owner", "coordinator_store_owners",
        "sealed_modules", "exceptions",
    }:
        raise _invalid("Birth boundary policy")
    owners = policy.get("coordinator_store_owners")
    exceptions = policy.get("exceptions")
    if (
        policy.get("schema") != _BIRTH_CLOSED_SCHEMA
        or policy.get("guard_version") != _BIRTH_CLOSED_GUARD_VERSION
        or policy.get("owner") != _BIRTH_CLOSED_OWNER
        or policy.get("sealed_modules") != list(_BIRTH_CLOSED_SEALED_MODULES)
        or owners != list(_BIRTH_CLOSED_COORDINATOR_STORE_OWNERS)
        or exceptions != [
            {"scope": scope, "exception": exception}
            for scope, exception in _BIRTH_CLOSED_EXCEPTION_SCOPES
        ]
    ):
        raise _invalid("Birth boundary policy")
    exception_map: dict[str, str] = {}
    for item in exceptions:
        if (
            not isinstance(item, dict) or set(item) != {"scope", "exception"}
            or not isinstance(item.get("scope"), str)
            or item.get("exception") not in {
                "localization_only", "retirement_only",
                "offline_nonproductive_authoring",
            }
            or item["scope"] in exception_map
        ):
            raise _invalid("Birth boundary exception")
        exception_map[item["scope"]] = item["exception"]
    if exceptions != [
        {"scope": scope, "exception": exception_map[scope]}
        for scope in sorted(exception_map, key=lambda item: item.encode("utf-8"))
    ]:
        raise _invalid("Birth boundary exception order")
    entries: dict[str, dict[str, object]] = {}
    for item in value["entries"]:
        if (
            not isinstance(item, dict)
            or set(item) not in {
                _BOUNDARY_ENTRY_KEYS,
                _BOUNDARY_ENTRY_KEYS | {"closed_exception"},
            }
        ):
            raise _invalid("boundary entry")
        path = item.get("path")
        scope = item.get("scope")
        role = item.get("role")
        capabilities = item.get("capabilities")
        if (
            not isinstance(path, str) or validate_relative_path_v1(path) != path
            or not isinstance(scope, str) or not scope or "\0" in scope
            or len(scope.encode("utf-8")) > 512
            or role not in _BOUNDARY_ROLES
            or not isinstance(capabilities, list) or not capabilities
            or any(not isinstance(capability, str) or not capability
                   or "\0" in capability for capability in capabilities)
            or capabilities != sorted(set(capabilities))
            or not isinstance(item.get("destination"), str)
            or not item["destination"] or "\0" in item["destination"]
            or item.get("phase") != "M4"
        ):
            raise _invalid("boundary entry")
        key = path + ":" + scope
        if key in entries:
            raise _invalid("duplicate boundary entry")
        entries[key] = item
    if entries.get(_BIRTH_CLOSED_OWNER, {}).get("role") != "birth_owner":
        raise _invalid("boundary owner")
    for scope in _BIRTH_CLOSED_COORDINATOR_STORE_OWNERS:
        if entries.get(scope, {}).get("role") != "store_owner":
            raise _invalid("boundary store owner")
    for scope, exception in _BIRTH_CLOSED_EXCEPTION_SCOPES:
        if entries.get(scope, {}).get("closed_exception") != exception:
            raise _invalid("boundary exception binding")
    for key, item in entries.items():
        if "closed_exception" in item and key not in exception_map:
            raise _invalid("unexpected boundary exception")
    return value


def _product_version_from_source_v1(content: bytes) -> str:
    try:
        tree = ast.parse(content.decode("utf-8"), filename="runtime/__version__.py")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise _invalid("product version source") from exc
    assignments: list[str] = []
    stores = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name) and node.id == "__version__"
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            stores += 1
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign) and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == "__version__"
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            assignments.append(statement.value.value)
    if (
        stores != 1 or len(assignments) != 1
        or _SEMVER_RE.fullmatch(assignments[0]) is None
    ):
        raise _invalid("product version source")
    return assignments[0]


def _verify_local_import_closure_v1(
    root: Path, files: tuple[DistributionFileV1, ...],
    content: dict[str, bytes],
) -> None:
    declared = frozenset(item.path for item in files)

    def candidates(module: str, source: str, level: int) -> tuple[str, ...]:
        pieces = [piece for piece in module.split(".") if piece]
        if level:
            parent = source.split("/")[:-1]
            if level > len(parent):
                return ()
            pieces = parent[:len(parent) - level + 1] + pieces
        result: list[str] = []
        for prefix in ([], ["runtime"]):
            stem = "/".join(prefix + pieces)
            if stem:
                result.extend((stem + ".py", stem + "/__init__.py"))
        return tuple(dict.fromkeys(result))

    for item in files:
        if not item.path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content[item.path].decode("utf-8"), filename=item.path)
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise _invalid("Python source") from exc
        imports: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend((alias.name, 0) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append((node.module or "", node.level))
                imports.extend(
                    (".".join(filter(None, (node.module or "", alias.name))), node.level)
                    for alias in node.names if alias.name != "*"
                )
            elif isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "__import__"
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            ):
                raise _invalid("dynamic import")
        for module, level in imports:
            possible = candidates(module, item.path, level)
            existing: list[str] = []
            for relative in possible:
                candidate = root.joinpath(*relative.split("/"))
                try:
                    candidate.lstat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise _invalid("import candidate") from exc
                existing.append(relative)
            if existing and (len(existing) != 1 or existing[0] not in declared):
                raise _invalid("uncovered local import")


def _verify_installed_distribution_core_v1(
    record: AuthenticatedDistributionV1 | _AuthenticatedDistributionForTestV1,
    root: Path, *, expected_type: type,
    uid: int, gid: int, chain_stop: Path | None,
) -> None:
    if type(record) is not expected_type:
        raise _invalid("authenticated distribution type")
    value, files = _parse_distribution_manifest_v1(record.encoded)
    if (
        _distribution_facts_v1(value) != record.facts or files != record.files
        or type(record.signature) is not bytes or len(record.signature) != 64
        or record.artifact_binding != _distribution_artifact_binding_v1(
            record.encoded, record.signature,
        )
    ):
        raise _invalid("authenticated distribution binding")
    inventory_path = value["boundary_inventory_path"]
    capture_paths = frozenset(
        item.path for item in files
        if item.path.endswith(".py") or item.path == inventory_path
    )

    def verify_semantics(verified: dict[str, bytes]) -> None:
        inventory = verified[inventory_path]
        if (
            _digest(BOUNDARY_INVENTORY_DOMAIN, inventory)
            != value["boundary_inventory_hash"]
        ):
            raise _invalid("boundary inventory hash")
        _validate_boundary_inventory_v1(inventory)
        if value["boundary_guard_version"] != _BIRTH_CLOSED_GUARD_VERSION:
            raise _invalid("boundary guard version")
        if _product_version_from_source_v1(
            verified["runtime/__version__.py"]
        ) != value["product_version"]:
            raise _invalid("product version")
        _verify_local_import_closure_v1(root, files, verified)

    _snapshot_exact_distribution_tree_v1(
        root, files, uid=uid, gid=gid, chain_stop=chain_stop,
        capture_paths=capture_paths, semantic_check=verify_semantics,
    )


def verify_installed_distribution_v1(record: AuthenticatedDistributionV1) -> None:
    require_linux_before_io_v1()
    if type(record) is not AuthenticatedDistributionV1:
        raise _invalid("productive distribution record")
    reauthenticated = authenticate_distribution_v1(record.encoded, record.signature)
    if reauthenticated != record:
        raise _invalid("productive distribution reauthentication")
    sequence = reauthenticated.facts.release_sequence
    root = RELEASE_ROOT / f"{sequence:020d}"
    if reauthenticated.facts.installation_root != root.as_posix():
        raise _invalid("installation root")
    architecture = {"x86_64": "x86_64", "amd64": "x86_64",
                    "aarch64": "aarch64", "arm64": "aarch64"}.get(
                        platform.machine().lower()
                    )
    if (
        reauthenticated.facts.platform != "linux"
        or reauthenticated.facts.architecture != architecture
    ):
        raise _invalid("distribution platform")
    _verify_installed_distribution_core_v1(
        reauthenticated, root, expected_type=AuthenticatedDistributionV1,
        uid=0, gid=0, chain_stop=None,
    )


def _verify_installed_distribution_for_test_v1(
    record: _AuthenticatedDistributionForTestV1, root: Path,
) -> None:
    if type(record) is not _AuthenticatedDistributionForTestV1:
        raise _invalid("test distribution record")
    _verify_installed_distribution_core_v1(
        record, root, expected_type=_AuthenticatedDistributionForTestV1,
        uid=os.getuid(), gid=os.getgid(), chain_stop=root,
    )


def parse_cli_v1(argv: list[str]) -> CliCommandV1:
    """Parse only the three byte-for-byte command forms from the contract."""
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        raise _invalid("CLI arguments")
    if argv == ["check-all"]:
        return CliCommandV1("check-all", None)
    if (
        len(argv) == 3 and argv[0] in {"check", "launch"}
        and argv[1] == "--entry-id"
    ):
        return CliCommandV1(argv[0], validate_entry_id_v1(argv[2]))
    raise _invalid("CLI arguments")


def _validate_property_request_v1(properties: object) -> tuple[str, ...]:
    if (
        type(properties) is not tuple or not properties
        or any(
            not isinstance(item, str) or _PROPERTY_RE.fullmatch(item) is None
            for item in properties
        )
    ):
        raise _invalid("systemctl property request")
    if (
        tuple(sorted(properties)) != properties
        or len(properties) != len(set(properties))
    ):
        raise _invalid("systemctl property request")
    return properties


def parse_systemctl_show_v1(
    stdout: bytes, requested_properties: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    """Parse the closed byte protocol used for every ``systemctl show`` call.

    Absence is retained as absence.  The caller, which knows the unit class,
    must enforce mandatory properties and the paired empty-Exec exception.
    """
    if (
        type(stdout) is not bytes or len(stdout) > 4 * 1024 * 1024
        or not stdout.endswith(b"\n") or stdout.endswith(b"\n\n")
        or b"\r" in stdout or b"\0" in stdout
    ):
        raise _invalid("systemctl output framing")
    _validate_property_request_v1(requested_properties)
    allowed = frozenset(requested_properties)
    result: dict[str, list[str]] = {}
    lines = stdout[:-1].split(b"\n")
    if len(lines) > 4096 or any(len(line) > 64 * 1024 for line in lines):
        raise _invalid("systemctl output bounds")
    for raw in lines:
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _invalid("systemctl UTF-8") from exc
        name, separator, value = line.partition("=")
        if separator != "=" or name not in allowed:
            raise _invalid("systemctl property line")
        values = result.setdefault(name, [])
        if values and name not in REPEATABLE_PROPERTIES:
            raise _invalid("duplicate systemctl property")
        values.append(value)
    return {name: tuple(values) for name, values in result.items()}


def systemctl_show_argv_v1(
    systemctl_executable: str, unit_name: str | None,
    properties: tuple[str, ...],
) -> tuple[str, ...]:
    _validate_property_request_v1(properties)
    executable = validate_absolute_path_v1(systemctl_executable)
    if executable == "/":
        raise _invalid("systemctl executable")
    argv = (
        executable, "--no-pager", "--plain", "--all", "show",
        "--property=" + ",".join(properties),
    )
    if unit_name is None:
        if properties != ("Version",):
            raise _invalid("manager property request")
        return argv
    if (
        type(unit_name) is not str or _OBSERVED_UNIT_RE.fullmatch(unit_name) is None
        or len(unit_name.encode("utf-8")) > 255
    ):
        raise _invalid("unit name")
    return argv + ("--", unit_name)


def parse_systemd_manager_version_v1(stdout: bytes) -> str:
    parsed = parse_systemctl_show_v1(stdout, ("Version",))
    if set(parsed) != {"Version"} or len(parsed["Version"]) != 1:
        raise _invalid("manager Version")
    version = parsed["Version"][0]
    if version not in SUPPORTED_SYSTEMD_VERSIONS:
        raise _invalid("unsupported manager Version")
    return version


def tokenize_systemd_words_v1(value: str) -> tuple[str, ...]:
    """Decode the bounded C-quoted word form emitted by systemd 255."""
    if not isinstance(value, str) or "\0" in value or "\n" in value or "\r" in value:
        raise _invalid("systemd word list")
    words: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    active = False
    escapes = {"\\": "\\", '"': '"', "'": "'", "s": " ", "t": "\t"}
    while index < len(value):
        char = value[index]
        if quote is None and char == " ":
            if not active:
                raise _invalid("systemd word spacing")
            words.append("".join(current))
            current = []
            active = False
            index += 1
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            else:
                current.append(char)
            active = True
            index += 1
            continue
        if char == "\\":
            index += 1
            if index >= len(value):
                raise _invalid("systemd escape")
            escaped = value[index]
            if escaped in escapes:
                current.append(escapes[escaped])
            elif escaped == "x" and index + 2 < len(value):
                raw = value[index + 1:index + 3]
                if re.fullmatch(r"[0-9A-Fa-f]{2}", raw) is None:
                    raise _invalid("systemd hex escape")
                codepoint = int(raw, 16)
                if codepoint == 0 or codepoint > 0x7f:
                    raise _invalid("systemd NUL escape")
                current.append(chr(codepoint))
                index += 2
            elif escaped in "01234567" and index + 2 < len(value):
                raw = value[index:index + 3]
                if re.fullmatch(r"[0-7]{3}", raw) is None:
                    raise _invalid("systemd octal escape")
                codepoint = int(raw, 8)
                if codepoint == 0 or codepoint > 0x7f:
                    raise _invalid("systemd NUL escape")
                current.append(chr(codepoint))
                index += 2
            else:
                raise _invalid("unknown systemd escape")
            active = True
            index += 1
            continue
        current.append(char)
        active = True
        index += 1
    if quote is not None:
        raise _invalid("unterminated systemd quote")
    if value and not active:
        raise _invalid("systemd word spacing")
    if active:
        words.append("".join(current))
    if any(not word or "\0" in word for word in words):
        raise _invalid("empty systemd word")
    return tuple(words)


def normalize_systemd_duration_usec_v1(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _invalid("duration")
    total = 0
    for component in value.split(" "):
        match = _DURATION_COMPONENT_RE.fullmatch(component)
        if match is None:
            raise _invalid("duration component")
        whole, fraction, suffix = match.groups()
        factor = _DURATION_FACTORS[suffix]
        total += int(whole) * factor
        if fraction is not None:
            numerator = int(fraction.ljust(6, "0")) * factor
            if numerator % 1_000_000:
                raise _invalid("fractional duration precision")
            total += numerator // 1_000_000
        if total > (1 << 63) - 1:
            raise _invalid("duration overflow")
    return str(total)


_EXEC_RE = re.compile(
    r"\{ path=(?P<path>[^ ;]+) ; argv\[\]=(?P<argv>.*?) ; "
    r"(?P<flag_name>flags|ignore_errors)=(?P<flags>[^ ;]*) ; "
    r"start_time=(?P<start>.*?) ; stop_time=(?P<stop>.*?) ; "
    r"pid=(?P<pid>[^ ;]+) ; code=(?P<code>[^ ;]+) ; "
    r"status=(?P<status>[^ }]+) \}\Z"
)


def parse_systemd_exec_v1(value: str, *, extended: bool) -> dict[str, object]:
    match = _EXEC_RE.fullmatch(value)
    if match is None:
        raise _invalid("Exec structure")
    flag_name = match.group("flag_name")
    if flag_name != ("flags" if extended else "ignore_errors"):
        raise _invalid("Exec flag field")
    path = validate_absolute_path_v1(match.group("path"))
    if path == "/":
        raise _invalid("Exec path")
    argv = tokenize_systemd_words_v1(match.group("argv"))
    if not path.startswith("/") or not argv or argv[0] != path:
        raise _invalid("Exec path/argv")
    if any(
        re.fullmatch(r"\[[A-Za-z0-9:.,+_~/\- ]{1,128}\]", match.group(name))
        is None
        for name in ("start", "stop")
    ):
        raise _invalid("Exec dynamic time")
    if _INTEGER_RE.fullmatch(match.group("pid")) is None:
        raise _invalid("Exec dynamic pid")
    code = match.group("code")
    status = match.group("status")
    if code not in {"(null)", "exited", "killed", "dumped"}:
        raise _invalid("Exec dynamic code")
    if (
        (code == "(null)" and re.fullmatch(r"[0-9]+/[0-9]+", status) is None)
        or (code != "(null)" and _INTEGER_RE.fullmatch(status) is None)
    ):
        raise _invalid("Exec dynamic status")
    raw_flags = match.group("flags")
    if extended:
        flags = () if raw_flags == "" else (raw_flags,)
        if raw_flags not in {"", "no-setuid"}:
            raise _invalid("Exec flags")
    else:
        if raw_flags not in {"yes", "no"}:
            raise _invalid("Exec ignore_errors")
        flags = ("ignore-failure",) if raw_flags == "yes" else ()
    return {
        "path": path, "argv": argv, "flags": flags,
        "_dynamic": (
            match.group("start"), match.group("stop"), match.group("pid"),
            code, status,
        ),
    }


_TIMER_RE = re.compile(
    r"\{ (?P<name>OnBootUSec|OnActiveUSec|OnUnitActiveUSec|OnCalendar)="
    r"(?P<value>.+?) ; next_elapse=(?P<next>.+?) \}\Z"
)


def parse_systemd_timer_v1(value: str) -> tuple[str, str]:
    match = _TIMER_RE.fullmatch(value)
    if match is None:
        raise _invalid("timer structure")
    dynamic = match.group("next")
    if (
        len(dynamic) > 256
        or re.fullmatch(r"(?:\[n/a\]|[A-Za-z0-9:.,+_~/\- ]+)", dynamic) is None
    ):
        raise _invalid("timer dynamic value")
    return match.group("name") + "=" + match.group("value"), dynamic


def parse_systemd_timer_properties_v1(
    monotonic: tuple[str, ...], calendar: tuple[str, ...],
) -> dict[str, str]:
    """Normalize the static timer bases and discard only next_elapse."""
    result: dict[str, str] = {}
    for value in monotonic:
        base, _dynamic = parse_systemd_timer_v1(value)
        name, separator, duration = base.partition("=")
        if separator != "=" or name not in {
            "OnBootUSec", "OnActiveUSec", "OnUnitActiveUSec"
        } or name in result:
            raise _invalid("TimersMonotonic base")
        result[name] = normalize_systemd_duration_usec_v1(duration)
    for value in calendar:
        base, _dynamic = parse_systemd_timer_v1(value)
        name, separator, expression = base.partition("=")
        if (
            separator != "=" or name != "OnCalendar" or name in result
            or not expression or expression != expression.strip()
            or "\0" in expression or "\n" in expression or "\r" in expression
            or len(expression) > 256
            or re.fullmatch(r"[A-Za-z0-9*,:.+_~/\- ]+", expression) is None
        ):
            raise _invalid("TimersCalendar base")
        result[name] = expression
    return result


def validate_exec_property_pair_v1(
    historical: tuple[str, ...], extended: tuple[str, ...],
    expected_flags: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    if len(historical) != len(extended):
        raise _invalid("Exec pair cardinality")
    result: list[dict[str, object]] = []
    for old_value, ex_value in zip(historical, extended):
        old = parse_systemd_exec_v1(old_value, extended=False)
        new = parse_systemd_exec_v1(ex_value, extended=True)
        if (
            old["path"] != new["path"] or old["argv"] != new["argv"]
            or old["_dynamic"] != new["_dynamic"]
        ):
            raise _invalid("Exec pair mismatch")
        if old["flags"] or new["flags"] != expected_flags:
            raise _invalid("Exec static flags")
        result.append({
            "path": new["path"], "argv": new["argv"], "flags": new["flags"],
        })
    return tuple(result)
