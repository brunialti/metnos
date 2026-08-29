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
from typing import Callable, Iterable, Mapping, NamedTuple, Sequence


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
MAX_BOUNDARY_SOURCE_FILES_V1 = 2_048
MAX_BOUNDARY_SOURCE_BYTES_V1 = 1 * 1024 * 1024
MAX_BOUNDARY_TOTAL_SOURCE_BYTES_V1 = 32 * 1024 * 1024
MAX_BOUNDARY_AST_NODES_V1 = 100_000
MAX_BOUNDARY_TOTAL_AST_NODES_V1 = 4_000_000
MAX_BOUNDARY_AST_DEPTH_V1 = 64
MAX_BOUNDARY_SCOPES_V1 = 512
MAX_BOUNDARY_CALLS_V1 = 8_192
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
    "metnos.contract-boundary-inventory/2+birth-closed/2"
)
_BIRTH_CLOSED_SOURCE_REVIEW_DOMAIN = (
    b"metnos.executor-birth.closed-python-source-review/v1\0"
)
_BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:87f7d309555793642066f778013be58024421b2026d00a2769e15c3324e1f4b5"
_SOURCE_REVIEW_PIN_LINE = re.compile(
    rb'(?m)^_?BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = (?:"sha256:" \+ "0" \* 64|"sha256:[0-9a-f]{64}")$'
)
_SOURCE_REVIEW_PIN_PLACEHOLDER = (
    b'BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:' + b"0" * 64 + b'"'
)
_BOUNDARY_SCAN_ROOTS = ("runtime", "install", "scripts", "executors")
_BOUNDARY_PREFLIGHT_ENTRYPOINT_V1 = "deployment/admin/preflight.py"
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
    boundary_source_files = 0
    boundary_source_bytes = 0
    for raw in raw_files:
        if not isinstance(raw, dict) or set(raw) != _MANIFEST_FILE_KEYS:
            raise _invalid("distribution file schema")
        path = validate_relative_path_v1(raw.get("path"))
        components = path.split("/")
        if any(
            component.casefold().endswith(".py")
            for component in components[:-1]
        ):
            raise _invalid("python source path")
        python_like = components[-1].casefold().endswith(".py")
        if python_like and (
            not components[-1].endswith(".py")
            or (
                path != _BOUNDARY_PREFLIGHT_ENTRYPOINT_V1
                and components[0] not in _BOUNDARY_SCAN_ROOTS
            )
        ):
            raise _invalid("python source path")
        guarded_python_source = python_like
        size = raw.get("size")
        role = raw.get("role")
        if (
            type(size) is not int or size < 0 or size > MAX_DISTRIBUTION_FILE_BYTES
            or role not in _MANIFEST_ROLES
        ):
            raise _invalid("distribution file entry")
        if guarded_python_source:
            boundary_source_files += 1
            boundary_source_bytes += size
            if (
                boundary_source_files > MAX_BOUNDARY_SOURCE_FILES_V1
                or size > MAX_BOUNDARY_SOURCE_BYTES_V1
                or boundary_source_bytes > MAX_BOUNDARY_TOTAL_SOURCE_BYTES_V1
            ):
                raise _invalid("python source budget")
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
        if any(
            component.casefold().endswith(".py")
            for component in components[:-1]
        ):
            raise _invalid("python source path")
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


# Autonomous standard-library clone of the certified boundary census.
# This code consumes only already-authenticated source bytes; it never imports
# or executes the boundary guard contained in the distribution.
SCAN_ROOTS = ("runtime", "install", "scripts", "executors")
AUTHORING_FILES = frozenset({
    "manifest.toml",
    "manifest.toml.sig",
    "manifest.lang_state.json",
})
# Public boundary APIs are classified by their owning module, never by a
# language, executor name or caller-chosen helper name.  Local wrappers inherit
# these capabilities through the per-file call graph below.
BOUNDARY_APIS: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "executor_birth": {
        "birth_executor": ("birth",),
    },
    "executor_birth_intent": {
        "submit_builtin_generation_birth": ("birth",),
        "submit_change_extend_birth": ("birth",),
        "submit_change_rollback_birth": ("birth",),
        "submit_installer_birth": ("birth",),
        "submit_promote_birth": ("birth",),
        "submit_promoter_rollback_birth": ("birth",),
        "submit_skills_birth": ("birth",),
        "submit_stack_reconcile_birth": ("birth",),
        "submit_synth_producer_birth": ("birth",),
    },
    "executor_birth_operational": {
        "birth_executor": ("birth",),
    },
    "executor_birth_synth": {
        "submit_synth_multistage": ("birth",),
        "submit_synth_specialize": ("birth",),
        "submit_synth_approve": ("birth",),
    },
    "contract_store": {
        "verify_manifest_source": ("authoring_read", "authoring_verify"),
        "prepare_technical_draft": ("authoring_read", "authoring_verify"),
        "read_binding": ("verified_store_read",),
        "current_revision_id": ("verified_store_read",),
        "current_contract": ("verified_store_read",),
        "current_manifest": ("verified_store_read",),
        "diagnose_store": ("verified_store_read",),
        "publish_localization": ("publish_localization",),
        "publish_technical_update": ("publish_technical",),
        "publish_signed_source": ("publish_bootstrap",),
        "retire": ("retire",),
        "reactivate_technical_update": ("reactivate",),
        "rollback": ("rollback",),
        "activate_store": ("legacy_bootstrap",),
        "acquire_current_reattestation_snapshot": ("verified_store_read",),
        "persist_current_reattestation_receipt": ("store_write",),
        "read_current_birth_receipt": ("verified_store_read",),
    },
    "sign": {
        "sign_executor": ("sign",),
        "verify_executor": ("authoring_read", "authoring_verify"),
        "publish_executor": ("publish_technical",),
        "publish_authoring_update": ("publish_technical",),
        "retire_executor_contract": ("retire",),
        "reactivate_executor_contract": ("reactivate",),
        "rollback_executor_contract": ("rollback",),
    },
    "loader": {
        "load_catalog": ("live_artifact_read",),
    },
    "invocations": {
        "load_executor_artifact": ("live_artifact_read",),
    },
    "i18n_migrate_manifests": {
        "prepare_contract_store_shadow": ("legacy_bootstrap",),
        "activate_prepared_contract_store": ("legacy_bootstrap",),
    },
    "contract_cutover_guard": {
        "contract_cutover_guard": ("cutover_guard",),
        "verify_store_only_catalog": (
            "live_artifact_read",
            "verified_store_read",
        ),
    },
    "manifest_inventory": {
        "inventory_authoring_manifests": ("authoring_read",),
        "inventory_manifests": ("authoring_read", "verified_store_read"),
        "inventory_store_manifests": ("verified_store_read",),
    },
    "executor_birth_authoring": {
        "read_manifest_ref_versioned": ("authoring_versioned_read",),
    },
    "executor_birth_ownership_chain": {
        "_InitialOwnershipChainStateV1": ("store_write",),
        "_append_pair": ("store_write",),
        "_inspect_ownership_chain_state_core_v1": ("store_write",),
        "_mint_initial_ownership_chain_state_v1": ("store_write",),
        "_replace_required_pointer": ("store_write",),
        "_required_head_lock": ("store_write",),
        "_update_required_head_locked": ("store_write",),
        "append_authenticated_build": ("store_write",),
        "append_cutover": ("store_write",),
        "append_head": ("store_write",),
        "initialize": ("store_write",),
        "update_required_head": ("store_write",),
    },
    "executor_birth_ownership_cutover": {
        "_publish_no_replace": ("store_write",),
        "_sync_directory": ("store_write",),
        "_write_temporary": ("store_write",),
        "install_ownership_cutover_certificate": ("store_write",),
    },
    "executor_birth_ownership_coordinator": {
        "_ACTIVE_DEPLOYMENT_LOCK_LEASES_V1": ("store_write",),
        "_ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1": ("store_write",),
        "_DEPLOYMENT_LOCK_FORK_GUARD": ("store_write",),
        "_DeploymentLockLeaseV1": ("store_write",),
        "_OPEN_DEPLOYMENT_LOCK_FDS_V1": ("store_write",),
        "_append_coordinator_record_v1": ("store_write",),
        "_deployment_lock_at_v1": ("store_write",),
        "_deployment_lock_for_test_v1": ("store_write",),
        "_deployment_lock_v1": ("store_write",),
        "_publish_certificate_with_prerequisite_v1": ("store_write",),
        "_LockedOwnershipCoordinatorGraphSnapshotV2": ("store_write",),
        "_require_locked_coordinator_graph_snapshot_v2": ("store_write",),
        "_require_locked_coordinator_graph_issued_v2": ("store_write",),
        "_resolve_locked_coordinator_graph_issued_v2": ("store_write",),
        "_resolve_ownership_coordinator_locked_v2": ("store_write",),
        "require_issued": ("store_write",),
        "resolve_issued": ("store_write",),
        "prepare_ownership_cutover_v1": ("cutover_guard",),
    },
    "birth_ownership_authority_provisioner": {
        "_discard_temporary": ("store_write",),
        "_load_or_create_pair": ("store_write",),
        "_publish_no_replace": ("store_write",),
        "_provision_ownership_authorities_at_v1": ("store_write",),
        "_provision_ownership_authorities_locked_v1": ("store_write",),
        "_provisioning_lock": ("store_write",),
        "_sync_directory": ("store_write",),
        "_write_exclusive": ("store_write",),
        "provision_root_ownership_authorities_v1": ("store_write",),
    },
    "executor_birth_source_receiver": {
        "<module>": ("store_write",),
        "_copy_source_file_v1": ("store_write",),
        "_create_private_directory_v1": ("store_write",),
        "_create_source_directories_v1": ("store_write",),
        "_ensure_child_directory_v1": ("store_write",),
        "_open_received_tree_at_v1": ("store_write",),
        "_receive_source_for_test_v1": ("store_write",),
        "_receive_source_locked_core_v1": ("store_write",),
        "_receive_source_v1": ("store_write",),
        "_receive_source_with_product_session_v1": ("store_write",),
        "_receive_source_with_test_session_v1": ("store_write",),
        "_remove_owned_tree_at_v1": ("store_write",),
        "_rename_no_replace_v1": ("store_write",),
        "_seal_temporary_directories_v1": ("store_write",),
        "_verify_received_tree_fd_v1": ("store_write",),
        "_write_all_v1": ("store_write",),
        "_write_descriptor_v1": ("store_write",),
        "copied_chunks": ("store_write",),
        "main": ("store_write",),
    },
}
BOUNDARY_MODULES: Mapping[str, frozenset[str]] = {
    "executor_birth": frozenset({"executor_birth", "runtime.executor_birth"}),
    "executor_birth_intent": frozenset({
        "executor_birth_intent", "runtime.executor_birth_intent",
    }),
    "executor_birth_operational": frozenset({
        "executor_birth_operational", "runtime.executor_birth_operational",
    }),
    "executor_birth_synth": frozenset({
        "executor_birth_synth", "runtime.executor_birth_synth",
    }),
    "contract_store": frozenset({"contract_store", "runtime.contract_store"}),
    "sign": frozenset({"sign", "runtime.sign"}),
    "loader": frozenset({"loader", "runtime.loader"}),
    "invocations": frozenset({"invocations", "runtime.invocations"}),
    "i18n_migrate_manifests": frozenset({
        "admin.i18n_migrate_manifests",
        "runtime.admin.i18n_migrate_manifests",
    }),
    "contract_cutover_guard": frozenset({
        "contract_cutover_guard",
        "runtime.contract_cutover_guard",
    }),
    "manifest_inventory": frozenset({
        "manifest_inventory",
        "runtime.manifest_inventory",
    }),
    "executor_birth_authoring": frozenset({
        "executor_birth_authoring", "runtime.executor_birth_authoring",
    }),
    "executor_birth_ownership_chain": frozenset({
        "executor_birth_ownership_chain", "runtime.executor_birth_ownership_chain",
    }),
    "executor_birth_ownership_cutover": frozenset({
        "executor_birth_ownership_cutover",
        "runtime.executor_birth_ownership_cutover",
    }),
    "executor_birth_ownership_coordinator": frozenset({
        "executor_birth_ownership_coordinator",
        "runtime.executor_birth_ownership_coordinator",
    }),
    "birth_ownership_authority_provisioner": frozenset({
        "install.birth_ownership_authority_provisioner",
    }),
    "executor_birth_source_receiver": frozenset({
        "install.executor_birth_source_receiver",
    }),
}
BOUNDARY_SOURCE_OWNERS: Mapping[str, str] = {
    "runtime/executor_birth.py": "executor_birth",
    "runtime/executor_birth_intent.py": "executor_birth_intent",
    "runtime/executor_birth_operational.py": "executor_birth_operational",
    "runtime/contract_store.py": "contract_store",
    "runtime/sign.py": "sign",
    "runtime/loader.py": "loader",
    "runtime/invocations.py": "invocations",
    "runtime/admin/i18n_migrate_manifests.py": "i18n_migrate_manifests",
    "runtime/contract_cutover_guard.py": "contract_cutover_guard",
    "runtime/manifest_inventory.py": "manifest_inventory",
    "runtime/executor_birth_authoring.py": "executor_birth_authoring",
    "runtime/executor_birth_ownership_chain.py": "executor_birth_ownership_chain",
    "runtime/executor_birth_ownership_coordinator.py": (
        "executor_birth_ownership_coordinator"
    ),
    "install/birth_ownership_authority_provisioner.py": (
        "birth_ownership_authority_provisioner"
    ),
    "install/executor_birth_source_receiver.py": (
        "executor_birth_source_receiver"
    ),
}
READ_OPERATIONS = frozenset({
    "exists",
    "glob",
    "is_dir",
    "is_file",
    "iterdir",
    "load",
    "loads",
    "open",
    "parse",
    "read",
    "read_bytes",
    "read_text",
    "resolve",
    "rglob",
    "stat",
})
WRITE_OPERATIONS = frozenset({
    "NamedTemporaryFile",
    "chmod",
    "chown",
    "copy",
    "copy2",
    "copyfile",
    "extract",
    "extractall",
    "fchmod",
    "fchown",
    "ftruncate",
    "fsync",
    "mkdir",
    "mkdtemp",
    "mkstemp",
    "open",
    "remove",
    "rename",
    "replace",
    "rmdir",
    "rmtree",
    "hardlink_to",
    "link",
    "symlink_to",
    "touch",
    "truncate",
    "unlink",
    "write",
    "write_bytes",
    "write_text",
})
PROCESS_CALLS = frozenset({"Popen", "call", "check_call", "check_output", "run", "system"})
DYNAMIC_CODE_LOADER_APIS = frozenset({
    "FunctionType", "SourceFileLoader", "SourcelessFileLoader",
    "exec_module", "load_module", "module_from_spec", "run_module",
    "run_path", "spec_from_file_location",
})
DYNAMIC_CODE_LOADER_CANONICALS = frozenset({
    "importlib.machinery.SourceFileLoader",
    "importlib.machinery.SourcelessFileLoader",
    "importlib.util.module_from_spec",
    "importlib.util.spec_from_file_location",
    "runpy.run_module",
    "runpy.run_path",
    "types.FunctionType",
})
SENSITIVE_FIRST_CLASS_REFERENCES = frozenset({
    "getattr", "builtins.getattr", "builtins.__getattribute__",
    "importlib.__getattribute__", "sys.modules.get",
})
SENSITIVE_IMPORT_NAMESPACES = frozenset({
    "__builtins__", "__loader__", "__spec__", "builtins",
    "builtins.__dict__", "importlib",
    "importlib.__dict__", "importlib.machinery", "importlib.util", "runpy",
    "sys.modules", "types",
})
SYS_MODULES_EXPOSING_METHODS = frozenset({
    "copy", "items", "pop", "popitem", "setdefault", "values",
})
SYS_MODULES_MUTATING_METHODS = frozenset({
    "__delitem__", "__setitem__", "clear", "pop", "popitem", "setdefault",
    "update",
})
AUTHENTICATED_EXECUTION_SCOPE = (
    "runtime/admitted_module_v1.py", "load_admitted_module_v1",
)
LIVE_READER_FORBIDDEN = frozenset({
    "ambiguous_local_authority",
    "authoring_read",
    "authoring_write",
    "authoring_verify",
    "birth",
    "legacy_bootstrap",
    "publish_bootstrap",
    "publish_localization",
    "publish_technical",
    "reactivate",
    "retire",
    "rollback",
    "sign",
    "store_write",
    "dynamic_boundary_access",
})
PUBLISH_CAPABILITIES = frozenset({
    "birth",
    "publish_bootstrap",
    "publish_localization",
    "publish_technical",
    "reactivate",
    "retire",
    "rollback",
})
FLOW_CAPABILITIES = PUBLISH_CAPABILITIES | frozenset({
    "ambiguous_local_authority",
    "authoring_write",
    "cutover_guard",
    "legacy_bootstrap",
    "sign",
    "store_write",
    "dynamic_boundary_access",
})

# These are implementation boundaries, not a caller-extensible allow-list.

SCHEMA = _BOUNDARY_INVENTORY_SCHEMA
BIRTH_CLOSED_SCHEMA = _BIRTH_CLOSED_SCHEMA
BIRTH_CLOSED_GUARD_VERSION = _BIRTH_CLOSED_GUARD_VERSION
BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = _BIRTH_CLOSED_SOURCE_REVIEW_SHA256
BIRTH_CLOSED_SEALED_MODULES = _BIRTH_CLOSED_SEALED_MODULES
BIRTH_CLOSED_OWNER = _BIRTH_CLOSED_OWNER
BIRTH_CLOSED_COORDINATOR_STORE_OWNERS = frozenset(_BIRTH_CLOSED_COORDINATOR_STORE_OWNERS)
BIRTH_CLOSED_LEGACY_CAPABILITIES = frozenset({
    "publish_localization", "publish_technical", "reactivate", "retire",
    "rollback", "sign",
})
BIRTH_CLOSED_EXCEPTIONS = frozenset({
    "localization_only", "retirement_only", "offline_nonproductive_authoring",
})
BIRTH_CLOSED_EXCEPTION_SCOPES: Mapping[str, str] = {
    "runtime/admin/manifest_refactor.py:<module>": "offline_nonproductive_authoring",
    "runtime/admin/manifest_refactor.py:main": "offline_nonproductive_authoring",
    "runtime/admin/manifest_refactor.py:refactor_manifest": "offline_nonproductive_authoring",
    "runtime/i18n_pipeline.py:live_contract_context": "localization_only",
    "runtime/i18n_translator.py:<module>": "offline_nonproductive_authoring",
    "runtime/i18n_translator.py:_align_one_manifest": "offline_nonproductive_authoring",
    "runtime/i18n_translator.py:align_manifest_descriptions": "offline_nonproductive_authoring",
    "runtime/manifest_normalize.py:<module>": "offline_nonproductive_authoring",
    "runtime/manifest_normalize.py:apply_one": "offline_nonproductive_authoring",
    "runtime/manifest_normalize.py:main": "offline_nonproductive_authoring",
    "runtime/migrate_manifest_descriptions.py:<module>": "offline_nonproductive_authoring",
    "runtime/migrate_manifest_descriptions.py:main": "offline_nonproductive_authoring",
    "runtime/migrate_manifest_descriptions.py:migrate_dirs": "offline_nonproductive_authoring",
    "runtime/migrate_manifest_descriptions.py:migrate_one": "offline_nonproductive_authoring",
    "runtime/change_rollback.py:_rollback_create_executor": "retirement_only",
    "runtime/cli/skills_cli.py:_cmd_uninstall": "retirement_only",
}
BIRTH_CLOSED_EXCEPTION_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "runtime/admin/manifest_refactor.py:<module>": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/admin/manifest_refactor.py:main": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/admin/manifest_refactor.py:refactor_manifest": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/i18n_pipeline.py:live_contract_context": frozenset({
        "publish_localization", "verified_store_read",
    }),
    "runtime/i18n_translator.py:<module>": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/i18n_translator.py:_align_one_manifest": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/i18n_translator.py:align_manifest_descriptions": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/manifest_normalize.py:<module>": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/manifest_normalize.py:apply_one": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/manifest_normalize.py:main": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/migrate_manifest_descriptions.py:<module>": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/migrate_manifest_descriptions.py:main": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/migrate_manifest_descriptions.py:migrate_dirs": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/migrate_manifest_descriptions.py:migrate_one": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/change_rollback.py:_rollback_create_executor": frozenset({
        "retire",
    }),
    "runtime/cli/skills_cli.py:_cmd_uninstall": frozenset({
        "authoring_read", "retire",
    }),
}
VALID_ROLES = frozenset({
    "administrative_tool",
    "birth_owner",
    "documentation",
    "live_reader",
    "migration_boundary",
    "offline_authoring",
    "operational_producer",
    "store_owner",
})
LIVE_MUTATIONS = frozenset({
    "birth",
    "publish_localization",
    "publish_technical",
    "reactivate",
    "retire",
    "rollback",
})

_AUTHORING_NAME_RE = re.compile(
    r"(?:^|_)(?:(?:authoring_manifest|manifest_source|source_manifest)_"
    r"(?:path|dir|root)|executor_(?:path|dir|root))(?:_|$)",
)
_AMBIGUOUS_AUTHORING_ARGUMENT_RE = re.compile(
    r"(?:^|_)manifest_(?:path|dir|root)(?:_|$)",
)
_STORE_NAME_RE = re.compile(
    r"(?:^|_)(?:(?:contract_publication|contract_store|publication_store)_"
    r"(?:path|dir|root)|store_root|shadow_root|active_marker|store_relative|"
    r"shadow_relative|active_relative)(?:_|$)",
)
_CONTRACT_SCOPE_RE = re.compile(r"(?:^|_)(?:contract|manifest)(?:_|$)")
_GENERIC_PATH_NAME_RE = re.compile(
    r"(?:^|_)(?:path|dir|root|file)(?:_|$)",
)


@dataclass(frozen=True)
class ScopeFacts:
    path: str
    scope: str
    line: int
    capabilities: tuple[str, ...]
    calls: tuple[str, ...]
    direct_manifest_dir_access: bool = False
    closed_dynamic_boundary: bool = False

    @property
    def key(self) -> str:
        return f"{self.path}:{self.scope}"


@dataclass(frozen=True)
class Finding:
    code: str
    scope: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.scope}: {self.message}"


def _leaf_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _module_leaf(module: str) -> str:
    return module.rsplit(".", 1)[-1]


def _boundary_owner(module: str) -> str | None:
    for owner, accepted in BOUNDARY_MODULES.items():
        if module in accepted:
            return owner
    return None


def _boundary_owner_or_descendant(module: str) -> str | None:
    for owner, accepted in BOUNDARY_MODULES.items():
        if any(module == value or module.startswith(value + ".") for value in accepted):
            return owner
    return None


def _relative_boundary_import(node: ast.ImportFrom) -> bool:
    if node.level <= 0:
        return False
    candidates = [node.module] if node.module else []
    candidates.extend(
        ".".join(filter(None, (node.module or "", alias.name)))
        for alias in node.names
    )
    return any(
        candidate is not None
        and (
            _boundary_owner_or_descendant(candidate) is not None
            or candidate in BOUNDARY_APIS
        )
        for candidate in candidates
    )


def _boundary_api_capabilities(canonical: str) -> tuple[str, ...]:
    """Resolve a reviewed public API by module and leaf name.

    A same-named local helper is deliberately not privileged.  Imported
    aliases retain their canonical module through ``_analyse_scope``.
    """

    module, separator, api = canonical.rpartition(".")
    if not separator:
        return ()
    owner = _boundary_owner(module)
    if owner is None:
        return ()
    # No module may import a private store implementation detail.  Treat every
    # such call as private store authority, rather than maintaining a brittle
    # nominal list of mutator names that a new helper could bypass.
    if owner == "contract_store" and api.startswith("_"):
        return ("store_write",)
    return tuple(BOUNDARY_APIS.get(owner, {}).get(api, ()))


def _defined_boundary_capabilities(path: str, scope: str) -> tuple[str, ...]:
    """Classify the definitions that implement the public boundary itself."""

    if scope == "<module>":
        return ()
    module = BOUNDARY_SOURCE_OWNERS.get(path)
    if module is None:
        return ()
    api = scope.rsplit(".", 1)[-1]
    capabilities = set(BOUNDARY_APIS.get(module, {}).get(api, ()))
    return tuple(sorted(capabilities))


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        result: set[str] = set()
        for item in node.elts:
            result.update(_target_names(item))
        return result
    return set()


def _string_values(node: ast.AST) -> Iterable[str]:
    for item in ast.walk(node):
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            yield item.value


def _static_string(node: ast.AST) -> str | None:
    """Evaluate only syntax that is unambiguously a constant string."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts = [_static_string(value) for value in node.values]
        return "".join(parts) if all(part is not None for part in parts) else None
    return None


def _static_strings(node: ast.AST) -> set[str]:
    return {
        value
        for item in ast.walk(node)
        if (value := _static_string(item)) is not None
    }


def _resolved_alias_name(
    node: ast.AST, aliases: Mapping[str, str],
) -> str | None:
    dotted = _dotted_name(node)
    if dotted is None:
        return None
    first, separator, remainder = dotted.partition(".")
    return aliases.get(first, first) + (
        separator + remainder if separator else ""
    )


def _has_bound_root(node: ast.AST, aliases: Mapping[str, str]) -> bool:
    """Whether the first name is an observed import or propagated alias."""

    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return isinstance(current, ast.Name) and current.id in aliases


def _is_dynamic_code_loader_call(func: ast.AST, canonical: str) -> bool:
    """Recognize actual stdlib code-loader doors, not same-named local APIs."""

    if canonical in DYNAMIC_CODE_LOADER_CANONICALS:
        return True
    if canonical.startswith("importlib.") and canonical.rsplit(".", 1)[-1] in (
        DYNAMIC_CODE_LOADER_APIS - {"run_module", "run_path"}
    ):
        return True
    dotted = _dotted_name(func) or ""
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"exec_module", "load_module"}
        and (
            f".loader.{func.attr}" in dotted
            or dotted.startswith("__loader__.")
            or canonical.startswith("importlib.")
        )
    )


def _is_sys_modules_registry(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    """Track the module registry through direct and reflective derivations."""

    resolved = _resolved_alias_name(node, aliases)
    if resolved == "sys.modules" and _has_bound_root(node, aliases):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return (
            _is_sys_modules_registry(node.left, aliases)
            or _is_sys_modules_registry(node.right, aliases)
        )
    if isinstance(node, ast.Subscript):
        key = _static_string(node.slice)
        if key != "modules":
            return False
        owner = _resolved_alias_name(node.value, aliases)
        if owner == "sys.__dict__" and _has_bound_root(node.value, aliases):
            return True
        if isinstance(node.value, ast.Call):
            called = _resolved_alias_name(node.value.func, aliases)
            return bool(
                called in {"vars", "builtins.vars"}
                and node.value.args
                and _resolved_alias_name(node.value.args[0], aliases) == "sys"
                and _has_bound_root(node.value.args[0], aliases)
            )
        return False
    if isinstance(node, ast.Call):
        called = _resolved_alias_name(node.func, aliases)
        if called in {"dict", "builtins.dict"} and node.args:
            return _is_sys_modules_registry(node.args[0], aliases)
        if (
            called in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and _resolved_alias_name(node.args[0], aliases) == "sys"
            and _has_bound_root(node.args[0], aliases)
            and _static_string(node.args[1]) == "modules"
        ):
            return True
        if isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in {"copy", "__or__", "__ior__"}
                and _is_sys_modules_registry(node.func.value, aliases)
            ):
                return True
            if (
                called == "object.__getattribute__"
                and len(node.args) >= 2
                and _resolved_alias_name(node.args[0], aliases) == "sys"
                and _has_bound_root(node.args[0], aliases)
                and _static_string(node.args[1]) == "modules"
            ):
                return True
            if (
                node.func.attr == "__getattribute__"
                and _resolved_alias_name(node.func.value, aliases) == "sys"
                and _has_bound_root(node.func.value, aliases)
                and node.args
                and _static_string(node.args[0]) == "modules"
            ):
                return True
    return False


def _propagate_sys_modules_registry_aliases(
    aliases: dict[str, str], nodes: Sequence[ast.AST],
) -> None:
    pairs = _assignment_pairs(nodes)
    changed = True
    while changed:
        changed = False
        for targets, value in pairs:
            if not _is_sys_modules_registry(value, aliases):
                continue
            for target in targets:
                if aliases.get(target) != "sys.modules":
                    aliases[target] = "sys.modules"
                    changed = True


def _is_bounded_boundary_getattr(
    node: ast.AST,
    parent: ast.AST | None,
    aliases: Mapping[str, str],
) -> bool:
    """Allow only literal lookup of an already reviewed boundary API."""

    if (
        not isinstance(parent, ast.Call)
        or len(parent.args) < 2
        or parent.args[0] is not node
        or _resolved_alias_name(parent.func, aliases)
        not in {"getattr", "builtins.getattr"}
    ):
        return False
    module = _resolved_alias_name(node, aliases)
    reflected = _static_string(parent.args[1])
    return bool(
        module is not None
        and reflected is not None
        and _boundary_api_capabilities(f"{module}.{reflected}")
    )


def _contains_builtin_namespace_source(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Name) and item.id == "__builtins__"
        or isinstance(item, ast.Call)
        and _leaf_name(item.func) in {"globals", "locals", "vars"}
        for item in ast.walk(node)
    )


def _static_module_reference_may_reach_boundary(
    node: ast.AST, path: str,
) -> bool:
    value = _static_string(node)
    if value is None and isinstance(node, ast.Name) and node.id == "__name__":
        components = path.removesuffix(".py").split("/")
        if components[-1:] == ["__init__"]:
            components.pop()
        candidates = {".".join(components)}
        if components:
            candidates.add(components[-1])
        return any(
            candidate.startswith(".")
            or _boundary_owner_or_descendant(candidate) is not None
            for candidate in candidates
        )
    return (
        value is None
        or value.startswith(".")
        or _boundary_owner_or_descendant(value) is not None
    )


def _static_reflection_key_may_import(node: ast.AST) -> bool:
    value = _static_string(node)
    return value is None or value in {"__import__", "import_module", "importlib"}


def _static_reflection_key_may_execute(node: ast.AST) -> bool:
    value = _static_string(node)
    return value is None or value in {"compile", "eval", "exec"}


def _may_be_import_namespace(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    resolved = _resolved_alias_name(node, aliases)
    if resolved in {
        "__builtins__", "__loader__", "__spec__", "builtins",
        "builtins.__dict__", "importlib",
        "importlib.__dict__", "importlib.machinery", "importlib.util", "runpy",
        "sys.modules", "types",
    } and (
        resolved in {"__builtins__", "__loader__", "__spec__"}
        or _has_bound_root(node, aliases)
    ):
        return True
    if isinstance(node, ast.Call):
        called = _resolved_alias_name(node.func, aliases)
        if called in {"globals", "locals", "vars"}:
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "copy":
            return _may_be_import_namespace(node.func.value, aliases)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "__getitem__"}
        ):
            owner = _resolved_alias_name(node.func.value, aliases)
            key = _static_string(node.args[0]) if node.args else None
            if owner == "sys.modules":
                return key is None or key in {
                    "builtins", "importlib", "importlib.machinery",
                    "importlib.util", "runpy", "types",
                }
            if _may_be_import_namespace(node.func.value, aliases):
                return key is None or key in {
                    "__builtins__", "__loader__", "__spec__", "builtins", "importlib",
                    "importlib.machinery", "importlib.util", "runpy", "types",
                }
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return _may_be_import_namespace(node.value, aliases)
    if isinstance(node, ast.Subscript):
        key = _static_string(node.slice)
        return _may_be_import_namespace(node.value, aliases) and (
            key is None or key in {
                "__builtins__", "__loader__", "__spec__", "builtins", "importlib",
                "importlib.machinery", "importlib.util", "runpy", "types",
            }
        )
    return False


def _may_resolve_import_callable(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    resolved = _resolved_alias_name(node, aliases)
    if resolved in {
        "__import__", "builtins.__import__", "importlib.import_module",
    }:
        return True
    if isinstance(node, ast.Subscript):
        if not isinstance(node.ctx, ast.Load):
            return False
        if isinstance(node.slice, ast.Name) and node.slice.id == "__name__":
            return False
        return (
            _may_be_import_namespace(node.value, aliases)
            and _static_reflection_key_may_import(node.slice)
        )
    if isinstance(node, ast.Call):
        called = _resolved_alias_name(node.func, aliases)
        if called in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            return (
                _may_be_import_namespace(node.args[0], aliases)
                and _static_reflection_key_may_import(node.args[1])
            )
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "__getitem__"}
        ):
            return (
                _may_be_import_namespace(node.func.value, aliases)
                and (not node.args or _static_reflection_key_may_import(node.args[0]))
            )
    return False


def _may_resolve_dynamic_loader_callable(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    canonical = _resolved_alias_name(node, aliases) or ""
    if _is_dynamic_code_loader_call(node, canonical):
        return True
    return (
        isinstance(node, ast.Attribute)
        and node.attr in DYNAMIC_CODE_LOADER_APIS
        and _may_be_import_namespace(node.value, aliases)
    )


def _may_resolve_dynamic_eval_callable(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    """Recognize reflected access to eval/exec/compile in builtins."""

    if isinstance(node, ast.Subscript):
        return (
            isinstance(node.ctx, ast.Load)
            and _may_be_import_namespace(node.value, aliases)
            and _static_reflection_key_may_execute(node.slice)
        )
    if isinstance(node, ast.Call):
        called = _resolved_alias_name(node.func, aliases)
        if called in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            return (
                _may_be_import_namespace(node.args[0], aliases)
                and _static_reflection_key_may_execute(node.args[1])
            )
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "__getitem__"}
        ):
            return (
                _may_be_import_namespace(node.func.value, aliases)
                and (
                    not node.args
                    or _static_reflection_key_may_execute(node.args[0])
                )
            )
    return False



def _is_authoring_filename(value: object) -> bool:
    return (
        isinstance(value, str)
        and Path(value.replace("\\", "/")).name in AUTHORING_FILES
    )


def _has_authoring_literal(node: ast.AST) -> bool:
    """Recognize contract filenames only when they form a filesystem path.

    The same strings are also legitimate keys in immutable payload mappings;
    treating those keys as filesystem authority would misclassify verified
    store readers as authoring readers.
    """

    if isinstance(node, ast.Constant):
        return _is_authoring_filename(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and any(
        isinstance(part, ast.Constant) and _is_authoring_filename(part.value)
        for part in ast.walk(node)
    ):
        # Filename filters such as ``{"manifest.toml", ...}`` taint the
        # paths yielded by the associated filesystem traversal.  Mapping keys
        # remain excluded because immutable publication payloads legitimately
        # use the same names as data keys.
        return True
    for item in ast.walk(node):
        if (
            isinstance(item, ast.Call)
            and _leaf_name(item.func) == "Path"
            and any(
                isinstance(part, ast.Subscript)
                and isinstance(part.slice, ast.Constant)
                and part.slice.value in {
                    "authoring_manifest_path", "manifest_path",
                }
                for argument in item.args
                for part in ast.walk(argument)
            )
        ):
            return True
        if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Div):
            if any(
                isinstance(part, ast.Constant)
                and _is_authoring_filename(part.value)
                for part in ast.walk(item)
            ):
                return True
        if not isinstance(item, ast.Call):
            continue
        api = _leaf_name(item.func)
        if api == "with_name":
            base = item.func.value if isinstance(item.func, ast.Attribute) else None
            if (
                base is not None
                and any(
                    isinstance(part, ast.Name) and part.id == "__file__"
                    for part in ast.walk(base)
                )
                and any(
                    isinstance(part, ast.Constant)
                    and _is_authoring_filename(part.value)
                    for argument in item.args
                    for part in ast.walk(argument)
                )
            ):
                return True
            continue
        if api not in {
            "Path", "glob", "joinpath", "open", "rglob",
        }:
            continue
        if any(
            isinstance(part, ast.Constant)
            and _is_authoring_filename(part.value)
            for argument in item.args
            for part in ast.walk(argument)
        ):
            return True
    return False


def _has_store_literal(node: ast.AST) -> bool:
    return any(
        any(
            component in {
                "contract-publications",
                "contract-publications-shadow",
                "contract-publications.ACTIVE",
            }
            for component in value.replace("\\", "/").split("/")
        )
        for value in _string_values(node)
    )


def _name_matches(node: ast.AST, names: set[str], pattern: re.Pattern[str]) -> bool:
    for item in ast.walk(node):
        if isinstance(item, ast.Name):
            name = item.id
        elif isinstance(item, ast.Attribute):
            name = item.attr
        else:
            continue
        if name in names or pattern.search(name.lower()):
            return True
    return False


def _open_writes(call: ast.Call) -> bool:
    """Return true only when an ``open`` call explicitly permits mutation."""

    if (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "os"
    ):
        flags: list[ast.AST] = list(call.args[1:2])
        flags.extend(kw.value for kw in call.keywords if kw.arg == "flags")
        write_flags = {
            "O_APPEND", "O_CREAT", "O_EXCL", "O_RDWR", "O_TRUNC", "O_WRONLY",
        }
        if any(
            (
                isinstance(part, ast.Name) and part.id in write_flags
            ) or (
                isinstance(part, ast.Attribute) and part.attr in write_flags
            )
            for value in flags
            for part in ast.walk(value)
        ):
            return True
    values: list[ast.AST] = list(call.args[1:2])
    values.extend(kw.value for kw in call.keywords if kw.arg == "mode")
    if isinstance(call.func, ast.Attribute):
        values = list(call.args[:1]) + [
            kw.value for kw in call.keywords if kw.arg == "mode"
        ]
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return any(flag in value.value for flag in "wax+")
    return False


def _writes_path(api: str, call: ast.Call) -> bool:
    if api == "open":
        return _open_writes(call)
    if api in WRITE_OPERATIONS:
        return True
    # Native Windows store operations are invoked through ``ctypes`` and do
    # not retain Python's Path method names.  Recognize the operation family,
    # independent of the concrete kernel32 alias used by the caller.
    if re.match(
        r"^(?:create|delete|move|replace|write)file",
        api,
        flags=re.IGNORECASE,
    ):
        return True
    if re.match(r"^_?atomic_(?:bytes|json|save|text)$", api, re.IGNORECASE):
        return True
    return bool(re.search(
        r"(?:^|_)(?:atomic_)?(?:copy|delete|extract|move|remove|rename|"
        r"replace|restore|save|unlink|write)(?:_|$)",
        api.lower(),
    ))


def _pathlike(node: ast.AST) -> bool:
    """Distinguish filesystem rename/replace from ``str.replace``."""

    if _has_authoring_literal(node) or _has_store_literal(node):
        return True
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and re.search(
            r"(?:^|_)(?:path|dir|root|file|target|source|destination)(?:_|$)",
            item.id.lower(),
        ):
            return True
        if isinstance(item, ast.Attribute) and re.search(
            r"(?:^|_)(?:path|dir|root|file|target|source|destination)(?:_|$)",
            item.attr.lower(),
        ):
            return True
    return False


class _LocalVisitor(ast.NodeVisitor):
    """Visit one lexical scope without folding nested function bodies into it."""

    def __init__(self, root: ast.AST) -> None:
        self.root = root
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST) -> None:
        if node is not self.root and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            return
        self.nodes.append(node)
        super().generic_visit(node)


def _scope_nodes(node: ast.AST) -> list[ast.AST]:
    visitor = _LocalVisitor(node)
    visitor.visit(node)
    return visitor.nodes


def _scope_arguments(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return set()
    args = node.args
    result = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg:
        result.add(args.vararg.arg)
    if args.kwarg:
        result.add(args.kwarg.arg)
    return result


def _manifest_ref_arguments(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return set()
    args = node.args
    annotated = (*args.posonlyargs, *args.args, *args.kwonlyargs)
    result: set[str] = set()
    for argument in annotated:
        annotation = _dotted_name(argument.annotation) if argument.annotation else None
        if annotation and annotation.rsplit(".", 1)[-1] == "ManifestRef":
            result.add(argument.arg)
    return result


def _assignment_pairs(nodes: Iterable[ast.AST]) -> list[tuple[set[str], ast.AST]]:
    pairs: list[tuple[set[str], ast.AST]] = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            targets: set[str] = set()
            for target in node.targets:
                targets.update(_target_names(target))
            pairs.append((targets, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs.append((_target_names(node.target), node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            pairs.append((_target_names(node.target), node.iter))
        elif isinstance(node, ast.NamedExpr):
            pairs.append((_target_names(node.target), node.value))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"add", "append", "extend", "update"}
        ):
            # Preserve path authority when it flows through a local collection
            # before a later traversal.  This is common for deterministic
            # inventories assembled from several roots and filename filters.
            targets = _target_names(node.func.value)
            if targets and node.args:
                pairs.append((targets, ast.Tuple(elts=list(node.args), ctx=ast.Load())))
    return pairs


def _drops_contract_file_identity(node: ast.AST) -> bool:
    """A manifest's parent is a code/source directory, not a contract file."""

    return (
        not _has_authoring_literal(node)
        and any(
            isinstance(item, ast.Attribute) and item.attr == "parent"
            for item in ast.walk(node)
        )
    )


def _tainted_names(
    node: ast.AST,
    nodes: Sequence[ast.AST],
    *,
    scope: str,
) -> tuple[set[str], set[str]]:
    arguments = _scope_arguments(node)
    authoring = {
        name
        for name in arguments
        if _AUTHORING_NAME_RE.search(name.lower())
        or _AMBIGUOUS_AUTHORING_ARGUMENT_RE.search(name.lower())
    }
    authoring.update(_manifest_ref_arguments(node))
    # A contract/manifest transformer commonly accepts a deliberately generic
    # ``path`` parameter.  Treat that parameter as authoring authority based on
    # the semantic scope, not on any executor or language name.  This closes a
    # real bypass where ``refactor_manifest(path)`` rewrote a source contract
    # but escaped the census because only its caller named ``manifest_path``.
    scope_leaf = scope.rsplit(".", 1)[-1].lower()
    if _CONTRACT_SCOPE_RE.search(scope_leaf):
        authoring.update(
            name for name in arguments if _GENERIC_PATH_NAME_RE.search(name.lower())
        )
    store = {name for name in arguments if _STORE_NAME_RE.search(name.lower())}
    pairs = _assignment_pairs(nodes)
    changed = True
    while changed:
        changed = False
        for targets, value in pairs:
            if (
                _has_authoring_literal(value)
                or (
                    _name_matches(value, authoring, _AUTHORING_NAME_RE)
                    and not _drops_contract_file_identity(value)
                )
            ):
                before = len(authoring)
                authoring.update(targets)
                changed |= len(authoring) != before
            if _has_store_literal(value) or _name_matches(value, store, _STORE_NAME_RE):
                before = len(store)
                store.update(targets)
                changed |= len(store) != before
    return authoring, store


def _call_target(call: ast.Call) -> ast.AST:
    if isinstance(call.func, ast.Attribute):
        return call.func.value
    if call.args:
        return call.args[0]
    return call


def _touches(
    node: ast.AST,
    *,
    tainted: set[str],
    pattern: re.Pattern[str],
    literal_test,
) -> bool:
    return literal_test(node) or _name_matches(node, tainted, pattern)


def _analyse_scope(
    path: str,
    scope: str,
    node: ast.AST,
    imported_aliases: Mapping[str, str],
    local_callables: frozenset[str],
) -> ScopeFacts:
    nodes = _scope_nodes(node)
    aliases = dict(imported_aliases)
    dynamic_boundary_access = False
    closed_dynamic_boundary = False
    boundary_text = re.compile(
        r"(?:contract_store|runtime\.sign|(?:^|[/\\])sign\.py|"
        r"publish_technical_update|reactivate_technical_update|"
        r"publish_signed_source|rollback_executor_contract)",
    )
    scope_boundary_strings = {
        value for value in _static_strings(node) if boundary_text.search(value)
    }
    for item in nodes:
        if isinstance(item, ast.ImportFrom) and item.module:
            if _relative_boundary_import(item):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
                continue
            if (
                _boundary_owner(item.module) is None
                and _boundary_owner_or_descendant(item.module) is not None
            ):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
            if _boundary_owner(item.module) and any(
                alias.name == "*" for alias in item.names
            ):
                dynamic_boundary_access = True
            for alias in item.names:
                canonical_import = f"{item.module}.{alias.name}"
                _remember_alias(
                    aliases, alias.asname or alias.name, canonical_import,
                    local_callables,
                )
        elif isinstance(item, ast.ImportFrom) and item.module is None:
            if _relative_boundary_import(item):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
        elif isinstance(item, ast.Import):
            for alias in item.names:
                if (
                    _boundary_owner(alias.name) is None
                    and _boundary_owner_or_descendant(alias.name) is not None
                ):
                    dynamic_boundary_access = True
                    closed_dynamic_boundary = True
                bound = alias.asname or alias.name.split(".", 1)[0]
                _remember_alias(
                    aliases, bound, alias.name if alias.asname else bound,
                    local_callables,
                )
    ambiguous_callable_authority = _apply_callable_aliases(
        aliases, nodes, local_callables,
    )
    _propagate_sys_modules_registry_aliases(aliases, nodes)
    parents = {
        id(child): parent
        for parent in nodes
        for child in ast.iter_child_nodes(parent)
    }
    direct_call_targets = {id(item.func) for item in nodes if isinstance(item, ast.Call)}
    for item in nodes:
        if (
            isinstance(item, (ast.Call, ast.BinOp, ast.Subscript))
            and _is_sys_modules_registry(item, aliases)
        ):
            dynamic_boundary_access = True
            closed_dynamic_boundary = True
        if isinstance(item, ast.Subscript):
            resolved = (
                "sys.modules"
                if _is_sys_modules_registry(item.value, aliases)
                else _resolved_alias_name(item.value, aliases)
            )
            parent = parents.get(id(item))
            authenticated_registration = (
                (path, scope) == AUTHENTICATED_EXECUTION_SCOPE
                and isinstance(item.ctx, ast.Store)
                and isinstance(item.slice, ast.Name)
                and item.slice.id == "module_name"
                and isinstance(parent, ast.Assign)
                and isinstance(parent.value, ast.Name)
                and parent.value.id == "module"
            )
            if resolved == "sys.modules" and (
                not isinstance(item.ctx, ast.Load)
                and not authenticated_registration
                or _static_module_reference_may_reach_boundary(item.slice, path)
                and isinstance(item.ctx, ast.Load)
            ):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
            if (
                resolved in {
                    "__builtins__", "builtins.__dict__", "importlib.__dict__",
                }
                or _contains_builtin_namespace_source(item.value)
            ) and _static_reflection_key_may_import(item.slice):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
            continue
        if (
            isinstance(item, ast.Attribute)
            and isinstance(item.ctx, (ast.Store, ast.Del))
            and _resolved_alias_name(item, aliases) == "sys.modules"
        ):
            dynamic_boundary_access = True
            closed_dynamic_boundary = True
            continue
        if (
            not isinstance(item, (ast.Name, ast.Attribute))
            or not isinstance(getattr(item, "ctx", None), ast.Load)
            or id(item) in direct_call_targets
        ):
            continue
        dotted = _dotted_name(item)
        if dotted is None:
            continue
        first, separator, remainder = dotted.partition(".")
        canonical = aliases.get(first, first) + (
            separator + remainder if separator else ""
        )
        if canonical in {
            "__import__", "builtins.__import__", "importlib.import_module",
        }:
            dynamic_boundary_access = True
            closed_dynamic_boundary = True
    authoring_names, store_names = _tainted_names(
        node,
        nodes,
        scope=scope,
    )
    capabilities: set[str] = set(
        _defined_boundary_capabilities(path, scope)
    )
    if ambiguous_callable_authority:
        capabilities.add("ambiguous_local_authority")
    if any(_may_resolve_import_callable(item, aliases) for item in nodes):
        capabilities.add("dynamic_boundary_access")
        closed_dynamic_boundary = True
    manifest_dir_locator_used = any(
        isinstance(item, ast.Attribute)
        and item.attr == "manifest_dir"
        and isinstance(item.ctx, ast.Load)
        for item in nodes
    )
    if dynamic_boundary_access:
        capabilities.add("dynamic_boundary_access")
    calls: set[str] = set()

    # Boundary functions can be passed as first-class callbacks (``partial``,
    # executor pools, futures) instead of appearing in ``Call.func``.  Any
    # loaded reference still grants the same authority and belongs in the
    # reviewed scope census.
    for item in nodes:
        if not isinstance(item, (ast.Name, ast.Attribute)):
            continue
        if not isinstance(getattr(item, "ctx", None), ast.Load):
            continue
        parent = parents.get(id(item))
        if isinstance(parent, ast.Attribute) and parent.value is item:
            continue
        canonical = _resolved_alias_name(item, aliases)
        if canonical is None:
            continue
        known_capabilities = _boundary_api_capabilities(canonical)
        capabilities.update(known_capabilities)
        direct_call = isinstance(parent, ast.Call) and parent.func is item
        direct_subscript = isinstance(parent, ast.Subscript) and parent.value is item
        if (
            not known_capabilities
            and _boundary_owner(canonical) is not None
            and canonical not in local_callables
            and _has_bound_root(item, aliases)
            and not _is_bounded_boundary_getattr(item, parent, aliases)
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if (
            canonical in SENSITIVE_IMPORT_NAMESPACES
            and not direct_subscript
            and (
                canonical == "__builtins__"
                or _has_bound_root(item, aliases)
            )
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if canonical in SENSITIVE_FIRST_CLASS_REFERENCES and not direct_call:
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True

    for item in nodes:
        if (
            isinstance(item, ast.Attribute)
            and item.attr == "__dict__"
            and (module_name := _dotted_name(item.value)) is not None
        ):
            first_module, separator_module, remainder_module = module_name.partition(".")
            resolved_module = aliases.get(first_module, first_module) + (
                separator_module + remainder_module if separator_module else ""
            )
            if _boundary_owner(resolved_module) is not None:
                closed_dynamic_boundary = True
            if resolved_module in {"builtins", "importlib"}:
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
    for item in nodes:
        if not isinstance(item, ast.Call):
            continue
        reflected_dynamic_eval = _may_resolve_dynamic_eval_callable(
            item.func, aliases,
        )
        if reflected_dynamic_eval:
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        leaf = _leaf_name(item.func)
        if leaf is None:
            continue
        dotted = _dotted_name(item.func) or leaf
        first, separator, remainder = dotted.partition(".")
        canonical = aliases.get(first, first) + (
            separator + remainder if separator else ""
        )
        if not separator:
            canonical = aliases.get(leaf, leaf)
        api = canonical.rsplit(".", 1)[-1]
        if canonical in local_callables:
            calls.add(canonical.rsplit(".", 1)[-1])
        elif (
            isinstance(item.func, ast.Name)
            and item.func.id not in aliases
        ) or (
            isinstance(item.func, ast.Attribute)
            and isinstance(item.func.value, ast.Name)
            and item.func.value.id not in aliases
        ):
            calls.add(api)

        capabilities.update(_boundary_api_capabilities(canonical))
        if (
            isinstance(item.func, ast.Attribute)
            and _is_sys_modules_registry(item.func.value, aliases)
            and api in (
                SYS_MODULES_EXPOSING_METHODS | SYS_MODULES_MUTATING_METHODS
            )
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if (
            _is_dynamic_code_loader_call(item.func, canonical)
            or _may_resolve_dynamic_loader_callable(item.func, aliases)
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if (
            isinstance(item.func, ast.Attribute)
            and _resolved_alias_name(item.func.value, aliases) == "sys.modules"
            and api == "get"
            and (
                not item.args
                or _static_module_reference_may_reach_boundary(item.args[0], path)
            )
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if api in {"getattr", "vars", "setattr", "delattr"} and item.args:
            reflected_namespace = _resolved_alias_name(item.args[0], aliases)
            if (
                reflected_namespace in {"builtins", "importlib", "__builtins__"}
                or reflected_namespace is not None
                and reflected_namespace.startswith("importlib.")
            ):
                capabilities.add("dynamic_boundary_access")
                closed_dynamic_boundary = True
        if (
            canonical in {
                "builtins.__getattribute__", "importlib.__getattribute__",
            }
            and item.args
            and _static_reflection_key_may_import(item.args[0])
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if api == "getattr" and item.args:
            module_name = _dotted_name(item.args[0])
            if module_name is not None:
                first_module, separator_module, remainder_module = (
                    module_name.partition(".")
                )
                resolved_module = aliases.get(first_module, first_module) + (
                    separator_module + remainder_module
                    if separator_module else ""
                )
                reflected = (
                    item.args[1].value
                    if len(item.args) > 1
                    and isinstance(item.args[1], ast.Constant)
                    and isinstance(item.args[1].value, str)
                    else None
                )
                owner = _boundary_owner(resolved_module)
                if owner is not None:
                    closed_dynamic_boundary = True
                    reflected_caps = (
                        _boundary_api_capabilities(
                            f"{resolved_module}.{reflected}",
                        )
                        if reflected is not None else ()
                    )
                    if reflected_caps:
                        capabilities.update(reflected_caps)
                    else:
                        capabilities.add("dynamic_boundary_access")
                if (
                    resolved_module in {"builtins", "importlib"}
                    and (
                        reflected is None
                        or reflected in {"__import__", "import_module"}
                    )
                ):
                    capabilities.add("dynamic_boundary_access")
                    closed_dynamic_boundary = True
        if api == "vars" and item.args:
            module_name = _dotted_name(item.args[0])
            if module_name is not None:
                first_module, separator_module, remainder_module = module_name.partition(".")
                resolved_module = aliases.get(first_module, first_module) + (
                    separator_module + remainder_module if separator_module else ""
                )
                if _boundary_owner(resolved_module) is not None:
                    closed_dynamic_boundary = True
                if resolved_module in {"builtins", "importlib"}:
                    capabilities.add("dynamic_boundary_access")
                    closed_dynamic_boundary = True
        builtin_dynamic_eval = canonical in {
            "builtins.compile", "builtins.eval", "builtins.exec",
        } or (
            isinstance(item.func, ast.Name)
            and item.func.id in {"compile", "eval", "exec"}
            and item.func.id not in aliases
            and item.func.id not in local_callables
        )
        if builtin_dynamic_eval:
            if (path, scope) != AUTHENTICATED_EXECUTION_SCOPE:
                capabilities.add("dynamic_boundary_access")
                closed_dynamic_boundary = True
            elif scope_boundary_strings:
                closed_dynamic_boundary = True
        dynamic_import = api in {"__import__", "import_module"}
        if dynamic_import:
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        command_parts = set(_string_values(item)) | _static_strings(item)
        sign_entrypoint = any(
            part.endswith("sign.py") or part == "runtime.sign"
            for part in command_parts
        )
        if api in PROCESS_CALLS and (
            "sign-all" in command_parts
            or ("sign" in command_parts and sign_entrypoint)
            or any(
                re.search(
                    r"(?:runtime\.sign|(?:^|[/\\])sign\.py)\s+"
                    r"(?:sign|sign-all)(?:\s|$)",
                    part,
                )
                for part in command_parts
            )
        ):
            capabilities.add("sign")
        if api in PROCESS_CALLS and (
            scope_boundary_strings
            or any(boundary_text.search(part) for part in command_parts)
        ):
            closed_dynamic_boundary = True

        target = _call_target(item)
        authoring_touch = _touches(
            target,
            tainted=authoring_names,
            pattern=_AUTHORING_NAME_RE,
            literal_test=_has_authoring_literal,
        ) or any(
            _touches(
                arg,
                tainted=authoring_names,
                pattern=_AUTHORING_NAME_RE,
                literal_test=_has_authoring_literal,
            )
            for arg in item.args
        ) or any(
            _touches(
                keyword.value,
                tainted=authoring_names,
                pattern=_AUTHORING_NAME_RE,
                literal_test=_has_authoring_literal,
            )
            for keyword in item.keywords
        )
        store_touch = _touches(
            target,
            tainted=store_names,
            pattern=_STORE_NAME_RE,
            literal_test=_has_store_literal,
        ) or any(
            _touches(
                arg,
                tainted=store_names,
                pattern=_STORE_NAME_RE,
                literal_test=_has_store_literal,
            )
            for arg in item.args
        ) or any(
            _touches(
                keyword.value,
                tainted=store_names,
                pattern=_STORE_NAME_RE,
                literal_test=_has_store_literal,
            )
            for keyword in item.keywords
        )

        reads = api in READ_OPERATIONS
        writes = _writes_path(api, item)
        if api in {
            "copy", "copy2", "copyfile", "hardlink_to", "link", "remove",
            "rename", "replace", "rmdir", "rmtree", "symlink_to",
        }:
            writes = writes and (
                _pathlike(target)
                or any(_pathlike(arg) for arg in item.args)
                or any(_pathlike(keyword.value) for keyword in item.keywords)
            )
        if authoring_touch and reads:
            capabilities.add("authoring_read")
        if authoring_touch and writes:
            capabilities.add("authoring_write")
        if store_touch and reads:
            capabilities.add("verified_store_read")
        if store_touch and writes:
            capabilities.add("store_write")

        # Every filesystem mutation implemented by the single store-owner
        # module is publication-store authority.  File-handle writes and
        # ``os.open`` flags do not retain their originating Path expression,
        # so requiring path-name taint here would create an easy bypass.
        if path == "runtime/contract_store.py" and writes:
            capabilities.add("store_write")

        # Importing any private store implementation detail is itself an
        # architectural breach; its target can be hidden behind a generic
        # ``Path`` and private read helpers are not a supported live boundary.
        if (
            api.startswith("_")
            and _boundary_owner(canonical.rpartition(".")[0]) == "contract_store"
        ):
            capabilities.add("store_write")

    return ScopeFacts(
        path=path,
        scope=scope,
        line=getattr(node, "lineno", 1),
        capabilities=tuple(sorted(capabilities)),
        calls=tuple(sorted(calls)),
        direct_manifest_dir_access=(
            manifest_dir_locator_used and "authoring_read" in capabilities
        ),
        closed_dynamic_boundary=closed_dynamic_boundary,
    )


class _ScopeCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.scopes: list[tuple[str, ast.AST]] = []
        self.containers: dict[str, ast.AST] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join((*self.stack, node.name))
        self.scopes.append((qualified, node))
        self.containers[qualified] = node
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(self, node: ast.AST, name: str) -> None:
        qualified = ".".join((*self.stack, name))
        self.scopes.append((qualified, node))
        self.containers[qualified] = node
        self.stack.append(name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, node.name)


def _apply_callable_aliases(
    aliases: dict[str, str],
    nodes: Sequence[ast.AST],
    local_callables: frozenset[str],
) -> bool:
    """Resolve aliases and report any rebinding of existing authority."""

    def trusted(value: str) -> bool:
        return bool(
            _boundary_owner(value) is not None
            or _boundary_api_capabilities(value)
            or value in local_callables
            or value in SENSITIVE_FIRST_CLASS_REFERENCES
            or value in SENSITIVE_IMPORT_NAMESPACES
            or value in DYNAMIC_CODE_LOADER_CANONICALS
            or value.startswith("importlib.")
            and value.rsplit(".", 1)[-1] in DYNAMIC_CODE_LOADER_APIS
        )

    def boundary_authoritative(value: str) -> bool:
        return bool(
            _boundary_owner(value) is not None
            or _boundary_api_capabilities(value)
        )

    ambiguous = False
    assignments = [
        item for item in nodes if isinstance(item, (ast.Assign, ast.AnnAssign))
    ]
    for item in assignments:
        value = item.value
        if value is None:
            continue
        dotted = _dotted_name(value)
        canonical = ""
        if dotted is not None:
            first, separator, remainder = dotted.partition(".")
            canonical = aliases.get(first, first) + (
                separator + remainder if separator else ""
            )
        targets = (
            set().union(*(_target_names(target) for target in item.targets))
            if isinstance(item, ast.Assign)
            else _target_names(item.target)
        )
        trusted_alias = bool(canonical and trusted(canonical))
        if not trusted_alias and _may_resolve_dynamic_loader_callable(
            value, aliases,
        ):
            canonical = "importlib." + (_leaf_name(value) or "dynamic_loader")
            trusted_alias = True
        for target in targets:
            previous = aliases.get(target)
            if (
                previous
                and boundary_authoritative(previous)
                and canonical != previous
            ):
                ambiguous = True
            if trusted_alias:
                _remember_alias(aliases, target, canonical, local_callables)
    return ambiguous


def _remember_alias(
    aliases: dict[str, str], bound: str, canonical: str,
    local_callables: frozenset[str],
) -> None:
    """Retain an earlier authority, or prefer a later authority over safety."""

    def authoritative(value: str) -> bool:
        return bool(
            _boundary_owner(value) is not None
            or _boundary_api_capabilities(value)
            or value in local_callables
            or value in SENSITIVE_FIRST_CLASS_REFERENCES
            or value in SENSITIVE_IMPORT_NAMESPACES
        )

    previous = aliases.get(bound)
    if previous is None or (authoritative(canonical) and not authoritative(previous)):
        aliases[bound] = canonical


def _import_aliases(
    tree: ast.Module,
    local_callables: frozenset[str],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    # Imports guarded by an optional-dependency ``try`` are still real module
    # aliases.  Walk the module lexical scope, but never descend into a
    # function or class where the alias has different lifetime.
    for node in _scope_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if _relative_boundary_import(node):
                continue
            for alias in node.names:
                _remember_alias(
                    aliases, alias.asname or alias.name,
                    f"{node.module}.{alias.name}", local_callables,
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                _remember_alias(
                    aliases, bound, alias.name if alias.asname else bound,
                    local_callables,
                )
    _apply_callable_aliases(aliases, _scope_nodes(tree), local_callables)
    return aliases


def _aliases_in_lexical_scope(
    scope: str,
    *,
    module_aliases: Mapping[str, str],
    containers: Mapping[str, ast.AST],
    local_callables: frozenset[str],
) -> dict[str, str]:
    """Return module plus enclosing-scope imports for one nested function."""

    aliases = dict(module_aliases)
    parts = scope.split(".")
    for size in range(1, len(parts)):
        parent = containers.get(".".join(parts[:size]))
        if parent is None:
            continue
        for item in _scope_nodes(parent):
            if isinstance(item, ast.ImportFrom) and item.module:
                if _relative_boundary_import(item):
                    continue
                for alias in item.names:
                    _remember_alias(
                        aliases, alias.asname or alias.name,
                        f"{item.module}.{alias.name}", local_callables,
                    )
            elif isinstance(item, ast.Import):
                for alias in item.names:
                    bound = alias.asname or alias.name.split(".", 1)[0]
                    _remember_alias(
                        aliases, bound, alias.name if alias.asname else bound,
                        local_callables,
                    )
        _apply_callable_aliases(aliases, _scope_nodes(parent), local_callables)
    return aliases



def _bounded_ast_metrics_v1(tree: ast.AST) -> int:
    nodes = 0
    scopes = 1
    calls = 0
    stack = [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes += 1
        if isinstance(node, ast.Call):
            calls += 1
        if (
            nodes > MAX_BOUNDARY_AST_NODES_V1
            or depth > MAX_BOUNDARY_AST_DEPTH_V1
            or scopes > MAX_BOUNDARY_SCOPES_V1
            or calls > MAX_BOUNDARY_CALLS_V1
        ):
            raise _invalid("boundary AST budget")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return nodes


def _scan_boundary_source_unchecked_v1(
    relative: str, content: bytes,
) -> tuple[list[ScopeFacts], int]:
    if type(content) is not bytes or len(content) > MAX_BOUNDARY_SOURCE_BYTES_V1:
        raise _invalid("boundary source byte budget")
    try:
        tree = ast.parse(content.decode("utf-8"), filename=relative)
        node_count = _bounded_ast_metrics_v1(tree)
    except PreflightError:
        raise
    except (
        UnicodeDecodeError, SyntaxError, RecursionError, ValueError,
        OverflowError,
    ) as exc:
        raise _invalid("boundary source") from exc
    collector = _ScopeCollector()
    try:
        collector.visit(tree)
    except (RecursionError, ValueError, OverflowError) as exc:
        raise _invalid("boundary source") from exc
    scopes: list[tuple[str, ast.AST]] = [("<module>", tree), *collector.scopes]
    local_callables = frozenset(
        name.rsplit(".", 1)[-1] for name, _node in collector.scopes
    )
    aliases = _import_aliases(tree, local_callables)
    direct = [
        _analyse_scope(
            relative,
            name,
            node,
            _aliases_in_lexical_scope(
                name,
                module_aliases=aliases,
                containers=collector.containers,
                local_callables=local_callables,
            ),
            local_callables,
        )
        for name, node in scopes
    ]
    by_leaf: dict[str, list[int]] = {}
    for index, fact in enumerate(direct):
        by_leaf.setdefault(fact.scope.rsplit(".", 1)[-1], []).append(index)
    effective = [set(fact.capabilities) for fact in direct]
    changed = True
    while changed:
        changed = False
        for index, fact in enumerate(direct):
            for callee in fact.calls:
                matches = by_leaf.get(callee, [])
                if len(matches) > 1:
                    authority = set().union(*(effective[item] for item in matches))
                    if authority & FLOW_CAPABILITIES:
                        before = len(effective[index])
                        effective[index].add("ambiguous_local_authority")
                        changed |= len(effective[index]) != before
                    continue
                if not matches:
                    continue
                before = len(effective[index])
                effective[index].update(effective[matches[0]] & FLOW_CAPABILITIES)
                changed |= len(effective[index]) != before
    result = [
        ScopeFacts(
            path=fact.path,
            scope=fact.scope,
            line=fact.line,
            capabilities=tuple(sorted(effective[index])),
            calls=fact.calls,
            direct_manifest_dir_access=fact.direct_manifest_dir_access,
            closed_dynamic_boundary=fact.closed_dynamic_boundary,
        )
        for index, fact in enumerate(direct)
    ]
    return result, node_count


def _scan_boundary_source_v1(
    relative: str, content: bytes,
) -> tuple[list[ScopeFacts], int]:
    try:
        return _scan_boundary_source_unchecked_v1(relative, content)
    except MemoryError as exc:
        raise _invalid("boundary source memory") from exc


def _discover_boundary_from_verified_v1(
    verified_content: Mapping[str, bytes],
) -> tuple[ScopeFacts, ...]:
    facts: list[ScopeFacts] = []
    sources: list[tuple[str, bytes]] = []
    total_source_bytes = 0
    for relative in sorted(verified_content, key=lambda item: item.encode("utf-8")):
        components = relative.split("/")
        if (
            not relative.endswith(".py")
            or not components
            or components[0] not in SCAN_ROOTS
            or "__pycache__" in components
        ):
            continue
        content = verified_content[relative]
        if type(content) is not bytes:
            raise _invalid("boundary source bytes")
        sources.append((relative, content))
        total_source_bytes += len(content)
    if (
        len(sources) > MAX_BOUNDARY_SOURCE_FILES_V1
        or total_source_bytes > MAX_BOUNDARY_TOTAL_SOURCE_BYTES_V1
        or any(len(content) > MAX_BOUNDARY_SOURCE_BYTES_V1 for _, content in sources)
    ):
        raise _invalid("boundary source budget")
    total_ast_nodes = 0
    for relative, content in sources:
        discovered, ast_nodes = _scan_boundary_source_v1(relative, content)
        total_ast_nodes += ast_nodes
        if total_ast_nodes > MAX_BOUNDARY_TOTAL_AST_NODES_V1:
            raise _invalid("boundary total AST budget")
        facts.extend(discovered)
    return tuple(sorted(
        (
            fact for fact in facts
            if fact.capabilities
            or fact.direct_manifest_dir_access
            or fact.closed_dynamic_boundary
        ),
        key=lambda fact: (fact.path, fact.scope),
    ))
def _entry_key(entry: Mapping[str, object]) -> str:
    return f"{entry.get('path', '')}:{entry.get('scope', '')}"


def check(
    facts: Sequence[ScopeFacts],
    inventory: Mapping[str, object],
) -> list[Finding]:
    findings: list[Finding] = []
    if inventory.get("schema") != SCHEMA:
        findings.append(Finding(
            "inventory_invalid", "<inventory>", "schema does not match the guard",
        ))
    if inventory.get("scan_roots") != list(SCAN_ROOTS):
        findings.append(Finding(
            "inventory_invalid",
            "<inventory>",
            f"scan_roots must be {list(SCAN_ROOTS)!r}",
        ))
    raw_entries = inventory.get("entries", [])
    if not isinstance(raw_entries, list):
        return [Finding("inventory_invalid", "<inventory>", "entries is not a list")]

    entries: dict[str, Mapping[str, object]] = {}
    for raw in raw_entries:
        if not isinstance(raw, dict):
            findings.append(Finding(
                "inventory_invalid", "<inventory>", "entry is not an object",
            ))
            continue
        key = _entry_key(raw)
        if key in entries:
            findings.append(Finding("inventory_duplicate", key, "duplicate scope"))
        entries[key] = raw

    discovered = {
        fact.key: fact
        for fact in facts
        if fact.capabilities or fact.direct_manifest_dir_access
    }
    for key, fact in discovered.items():
        entry = entries.get(key)
        if entry is None:
            findings.append(Finding(
                "unclassified_boundary_scope",
                key,
                f"classify capabilities {list(fact.capabilities)!r}",
            ))
            continue
        role = entry.get("role")
        if role not in VALID_ROLES:
            findings.append(Finding(
                "inventory_role_invalid", key, f"unsupported role {role!r}",
            ))
            continue
        destination = entry.get("destination")
        if (
            not isinstance(destination, str)
            or not destination.strip()
            or destination.strip() == "review-required"
        ):
            findings.append(Finding(
                "inventory_invalid", key, "destination must explain the boundary",
            ))
            continue
        if entry.get("phase") != "M4":
            findings.append(Finding(
                "inventory_invalid", key, "phase must be M4",
            ))
            continue
        expected = entry.get("capabilities")
        if not isinstance(expected, list) or any(not isinstance(v, str) for v in expected):
            findings.append(Finding(
                "inventory_invalid", key, "capabilities must be a string list",
            ))
            continue
        if expected != sorted(set(expected)):
            findings.append(Finding(
                "inventory_invalid",
                key,
                "capabilities must be sorted and contain no duplicates",
            ))
            continue
        if tuple(expected) != fact.capabilities:
            findings.append(Finding(
                "boundary_scope_changed",
                key,
                f"inventory={sorted(set(expected))!r}, discovered={list(fact.capabilities)!r}",
            ))
            continue

        capabilities = set(fact.capabilities)
        if fact.direct_manifest_dir_access and not (
            role in {"offline_authoring", "migration_boundary", "store_owner"}
            or fact.path == "runtime/executor_birth_authoring.py"
        ):
            findings.append(Finding(
                "direct_manifest_dir_read_without_token",
                key,
                "ManifestRef.manifest_dir is private to the versioned reader; "
                "use read_manifest_ref_versioned()",
            ))
        if role == "birth_owner" and (
            fact.path not in {
                "runtime/executor_birth.py",
                "runtime/executor_birth_intent.py",
                "runtime/executor_birth_operational.py",
            }
            or bool(capabilities & {
                "legacy_bootstrap", "publish_bootstrap", "publish_localization",
                "retire", "rollback", "sign",
            })
        ):
            findings.append(Finding(
                "birth_owner_invalid",
                key,
                "birth ownership is restricted to the sealed Executor Birth modules and "
                "cannot absorb dedicated or migration boundaries",
            ))
        if role == "operational_producer" and "birth" in capabilities and (
            capabilities & {"publish_technical", "reactivate", "sign"}
        ):
            findings.append(Finding(
                "operational_birth_mixed_authority",
                key,
                "a migrated producer may request birth but cannot retain low-level authority",
            ))
        if "ambiguous_local_authority" in capabilities:
            findings.append(Finding(
                "ambiguous_local_authority",
                key,
                "a same-named local helper hides which authority is invoked",
            ))
        if "dynamic_boundary_access" in capabilities:
            findings.append(Finding(
                "dynamic_boundary_access",
                key,
                "boundary authority must use a statically resolved canonical API",
            ))
        if "store_write" in capabilities and role != "store_owner":
            findings.append(Finding(
                "store_write_outside_boundary",
                key,
                "only a reviewed store_owner scope may mutate the publication store",
            ))
        if (
            capabilities & {"legacy_bootstrap", "publish_bootstrap"}
            and role not in {"migration_boundary", "store_owner"}
        ):
            findings.append(Finding(
                "legacy_bootstrap_outside_boundary",
                key,
                "initial publication and activation require a migration_boundary",
            ))
        if role == "migration_boundary" and "legacy_bootstrap" not in capabilities:
            findings.append(Finding(
                "migration_boundary_not_constrained",
                key,
                "migration boundary has no discovered legacy-bootstrap operation",
            ))
        if capabilities & LIVE_MUTATIONS and role not in {
            "administrative_tool",
            "birth_owner",
            "migration_boundary",
            "operational_producer",
            "store_owner",
        }:
            findings.append(Finding(
                "live_mutation_role_invalid",
                key,
                "live contract mutation is not an offline or documentation action",
            ))
        if role == "live_reader":
            forbidden = sorted(capabilities & LIVE_READER_FORBIDDEN)
            if forbidden:
                findings.append(Finding(
                    "live_reader_uses_authoring",
                    key,
                    f"live reader uses {forbidden!r} instead of a verified snapshot",
                ))
        if role == "operational_producer":
            if "sign" in capabilities:
                findings.append(Finding(
                    "operational_sign_after_cutover",
                    key,
                    "operational flow must call one publisher, never sign_executor",
                ))
            if "authoring_write" in capabilities and not (
                capabilities & PUBLISH_CAPABILITIES
            ):
                findings.append(Finding(
                    "operational_write_without_publish",
                    key,
                    "authoring mutation has no publication boundary in the same scope",
                ))
        if role == "documentation" and capabilities & (
            LIVE_READER_FORBIDDEN - {"authoring_read", "authoring_verify"}
        ):
            findings.append(Finding(
                "documentation_mutates_boundary",
                key,
                "documentation scope may inspect authoring, but cannot mutate authority",
            ))

    for key in sorted(set(entries) - set(discovered)):
        findings.append(Finding(
            "stale_boundary_classification",
            key,
            "remove or regenerate this no-longer-discovered scope",
        ))
    return sorted(findings, key=lambda finding: (finding.code, finding.scope))


def birth_closed_findings(
    facts: Sequence[ScopeFacts],
    inventory: Mapping[str, object],
) -> list[Finding]:
    """Enforce the irreversible RM-0008 F4 closed-build boundary."""

    findings = list(check(facts, inventory))
    if inventory.get("source_census") != BIRTH_CLOSED_SOURCE_REVIEW_SHA256:
        findings.append(Finding(
            "birth_closed_source_review_invalid", "<inventory>",
            "source_census must equal the compiled Python source-review root",
        ))
    policy = inventory.get("birth_closed")
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
    if policy != expected_policy:
        findings.append(Finding(
            "birth_closed_inventory_invalid", "<inventory>",
            "birth_closed policy must exactly match the compiled closed policy",
        ))

    raw_entries = inventory.get("entries", [])
    entries = {
        _entry_key(entry): entry
        for entry in raw_entries
        if isinstance(entry, dict)
    } if isinstance(raw_entries, list) else {}
    owners = sorted(
        key for key, entry in entries.items() if entry.get("role") == "birth_owner"
    )
    if owners != [BIRTH_CLOSED_OWNER]:
        findings.append(Finding(
            "birth_closed_owner_invalid", "<inventory>",
            f"expected exactly {[BIRTH_CLOSED_OWNER]!r}, found {owners!r}",
        ))

    fact_keys = {fact.key for fact in facts}
    for scope in sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS - fact_keys):
        findings.append(Finding(
            "birth_closed_coordinator_scope_missing", scope,
            "compiled ownership coordinator has no discovered store boundary",
        ))
    for scope in sorted(set(BIRTH_CLOSED_EXCEPTION_SCOPES) - fact_keys):
        findings.append(Finding(
            "birth_closed_exception_scope_missing", scope,
            "compiled closed exception has no discovered boundary scope",
        ))

    for fact in facts:
        capabilities = set(fact.capabilities)
        entry = entries.get(fact.key, {})
        exception = entry.get("closed_exception")
        expected_exception = BIRTH_CLOSED_EXCEPTION_SCOPES.get(fact.key)
        if fact.key in BIRTH_CLOSED_COORDINATOR_STORE_OWNERS:
            if entry.get("role") != "store_owner" or capabilities != {"store_write"}:
                findings.append(Finding(
                    "birth_closed_coordinator_invalid", fact.key,
                    "ownership coordinator must be an exact store_write owner",
                ))
        if exception != expected_exception:
            findings.append(Finding(
                "birth_closed_exception_invalid", fact.key,
                f"expected compiled exception {expected_exception!r}, found {exception!r}",
            ))
            continue
        expected_capabilities = BIRTH_CLOSED_EXCEPTION_CAPABILITIES.get(fact.key)
        if expected_capabilities is not None and capabilities != expected_capabilities:
            findings.append(Finding(
                "birth_closed_exception_invalid", fact.key,
                "compiled exception must have exact capabilities "
                f"{sorted(expected_capabilities)!r}, found {sorted(capabilities)!r}",
            ))
        if "dynamic_boundary_access" in capabilities or fact.closed_dynamic_boundary:
            findings.append(Finding(
                "birth_closed_dynamic_boundary", fact.key,
                "closed builds permit no reflective, dynamic-import, or subprocess boundary",
            ))

        relevant_exception_capabilities = {
            "localization_only": {"publish_localization"},
            "retirement_only": {"retire"},
            "offline_nonproductive_authoring": {"sign"},
        }.get(exception, set())
        forbidden = capabilities & BIRTH_CLOSED_LEGACY_CAPABILITIES
        if not forbidden:
            if exception is not None and not (
                capabilities & relevant_exception_capabilities
            ):
                findings.append(Finding(
                    "birth_closed_exception_unused", fact.key,
                    "closed exception does not justify a discovered capability",
                ))
            continue
        if fact.path in BIRTH_CLOSED_SEALED_MODULES:
            continue
        if not forbidden <= relevant_exception_capabilities:
            findings.append(Finding(
                "birth_closed_legacy_authority", fact.key,
                f"legacy capabilities {sorted(forbidden)!r} remain outside sealed definitions",
            ))

    return sorted(
        set(findings),
        key=lambda finding: (finding.code, finding.scope, finding.message),
    )




def _birth_closed_finding_tuples_v1(
    verified_content: Mapping[str, bytes], inventory: Mapping[str, object],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (finding.code, finding.scope, finding.message)
        for finding in birth_closed_findings(
            _discover_boundary_from_verified_v1(verified_content), inventory,
        )
    )


def _normalized_source_review_bytes_v1(content: bytes) -> bytes:
    return _SOURCE_REVIEW_PIN_LINE.sub(
        _SOURCE_REVIEW_PIN_PLACEHOLDER, content,
    )


def _closed_python_source_review_sha256_v1(
    sources: Mapping[str, bytes],
) -> str:
    digest = hashlib.sha256(_BIRTH_CLOSED_SOURCE_REVIEW_DOMAIN)
    selected = []
    for relative, content in sources.items():
        components = relative.split("/")
        if (
            not relative.endswith(".py")
            or not components
            or components[0] not in _BOUNDARY_SCAN_ROOTS
            or "__pycache__" in components
            or type(content) is not bytes
        ):
            continue
        selected.append((relative, _normalized_source_review_bytes_v1(content)))
    for relative, content in sorted(
        selected, key=lambda item: item[0].encode("utf-8"),
    ):
        encoded_path = relative.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return f"sha256:{digest.hexdigest()}"


def _require_compiled_source_review_v1(
    verified_content: Mapping[str, bytes],
) -> None:
    if (
        _closed_python_source_review_sha256_v1(verified_content)
        != _BIRTH_CLOSED_SOURCE_REVIEW_SHA256
    ):
        raise _invalid("Python source-review root")
    # The installed root-owned entrypoint is an exact copy of the reviewed
    # autonomous verifier, never candidate-provided verifier logic.
    if (
        verified_content.get(_BOUNDARY_PREFLIGHT_ENTRYPOINT_V1)
        != verified_content.get("runtime/executor_birth_admin_preflight.py")
    ):
        raise _invalid("administrative preflight source binding")


def _require_birth_closed_sources_v1(
    verified_content: Mapping[str, bytes], inventory: Mapping[str, object],
) -> None:
    if _birth_closed_finding_tuples_v1(verified_content, inventory):
        raise _invalid("boundary source census")


def _validate_boundary_inventory_v1(content: bytes) -> dict[str, object]:
    value = decode_canonical_json_v1(content, MAX_DISTRIBUTION_FILE_BYTES)
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema", "source_census", "scan_roots", "entries", "birth_closed",
        }
        or value.get("schema") != _BOUNDARY_INVENTORY_SCHEMA
        or value.get("scan_roots") != list(_BOUNDARY_SCAN_ROOTS)
        or value.get("source_census") != _BIRTH_CLOSED_SOURCE_REVIEW_SHA256
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
        _bounded_ast_metrics_v1(tree)
    except PreflightError:
        raise
    except (
        UnicodeDecodeError, SyntaxError, RecursionError, ValueError,
        OverflowError, MemoryError,
    ) as exc:
        raise _invalid("product version source") from exc
    assignments: list[str] = []
    stores = 0
    try:
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
    except MemoryError as exc:
        raise _invalid("product version source") from exc
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

    total_ast_nodes = 0
    for item in files:
        if not item.path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content[item.path].decode("utf-8"), filename=item.path)
            total_ast_nodes += _bounded_ast_metrics_v1(tree)
            if total_ast_nodes > MAX_BOUNDARY_TOTAL_AST_NODES_V1:
                raise _invalid("boundary total AST budget")
        except PreflightError:
            raise
        except (
            UnicodeDecodeError, SyntaxError, RecursionError, ValueError,
            OverflowError, MemoryError,
        ) as exc:
            raise _invalid("Python source") from exc
        imports: list[tuple[str, int]] = []
        try:
            parents = {
                id(child): parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }

            def authenticated_door_eval(call: ast.Call) -> bool:
                if (
                    item.path != "runtime/admitted_module_v1.py"
                    or not isinstance(call.func, ast.Name)
                    or call.func.id not in {"compile", "exec"}
                ):
                    return False
                current: ast.AST = call
                while (parent := parents.get(id(current))) is not None:
                    if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        return parent.name == "load_admitted_module_v1"
                    current = parent
                return False

            aliases: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        bound = alias.asname or alias.name.split(".", 1)[0]
                        aliases[bound] = alias.name if alias.asname else bound
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        if alias.name != "*":
                            aliases[alias.asname or alias.name] = (
                                f"{node.module}.{alias.name}"
                            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend((alias.name, 0) for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append((node.module or "", node.level))
                    imports.extend(
                        (".".join(filter(None, (node.module or "", alias.name))), node.level)
                        for alias in node.names if alias.name != "*"
                    )
                elif isinstance(node, ast.Call) and _resolved_alias_name(
                    node.func, aliases,
                ) in {
                    "__import__", "builtins.__import__", "importlib.import_module",
                }:
                    raise _invalid("dynamic import")
                elif isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name)
                    and node.func.id in {
                        "compile", "eval", "exec", "FunctionType",
                    }
                    and not authenticated_door_eval(node)
                    or _is_dynamic_code_loader_call(
                        node.func,
                        _resolved_alias_name(node.func, aliases) or "",
                    )
                    or _may_resolve_dynamic_loader_callable(node.func, aliases)
                ):
                    raise _invalid("dynamic code loader")
                elif (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.slice, ast.Constant)
                    and node.slice.value == "__import__"
                    and any(
                        isinstance(part, ast.Name) and part.id == "__builtins__"
                        or isinstance(part, ast.Call)
                        and isinstance(part.func, ast.Name)
                        and part.func.id in {"globals", "locals", "vars"}
                        for part in ast.walk(node.value)
                    )
                ):
                    raise _invalid("dynamic import")
        except MemoryError as exc:
            raise _invalid("Python source") from exc
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
        inventory_value = _validate_boundary_inventory_v1(inventory)
        _require_birth_closed_sources_v1(verified, inventory_value)
        if expected_type is AuthenticatedDistributionV1:
            _require_compiled_source_review_v1(verified)
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
