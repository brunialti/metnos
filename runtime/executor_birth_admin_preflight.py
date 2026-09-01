#!/usr/bin/python3
"""Root-only, standard-library startup preflight for executor Birth V1.

The installed copy is invoked with ``python3 -I -S``.  Consequently this file
must remain self contained: importing a Metnos module here would make the
component being authenticated part of its own trust path.
"""
from __future__ import annotations

import ast
import base64
import ctypes
import errno
import hashlib
import json
import os
import platform
import re
import runpy
import selectors
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Iterable, Mapping, NamedTuple, Sequence

try:  # Windows imports the codec surface but is denied before operational I/O.
    import fcntl
except ImportError:  # pragma: no cover - exercised by the Windows CI lane
    fcntl = None  # type: ignore[assignment]


SUPPORTED_SYSTEMD_VERSIONS = ("255.4-1ubuntu8.17",)
OWNERSHIP_ROOT = Path("/var/lib/metnos/executor-birth")
AUTHORITY_ROOT = OWNERSHIP_ROOT / "authorities-v1"
CHAIN_ROOT = OWNERSHIP_ROOT / "chain-v1"
COORDINATOR_ROOT = OWNERSHIP_ROOT / "coordinator-v1"
RELEASE_ROOT = OWNERSHIP_ROOT / "releases-v1"
RUNTIME_ROOT = Path("/run/metnos-executor-birth-v1")
# The gate lives inside the product's OWN private runtime root, never under
# the shared `/run/lock`: that directory is `1777` by the FHS, and the same
# chain rule this module applies to every path (`st_mode & 0o022`) refuses
# any ancestor writable by group or others. A gate under `/run/lock` was
# therefore unopenable by construction, on every standard system and even
# as root. The rule is not relaxed; the location is one the product owns.
STARTUP_GATE_PATH_V1 = RUNTIME_ROOT / "startup-v1.lock"
PREFLIGHT_ATTESTATION_ROOT_V1 = OWNERSHIP_ROOT / "preflight-attestations-v1"
OPENSSL_LINK = Path("/usr/bin/openssl")
PYTHON_LINK = Path("/usr/bin/python3")
SYSTEMCTL_LINK = Path("/usr/bin/systemctl")
SYSTEMD_ANALYZE_LINK = Path("/usr/bin/systemd-analyze")

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
REQUIRED_HEAD_MAGIC_V1 = b"metnos-ownership-required-head-v1\0"
MAX_CUTOVER_BYTES_V1 = 4 * 1024 * 1024
MAX_HEAD_BYTES_V1 = 16 * 1024
MAX_REQUIRED_HEAD_BYTES_V1 = (
    len(REQUIRED_HEAD_MAGIC_V1) + 4 + MAX_HEAD_BYTES_V1 + 64
)
MAX_COORDINATOR_CONTROL_BYTES_V2 = 16 * 1024
MAX_COORDINATOR_RECORD_BYTES_V2 = 8 * 1024 * 1024
MAX_PREDECESSOR_DESCRIPTOR_BYTES_V1 = 16 * 1024 * 1024
MAX_PREDECESSOR_FILES_V1 = 20_000
MAX_PREDECESSOR_SERVICE_COMMANDS_V1 = 20_000
MAX_PREDECESSOR_PATH_DEPTH_V1 = 32
MAX_AUTHORITY_CHECKPOINT_BYTES_V1 = 4096
MAX_PREFLIGHT_ATTESTATIONS_V1 = 20_000
MAX_CONTEXT_TRANSITION_BYTES_V1 = 64 * 1024
MAX_CONTEXT_TRANSITIONS_V1 = 20_000
RECEIVED_SOURCE_DESCRIPTOR_BASENAME_V1 = "received-source-v1.json"
MAX_OPENSSL_STREAM_BYTES = 4096
OPENSSL_TIMEOUT_SECONDS = 5.0
OPENSSL_TEARDOWN_TIMEOUT_SECONDS = 1.0
SYSTEMCTL_TIMEOUT_SECONDS_V1 = 10.0
SYSTEMCTL_TEARDOWN_TIMEOUT_SECONDS_V1 = 1.0
MAX_SYSTEMCTL_STDOUT_BYTES_V1 = 4 * 1024 * 1024
MAX_SYSTEMCTL_STDERR_BYTES_V1 = 4 * 1024
OPENSSL_TEMPORARY_PREFIX = ".verify-"
SIGNATURE_DOMAIN = b"metnos.executor-birth.closed-build/v1\0"
BUILD_ID_DOMAIN = b"metnos.executor-birth.closed-build-id/v1\0"
FILE_HASH_DOMAIN = b"metnos.executor-birth.closed-build-file/v1\0"
BOUNDARY_INVENTORY_DOMAIN = b"metnos.executor-birth.boundary-inventory/v1\0"
CUTOVER_ID_DOMAIN_V1 = b"metnos.executor-birth.ownership-cutover-id/v1\0"
CUTOVER_CATALOG_ID_DOMAIN_V1 = b"metnos.executor-birth.current-catalog/v1\0"
CUTOVER_SIGNATURE_DOMAIN_V1 = b"metnos.executor-birth.ownership-cutover/v1\0"
HEAD_ID_DOMAIN_V1 = b"metnos.executor-birth.ownership-head-id/v1\0"
HEAD_SIGNATURE_DOMAIN_V1 = b"metnos.executor-birth.ownership-head/v1\0"
HEAD_PAYLOAD_HASH_DOMAIN_V2 = b"metnos.executor-birth.head-payload-hash/v2\0"
HEAD_SIGNATURE_HASH_DOMAIN_V2 = (
    b"metnos.executor-birth.head-signature-hash/v2\0"
)
REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2 = (
    b"metnos.executor-birth.required-head-frame-hash/v2\0"
)
SUCCESSOR_CLAIM_ID_DOMAIN_V1 = b"metnos.executor-birth.successor-claim/v1\0"
COORDINATOR_REQUEST_DOMAIN_V1 = (
    b"metnos.executor-birth.ownership-coordinator-request/v1\0"
)
LEGACY_COORDINATOR_RECORD_DOMAIN_V1 = (
    b"metnos.executor-birth.ownership-coordinator-record/v1\0"
)
COORDINATOR_RECORD_DOMAIN_V2 = (
    b"metnos.executor-birth.ownership-coordinator-record/v2\0"
)
LEGACY_JOURNAL_DOMAIN_V2 = b"metnos.executor-birth.legacy-journal/v2\0"
LEGACY_DISPOSITION_DOMAIN_V2 = b"metnos.executor-birth.legacy-disposition/v2\0"
INSTALL_TRANSACTION_ID_DOMAIN_V1 = (
    b"metnos.executor-birth.install-transaction/v1\0"
)
MAINTENANCE_PROOF_DOMAIN_V1 = b"metnos.executor-birth.maintenance-proof/v1\0"
PREDECESSOR_DESCRIPTOR_ID_DOMAIN_V1 = (
    b"metnos.executor-birth.predecessor-descriptor/v1\0"
)
SERVICE_CATALOG_ID_DOMAIN_V1 = (
    b"metnos.executor-birth.service-catalog/v1\0"
)
SERVICE_COVERAGE_DOMAIN_V1 = (
    b"metnos.executor-birth.service-coverage/v1\0"
)
SYSTEMD_FRAGMENT_DOMAIN_V1 = (
    b"metnos.executor-birth.systemd-fragment/v1\0"
)
TARGET_EXECUTABLE_DOMAIN_V1 = (
    b"metnos.executor-birth.target-executable/v1\0"
)
DEPLOYMENT_DESCRIPTOR_ID_DOMAIN_V1 = (
    b"metnos.executor-birth.deployment-descriptor/v1\0"
)
STARTUP_PREREQUISITE_ID_DOMAIN_V1 = (
    b"metnos.executor-birth.startup-prerequisite/v1\0"
)
ADMINISTRATIVE_BUNDLE_DOMAIN_V1 = (
    b"metnos.executor-birth.administrative-bundle/v1\0"
)
CANDIDATE_UNITS_DOMAIN_V1 = b"metnos.executor-birth.candidate-units/v1\0"
INSTALLED_TREE_DOMAIN_V1 = b"metnos.executor-birth.installed-tree/v1\0"
CURRENT_INVENTORY_DOMAIN_V1 = (
    b"metnos.executor-birth.current-inventory/v1\0"
)
CONTEXT_TRANSITION_ID_DOMAIN_V1 = (
    b"metnos.executor-birth.context-transition-id/v1\0"
)
ADMINISTRATIVE_EXECUTABLE_DOMAIN_V1 = (
    b"metnos.executor-birth.administrative-executable/v1\0"
)
OPENSSL_TCB_DOMAIN_V1 = b"metnos.executor-birth.openssl-tcb/v1\0"
OPENSSL_TCB_FILE_DOMAIN_V1 = b"metnos.executor-birth.openssl-tcb-file/v1\0"
SYSTEMD_CONFIGURED_DIRECTIVES_DOMAIN_V1 = (
    b"metnos.executor-birth.systemd-configured-directives/v1\0"
)
EFFECTIVE_UNITS_DOMAIN_V1 = b"metnos.executor-birth.effective-units/v1\0"
PREFLIGHT_ATTESTATION_DOMAIN_V1 = (
    b"metnos.executor-birth.preflight-attestation/v1\0"
)
PREFLIGHT_ATTESTATION_RECORD_DOMAIN_V1 = (
    b"metnos.executor-birth.preflight-attestation-record/v1\0"
)
SYSTEMD_ORIGIN_FILE_DOMAIN_V1 = (
    b"metnos.executor-birth.systemd-origin-file/v1\0"
)
SYSTEMD_ORIGIN_SOURCE_DOMAIN_V1 = (
    b"metnos.executor-birth.systemd-origin-source/v1\0"
)
SERVICE_SOURCE_IDENTITY_DOMAIN_V1 = (
    b"metnos.executor-birth.service-source-identity/v1\0"
)

SERVICE_CATALOG_PATH_V1 = (
    "deployment/executor-birth-service-catalog-v1.json"
)
DEPLOYMENT_DESCRIPTOR_PATH_V1 = (
    "deployment/executor-birth-deployment-v1.json"
)
ADMINISTRATIVE_ADAPTER_PATH_V1 = (
    "/usr/libexec/metnos/executor-birth-v1/preflight.py"
)
ADMINISTRATIVE_ROOT_TEXT_V1 = "/usr/libexec/metnos/executor-birth-v1"
SYSTEM_UNIT_ROOT_TEXT_V1 = "/etc/systemd/system"
MAX_SERVICE_CATALOG_BYTES_V1 = 256 * 1024
MAX_UNIT_FRAGMENT_BYTES_V1 = 256 * 1024
MAX_DEPLOYMENT_DESCRIPTOR_BYTES_V1 = 1024 * 1024
MAX_STARTUP_PREREQUISITE_BYTES_V1 = 256 * 1024
MAX_DEPLOYMENT_ARTIFACTS_V1 = 20_000
MAX_TCB_SUBPROCESS_STREAM_BYTES_V1 = 64 * 1024
MAX_ELF_PROGRAM_HEADERS_V1 = 4_096
MAX_ELF_INTERPRETER_BYTES_V1 = 4_096
MAX_OPENSSL_LOADER_ENTRIES_V1 = 512
MAX_OPENSSL_MODULE_FILES_V1 = 256
MAX_OPENSSL_MODULE_BYTES_V1 = 256 * 1024 * 1024
MAX_SYSTEMD_ORIGIN_BYTES_V1 = 1024 * 1024
MAX_SYSTEMD_ADDED_EDGES_PER_UNIT_V1 = 4096
MAX_SYSTEMD_ADDED_EDGES_TOTAL_V1 = 65536
MAX_PREFLIGHT_ATTESTATION_BYTES_V1 = 256 * 1024
MAX_PROC_STATUS_BYTES_V1 = 64 * 1024
_PR_CAPBSET_DROP_V1 = 24
_PR_SET_NO_NEW_PRIVS_V1 = 38
_PR_CAP_AMBIENT_V1 = 47
_PR_CAP_AMBIENT_CLEAR_ALL_V1 = 4
_LAUNCHER_BOUNDING_CAPABILITIES_V1 = (6, 7, 8)  # SETGID, SETUID, SETPCAP
_EXPECTED_SERVICE_SOURCE_IDENTITY_V1 = (
    "sha256:7727bc054bb411bfdd853148ac1a4c06945a710234d2db7fdb84d5e1851a2765"
)
_ISOLATED_G6C_NAMESPACE_RE_V1 = re.compile(r"[0-9a-f]{16}")
_ISOLATED_G6C_SOURCE_IDENTITY_V1 = (
    "sha256:b7ac6b75577b806a6f582bffa22db8f5a658bd01efa8e1cd8014030b7423040b"
)

_EXPECTED_PRODUCT_ENABLEMENT_LINKS_V1 = (
    (
        "/etc/systemd/system/default.target.wants/metnos.target",
        "../metnos.target",
    ),
    (
        "/etc/systemd/system/metnos.target.requires/metnos-http.service",
        "../metnos-http.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-durable-worker.service",
        "../metnos-durable-worker.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-i18n-translator.timer",
        "../metnos-i18n-translator.timer",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-llm.service",
        "../metnos-llm.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-photon.service",
        "../metnos-photon.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-playwright.service",
        "../metnos-playwright.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-searxng.service",
        "../metnos-searxng.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-side-display.service",
        "../metnos-side-display.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-stack-watchdog.timer",
        "../metnos-stack-watchdog.timer",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-telegram-daemon.service",
        "../metnos-telegram-daemon.service",
    ),
)

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
_OWNERSHIP_KEY_RE = re.compile(
    r"birth-ed25519-v1-sha256-[0-9a-f]{64}\Z"
)
_SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_PREDECESSOR_MODULE_RE_V1 = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,31}\Z"
)
_PREDECESSOR_ENVIRONMENT_RE_V1 = re.compile(
    r"[A-Z_][A-Z0-9_]{0,127}\Z"
)
# Deliberately mirrors the assembler's unbounded unit regex.  The general
# preflight _UNIT_RE above has a stricter length policy and is not equivalent.
_PREDECESSOR_UNIT_RE_V1 = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.@-]*\.(?:service|timer|target)\Z"
)
_CATALOG_MODULE_RE_V1 = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,31}\Z"
)
_CATALOG_ENVIRONMENT_RE_V1 = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
_SERVICE_ACCOUNT_RE_V1 = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_CATALOG_DURATION_RE_V1 = re.compile(
    r"(?:0|[1-9][0-9]*)(?:us|ms|s|min|h|d|w)\Z"
)
_CATALOG_SAFE_TOKEN_RE_V1 = re.compile(r"!?[A-Za-z0-9_./:@+=,-]+\Z")
_SYSTEMD_VERSION_RE_V1 = re.compile(
    r"(?P<major>[0-9]{3})(?:\.[0-9]+)*(?:[-+~.][A-Za-z0-9]+)*\Z"
)
_ARCHIVED_DIGEST_STEM_RE_V1 = re.compile(r"[0-9a-f]{64}\Z")
_ARCHIVED_HEAD_STEM_RE_V1 = re.compile(r"[0-9]{20}-[0-9a-f]{64}\Z")
_SUCCESSOR_CLAIM_BASENAME_RE_V1 = re.compile(
    r"(?:initial|[0-9a-f]{64})\.json\Z"
)
_TRANSACTION_DIRECTORY_RE_V2 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TRANSACTION_RECORD_RE_V2 = re.compile(r"record-([0-9]{3})-v2\.json\Z")
_PREFLIGHT_ATTESTATION_BASENAME_RE_V1 = re.compile(
    r"(sha256:[0-9a-f]{64})\.json\Z"
)
_CONTEXT_TRANSITION_BASENAME_RE_V1 = re.compile(r"([0-9a-f]{64})\.json\Z")
_HEX_SHA256_RE_V2 = re.compile(r"[0-9a-f]{64}\Z")
_PROVISIONING_TRANSACTION_RE_V2 = re.compile(r"[0-9a-f]{32}\Z")
_LEGACY_RECORD_RE_V1 = re.compile(r"record-([0-9]{3})-v1\.json\Z")

_AUTHORITY_KINDS_V1 = ("distribution", "cutover", "head")
_AUTHORITY_REGISTRY_BASENAMES_V1 = tuple(
    f"{kind}-registry-v1.json" for kind in _AUTHORITY_KINDS_V1
)
_AUTHORITY_PRIVATE_BASENAMES_V1 = tuple(
    f"{kind}-private-v1.bin" for kind in _AUTHORITY_KINDS_V1
)
_AUTHORITY_CHECKPOINT_BASENAMES_V1 = tuple(
    f"checkpoint-{index:03d}-v1.json" for index in range(5)
)

_REGISTRY_KEYS = frozenset({
    "schema_version", "authority", "key_id", "public_key", "purposes",
    "first_release_sequence", "last_release_sequence",
})
_CUTOVER_KEYS_V1 = frozenset({
    "schema_version", "cutover_id", "previous_cutover_id", "request_id",
    "signing_key_id", "catalog_id", "current_count", "current_receipts",
    "maintenance_evidence_hash", "boundary_inventory_hash",
    "boundary_guard_version", "closed_build_id", "context_transition_id",
    "dominant_startup_receipt",
})
_CUTOVER_RECEIPT_KEYS_V1 = frozenset({
    "contract_id", "generation_id", "receipt_hash",
})
_HEAD_KEYS_V1 = frozenset({
    "schema_version", "release_sequence", "cutover_id", "closed_build_id",
    "previous_head_id", "head_id", "signing_key_id",
})
_SUCCESSOR_CLAIM_KEYS_V1 = frozenset({
    "schema_version", "claim_id", "previous_head_id", "release_sequence",
    "request_id", "source_id", "closed_build_id",
})
_COORDINATOR_RECORD_KEYS_V2 = frozenset({
    "schema_version", "sequence", "state", "previous_record_sha256",
    "request_id", "previous_closed_build_id", "previous_cutover_id",
    "closed_build_id", "distribution_payload_hash",
    "distribution_signature_hash", "boundary_inventory_hash",
    "boundary_guard_version", "current_receipts",
    "maintenance_before_hash", "maintenance_after_hash",
    "maintenance_proof_b64", "startup_prerequisite_id",
    "startup_prerequisite_digest", "cutover_id", "catalog_id",
    "certificate_payload_hash", "certificate_signature_hash", "source_id",
    "successor_claim_id", "deployment_descriptor_id",
    "install_transaction_id", "installed_tree_hash", "release_sequence",
    "previous_head_id", "head_id", "head_payload_hash",
    "head_signature_hash", "required_head_frame_hash",
    "verified_chain_head_id", "preflight_attestation_hash",
    "service_coverage_hash", "administrative_bundle_hash",
    "provisioning_transaction_id", "previous_set_id",
    "previous_admission_context_id", "previous_context_epoch",
    "target_set_id", "target_admission_context_id", "target_context_epoch",
    "target_context_material_sha256", "target_set_json_sha256",
    "context_transition_id", "current_inventory_hash",
    "dominant_startup_receipt",
})
_LEGACY_COORDINATOR_RECORD_KEYS_V1 = frozenset({
    "schema_version", "sequence", "state", "previous_record_sha256",
    "request_id", "previous_closed_build_id", "previous_cutover_id",
    "closed_build_id", "distribution_payload_hash",
    "distribution_signature_hash", "boundary_inventory_hash",
    "boundary_guard_version", "current_receipts",
    "maintenance_before_hash", "maintenance_after_hash",
    "maintenance_proof_b64", "startup_prerequisite_id",
    "startup_prerequisite_digest", "cutover_id", "catalog_id",
    "certificate_payload_hash", "certificate_signature_hash",
})
_LEGACY_DISPOSITION_KEYS_V2 = frozenset({
    "schema_version", "disposition_id", "legacy_journal_hash",
    "legacy_request_id", "legacy_state", "successor_request_id", "reason",
})
_INSTALL_TRANSACTION_KEYS_V1 = frozenset({
    "schema_version", "request_id", "source_id", "closed_build_id",
    "release_sequence", "previous_head_id", "successor_claim_id",
    "deployment_descriptor_id", "service_coverage_hash",
    "administrative_bundle_hash",
})
_MAINTENANCE_PROOF_KEYS_V1 = frozenset({"schema_version", "source", "units"})
_MAINTENANCE_UNIT_KEYS_V1 = frozenset({
    "scope", "unit", "load_state", "active_state", "main_pid",
})
_MAINTENANCE_SOURCES_V1 = frozenset({
    "inactive_http_and_inactive_sidecar",
    "inactive_http_and_sidecar_broker",
})
_MAINTENANCE_TARGETS_V1 = (
    ("system", "metnos-backup.service"),
    ("system", "metnos-backup.timer"),
    ("system", "metnos-http.service"),
    ("system", "metnos-prompts-translator.service"),
    ("system", "metnos-prompts-translator.timer"),
    ("user", "metnos-durable-worker.service"),
    ("user", "metnos-http.service"),
    ("user", "metnos-i18n-translator.service"),
    ("user", "metnos-i18n-translator.timer"),
    ("user", "metnos-llm.service"),
    ("user", "metnos-photon.service"),
    ("user", "metnos-playwright.service"),
    ("user", "metnos-searxng.service"),
    ("user", "metnos-side-display.service"),
    ("user", "metnos-stack-quarantine.service"),
    ("user", "metnos-stack-ready.service"),
    ("user", "metnos-stack-watchdog.service"),
    ("user", "metnos-stack-watchdog.timer"),
    ("user", "metnos-telegram-daemon.service"),
    ("user", "metnos.target"),
)
_COORDINATOR_STATES_V1 = (
    "PREPARED", "RECEIPTS_COMPLETE", "CERTIFICATE_READY",
    "CERTIFICATE_PUBLISHED", "BUILD_VERIFIED", "HEAD_REQUIRED",
    "PREFLIGHT_VERIFIED",
)
_LEGACY_DISPOSITION_REASON_V2 = "superseded_before_certificate"
_COORDINATOR_CARRY_KEYS_V2 = frozenset({
    "request_id", "previous_closed_build_id", "previous_cutover_id",
    "closed_build_id", "distribution_payload_hash",
    "distribution_signature_hash", "boundary_inventory_hash",
    "boundary_guard_version", "source_id", "successor_claim_id",
    "deployment_descriptor_id", "install_transaction_id", "release_sequence",
    "previous_head_id", "service_coverage_hash", "administrative_bundle_hash",
    "provisioning_transaction_id", "previous_set_id",
    "previous_admission_context_id", "previous_context_epoch",
    "target_set_id", "target_admission_context_id", "target_context_epoch",
    "target_context_material_sha256", "target_set_json_sha256",
    "context_transition_id", "current_inventory_hash",
})
_LEGACY_COORDINATOR_CARRY_KEYS_V1 = frozenset({
    "request_id", "previous_closed_build_id", "previous_cutover_id",
    "closed_build_id", "distribution_payload_hash",
    "distribution_signature_hash", "boundary_inventory_hash",
    "boundary_guard_version",
})
_COORDINATOR_THRESHOLD_KEYS_V2 = (
    (1, frozenset({
        "current_receipts", "maintenance_before_hash",
        "maintenance_after_hash", "maintenance_proof_b64",
    })),
    (2, frozenset({
        "startup_prerequisite_id", "startup_prerequisite_digest",
        "cutover_id", "catalog_id", "certificate_payload_hash",
        "certificate_signature_hash", "dominant_startup_receipt",
    })),
    (4, frozenset({"installed_tree_hash"})),
    (5, frozenset({
        "head_id", "head_payload_hash", "head_signature_hash",
        "required_head_frame_hash", "verified_chain_head_id",
    })),
    (6, frozenset({"preflight_attestation_hash"})),
)
_PREFLIGHT_ATTESTATION_KEYS_V1 = frozenset({
    "schema_version", "attestation_id", "request_id", "closed_build_id",
    "release_sequence", "head_id", "required_head_frame_hash",
    "deployment_descriptor_id", "service_catalog_id",
    "service_coverage_hash", "candidate_units_hash",
    "administrative_bundle_hash", "python_binary_hash",
    "openssl_binary_hash", "openssl_tcb_hash", "systemctl_binary_hash",
    "systemd_analyze_binary_hash", "effective_units_hash",
    "checked_entry_ids",
})
_CONTEXT_TRANSITION_KEYS_V1 = frozenset({
    "schema_version", "transition_id", "request_id", "closed_build_id",
    "previous_cutover_id", "previous_set_id",
    "previous_admission_context_id", "previous_context_epoch", "set_id",
    "prepared_admission_context_id", "prepared_context_epoch",
    "context_material_sha256", "set_json_sha256", "current_inventory_hash",
})
_PREDECESSOR_KEYS_V1 = frozenset({
    "schema_version", "predecessor_id", "transaction_id",
    "installation_root", "files", "service_commands",
    "administrative_bundle_hash", "service_catalog_id",
    "service_coverage_hash",
})
_PREDECESSOR_FILE_KEYS_V1 = frozenset({"path", "size", "content_hash"})
_PREDECESSOR_SERVICE_COMMAND_KEYS_V1 = frozenset({
    "entry_id", "execution_kind", "target_executable",
    "target_executable_hash", "python_module", "target_args",
    "target_working_directory", "target_environment",
})
_PREDECESSOR_ENVIRONMENT_KEYS_V1 = frozenset({"name", "value"})
_PREDECESSOR_EXECUTION_KINDS_V1 = frozenset({
    "none", "python_module", "native_executable", "systemctl_stop",
})
_PREDECESSOR_FORBIDDEN_ENVIRONMENT_NAMES_V1 = frozenset({
    "PATH", "HOME", "SHELL", "VIRTUAL_ENV", "METNOS_INSTALL_ROOT",
    "METNOS_VENV", "METNOS_CONFIG", "METNOS_OWNERSHIP_ROOT",
    "METNOS_EXECUTOR_BIRTH_ROOT",
})
_SERVICE_CATALOG_KEYS_V1 = frozenset({
    "schema_version", "catalog_id", "entries", "legacy_bindings",
})
_SERVICE_CATALOG_ENTRY_KEYS_V1 = frozenset({
    "entry_id", "unit_name", "external_unit_name", "adapter_path", "class",
    "scope", "execution_kind", "target_executable", "target_executable_hash",
    "python_module", "target_args", "target_working_directory",
    "target_environment", "timer_target", "unit_spec", "requires_preflight",
    "readiness_owner",
})
_SERVICE_CATALOG_LEGACY_KEYS_V1 = frozenset({
    "legacy_id", "entry_id", "kind", "scope", "locator", "disposition",
})
_SERVICE_CATALOG_ENVIRONMENT_KEYS_V1 = frozenset({"name", "value"})
_SERVICE_CATALOG_UNIT_SPEC_KEYS_V1 = frozenset({
    "fragment_hash", "directives",
})
_SERVICE_CATALOG_DIRECTIVE_KEYS_V1 = frozenset({
    "section", "name", "value_type", "values",
})
_SERVICE_CATALOG_CLASSES_V1 = frozenset({
    "gated_service", "gated_timer", "stop_only", "target",
    "external_dependency", "gated_entrypoint",
})
_SERVICE_CATALOG_EXECUTION_KINDS_V1 = frozenset({
    "none", "python_module", "native_executable", "systemctl_stop",
})
_SERVICE_CATALOG_LEGACY_KINDS_V1 = frozenset({
    "user_unit", "system_unit", "script", "python_module", "powershell",
})
_SERVICE_CATALOG_LEGACY_SCOPES_V1 = frozenset({
    "user", "system", "repository", "installed",
})
_SERVICE_CATALOG_SECTION_ORDER_V1 = {
    "Unit": 0, "Service": 1, "Timer": 2, "Install": 3,
}
_SERVICE_CATALOG_DIRECTIVE_TYPES_V1 = {
    ("Unit", "Description"): "scalar",
    ("Unit", "Documentation"): "scalar",
    ("Unit", "DefaultDependencies"): "boolean",
    ("Unit", "Requires"): "unit_list",
    ("Unit", "Wants"): "unit_list",
    ("Unit", "BindsTo"): "unit_list",
    ("Unit", "After"): "unit_list",
    ("Unit", "Before"): "unit_list",
    ("Unit", "PartOf"): "unit_list",
    ("Unit", "OnFailure"): "unit_list",
    ("Unit", "StartLimitIntervalSec"): "duration",
    ("Unit", "StartLimitBurst"): "integer",
    ("Service", "Type"): "scalar",
    ("Service", "User"): "scalar",
    ("Service", "Group"): "scalar",
    ("Service", "SupplementaryGroups"): "scalar",
    ("Service", "ExecStartPre"): "argv",
    ("Service", "ExecStart"): "argv",
    ("Service", "ExecStop"): "argv",
    ("Service", "Restart"): "scalar",
    ("Service", "RestartSec"): "duration",
    ("Service", "TimeoutStartSec"): "duration",
    ("Service", "TimeoutStopSec"): "duration",
    ("Service", "WorkingDirectory"): "path_list",
    ("Service", "Environment"): "environment",
    ("Service", "NoNewPrivileges"): "boolean",
    ("Service", "PrivateTmp"): "boolean",
    ("Service", "ProtectSystem"): "scalar",
    ("Service", "ProtectHome"): "scalar",
    ("Service", "ReadWritePaths"): "path_list",
    ("Service", "CapabilityBoundingSet"): "scalar",
    ("Service", "AmbientCapabilities"): "scalar",
    ("Service", "KillMode"): "scalar",
    ("Service", "KillSignal"): "scalar",
    ("Service", "SuccessExitStatus"): "scalar",
    ("Service", "RemainAfterExit"): "boolean",
    ("Service", "UMask"): "scalar",
    ("Service", "NotifyAccess"): "scalar",
    ("Service", "WatchdogSec"): "duration",
    ("Service", "Delegate"): "boolean",
    ("Service", "DelegateSubgroup"): "scalar",
    ("Service", "ProtectKernelTunables"): "boolean",
    ("Service", "ProtectKernelModules"): "boolean",
    ("Service", "ProtectControlGroups"): "boolean",
    ("Service", "RestrictNamespaces"): "boolean",
    ("Service", "RestrictRealtime"): "boolean",
    ("Service", "RestrictAddressFamilies"): "scalar",
    ("Service", "LockPersonality"): "boolean",
    ("Service", "MemoryDenyWriteExecute"): "boolean",
    ("Service", "SystemCallArchitectures"): "scalar",
    ("Service", "MemoryAccounting"): "boolean",
    ("Service", "MemoryHigh"): "scalar",
    ("Service", "MemoryMax"): "scalar",
    ("Service", "TasksAccounting"): "boolean",
    ("Service", "TasksMax"): "integer",
    ("Service", "Nice"): "integer",
    ("Service", "LimitNOFILE"): "integer",
    ("Service", "StandardOutput"): "scalar",
    ("Service", "StandardError"): "scalar",
    ("Service", "SyslogIdentifier"): "scalar",
    ("Timer", "OnBootSec"): "duration",
    ("Timer", "OnActiveSec"): "duration",
    ("Timer", "OnUnitActiveSec"): "duration",
    ("Timer", "OnCalendar"): "scalar",
    ("Timer", "RandomizedDelaySec"): "duration",
    ("Timer", "Persistent"): "boolean",
    ("Timer", "AccuracySec"): "duration",
    ("Timer", "Unit"): "unit_list",
    ("Install", "WantedBy"): "unit_list",
    ("Install", "RequiredBy"): "unit_list",
}
_SYSTEMD_BASE_PROPERTIES_V1 = frozenset({
    "DropInPaths", "FragmentPath", "LoadState", "NeedDaemonReload",
    "UnitFileState",
})
_SYSTEMD_ADDED_EDGE_RELATIONS_V1 = frozenset({
    "Requires", "Requisite", "Wants", "BindsTo", "PartOf", "Upholds",
    "RequiredBy", "RequisiteOf", "WantedBy", "BoundBy", "ConsistsOf",
    "UpheldBy", "Conflicts", "ConflictedBy", "Before", "After",
    "OnFailure", "OnSuccess", "Triggers", "TriggeredBy",
    "PropagatesReloadTo", "ReloadPropagatedFrom", "PropagatesStopTo",
    "StopPropagatedFrom", "JoinsNamespaceOf",
})
_SYSTEMD_DIRECT_RELATIONS_V1 = frozenset({
    "Requires", "Wants", "BindsTo", "After", "Before", "PartOf",
    "OnFailure",
})
_SYSTEMD_REPEATABLE_PROPERTIES_V1 = frozenset({
    "ExecStartPre", "ExecStartPreEx", "ExecStart", "ExecStartEx",
    "ExecStop", "ExecStopEx", "TimersMonotonic", "TimersCalendar",
})
_SYSTEMD_EXEC_PROPERTY_PAIRS_V1 = {
    "ExecStartPre": ("ExecStartPre", "ExecStartPreEx"),
    "ExecStart": ("ExecStart", "ExecStartEx"),
    "ExecStop": ("ExecStop", "ExecStopEx"),
}
_SYSTEMD_TIMER_BASE_PROPERTIES_V1 = {
    "OnBootSec": ("TimersMonotonic", "OnBootUSec"),
    "OnActiveSec": ("TimersMonotonic", "OnActiveUSec"),
    "OnUnitActiveSec": ("TimersMonotonic", "OnUnitActiveUSec"),
    "OnCalendar": ("TimersCalendar", "OnCalendar"),
}
_SYSTEMD_DIRECTIVE_PROPERTY_REMAP_V1 = {
    ("Unit", "StartLimitIntervalSec"): ("StartLimitIntervalUSec",),
    ("Service", "RestartSec"): ("RestartUSec",),
    ("Service", "TimeoutStartSec"): ("TimeoutStartUSec",),
    ("Service", "TimeoutStopSec"): ("TimeoutStopUSec",),
    ("Service", "WatchdogSec"): ("WatchdogUSec",),
    ("Service", "LimitNOFILE"): ("LimitNOFILE", "LimitNOFILESoft"),
    ("Service", "ExecStartPre"): ("ExecStartPre", "ExecStartPreEx"),
    ("Service", "ExecStart"): ("ExecStart", "ExecStartEx"),
    ("Service", "ExecStop"): ("ExecStop", "ExecStopEx"),
    ("Timer", "OnBootSec"): ("TimersMonotonic",),
    ("Timer", "OnActiveSec"): ("TimersMonotonic",),
    ("Timer", "OnUnitActiveSec"): ("TimersMonotonic",),
    ("Timer", "OnCalendar"): ("TimersCalendar",),
    ("Timer", "RandomizedDelaySec"): ("RandomizedDelayUSec",),
    ("Timer", "AccuracySec"): ("AccuracyUSec",),
}
_SYSTEMD_SIGNAL_NAMES_V1 = {
    1: "SIGHUP", 2: "SIGINT", 3: "SIGQUIT", 4: "SIGILL", 5: "SIGTRAP",
    6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE", 9: "SIGKILL",
    10: "SIGUSR1", 11: "SIGSEGV", 12: "SIGUSR2", 13: "SIGPIPE",
    14: "SIGALRM", 15: "SIGTERM", 16: "SIGSTKFLT", 17: "SIGCHLD",
    18: "SIGCONT", 19: "SIGSTOP", 20: "SIGTSTP", 21: "SIGTTIN",
    22: "SIGTTOU", 23: "SIGURG", 24: "SIGXCPU", 25: "SIGXFSZ",
    26: "SIGVTALRM", 27: "SIGPROF", 28: "SIGWINCH", 29: "SIGIO",
    30: "SIGPWR", 31: "SIGSYS",
}
_SYSTEMD_CAPABILITY_NAMES_V1 = frozenset({
    "CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_DAC_READ_SEARCH", "CAP_FOWNER",
    "CAP_FSETID", "CAP_KILL", "CAP_SETGID", "CAP_SETUID", "CAP_SETPCAP",
    "CAP_LINUX_IMMUTABLE", "CAP_NET_BIND_SERVICE", "CAP_NET_BROADCAST",
    "CAP_NET_ADMIN", "CAP_NET_RAW", "CAP_IPC_LOCK", "CAP_IPC_OWNER",
    "CAP_SYS_MODULE", "CAP_SYS_RAWIO", "CAP_SYS_CHROOT", "CAP_SYS_PTRACE",
    "CAP_SYS_PACCT", "CAP_SYS_ADMIN", "CAP_SYS_BOOT", "CAP_SYS_NICE",
    "CAP_SYS_RESOURCE", "CAP_SYS_TIME", "CAP_SYS_TTY_CONFIG", "CAP_MKNOD",
    "CAP_LEASE", "CAP_AUDIT_WRITE", "CAP_AUDIT_CONTROL", "CAP_SETFCAP",
    "CAP_MAC_OVERRIDE", "CAP_MAC_ADMIN", "CAP_SYSLOG", "CAP_WAKE_ALARM",
    "CAP_BLOCK_SUSPEND", "CAP_AUDIT_READ", "CAP_PERFMON", "CAP_BPF",
    "CAP_CHECKPOINT_RESTORE",
})
_SYSTEMD_ROOT_FRAGMENT_ROOTS_V1 = (
    "/etc/systemd/system", "/run/systemd/system",
    "/usr/local/lib/systemd/system", "/usr/lib/systemd/system",
)
_SYSTEMD_ROOT_GENERATOR_ROOTS_V1 = (
    "/run/systemd/generator", "/run/systemd/generator.early",
    "/run/systemd/generator.late",
)
_SYSTEMD_ROOT_FRAGMENT_STATES_V1 = frozenset({
    "enabled", "enabled-runtime", "linked", "linked-runtime", "alias",
    "static", "disabled", "indirect",
})
_SYSTEMD_MANAGER_VIRTUAL_UNITS_V1 = frozenset({"-.slice", "system.slice"})
_SYSTEMD_ORIGIN_PROPERTIES_V1 = (
    "FragmentPath", "Id", "LoadState", "SourcePath", "Transient",
    "UnitFileState",
)
_DEPLOYMENT_DESCRIPTOR_KEYS_V1 = frozenset({
    "schema_version", "descriptor_id", "release_sequence",
    "installation_root", "service_user", "service_uid", "service_gid",
    "service_supplementary_gids", "service_home", "service_shell",
    "administrative_root", "system_unit_root", "artifacts",
    "service_catalog_id", "service_coverage_hash", "python_executable",
    "openssl_executable", "systemctl_executable",
    "systemd_analyze_executable",
})
_DEPLOYMENT_ARTIFACT_KEYS_V1 = frozenset({
    "source_path", "destination_path", "kind", "install_phase", "size",
    "content_hash", "mode", "uid", "gid",
})
_DEPLOYMENT_ARTIFACT_KINDS_V1 = frozenset({
    "administrative_program", "service_unit", "timer_unit", "target_unit",
    "stop_only_unit",
})
_STARTUP_PREREQUISITE_KEYS_V1 = frozenset({
    "schema_version", "prerequisite_id", "request_id", "closed_build_id",
    "release_sequence", "deployment_descriptor_id", "predecessor_id",
    "administrative_bundle_hash", "python_binary_hash",
    "openssl_binary_hash", "openssl_tcb_hash", "systemctl_binary_hash",
    "systemd_analyze_binary_hash", "service_catalog_id",
    "service_coverage_hash", "systemd_manager_version",
    "candidate_units_hash", "effective_units_hash",
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
_BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:414d407771d6e4be5c328e95a6f45d186a9715149db9bf4433b93e821b3e2699"
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
# Keep membership independently compiled while matching the canonical JSON
# ordering without relying on insertion position in this frozen snapshot.
_BIRTH_CLOSED_COORDINATOR_STORE_OWNERS = tuple(sorted((
    "install/birth_authority_provisioner.py:complete_transition_cutover_v2",
    "install/birth_authority_provisioner.py:prepare_transition_receipts_v2",
    "install/birth_ownership_authority_provisioner.py:_discard_temporary",
    "install/birth_ownership_authority_provisioner.py:_load_or_create_pair",
    "install/birth_ownership_authority_provisioner.py:_provision_ownership_authorities_at_v1",
    "install/birth_ownership_authority_provisioner.py:_provision_ownership_authorities_locked_v1",
    "install/birth_ownership_authority_provisioner.py:_provisioning_lock",
    "install/birth_ownership_authority_provisioner.py:_publish_no_replace",
    "install/birth_ownership_authority_provisioner.py:_sync_directory",
    "install/birth_ownership_authority_provisioner.py:_write_exclusive",
    "install/birth_ownership_authority_provisioner.py:provision_root_ownership_authorities_v1",
    "install/executor_birth_distribution_release.py:build_and_install_received_source_v1",
    "install/executor_birth_source_receiver.py:<module>",
    "install/executor_birth_source_receiver.py:_copy_source_file_v1",
    "install/executor_birth_source_receiver.py:_copy_source_file_v1.copied_chunks",
    "install/executor_birth_source_receiver.py:_create_private_directory_v1",
    "install/executor_birth_source_receiver.py:_create_source_directories_v1",
    "install/executor_birth_source_receiver.py:_ensure_child_directory_v1",
    "install/executor_birth_source_receiver.py:_open_received_tree_at_v1",
    "install/executor_birth_source_receiver.py:_load_received_source_locked_core_v1",
    "install/executor_birth_source_receiver.py:_load_received_source_with_product_session_v1",
    "install/executor_birth_source_receiver.py:_load_received_source_with_test_session_v1",
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
    "install/executor_birth_transition.py:<module>",
    "install/executor_birth_transition.py:deploy_source_v1",
    "install/executor_birth_transition.py:main",
    "runtime/executor_birth_ownership_coordinator.py:_publish_control_no_replace_v2",
    "runtime/executor_birth_ownership_coordinator.py:_reserve_transition_edge_core_v2",
    "runtime/executor_birth_ownership_coordinator.py:_reserve_transition_edge_locked_for_test_v2",
    "runtime/executor_birth_ownership_coordinator.py:_reserve_transition_edge_locked_v2",
    "install/executor_birth_startup_gate.py:_install_startup_gate_core_v1",
    "install/executor_birth_startup_gate.py:_install_startup_gate_for_test_v1",
    "install/executor_birth_startup_gate.py:install_startup_gate_v1",
    "install/executor_birth_startup_prerequisite.py:_finish_temporary_v1",
    "install/executor_birth_startup_prerequisite.py:_publish_core_v1",
    "install/executor_birth_startup_prerequisite.py:_publish_startup_prerequisite_for_test_v2",
    "install/executor_birth_startup_prerequisite.py:_publish_startup_prerequisite_locked_v2",
    "install/executor_birth_systemd.py:_install_group6_administrative_for_test_v1",
    "install/executor_birth_systemd.py:_install_locked_core_v1",
    "install/executor_birth_systemd.py:_install_signed_isolated_systemd_for_test_v1",
    "install/executor_birth_systemd.py:_open_parent_v1",
    "install/executor_birth_systemd.py:_publish_administrative_tree_v1",
    "install/executor_birth_systemd.py:_publish_isolated_units_for_test_v1",
    "install/executor_birth_systemd.py:install_group6_administrative_v1",
    "runtime/executor_birth_admin_preflight.py:<module>",
    "runtime/executor_birth_admin_preflight.py:_publish_preflight_attestation_core_v1",
    "runtime/executor_birth_admin_preflight.py:_publish_preflight_attestation_for_test_v1",
    "runtime/executor_birth_admin_preflight.py:_publish_preflight_attestation_v1",
    "runtime/executor_birth_admin_preflight.py:_run_operational_command_v1",
    "runtime/executor_birth_admin_preflight.py:_write_all_exact_v1",
    "runtime/executor_birth_admin_preflight.py:main",
    "runtime/executor_birth_commit_publisher.py:_BirthCommitPublisher._persist_current_reattestation",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore._append_pair",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore._update_required_head_locked",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.append_authenticated_build",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.append_context_transition",
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
    "runtime/executor_birth_ownership_coordinator.py:_OwnershipCoordinatorTransactionJournalV2.__init__",
    "runtime/executor_birth_ownership_coordinator.py:_OwnershipCoordinatorTransactionJournalV2._append_initial",
    "runtime/executor_birth_ownership_coordinator.py:_OwnershipCoordinatorTransactionJournalV2._committed",
    "runtime/executor_birth_ownership_coordinator.py:_OwnershipCoordinatorTransactionJournalV2._inventory",
    "runtime/executor_birth_ownership_coordinator.py:_OwnershipCoordinatorTransactionJournalV2.append_transaction_record",
    "runtime/executor_birth_ownership_coordinator.py:_append_coordinator_record_v1",
    "runtime/executor_birth_ownership_coordinator.py:_append_ownership_transaction_locked_for_test_v2",
    "runtime/executor_birth_ownership_coordinator.py:_append_ownership_transaction_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_append_prepared_transition_locked_for_test_v2",
    "runtime/executor_birth_ownership_coordinator.py:_append_prepared_transition_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_append_receipts_complete",
    "runtime/executor_birth_ownership_coordinator.py:_append_receipts_complete_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_build_locked_coordinator_graph_registry_v2.require_issued",
    "runtime/executor_birth_ownership_coordinator.py:_build_locked_coordinator_graph_registry_v2.resolve_issued",
    "runtime/executor_birth_ownership_coordinator.py:_completed_transition_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_cross_certificate_boundary_core_v2",
    "runtime/executor_birth_ownership_coordinator.py:_cross_certificate_boundary_locked_for_test_v2",
    "runtime/executor_birth_ownership_coordinator.py:_cross_certificate_boundary_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_cross_certificate_boundary_locked_v2.observe_certificate_graph",
    "runtime/executor_birth_ownership_coordinator.py:_cross_head_boundary_locked_for_test_v2",
    "runtime/executor_birth_ownership_coordinator.py:_cross_head_boundary_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_cross_head_boundary_locked_v2.observe_head_graph",
    "runtime/executor_birth_ownership_coordinator.py:_cross_preflight_boundary_locked_for_test_v2",
    "runtime/executor_birth_ownership_coordinator.py:_cross_preflight_boundary_locked_for_test_v2.publish",
    "runtime/executor_birth_ownership_coordinator.py:_cross_preflight_boundary_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_cross_preflight_boundary_locked_v2.observe_preflight_graph",
    "runtime/executor_birth_ownership_coordinator.py:_decode_record",
    "runtime/executor_birth_ownership_coordinator.py:_decode_record_v2",
    "runtime/executor_birth_ownership_coordinator.py:_deployment_lock_at_v1",
    "runtime/executor_birth_ownership_coordinator.py:_deployment_lock_for_test_v1",
    "runtime/executor_birth_ownership_coordinator.py:_deployment_lock_v1",
    "runtime/executor_birth_ownership_coordinator.py:_ensure_coordinator_child_directory_v2",
    "runtime/executor_birth_ownership_coordinator.py:_observe_dominant_identity_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_prepare_under_maintenance_v1",
    "runtime/executor_birth_ownership_coordinator.py:_proof_from_values",
    "runtime/executor_birth_ownership_coordinator.py:_publish_certificate_material_v2",
    "runtime/executor_birth_ownership_coordinator.py:_publish_certificate_with_prerequisite_v1",
    "runtime/executor_birth_ownership_coordinator.py:_publish_context_transition_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_publish_transaction_directory_no_replace_v2",
    "runtime/executor_birth_ownership_coordinator.py:_read_staged_transaction_directory_v2",
    "runtime/executor_birth_ownership_coordinator.py:_require_locked_coordinator_graph_snapshot_v2",
    "runtime/executor_birth_ownership_coordinator.py:_resolve_ownership_coordinator_locked_v2",
    "runtime/executor_birth_ownership_coordinator.py:_transition_edge_locked_v2",
)))
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

REPEATABLE_PROPERTIES = _SYSTEMD_REPEATABLE_PROPERTIES_V1
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


class OwnershipPublicKeyFactsV1(NamedTuple):
    authority: str
    key_id: str
    raw_public_key: bytes
    purpose: str


class OwnershipReceiptFactsV1(NamedTuple):
    contract_id: str
    generation_id: str
    receipt_hash: str


class _DecodedOwnershipCutoverV1(NamedTuple):
    cutover_id: str
    previous_cutover_id: str | None
    request_id: str
    signing_key_id: str
    catalog_id: str
    current_receipts: tuple[OwnershipReceiptFactsV1, ...]
    maintenance_evidence_hash: str
    boundary_inventory_hash: str
    boundary_guard_version: str
    closed_build_id: str
    context_transition_id: str
    dominant_startup_receipt: str
    encoded: bytes
    signature: bytes


class _DecodedOwnershipHeadV1(NamedTuple):
    release_sequence: int
    cutover_id: str
    closed_build_id: str
    previous_head_id: str | None
    head_id: str
    signing_key_id: str
    encoded: bytes
    signature: bytes


class _DecodedSuccessorClaimV1(NamedTuple):
    claim_id: str
    previous_head_id: str | None
    release_sequence: int
    request_id: str
    source_id: str
    closed_build_id: str


class _DecodedCoordinatorRecordV2(NamedTuple):
    sequence: int
    state: str
    previous_record_sha256: str | None
    request_id: str
    previous_closed_build_id: str | None
    previous_cutover_id: str | None
    closed_build_id: str
    distribution_payload_hash: str
    distribution_signature_hash: str
    boundary_inventory_hash: str
    boundary_guard_version: str
    current_receipts: tuple[OwnershipReceiptFactsV1, ...]
    maintenance_before_hash: str | None
    maintenance_after_hash: str | None
    maintenance_proof: bytes | None
    startup_prerequisite_id: str | None
    startup_prerequisite_digest: str | None
    cutover_id: str | None
    catalog_id: str | None
    certificate_payload_hash: str | None
    certificate_signature_hash: str | None
    dominant_startup_receipt: str | None
    source_id: str
    successor_claim_id: str
    deployment_descriptor_id: str
    install_transaction_id: str
    installed_tree_hash: str | None
    release_sequence: int
    previous_head_id: str | None
    head_id: str | None
    head_payload_hash: str | None
    head_signature_hash: str | None
    required_head_frame_hash: str | None
    verified_chain_head_id: str | None
    preflight_attestation_hash: str | None
    service_coverage_hash: str
    administrative_bundle_hash: str
    provisioning_transaction_id: str
    previous_set_id: str
    previous_admission_context_id: str
    previous_context_epoch: str
    target_set_id: str
    target_admission_context_id: str
    target_context_epoch: str
    target_context_material_sha256: str
    target_set_json_sha256: str
    context_transition_id: str
    current_inventory_hash: str

    def as_value(self) -> dict[str, object]:
        return {
            "schema_version": 2, "sequence": self.sequence,
            "state": self.state,
            "previous_record_sha256": self.previous_record_sha256,
            "request_id": self.request_id,
            "previous_closed_build_id": self.previous_closed_build_id,
            "previous_cutover_id": self.previous_cutover_id,
            "closed_build_id": self.closed_build_id,
            "distribution_payload_hash": self.distribution_payload_hash,
            "distribution_signature_hash": self.distribution_signature_hash,
            "boundary_inventory_hash": self.boundary_inventory_hash,
            "boundary_guard_version": self.boundary_guard_version,
            "current_receipts": [{
                "contract_id": item.contract_id,
                "generation_id": item.generation_id,
                "receipt_hash": item.receipt_hash,
            } for item in self.current_receipts],
            "maintenance_before_hash": self.maintenance_before_hash,
            "maintenance_after_hash": self.maintenance_after_hash,
            "maintenance_proof_b64": (
                base64.b64encode(self.maintenance_proof).decode("ascii")
                if self.maintenance_proof is not None else None
            ),
            "startup_prerequisite_id": self.startup_prerequisite_id,
            "startup_prerequisite_digest": self.startup_prerequisite_digest,
            "cutover_id": self.cutover_id,
            "catalog_id": self.catalog_id,
            "certificate_payload_hash": self.certificate_payload_hash,
            "certificate_signature_hash": self.certificate_signature_hash,
            "dominant_startup_receipt": self.dominant_startup_receipt,
            "source_id": self.source_id,
            "successor_claim_id": self.successor_claim_id,
            "deployment_descriptor_id": self.deployment_descriptor_id,
            "install_transaction_id": self.install_transaction_id,
            "installed_tree_hash": self.installed_tree_hash,
            "release_sequence": self.release_sequence,
            "previous_head_id": self.previous_head_id,
            "head_id": self.head_id,
            "head_payload_hash": self.head_payload_hash,
            "head_signature_hash": self.head_signature_hash,
            "required_head_frame_hash": self.required_head_frame_hash,
            "verified_chain_head_id": self.verified_chain_head_id,
            "preflight_attestation_hash": self.preflight_attestation_hash,
            "service_coverage_hash": self.service_coverage_hash,
            "administrative_bundle_hash": self.administrative_bundle_hash,
            "provisioning_transaction_id": self.provisioning_transaction_id,
            "previous_set_id": self.previous_set_id,
            "previous_admission_context_id": (
                self.previous_admission_context_id
            ),
            "previous_context_epoch": self.previous_context_epoch,
            "target_set_id": self.target_set_id,
            "target_admission_context_id": self.target_admission_context_id,
            "target_context_epoch": self.target_context_epoch,
            "target_context_material_sha256": (
                self.target_context_material_sha256
            ),
            "target_set_json_sha256": self.target_set_json_sha256,
            "context_transition_id": self.context_transition_id,
            "current_inventory_hash": self.current_inventory_hash,
        }


class _DecodedCoordinatorPrefixV2(NamedTuple):
    records: tuple[_DecodedCoordinatorRecordV2, ...]
    encoded_records: tuple[bytes, ...]


class _DecodedLegacyCoordinatorRecordV1(NamedTuple):
    sequence: int
    state: str
    previous_record_sha256: str | None
    request_id: str
    previous_closed_build_id: str | None
    previous_cutover_id: str | None
    closed_build_id: str
    distribution_payload_hash: str
    distribution_signature_hash: str
    boundary_inventory_hash: str
    boundary_guard_version: str
    current_receipts: tuple[OwnershipReceiptFactsV1, ...]
    maintenance_before_hash: str | None
    maintenance_after_hash: str | None
    maintenance_proof: bytes | None
    startup_prerequisite_id: str | None
    startup_prerequisite_digest: str | None
    cutover_id: str | None
    catalog_id: str | None
    certificate_payload_hash: str | None
    certificate_signature_hash: str | None

    def as_value(self) -> dict[str, object]:
        return {
            "schema_version": 1, "sequence": self.sequence,
            "state": self.state,
            "previous_record_sha256": self.previous_record_sha256,
            "request_id": self.request_id,
            "previous_closed_build_id": self.previous_closed_build_id,
            "previous_cutover_id": self.previous_cutover_id,
            "closed_build_id": self.closed_build_id,
            "distribution_payload_hash": self.distribution_payload_hash,
            "distribution_signature_hash": self.distribution_signature_hash,
            "boundary_inventory_hash": self.boundary_inventory_hash,
            "boundary_guard_version": self.boundary_guard_version,
            "current_receipts": [{
                "contract_id": item.contract_id,
                "generation_id": item.generation_id,
                "receipt_hash": item.receipt_hash,
            } for item in self.current_receipts],
            "maintenance_before_hash": self.maintenance_before_hash,
            "maintenance_after_hash": self.maintenance_after_hash,
            "maintenance_proof_b64": (
                base64.b64encode(self.maintenance_proof).decode("ascii")
                if self.maintenance_proof is not None else None
            ),
            "startup_prerequisite_id": self.startup_prerequisite_id,
            "startup_prerequisite_digest": self.startup_prerequisite_digest,
            "cutover_id": self.cutover_id,
            "catalog_id": self.catalog_id,
            "certificate_payload_hash": self.certificate_payload_hash,
            "certificate_signature_hash": self.certificate_signature_hash,
        }


class _DecodedLegacyDispositionV2(NamedTuple):
    disposition_id: str
    legacy_journal_hash: str
    legacy_request_id: str
    legacy_state: str
    successor_request_id: str
    reason: str


class _DecodedLegacyCoordinatorPrefixV1(NamedTuple):
    records: tuple[_DecodedLegacyCoordinatorRecordV1, ...]
    encoded_records: tuple[bytes, ...]


class _DecodedPreflightAttestationV1(NamedTuple):
    attestation_id: str
    request_id: str
    closed_build_id: str
    release_sequence: int
    head_id: str
    required_head_frame_hash: str
    deployment_descriptor_id: str
    service_catalog_id: str
    service_coverage_hash: str
    candidate_units_hash: str
    administrative_bundle_hash: str
    python_binary_hash: str
    openssl_binary_hash: str
    openssl_tcb_hash: str
    systemctl_binary_hash: str
    systemd_analyze_binary_hash: str
    effective_units_hash: str
    checked_entry_ids: tuple[str, ...]

    def as_value(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "attestation_id": self.attestation_id,
            "request_id": self.request_id,
            "closed_build_id": self.closed_build_id,
            "release_sequence": self.release_sequence,
            "head_id": self.head_id,
            "required_head_frame_hash": self.required_head_frame_hash,
            "deployment_descriptor_id": self.deployment_descriptor_id,
            "service_catalog_id": self.service_catalog_id,
            "service_coverage_hash": self.service_coverage_hash,
            "candidate_units_hash": self.candidate_units_hash,
            "administrative_bundle_hash": self.administrative_bundle_hash,
            "python_binary_hash": self.python_binary_hash,
            "openssl_binary_hash": self.openssl_binary_hash,
            "openssl_tcb_hash": self.openssl_tcb_hash,
            "systemctl_binary_hash": self.systemctl_binary_hash,
            "systemd_analyze_binary_hash": self.systemd_analyze_binary_hash,
            "effective_units_hash": self.effective_units_hash,
            "checked_entry_ids": list(self.checked_entry_ids),
        }


class _DecodedContextTransitionV1(NamedTuple):
    transition_id: str
    request_id: str
    closed_build_id: str
    previous_cutover_id: str | None
    previous_set_id: str
    previous_admission_context_id: str
    previous_context_epoch: str
    set_id: str
    prepared_admission_context_id: str
    prepared_context_epoch: str
    context_material_sha256: str
    set_json_sha256: str
    current_inventory_hash: str


class _DecodedPredecessorFileV1(NamedTuple):
    path: str
    size: int
    content_hash: str


class _DecodedPredecessorEnvironmentV1(NamedTuple):
    name: str
    value: str


class _DecodedPredecessorServiceCommandV1(NamedTuple):
    entry_id: str
    execution_kind: str
    target_executable: str | None
    target_executable_hash: str | None
    python_module: str | None
    target_args: tuple[str, ...]
    target_working_directory: str | None
    target_environment: tuple[_DecodedPredecessorEnvironmentV1, ...]


class _DecodedPredecessorDescriptorV1(NamedTuple):
    predecessor_id: str
    transaction_id: str
    installation_root: str
    files: tuple[_DecodedPredecessorFileV1, ...]
    service_commands: tuple[_DecodedPredecessorServiceCommandV1, ...]
    administrative_bundle_hash: str
    service_catalog_id: str
    service_coverage_hash: str

    def as_value(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "predecessor_id": self.predecessor_id,
            "transaction_id": self.transaction_id,
            "installation_root": self.installation_root,
            "files": [{
                "path": item.path,
                "size": item.size,
                "content_hash": item.content_hash,
            } for item in self.files],
            "service_commands": [{
                "entry_id": item.entry_id,
                "execution_kind": item.execution_kind,
                "target_executable": item.target_executable,
                "target_executable_hash": item.target_executable_hash,
                "python_module": item.python_module,
                "target_args": list(item.target_args),
                "target_working_directory": item.target_working_directory,
                "target_environment": [{
                    "name": variable.name,
                    "value": variable.value,
                } for variable in item.target_environment],
            } for item in self.service_commands],
            "administrative_bundle_hash": self.administrative_bundle_hash,
            "service_catalog_id": self.service_catalog_id,
            "service_coverage_hash": self.service_coverage_hash,
        }


class _CapturedSignedObjectCandidateV1(NamedTuple):
    stem: str
    encoded: bytes
    signature: bytes


class _CapturedClaimCandidateV1(NamedTuple):
    basename: str
    encoded: bytes
    decoded: _DecodedSuccessorClaimV1


class _CapturedTransactionCandidateV2(NamedTuple):
    request_id: str
    encoded_records: tuple[bytes, ...]
    decoded_prefix: _DecodedCoordinatorPrefixV2 | None


class _CapturedPreflightAttestationCandidateV1(NamedTuple):
    basename: str
    encoded: bytes


class _CapturedContextTransitionCandidateV1(NamedTuple):
    basename: str
    encoded: bytes


class _CapturedFixedOwnershipStateCandidateV1(NamedTuple):
    """One coherent fixed-store observation; it grants no authority."""

    registries: tuple[
        OwnershipPublicKeyFactsV1,
        OwnershipPublicKeyFactsV1,
        OwnershipPublicKeyFactsV1,
    ]
    anchor: _DecodedOwnershipCutoverV1 | None
    required_head: _DecodedOwnershipHeadV1 | None
    builds: tuple[_CapturedSignedObjectCandidateV1, ...]
    cutovers: tuple[_CapturedSignedObjectCandidateV1, ...]
    heads: tuple[_CapturedSignedObjectCandidateV1, ...]
    claims: tuple[_CapturedClaimCandidateV1, ...]
    transactions: tuple[_CapturedTransactionCandidateV2, ...]
    context_transitions: tuple[_CapturedContextTransitionCandidateV1, ...]
    preflight_attestations: tuple[
        _CapturedPreflightAttestationCandidateV1, ...
    ]
    legacy_records: tuple[tuple[str, bytes], ...]
    legacy_disposition: bytes | None
    predecessor: _DecodedPredecessorDescriptorV1 | None


class _CapturedFixedOwnershipStateForTestV1(NamedTuple):
    candidate: _CapturedFixedOwnershipStateCandidateV1


class _AuthenticatedDistributionObjectV1(NamedTuple):
    facts: DistributionFactsV1
    files: tuple[DistributionFileV1, ...]
    encoded: bytes
    signature: bytes


class _AuthenticatedTransactionSnapshotV2(NamedTuple):
    claim: _DecodedSuccessorClaimV1
    prefix: _DecodedCoordinatorPrefixV2


class _ReconciledFixedOwnershipSnapshotV1(NamedTuple):
    """Authenticated durable bytes; not a live operational attestation."""

    registries: tuple[
        OwnershipPublicKeyFactsV1,
        OwnershipPublicKeyFactsV1,
        OwnershipPublicKeyFactsV1,
    ]
    anchor: _DecodedOwnershipCutoverV1 | None
    required_head: _DecodedOwnershipHeadV1 | None
    builds: tuple[_AuthenticatedDistributionObjectV1, ...]
    cutovers: tuple[_DecodedOwnershipCutoverV1, ...]
    heads: tuple[_DecodedOwnershipHeadV1, ...]
    claims: tuple[_DecodedSuccessorClaimV1, ...]
    transactions: tuple[_AuthenticatedTransactionSnapshotV2, ...]
    pending_claims: tuple[_DecodedSuccessorClaimV1, ...]
    legacy_prefix: _DecodedLegacyCoordinatorPrefixV1 | None
    legacy_disposition: _DecodedLegacyDispositionV2 | None
    predecessor: _DecodedPredecessorDescriptorV1 | None


class _SelectedOwnershipEpochV1(NamedTuple):
    registries: tuple[
        OwnershipPublicKeyFactsV1,
        OwnershipPublicKeyFactsV1,
        OwnershipPublicKeyFactsV1,
    ]
    anchor: _DecodedOwnershipCutoverV1 | None
    required_head: _DecodedOwnershipHeadV1
    build: _AuthenticatedDistributionObjectV1
    transaction: _AuthenticatedTransactionSnapshotV2
    predecessor: _DecodedPredecessorDescriptorV1


class _AuthenticatedFixedOwnershipSnapshotV1(NamedTuple):
    """Product fixed-root result, deliberately not accepted by dispatch yet."""

    snapshot: _ReconciledFixedOwnershipSnapshotV1
    administrative_tcb: _CapturedAdministrativeTcbProductV1


class _AuthenticatedFixedOwnershipSnapshotForTestV1(NamedTuple):
    """Portable seam nominally incompatible with the product result."""

    snapshot: _ReconciledFixedOwnershipSnapshotV1


class _ServiceDirectiveV1(NamedTuple):
    section: str
    name: str
    value_type: str
    values: tuple[str, ...]

    def as_value(self) -> dict[str, object]:
        return {
            "section": self.section,
            "name": self.name,
            "value_type": self.value_type,
            "values": list(self.values),
        }


class _ServiceUnitSpecV1(NamedTuple):
    fragment_hash: str
    directives: tuple[_ServiceDirectiveV1, ...]


class _ServiceEnvironmentV1(NamedTuple):
    name: str
    value: str


class _ServiceCatalogEntryV1(NamedTuple):
    entry_id: str
    unit_name: str | None
    external_unit_name: str | None
    adapter_path: str | None
    class_name: str
    scope: str
    execution_kind: str
    target_executable: str | None
    target_executable_hash: str | None
    python_module: str | None
    target_args: tuple[str, ...]
    target_working_directory: str | None
    target_environment: tuple[_ServiceEnvironmentV1, ...]
    timer_target: str | None
    unit_spec: _ServiceUnitSpecV1 | None
    requires_preflight: bool
    readiness_owner: bool


class _ServiceLegacyBindingV1(NamedTuple):
    legacy_id: str
    entry_id: str
    kind: str
    scope: str
    locator: str
    disposition: str


class _DecodedServiceCatalogV1(NamedTuple):
    """Canonical catalog facts; by themselves they grant no authority."""

    catalog_id: str
    entries: tuple[_ServiceCatalogEntryV1, ...]
    legacy_bindings: tuple[_ServiceLegacyBindingV1, ...]
    encoded: bytes
    service_coverage_hash: str


class _DeploymentArtifactV1(NamedTuple):
    source_path: str
    destination_path: str
    kind: str
    install_phase: str
    size: int
    content_hash: str
    mode: int
    uid: int
    gid: int


class _DecodedDeploymentDescriptorV1(NamedTuple):
    """Canonical deployment claims; they are not a live installation proof."""

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
    artifacts: tuple[_DeploymentArtifactV1, ...]
    service_catalog_id: str
    service_coverage_hash: str
    python_executable: str
    openssl_executable: str
    systemctl_executable: str
    systemd_analyze_executable: str


class _DecodedStartupPrerequisiteV1(NamedTuple):
    """Structural prerequisite claim, deliberately not a sealed live proof."""

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


class _EnablementLinkV1(NamedTuple):
    path: str
    target: str

    def as_value(self) -> dict[str, str]:
        return {"path": self.path, "target": self.target}


class _CandidateUnitV1(NamedTuple):
    entry_id: str
    unit_name: str
    fragment_hash: str
    directives: tuple[_ServiceDirectiveV1, ...]
    enablement_links: tuple[_EnablementLinkV1, ...]

    def as_value(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "unit_name": self.unit_name,
            "fragment_hash": self.fragment_hash,
            "directives": [{
                "section": item.section,
                "name": item.name,
                "value_type": item.value_type,
                "values": list(item.values),
            } for item in self.directives],
            "enablement_links": [
                item.as_value() for item in self.enablement_links
            ],
        }


class _CandidateUnitsSnapshotV1(NamedTuple):
    entries: tuple[_CandidateUnitV1, ...]
    encoded: bytes
    candidate_units_hash: str


class _SystemdManagerPropertyV1(NamedTuple):
    name: str
    value_type: str
    values: tuple[str, ...]

    def as_value(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value_type": self.value_type,
            "values": list(self.values),
        }


class _SystemdManagerProjectionV1(NamedTuple):
    properties: tuple[_SystemdManagerPropertyV1, ...]

    def as_value(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "properties": [item.as_value() for item in self.properties],
        }


class _SystemdPropertyPlanV1(NamedTuple):
    class_name: str
    requested_properties: tuple[str, ...]
    cardinalities: tuple[tuple[str, int], ...]


class _EffectiveSystemdDropinV1(NamedTuple):
    path: str
    content_hash: str
    uid: int
    gid: int
    mode: int

    def as_value(self) -> dict[str, object]:
        return {
            "path": self.path,
            "content_hash": self.content_hash,
            "uid": self.uid,
            "gid": self.gid,
            "mode": self.mode,
        }


class _SystemdManagerAddedEdgeV1(NamedTuple):
    relation: str
    unit_name: str
    origin_kind: str
    fragment_path: str | None
    source_path: str | None
    source_size: int | None
    source_content_hash: str | None
    source_uid: int | None
    source_gid: int | None
    source_mode: int | None
    size: int | None
    content_hash: str | None
    uid: int | None
    gid: int | None
    mode: int | None
    load_state: str
    unit_file_state: str | None

    def as_value(self) -> dict[str, object]:
        return {
            "relation": self.relation,
            "unit_name": self.unit_name,
            "origin_kind": self.origin_kind,
            "fragment_path": self.fragment_path,
            "source_path": self.source_path,
            "source_size": self.source_size,
            "source_content_hash": self.source_content_hash,
            "source_uid": self.source_uid,
            "source_gid": self.source_gid,
            "source_mode": self.source_mode,
            "size": self.size,
            "content_hash": self.content_hash,
            "uid": self.uid,
            "gid": self.gid,
            "mode": self.mode,
            "load_state": self.load_state,
            "unit_file_state": self.unit_file_state,
        }


class _EffectiveSystemdUnitV1(NamedTuple):
    entry_id: str
    unit_name: str
    fragment_path: str
    fragment_hash: str
    fragment_uid: int
    fragment_gid: int
    fragment_mode: int
    dropins: tuple[_EffectiveSystemdDropinV1, ...]
    enablement_links: tuple[_EnablementLinkV1, ...]
    load_state: str
    unit_file_state: str
    need_daemon_reload: str
    configured_directives_hash: str
    manager_projection: _SystemdManagerProjectionV1
    manager_added_edges: tuple[_SystemdManagerAddedEdgeV1, ...]

    def as_value(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "unit_name": self.unit_name,
            "fragment_path": self.fragment_path,
            "fragment_hash": self.fragment_hash,
            "fragment_uid": self.fragment_uid,
            "fragment_gid": self.fragment_gid,
            "fragment_mode": self.fragment_mode,
            "dropins": [item.as_value() for item in self.dropins],
            "enablement_links": [
                item.as_value() for item in self.enablement_links
            ],
            "load_state": self.load_state,
            "unit_file_state": self.unit_file_state,
            "need_daemon_reload": self.need_daemon_reload,
            "configured_directives_hash": self.configured_directives_hash,
            "manager_projection": self.manager_projection.as_value(),
            "manager_added_edges": [
                item.as_value() for item in self.manager_added_edges
            ],
        }


class _EffectiveSystemdUnitsSnapshotV1(NamedTuple):
    entries: tuple[_EffectiveSystemdUnitV1, ...]
    encoded: bytes
    effective_units_hash: str


class _CapturedSystemdFileV1(NamedTuple):
    """One exact no-follow systemd file and its logical manager path."""

    logical_path: str
    maximum: int
    captured: _CapturedTrustedFileV1


class _CapturedSystemdLinkV1(NamedTuple):
    """One exact enablement symlink plus its authenticated parent chain."""

    logical_path: str
    actual_path: str
    parent: _TrustedResolvedPathV1
    identity: tuple[int, ...]
    target: str


class _CapturedEffectiveSystemdUnitsV1(NamedTuple):
    """Complete live observation; it grants no operational authority."""

    manager_version: str
    snapshot: _EffectiveSystemdUnitsSnapshotV1
    files: tuple[_CapturedSystemdFileV1, ...]
    links: tuple[_CapturedSystemdLinkV1, ...]


class _SystemdOriginObservationV1(NamedTuple):
    unit_name: str
    origin_kind: str
    fragment_path: str | None
    source_path: str | None
    source_size: int | None
    source_content_hash: str | None
    source_uid: int | None
    source_gid: int | None
    source_mode: int | None
    size: int | None
    content_hash: str | None
    uid: int | None
    gid: int | None
    mode: int | None
    load_state: str
    unit_file_state: str | None


class _ObservedEffectiveSystemdV1(NamedTuple):
    """Signed/live cross-binding that still cannot enter dispatch."""

    administrative_tcb: _ObservedAdministrativeTcbV1
    prerequisite: _CapturedTrustedFileV1
    effective_systemd: _CapturedEffectiveSystemdUnitsV1


class _ObservedEffectiveSystemdProductV1(NamedTuple):
    observation: _ObservedEffectiveSystemdV1


class _ObservedEffectiveSystemdForTestV1(NamedTuple):
    observation: _ObservedEffectiveSystemdV1


class _OperationalPreflightV1(NamedTuple):
    """Complete product proof held while the shared startup gate is live."""

    authenticated: _AuthenticatedFixedOwnershipSnapshotV1
    selected: _SelectedOwnershipEpochV1
    observation: _ObservedEffectiveSystemdProductV1


class _OperationalPreflightForTestV1(NamedTuple):
    """Nominal test seam; it cannot enter productive dispatch."""

    selected: _SelectedOwnershipEpochV1
    observation: _ObservedEffectiveSystemdForTestV1


@dataclass(slots=True)
class _LaunchGateLeaseV1:
    descriptor: int

    def close(self) -> None:
        if self.descriptor < 0:
            return
        descriptor = self.descriptor
        self.descriptor = -1
        _release_startup_gate_v1(descriptor)


class _LaunchPlanV1(NamedTuple):
    entry: _ServiceCatalogEntryV1
    installation_root: str
    service_user: str
    service_uid: int
    service_gid: int
    service_supplementary_gids: tuple[int, ...]
    service_home: str
    service_shell: str
    python_module: str | None
    target_args: tuple[str, ...]
    target_working_directory: str
    environment: tuple[tuple[str, str], ...]
    python_path: tuple[str, ...]
    umask: int


class _BoundPreflightMaterialsV1(NamedTuple):
    """Cross-bound signed claims; still not a live operational attestation."""

    distribution: _AuthenticatedDistributionObjectV1
    transaction: _DecodedCoordinatorRecordV2
    catalog: _DecodedServiceCatalogV1
    descriptor: _DecodedDeploymentDescriptorV1
    prerequisite: _DecodedStartupPrerequisiteV1
    candidate_units: _CandidateUnitsSnapshotV1
    unit_fragments: tuple[tuple[str, bytes], ...]
    administrative_bundle_hash: str
    installed_tree_hash: str


class _CandidateCutoverMaterialsV1(NamedTuple):
    """Signed candidate facts available before the prerequisite is published."""

    distribution: _AuthenticatedDistributionObjectV1
    transaction: _DecodedCoordinatorRecordV2
    predecessor: _DecodedPredecessorDescriptorV1
    catalog: _DecodedServiceCatalogV1
    descriptor: _DecodedDeploymentDescriptorV1
    candidate_units: _CandidateUnitsSnapshotV1
    unit_fragments: tuple[tuple[str, bytes], ...]
    administrative_bundle_hash: str
    installed_tree_hash: str


_PREPARED_CUTOVER_CANDIDATE_SEAL_V2 = object()


@dataclass(frozen=True, slots=True)
class _PreparedCutoverCandidateV2:
    materials: _CandidateCutoverMaterialsV1
    administrative_tcb: _CapturedAdministrativeTcbV1
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _PREPARED_CUTOVER_CANDIDATE_SEAL_V2
            or type(self.materials) is not _CandidateCutoverMaterialsV1
            or type(self.administrative_tcb) is not _CapturedAdministrativeTcbV1
        ):
            raise _invalid("prepared cutover candidate")


class _BoundPreflightMaterialsForTestV1(NamedTuple):
    """Portable seam result, nominally incompatible with product authority."""

    materials: _BoundPreflightMaterialsV1


class _TrustedPathComponentV1(NamedTuple):
    path: str
    identity: tuple[int, ...]
    link_target: str | None


class _TrustedResolvedPathV1(NamedTuple):
    requested_path: str
    canonical_path: str
    kind: str
    components: tuple[_TrustedPathComponentV1, ...]


class _CapturedTrustedFileV1(NamedTuple):
    resolved: _TrustedResolvedPathV1
    identity: tuple[int, ...]
    content: bytes


class _AdministrativeExecutableSnapshotV1(NamedTuple):
    python: _CapturedTrustedFileV1
    openssl: _CapturedTrustedFileV1
    systemctl: _CapturedTrustedFileV1
    systemd_analyze: _CapturedTrustedFileV1
    python_binary_hash: str
    openssl_binary_hash: str
    systemctl_binary_hash: str
    systemd_analyze_binary_hash: str


class _OpenSslTcbFileV1(NamedTuple):
    path: str
    size: int
    content_hash: str

    def as_value(self) -> dict[str, object]:
        return {
            "path": self.path,
            "size": self.size,
            "content_hash": self.content_hash,
        }


class _LoaderDependencyV1(NamedTuple):
    name: str | None
    path: str | None


class _OpenSslTcbSnapshotV1(NamedTuple):
    architecture: str
    openssl_executable: str
    elf_loader: str
    module_directory: str
    files: tuple[_OpenSslTcbFileV1, ...]
    encoded: bytes
    openssl_tcb_hash: str
    captures: tuple[_CapturedTrustedFileV1, ...]
    module_captures: tuple[_CapturedTrustedFileV1, ...]
    module_directory_resolution: _TrustedResolvedPathV1


class _CapturedAdministrativeTcbV1(NamedTuple):
    """Live bytes and identities; by themselves they authorize nothing."""

    executables: _AdministrativeExecutableSnapshotV1
    openssl_tcb: _OpenSslTcbSnapshotV1


class _CapturedAdministrativeTcbProductV1(NamedTuple):
    capture: _CapturedAdministrativeTcbV1


class _CapturedAdministrativeTcbForTestV1(NamedTuple):
    capture: _CapturedAdministrativeTcbV1


class _ExternalTargetMeasurementV1(NamedTuple):
    declared_path: str
    target_hash: str
    captured: _CapturedTrustedFileV1


class _ObservedAdministrativeTcbV1(NamedTuple):
    """Signed/live cross-binding that remains non-operational."""

    materials: _BoundPreflightMaterialsV1
    capture: _CapturedAdministrativeTcbV1
    external_targets: tuple[_ExternalTargetMeasurementV1, ...]


class _ObservedAdministrativeTcbProductV1(NamedTuple):
    observation: _ObservedAdministrativeTcbV1


class _ObservedAdministrativeTcbForTestV1(NamedTuple):
    observation: _ObservedAdministrativeTcbV1


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


def _require_json_depth_v1(value: object, maximum_nodes: int) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        # Every JSON value consumes at least one input byte.  Deriving the
        # node budget from the already-enforced byte bound preserves the
        # canonical runtime's accepted V2 journal surface without creating
        # an independent, lower cardinality limit.
        if depth > 64 or nodes > maximum_nodes:
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
    _require_json_depth_v1(value, len(encoded) + 1)
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


def _decode_ownership_registry_v1(
    encoded: bytes, expected_authority: str,
) -> OwnershipPublicKeyFactsV1:
    registry_contracts = {
        "distribution": (
            "closed_distribution_v1", 1, _DISTRIBUTION_KEY_RE,
            "distribution-ed25519-v1-sha256-",
        ),
        "cutover": (
            "ownership_cutover_v1", None, _OWNERSHIP_KEY_RE,
            "birth-ed25519-v1-sha256-",
        ),
        "head": (
            "ownership_head_v1", None, _OWNERSHIP_KEY_RE,
            "birth-ed25519-v1-sha256-",
        ),
    }
    contract = registry_contracts.get(expected_authority)
    if contract is None:
        raise _invalid("ownership registry authority")
    purpose, first_sequence, key_pattern, key_prefix = contract
    value = decode_canonical_json_v1(encoded, MAX_REGISTRY_BYTES)
    if (
        not isinstance(value, dict) or set(value) != _REGISTRY_KEYS
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("authority") != expected_authority
        or value.get("purposes") != [purpose]
        or value.get("first_release_sequence") != first_sequence
        or (
            expected_authority == "distribution"
            and type(value.get("first_release_sequence")) is not int
        )
        or value.get("last_release_sequence") is not None
    ):
        raise _invalid("ownership registry schema")
    key_id = value.get("key_id")
    public_key = value.get("public_key")
    if (
        not isinstance(key_id, str) or key_pattern.fullmatch(key_id) is None
        or not isinstance(public_key, str)
    ):
        raise _invalid("ownership registry key")
    try:
        raw = base64.b64decode(public_key, validate=True)
    except (ValueError, TypeError) as exc:
        raise _invalid("ownership public key") from exc
    if (
        len(raw) != 32 or base64.b64encode(raw).decode("ascii") != public_key
        or key_id != key_prefix + hashlib.sha256(raw).hexdigest()
    ):
        raise _invalid("ownership public key")
    return OwnershipPublicKeyFactsV1(
        expected_authority, key_id, raw, purpose,
    )


def _decode_ownership_registry_set_v1(
    distribution_encoded: bytes, cutover_encoded: bytes, head_encoded: bytes,
) -> tuple[
    OwnershipPublicKeyFactsV1,
    OwnershipPublicKeyFactsV1,
    OwnershipPublicKeyFactsV1,
]:
    registries = (
        _decode_ownership_registry_v1(distribution_encoded, "distribution"),
        _decode_ownership_registry_v1(cutover_encoded, "cutover"),
        _decode_ownership_registry_v1(head_encoded, "head"),
    )
    if len({item.raw_public_key for item in registries}) != len(registries):
        raise _invalid("shared ownership registry key")
    return registries


def _decode_distribution_registry_v1(encoded: bytes) -> DistributionPublicKeyV1:
    facts = _decode_ownership_registry_v1(encoded, "distribution")
    return DistributionPublicKeyV1(facts.key_id, facts.raw_public_key)


def _nullable_digest_v1(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_digest(value, field)


def _cutover_catalog_id_v1(receipts: Sequence[dict[str, object]]) -> str:
    material = bytearray()
    for receipt in receipts:
        encoded = _canonical_json(receipt)
        material.extend(len(encoded).to_bytes(8, "big"))
        material.extend(encoded)
    return _digest(CUTOVER_CATALOG_ID_DOMAIN_V1, bytes(material))


def _u64be_v1(value: int) -> bytes:
    if type(value) is not int or not 0 <= value <= (1 << 64) - 1:
        raise _invalid("u64")
    return value.to_bytes(8, "big", signed=False)


def _service_coverage_hash_v1(encoded: bytes) -> str:
    if type(encoded) is not bytes:
        raise _invalid("service catalog bytes")
    return _digest(SERVICE_COVERAGE_DOMAIN_V1, encoded)


def _target_executable_hash_v1(path: str, content: bytes) -> str:
    canonical = _catalog_absolute_path_v1(path, "target executable")
    if type(content) is not bytes:
        raise _invalid("target executable bytes")
    encoded_path = canonical.encode("utf-8")
    return _digest(
        TARGET_EXECUTABLE_DOMAIN_V1,
        _u64be_v1(len(encoded_path)) + encoded_path
        + _u64be_v1(len(content)) + content,
    )


def _framed_system_file_hash_v1(
    domain: bytes, path: str, content: bytes, detail: str,
) -> str:
    canonical = _catalog_absolute_path_v1(path, detail)
    if type(domain) is not bytes or not domain or type(content) is not bytes:
        raise _invalid(detail)
    encoded_path = canonical.encode("utf-8")
    return _digest(
        domain,
        _u64be_v1(len(encoded_path)) + encoded_path
        + _u64be_v1(len(content)) + content,
    )


def _administrative_executable_hash_v1(path: str, content: bytes) -> str:
    return _framed_system_file_hash_v1(
        ADMINISTRATIVE_EXECUTABLE_DOMAIN_V1, path, content,
        "administrative executable",
    )


def _openssl_tcb_file_hash_v1(path: str, content: bytes) -> str:
    return _framed_system_file_hash_v1(
        OPENSSL_TCB_FILE_DOMAIN_V1, path, content, "OpenSSL TCB file",
    )


def _service_fragment_hash_v1(unit_name: str, fragment: bytes) -> str:
    if (
        not isinstance(unit_name, str)
        or _PREDECESSOR_UNIT_RE_V1.fullmatch(unit_name) is None
        or type(fragment) is not bytes
    ):
        raise _invalid("unit fragment")
    encoded_name = unit_name.encode("utf-8")
    return _digest(
        SYSTEMD_FRAGMENT_DOMAIN_V1,
        _u64be_v1(len(encoded_name)) + encoded_name
        + _u64be_v1(len(fragment)) + fragment,
    )


def _catalog_relative_path_v1(value: object, detail: str) -> str:
    if (
        not isinstance(value, str) or not value or "\0" in value
        or "\\" in value
    ):
        raise _invalid(detail)
    path = PurePosixPath(value)
    if (
        path.is_absolute() or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or len(path.parts) > 32
    ):
        raise _invalid(detail)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(detail) from exc
    return value


def _catalog_absolute_path_v1(value: object, detail: str) -> str:
    if (
        not isinstance(value, str) or not value or "\0" in value
        or "\\" in value
    ):
        raise _invalid(detail)
    path = PurePosixPath(value)
    if (
        not path.is_absolute() or value != path.as_posix()
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise _invalid(detail)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(detail) from exc
    return value


def _service_safe_scalar_v1(value: object, detail: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or "\0" in value or "\n" in value or "\r" in value or "%" in value
        or '"' in value or "'" in value or value.startswith(("#", ";"))
        or "\\" in value or len(value.encode("utf-8")) > 4096
    ):
        raise _invalid(detail)
    return value


def _validate_catalog_environment_name_v1(
    name: object, *, target: bool,
) -> str:
    if (
        not isinstance(name, str)
        or _CATALOG_ENVIRONMENT_RE_V1.fullmatch(name) is None
    ):
        raise _invalid("environment name")
    if target and (
        name in {"PATH", "HOME", "SHELL", "VIRTUAL_ENV"}
        or name.startswith(("PYTHON", "LD_", "DYLD_", "OPENSSL_"))
        or name in _PREDECESSOR_FORBIDDEN_ENVIRONMENT_NAMES_V1
    ):
        raise _invalid("forbidden environment")
    return name


def _validate_service_directive_v1(
    section: object, name: object, value_type: object, values: object,
) -> _ServiceDirectiveV1:
    if not isinstance(section, str) or not isinstance(name, str):
        raise _invalid("service directive")
    expected_type = _SERVICE_CATALOG_DIRECTIVE_TYPES_V1.get((section, name))
    if expected_type is None or value_type != expected_type:
        raise _invalid("service directive type")
    if not isinstance(values, list) or not values or len(values) > 128:
        raise _invalid("service directive values")
    parsed = tuple(values)
    if any(not isinstance(value, str) for value in parsed):
        raise _invalid("service directive value")
    if expected_type == "scalar":
        if len(parsed) != 1:
            raise _invalid("service scalar cardinality")
        _service_safe_scalar_v1(parsed[0], "service scalar")
    elif expected_type == "boolean":
        if len(parsed) != 1 or parsed[0] not in {"yes", "no"}:
            raise _invalid("service boolean")
    elif expected_type == "duration":
        if (
            len(parsed) != 1
            or _CATALOG_DURATION_RE_V1.fullmatch(parsed[0]) is None
        ):
            raise _invalid("service duration")
    elif expected_type == "integer":
        if (
            len(parsed) != 1
            or re.fullmatch(r"(?:0|-?[1-9][0-9]*)", parsed[0]) is None
        ):
            raise _invalid("service integer")
    elif expected_type == "argv":
        if len(parsed) > 32 or any(
            _CATALOG_SAFE_TOKEN_RE_V1.fullmatch(value) is None
            or "%" in value or len(value.encode("utf-8")) > 4096
            for value in parsed
        ) or any(value.startswith("!") for value in parsed[1:]):
            raise _invalid("service argv")
    elif expected_type == "environment":
        names: list[str] = []
        for value in parsed:
            _service_safe_scalar_v1(value, "service environment")
            if "=" not in value or any(character.isspace() for character in value):
                raise _invalid("service environment")
            variable, _raw = value.split("=", 1)
            names.append(_validate_catalog_environment_name_v1(
                variable, target=False,
            ))
        if (
            names != sorted(names, key=lambda item: item.encode("utf-8"))
            or len(names) != len(set(names))
        ):
            raise _invalid("service environment order")
    elif expected_type == "unit_list":
        if any(
            _PREDECESSOR_UNIT_RE_V1.fullmatch(value) is None
            for value in parsed
        ) or parsed != tuple(sorted(
            set(parsed), key=lambda item: item.encode("utf-8"),
        )):
            raise _invalid("service unit list")
    elif expected_type == "path_list":
        for value in parsed:
            _catalog_absolute_path_v1(value, "service path list")
            if (
                "%" in value or '"' in value or "'" in value
                or any(character.isspace() for character in value)
            ):
                raise _invalid("service path specifier")
        if parsed != tuple(sorted(
            set(parsed), key=lambda item: item.encode("utf-8"),
        )):
            raise _invalid("service path list order")
    return _ServiceDirectiveV1(section, name, str(value_type), parsed)


def _service_directive_sort_key_v1(
    directive: _ServiceDirectiveV1,
) -> tuple[int, bytes]:
    return (
        _SERVICE_CATALOG_SECTION_ORDER_V1[directive.section],
        directive.name.encode("utf-8"),
    )


def _render_service_directives_v1(
    directives: tuple[_ServiceDirectiveV1, ...],
) -> bytes:
    lines: list[str] = []
    current_section: str | None = None
    for directive in directives:
        if directive.section != current_section:
            if current_section is not None:
                lines.append("")
            current_section = directive.section
            lines.append(f"[{current_section}]")
        rendered = (
            " ".join(directive.values)
            if directive.value_type in {
                "argv", "environment", "unit_list", "path_list",
            }
            else directive.values[0]
        )
        lines.append(f"{directive.name}={rendered}")
    if not lines:
        raise _invalid("empty service unit")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _make_service_unit_spec_v1(
    unit_name: str, directives: object,
) -> _ServiceUnitSpecV1:
    if (
        not isinstance(unit_name, str)
        or _PREDECESSOR_UNIT_RE_V1.fullmatch(unit_name) is None
        or not isinstance(directives, list)
    ):
        raise _invalid("service unit specification")
    parsed: list[_ServiceDirectiveV1] = []
    for raw in directives:
        if (
            not isinstance(raw, dict)
            or set(raw) != _SERVICE_CATALOG_DIRECTIVE_KEYS_V1
        ):
            raise _invalid("service directive schema")
        parsed.append(_validate_service_directive_v1(
            raw.get("section"), raw.get("name"), raw.get("value_type"),
            raw.get("values"),
        ))
    expected = sorted(parsed, key=_service_directive_sort_key_v1)
    keys = tuple((item.section, item.name) for item in parsed)
    if parsed != expected or len(keys) != len(set(keys)):
        raise _invalid("service directive order")
    fragment = _render_service_directives_v1(tuple(parsed))
    if len(fragment) > MAX_UNIT_FRAGMENT_BYTES_V1:
        raise _invalid("service unit size")
    return _ServiceUnitSpecV1(
        _service_fragment_hash_v1(unit_name, fragment), tuple(parsed),
    )


def _parse_service_unit_fragment_v1(
    unit_name: str, fragment: bytes,
) -> _ServiceUnitSpecV1:
    """Independently parse the exact closed renderer output."""
    if (
        type(fragment) is not bytes or not fragment
        or len(fragment) > MAX_UNIT_FRAGMENT_BYTES_V1
    ):
        raise _invalid("service unit size")
    try:
        text = fragment.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid("service unit encoding") from exc
    if (
        not text.endswith("\n") or text.endswith("\n\n")
        or "\r" in text or "\\\n" in text
    ):
        raise _invalid("service unit framing")
    section: str | None = None
    seen_sections: set[str] = set()
    last_order = -1
    documents: list[dict[str, object]] = []
    for line in text[:-1].split("\n"):
        if not line:
            section = None
            continue
        if line.startswith(("#", ";")) or "%" in line or line != line.strip():
            raise _invalid("service unit line")
        if line.startswith("["):
            if (
                not line.endswith("]")
                or line[1:-1] not in _SERVICE_CATALOG_SECTION_ORDER_V1
            ):
                raise _invalid("service unit section")
            candidate = line[1:-1]
            order = _SERVICE_CATALOG_SECTION_ORDER_V1[candidate]
            if candidate in seen_sections or order <= last_order:
                raise _invalid("service section order")
            seen_sections.add(candidate)
            last_order = order
            section = candidate
            continue
        if section is None or "=" not in line:
            raise _invalid("service unit syntax")
        name, raw_value = line.split("=", 1)
        value_type = _SERVICE_CATALOG_DIRECTIVE_TYPES_V1.get((section, name))
        if value_type is None or not raw_value:
            raise _invalid("service unit directive")
        values = (
            raw_value.split(" ")
            if value_type in {"argv", "environment", "unit_list", "path_list"}
            else [raw_value]
        )
        if any(not value for value in values):
            raise _invalid("service unit spacing")
        documents.append({
            "section": section, "name": name,
            "value_type": value_type, "values": values,
        })
    parsed = _make_service_unit_spec_v1(unit_name, documents)
    if _render_service_directives_v1(parsed.directives) != fragment:
        raise _invalid("service unit canonicality")
    return parsed


def _service_entry_scope_v1(class_name: str) -> str:
    if class_name in {"gated_service", "gated_timer", "stop_only", "target"}:
        return "system"
    if class_name == "external_dependency":
        return "external"
    return "administrative"


def _service_unit_suffix_v1(class_name: str) -> str | None:
    return {
        "gated_service": ".service",
        "gated_timer": ".timer",
        "stop_only": ".service",
        "target": ".target",
    }.get(class_name)


def _parse_service_target_environment_v1(
    value: object,
) -> tuple[_ServiceEnvironmentV1, ...]:
    if not isinstance(value, list) or len(value) > 256:
        raise _invalid("service target environment")
    result: list[_ServiceEnvironmentV1] = []
    for raw in value:
        if (
            not isinstance(raw, dict)
            or set(raw) != _SERVICE_CATALOG_ENVIRONMENT_KEYS_V1
        ):
            raise _invalid("service target environment schema")
        name = _validate_catalog_environment_name_v1(
            raw.get("name"), target=True,
        )
        environment_value = raw.get("value")
        if (
            not isinstance(environment_value, str) or "\0" in environment_value
            or len(environment_value.encode("utf-8")) > 16 * 1024
        ):
            raise _invalid("service target environment value")
        result.append(_ServiceEnvironmentV1(name, environment_value))
    names = tuple(item.name for item in result)
    if (
        names != tuple(sorted(names, key=lambda item: item.encode("utf-8")))
        or len(names) != len(set(names))
    ):
        raise _invalid("service target environment order")
    return tuple(result)


def _service_directive_index_v1(
    unit_spec: _ServiceUnitSpecV1 | None,
) -> dict[tuple[str, str], _ServiceDirectiveV1]:
    return {
        (item.section, item.name): item
        for item in (() if unit_spec is None else unit_spec.directives)
    }


def _require_gated_service_shape_v1(entry: _ServiceCatalogEntryV1) -> None:
    directives = _service_directive_index_v1(entry.unit_spec)
    required = {
        ("Service", "User"), ("Service", "Group"),
        ("Service", "ExecStartPre"), ("Service", "ExecStart"),
        ("Service", "WorkingDirectory"), ("Service", "KillMode"),
        ("Service", "CapabilityBoundingSet"),
        ("Service", "NoNewPrivileges"),
        ("Service", "ReadWritePaths"),
    }
    if (
        not required.issubset(directives)
        or ("Service", "Environment") in directives
        or ("Service", "ExecStop") in directives
    ):
        raise _invalid("gated service directives")
    if directives[("Service", "WorkingDirectory")].values != ("/",):
        raise _invalid("gated service working directory")
    if directives[("Service", "KillMode")].values != ("control-group",):
        raise _invalid("gated service kill mode")
    if (
        directives[("Service", "CapabilityBoundingSet")].values
        != ("CAP_SETGID CAP_SETPCAP CAP_SETUID",)
        or directives[("Service", "NoNewPrivileges")].values != ("yes",)
    ):
        raise _invalid("gated service launcher capabilities")
    # The gate this unit runs before its payload verifies signatures through
    # openssl, in a temporary directory under the product's runtime root. A
    # hardened unit mounts the hierarchy read-only, so a unit that does not
    # declare that root writable dies at launch with the generic recovery code
    # and no reason at all — measured on the live cell. Refusing here names it
    # at capture time instead. The grant gives the demoted payload nothing:
    # the root stays `0700` root-owned and discretionary permissions apply.
    if RUNTIME_ROOT.as_posix() not in (
        directives[("Service", "ReadWritePaths")].values
    ):
        raise _invalid("gated service writable roots")
    group = directives[("Service", "Group")].values[0]
    if _INTEGER_RE.fullmatch(group) is None or group == "0":
        raise _invalid("gated service gid")
    supplementary = directives.get(("Service", "SupplementaryGroups"))
    if supplementary is not None:
        groups = supplementary.values[0].split(" ")
        if (
            any(_INTEGER_RE.fullmatch(item) is None or item == "0" for item in groups)
            or groups != sorted(set(groups), key=int)
        ):
            raise _invalid("gated service supplementary groups")
    check = directives[("Service", "ExecStartPre")].values
    launch = directives[("Service", "ExecStart")].values
    expected_tail = ("-I", "-S", ADMINISTRATIVE_ADAPTER_PATH_V1)
    if (
        len(check) != 7 or len(launch) != 7
        or not check[0].startswith("!/") or launch[0] != check[0]
        or check[1:4] != expected_tail or launch[1:4] != expected_tail
        or check[4:] != ("check", "--entry-id", entry.entry_id)
        or launch[4:] != ("launch", "--entry-id", entry.entry_id)
    ):
        raise _invalid("gated service administrative command")


def _parse_service_catalog_entry_v1(value: object) -> _ServiceCatalogEntryV1:
    if (
        not isinstance(value, dict)
        or set(value) != _SERVICE_CATALOG_ENTRY_KEYS_V1
    ):
        raise _invalid("service entry schema")
    entry_id = value.get("entry_id")
    class_name = value.get("class")
    if (
        not isinstance(entry_id, str) or _ENTRY_ID_RE.fullmatch(entry_id) is None
        or not isinstance(class_name, str)
        or class_name not in _SERVICE_CATALOG_CLASSES_V1
        or value.get("scope") != _service_entry_scope_v1(class_name)
    ):
        raise _invalid("service entry identity")
    suffix = _service_unit_suffix_v1(class_name)
    unit_name = value.get("unit_name")
    if suffix is None:
        if unit_name is not None:
            raise _invalid("service unit nullability")
    elif (
        not isinstance(unit_name, str)
        or _PREDECESSOR_UNIT_RE_V1.fullmatch(unit_name) is None
        or not unit_name.endswith(suffix)
        or len(unit_name.encode("utf-8")) > 192
    ):
        raise _invalid("service unit name")
    external_unit_name = value.get("external_unit_name")
    if class_name == "external_dependency":
        if (
            not isinstance(external_unit_name, str)
            or _PREDECESSOR_UNIT_RE_V1.fullmatch(external_unit_name) is None
        ):
            raise _invalid("service external unit")
    elif external_unit_name is not None:
        raise _invalid("service external unit nullability")
    adapter_path = value.get("adapter_path")
    if class_name == "gated_entrypoint":
        if adapter_path != ADMINISTRATIVE_ADAPTER_PATH_V1:
            raise _invalid("service adapter path")
    elif adapter_path is not None:
        raise _invalid("service adapter nullability")
    execution_kind = value.get("execution_kind")
    if (
        not isinstance(execution_kind, str)
        or execution_kind not in _SERVICE_CATALOG_EXECUTION_KINDS_V1
    ):
        raise _invalid("service execution kind")
    if class_name in {"gated_service", "gated_entrypoint"}:
        if execution_kind not in {"python_module", "native_executable"}:
            raise _invalid("service executable class")
    elif class_name == "stop_only":
        if execution_kind != "systemctl_stop":
            raise _invalid("service stop execution")
    elif execution_kind != "none":
        raise _invalid("service non-executable class")
    target_executable = value.get("target_executable")
    target_hash = value.get("target_executable_hash")
    if execution_kind == "none":
        if target_executable is not None or target_hash is not None:
            raise _invalid("service target nullability")
    else:
        target_executable = _catalog_absolute_path_v1(
            target_executable, "service target executable",
        )
        target_hash = _require_digest(target_hash, "service target hash")
    python_module = value.get("python_module")
    if execution_kind == "python_module":
        if (
            not isinstance(python_module, str)
            or _CATALOG_MODULE_RE_V1.fullmatch(python_module) is None
            or len(python_module.encode("utf-8")) > 255
        ):
            raise _invalid("service Python module")
    elif python_module is not None:
        raise _invalid("service Python module nullability")
    target_args = value.get("target_args")
    if not isinstance(target_args, list) or len(target_args) > 28 or any(
        not isinstance(item, str) or "\0" in item
        or len(item.encode("utf-8")) > 4096
        for item in target_args
    ):
        raise _invalid("service target arguments")
    target_environment = _parse_service_target_environment_v1(
        value.get("target_environment"),
    )
    working_directory = value.get("target_working_directory")
    if execution_kind == "none":
        if target_args or target_environment or working_directory is not None:
            raise _invalid("service none target fields")
    else:
        working_directory = _catalog_absolute_path_v1(
            working_directory, "service target working directory",
        )
    if execution_kind == "systemctl_stop":
        if (
            working_directory != "/" or len(target_args) < 2
            or target_args[0] != "stop"
        ):
            raise _invalid("service stop command")
        stop_units = target_args[1:]
        if (
            any(
                _PREDECESSOR_UNIT_RE_V1.fullmatch(item) is None
                for item in stop_units
            )
            or stop_units != sorted(
                set(stop_units), key=lambda item: item.encode("utf-8"),
            )
            or target_environment
        ):
            raise _invalid("service stop units")
    timer_target = value.get("timer_target")
    if class_name == "gated_timer":
        if (
            not isinstance(timer_target, str)
            or _ENTRY_ID_RE.fullmatch(timer_target) is None
        ):
            raise _invalid("service timer target")
    elif timer_target is not None:
        raise _invalid("service timer nullability")
    raw_spec = value.get("unit_spec")
    if class_name in {"gated_entrypoint", "external_dependency"}:
        if raw_spec is not None:
            raise _invalid("service unit specification nullability")
        unit_spec = None
    else:
        if (
            not isinstance(raw_spec, dict)
            or set(raw_spec) != _SERVICE_CATALOG_UNIT_SPEC_KEYS_V1
        ):
            raise _invalid("service unit specification schema")
        unit_spec = _make_service_unit_spec_v1(
            str(unit_name), raw_spec.get("directives"),
        )
        if _require_digest(
            raw_spec.get("fragment_hash"), "service fragment hash",
        ) != unit_spec.fragment_hash:
            raise _invalid("service fragment hash")
    requires_preflight = value.get("requires_preflight")
    readiness_owner = value.get("readiness_owner")
    if (
        type(requires_preflight) is not bool
        or requires_preflight
        != (class_name in {"gated_service", "gated_entrypoint"})
        or type(readiness_owner) is not bool
        or (readiness_owner and class_name != "gated_service")
    ):
        raise _invalid("service preflight/readiness flags")
    entry = _ServiceCatalogEntryV1(
        entry_id, unit_name, external_unit_name, adapter_path, class_name,
        str(value["scope"]), execution_kind, target_executable, target_hash,
        python_module, tuple(target_args), working_directory,
        target_environment, timer_target, unit_spec, requires_preflight,
        readiness_owner,
    )
    directives = _service_directive_index_v1(unit_spec)
    if class_name == "gated_service":
        _require_gated_service_shape_v1(entry)
    elif ("Service", "ExecStartPre") in directives:
        raise _invalid("unexpected service preflight command")
    if class_name == "stop_only":
        start = directives.get(("Service", "ExecStart"))
        allowed = {
            ("Unit", "Description"), ("Service", "ExecStart"),
            ("Service", "SyslogIdentifier"),
            ("Service", "TimeoutStartSec"), ("Service", "Type"),
        }
        if (
            start is None
            or start.values != (str(target_executable), *tuple(target_args))
            or ("Service", "ExecStop") in directives
            or not set(directives).issubset(allowed)
        ):
            raise _invalid("service stop unit command")
    elif class_name in {"gated_timer", "target"} and any(
        name in {"ExecStart", "ExecStop"} for _section, name in directives
    ):
        raise _invalid("unexpected service command")
    return entry


def _validate_legacy_locator_v1(kind: str, value: object) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise _invalid("service legacy locator")
    if kind in {"user_unit", "system_unit"}:
        if _PREDECESSOR_UNIT_RE_V1.fullmatch(value) is None:
            raise _invalid("service legacy unit")
        return value
    if value.startswith("/"):
        return _catalog_absolute_path_v1(value, "service legacy locator")
    return _catalog_relative_path_v1(value, "service legacy locator")


def _parse_service_legacy_binding_v1(
    value: object, entry_ids: frozenset[str],
) -> _ServiceLegacyBindingV1:
    if (
        not isinstance(value, dict)
        or set(value) != _SERVICE_CATALOG_LEGACY_KEYS_V1
    ):
        raise _invalid("service legacy schema")
    legacy_id = value.get("legacy_id")
    entry_id = value.get("entry_id")
    kind = value.get("kind")
    scope = value.get("scope")
    if (
        not isinstance(legacy_id, str)
        or _ENTRY_ID_RE.fullmatch(legacy_id) is None
        or not isinstance(entry_id, str) or entry_id not in entry_ids
        or not isinstance(kind, str)
        or kind not in _SERVICE_CATALOG_LEGACY_KINDS_V1
        or not isinstance(scope, str)
        or scope not in _SERVICE_CATALOG_LEGACY_SCOPES_V1
        or value.get("disposition") != "retire_in_group7"
        or (kind == "user_unit" and scope != "user")
        or (kind == "system_unit" and scope != "system")
    ):
        raise _invalid("service legacy binding")
    return _ServiceLegacyBindingV1(
        legacy_id, entry_id, kind, scope,
        _validate_legacy_locator_v1(kind, value.get("locator")),
        "retire_in_group7",
    )


def _decode_service_catalog_v1(encoded: bytes) -> _DecodedServiceCatalogV1:
    value = decode_canonical_json_v1(encoded, MAX_SERVICE_CATALOG_BYTES_V1)
    if (
        not isinstance(value, dict) or set(value) != _SERVICE_CATALOG_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("service catalog schema")
    declared_id = _require_digest(value.get("catalog_id"), "service catalog id")
    unsigned = dict(value)
    unsigned.pop("catalog_id")
    if declared_id != _digest(
        SERVICE_CATALOG_ID_DOMAIN_V1, _canonical_json(unsigned),
    ):
        raise _invalid("service catalog id")
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise _invalid("service catalog entries")
    entries = tuple(_parse_service_catalog_entry_v1(item) for item in raw_entries)
    entry_ids = tuple(item.entry_id for item in entries)
    if (
        entry_ids != tuple(sorted(
            entry_ids, key=lambda item: item.encode("utf-8"),
        ))
        or len(entry_ids) != len(set(entry_ids))
    ):
        raise _invalid("service entry order")
    unit_names = tuple(
        item.unit_name for item in entries if item.unit_name is not None
    ) + tuple(
        item.external_unit_name
        for item in entries if item.external_unit_name is not None
    )
    if len(unit_names) != len(set(unit_names)):
        raise _invalid("service unit duplicate")
    by_id = {item.entry_id: item for item in entries}
    for item in entries:
        if item.class_name != "gated_timer":
            continue
        target = by_id.get(item.timer_target or "")
        directive = _service_directive_index_v1(item.unit_spec).get(
            ("Timer", "Unit"),
        )
        if (
            target is None or target.class_name != "gated_service"
            or directive is None or directive.values != (target.unit_name,)
        ):
            raise _invalid("service timer relation")
    if sum(item.readiness_owner for item in entries) != 1:
        raise _invalid("service readiness owner")
    raw_legacy = value.get("legacy_bindings")
    if not isinstance(raw_legacy, list):
        raise _invalid("service legacy bindings")
    legacy = tuple(
        _parse_service_legacy_binding_v1(item, frozenset(entry_ids))
        for item in raw_legacy
    )
    legacy_ids = tuple(item.legacy_id for item in legacy)
    if (
        legacy_ids != tuple(sorted(
            legacy_ids, key=lambda item: item.encode("utf-8"),
        ))
        or len(legacy_ids) != len(set(legacy_ids))
    ):
        raise _invalid("service legacy order")
    return _DecodedServiceCatalogV1(
        declared_id, entries, legacy, encoded, _service_coverage_hash_v1(encoded),
    )


def _bound_path_projection_v1(
    value: str, root: str, marker: str, detail: str,
) -> dict[str, str]:
    """Project one already-canonical path onto its signed context root."""
    if value == root:
        suffix = ""
    elif value.startswith(root + "/"):
        suffix = value[len(root):]
    else:
        raise _invalid(detail)
    # A typed projection cannot collide with any catalog field involved here:
    # the validated source schema admits strings only at those positions.
    return {"binding": marker, "suffix": suffix}


def _service_source_identity_v1(
    catalog: _DecodedServiceCatalogV1,
    descriptor: _DecodedDeploymentDescriptorV1,
) -> str:
    """Freeze the complete V1 recipe while abstracting signed host context.

    This autonomous fingerprint is the stdlib-only equivalent of the
    canonical catalog compiler's ``_source_identity`` check.  Dynamic values
    are first required to equal the deployment descriptor and then replaced
    by unambiguous markers; every remaining byte is fixed V1 topology.
    """
    if (
        type(catalog) is not _DecodedServiceCatalogV1
        or type(descriptor) is not _DecodedDeploymentDescriptorV1
    ):
        raise _invalid("service source identity arguments")
    if (
        descriptor.service_home == descriptor.installation_root
        or descriptor.service_home.startswith(descriptor.installation_root + "/")
    ):
        # Release bytes are root-owned and immutable; the account home and
        # writable data/cache hierarchy cannot live inside that tree.
        raise _invalid("service home inside installation root")
    try:
        document = json.loads(catalog.encoded.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid("service source identity encoding") from exc
    if not isinstance(document, dict):
        raise _invalid("service source identity document")
    document.pop("catalog_id", None)
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) != len(catalog.entries):
        raise _invalid("service source identity entries")

    target_hashes: dict[str, str] = {}
    supplementary = " ".join(
        str(value) for value in descriptor.service_supplementary_gids
    )
    for raw_entry, entry in zip(raw_entries, catalog.entries, strict=True):
        if not isinstance(raw_entry, dict):
            raise _invalid("service source identity entry")
        executable = entry.target_executable
        if executable is not None:
            if entry.target_executable_hash is None:
                raise _invalid("service target executable hash")
            previous = target_hashes.setdefault(
                executable, entry.target_executable_hash,
            )
            if previous != entry.target_executable_hash:
                raise _invalid("service target hash alias")
            raw_entry["target_executable_hash"] = {
                "binding": "target-executable-hash",
            }
            if entry.execution_kind == "python_module":
                if executable != descriptor.python_executable:
                    raise _invalid("service Python executable binding")
                raw_entry["target_executable"] = {
                    "binding": "python-executable",
                }
            elif entry.execution_kind == "systemctl_stop":
                if executable != descriptor.systemctl_executable:
                    raise _invalid("service systemctl executable binding")
                raw_entry["target_executable"] = {
                    "binding": "systemctl-executable",
                }
            elif executable == descriptor.python_executable:
                raise _invalid("service native Python alias")
            elif executable == descriptor.systemctl_executable:
                raise _invalid("service native systemctl alias")
            elif executable == descriptor.installation_root or executable.startswith(
                descriptor.installation_root + "/"
            ):
                raw_entry["target_executable"] = _bound_path_projection_v1(
                    executable, descriptor.installation_root,
                    "installation-root", "service target executable root",
                )

        working_directory = entry.target_working_directory
        if (
            working_directory is not None
            and entry.execution_kind in {"python_module", "native_executable"}
        ):
            raw_entry["target_working_directory"] = _bound_path_projection_v1(
                working_directory, descriptor.installation_root,
                "installation-root", "service target working directory",
            )

        raw_arguments = raw_entry.get("target_args")
        if not isinstance(raw_arguments, list):
            raise _invalid("service source target arguments")
        for index, argument in enumerate(entry.target_args):
            if argument == descriptor.installation_root or argument.startswith(
                descriptor.installation_root + "/"
            ):
                raw_arguments[index] = _bound_path_projection_v1(
                    argument, descriptor.installation_root,
                    "installation-root", "service target argument root",
                )
            elif argument == descriptor.service_home or argument.startswith(
                descriptor.service_home + "/"
            ):
                raw_arguments[index] = _bound_path_projection_v1(
                    argument, descriptor.service_home, "service-home",
                    "service target argument home",
                )

        raw_environment = raw_entry.get("target_environment")
        if not isinstance(raw_environment, list):
            raise _invalid("service source environment")
        for raw_variable, variable in zip(
            raw_environment, entry.target_environment, strict=True,
        ):
            if not isinstance(raw_variable, dict):
                raise _invalid("service source environment")
            value = variable.value
            if value == descriptor.service_home or value.startswith(
                descriptor.service_home + "/"
            ):
                raw_variable["value"] = _bound_path_projection_v1(
                    value, descriptor.service_home, "service-home",
                    "service home binding",
                )

        if entry.unit_spec is None:
            continue
        raw_spec = raw_entry.get("unit_spec")
        if not isinstance(raw_spec, dict):
            raise _invalid("service source unit specification")
        raw_spec["fragment_hash"] = {"binding": "fragment-hash"}
        raw_directives = raw_spec.get("directives")
        if not isinstance(raw_directives, list):
            raise _invalid("service source directives")
        if entry.class_name == "gated_service":
            filtered: list[object] = []
            seen_supplementary = False
            for raw_directive in raw_directives:
                if not isinstance(raw_directive, dict):
                    raise _invalid("service source directive")
                key = (raw_directive.get("section"), raw_directive.get("name"))
                values = raw_directive.get("values")
                if not isinstance(values, list):
                    raise _invalid("service source directive values")
                if key == ("Service", "User"):
                    if values != [descriptor.service_user]:
                        raise _invalid("service user binding")
                    raw_directive["values"] = [{"binding": "service-user"}]
                elif key == ("Service", "Group"):
                    if values != [str(descriptor.service_gid)]:
                        raise _invalid("service group binding")
                    raw_directive["values"] = [{"binding": "service-gid"}]
                elif key == ("Service", "SupplementaryGroups"):
                    seen_supplementary = True
                    if not supplementary or values != [supplementary]:
                        raise _invalid("service supplementary groups binding")
                    # The canonical compiler omits this source placeholder when
                    # the signed supplementary set is empty.  Removing it from
                    # both projections preserves one source identity.
                    continue
                elif key in {
                    ("Service", "ExecStartPre"),
                    ("Service", "ExecStart"),
                }:
                    expected = "!" + descriptor.python_executable
                    if not values or values[0] != expected:
                        raise _invalid("service administrative Python binding")
                    raw_directive["values"] = [
                        {"binding": "administrative-python"}, *values[1:],
                    ]
                filtered.append(raw_directive)
            if seen_supplementary is not bool(supplementary):
                raise _invalid("service supplementary groups coverage")
            raw_spec["directives"] = filtered
        elif entry.class_name == "stop_only":
            for raw_directive in raw_directives:
                if (
                    isinstance(raw_directive, dict)
                    and raw_directive.get("section") == "Service"
                    and raw_directive.get("name") == "ExecStart"
                ):
                    values = raw_directive.get("values")
                    if (
                        not isinstance(values, list) or not values
                        or values[0] != descriptor.systemctl_executable
                    ):
                        raise _invalid("service stop systemctl binding")
                    raw_directive["values"] = [
                        {"binding": "systemctl-executable"}, *values[1:],
                    ]

    identity = _digest(
        SERVICE_SOURCE_IDENTITY_DOMAIN_V1, _canonical_json(document),
    )
    if identity != _EXPECTED_SERVICE_SOURCE_IDENTITY_V1:
        _require_isolated_g6c_source_recipe_v1(catalog, descriptor)
        return _ISOLATED_G6C_SOURCE_IDENTITY_V1
    return identity


def _isolated_g6c_namespace_v1(
    catalog: _DecodedServiceCatalogV1,
) -> str | None:
    """Recognize only the two-entry, fully prefixed disposable C3 topology."""
    if (
        type(catalog) is not _DecodedServiceCatalogV1
        or catalog.legacy_bindings
    ):
        return None
    if len(catalog.entries) != 2:
        return None
    service, timer = catalog.entries
    prefix = "g6c-"
    suffix = "-probe"
    if (
        service.class_name != "gated_service"
        or timer.class_name != "gated_timer"
        or not service.entry_id.startswith(prefix)
        or not service.entry_id.endswith(suffix)
    ):
        return None
    namespace = service.entry_id[len(prefix):-len(suffix)]
    unit_prefix = f"metnos-g6c-{namespace}-probe"
    if (
        _ISOLATED_G6C_NAMESPACE_RE_V1.fullmatch(namespace) is None
        or service.unit_name != unit_prefix + ".service"
        or timer.entry_id != service.entry_id + "-timer"
        or timer.unit_name != unit_prefix + ".timer"
        or timer.timer_target != service.entry_id
    ):
        return None
    return namespace


def _require_isolated_g6c_source_recipe_v1(
    catalog: _DecodedServiceCatalogV1,
    descriptor: _DecodedDeploymentDescriptorV1,
) -> str:
    """Require the sole signed topology admitted by the disposable C3 cell."""
    namespace = _isolated_g6c_namespace_v1(catalog)
    if namespace is None:
        raise _invalid("service source recipe")
    service, timer = catalog.entries
    marker_root = f"/run/metnos-g6c-{namespace}"
    marker_path = marker_root + "/marker.json"
    supplementary = " ".join(
        str(value) for value in descriptor.service_supplementary_gids
    )
    executable = "!" + descriptor.python_executable
    expected_service_directives = (
        ("Unit", "Description", "scalar", ("isolated signed G6-C probe",)),
        (
            "Service", "CapabilityBoundingSet", "scalar",
            ("CAP_SETGID CAP_SETPCAP CAP_SETUID",),
        ),
        (
            "Service", "ExecStart", "argv",
            (
                executable, "-I", "-S", ADMINISTRATIVE_ADAPTER_PATH_V1,
                "launch", "--entry-id", service.entry_id,
            ),
        ),
        (
            "Service", "ExecStartPre", "argv",
            (
                executable, "-I", "-S", ADMINISTRATIVE_ADAPTER_PATH_V1,
                "check", "--entry-id", service.entry_id,
            ),
        ),
        ("Service", "Group", "scalar", (str(descriptor.service_gid),)),
        ("Service", "KillMode", "scalar", ("control-group",)),
        ("Service", "NoNewPrivileges", "boolean", ("yes",)),
        ("Service", "PrivateTmp", "boolean", ("yes",)),
        ("Service", "ProtectSystem", "scalar", ("strict",)),
        (
            "Service", "ReadWritePaths", "path_list",
            # The runtime root as well as the marker: the gate this unit runs
            # before its payload verifies signatures through openssl in a
            # temporary directory there, and `ProtectSystem=strict` mounts
            # everything else read-only. Ordered by UTF-8 bytes.
            tuple(sorted(
                (marker_root, RUNTIME_ROOT.as_posix()),
                key=lambda item: item.encode("utf-8"),
            )),
        ),
        ("Service", "SupplementaryGroups", "scalar", (supplementary,)),
        ("Service", "Type", "scalar", ("oneshot",)),
        ("Service", "User", "scalar", (descriptor.service_user,)),
        ("Service", "WorkingDirectory", "path_list", ("/",)),
    )
    expected_timer_directives = (
        ("Unit", "Description", "scalar", ("isolated signed G6-C timer",)),
        ("Timer", "AccuracySec", "duration", ("1ms",)),
        ("Timer", "OnActiveSec", "duration", ("100ms",)),
        ("Timer", "Unit", "unit_list", (service.unit_name,)),
    )

    def directive_projection(
        entry: _ServiceCatalogEntryV1,
    ) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
        if entry.unit_spec is None:
            return ()
        return tuple(
            (item.section, item.name, item.value_type, item.values)
            for item in entry.unit_spec.directives
        )

    if (
        service.external_unit_name is not None or service.adapter_path is not None
        or service.scope != "system" or service.execution_kind != "python_module"
        or service.target_executable != descriptor.python_executable
        or service.target_executable_hash is None
        or service.python_module != "runtime.executor_birth_activation_probe"
        or service.target_args != (marker_path,)
        or service.target_working_directory != descriptor.installation_root
        or service.target_environment or not service.requires_preflight
        or not service.readiness_owner
        or timer.external_unit_name is not None or timer.adapter_path is not None
        or timer.scope != "system" or timer.execution_kind != "none"
        or timer.target_executable is not None
        or timer.target_executable_hash is not None
        or timer.python_module is not None or timer.target_args
        or timer.target_working_directory is not None
        or timer.target_environment or timer.requires_preflight
        or timer.readiness_owner
        or directive_projection(service) != expected_service_directives
        or directive_projection(timer) != expected_timer_directives
    ):
        raise _invalid("service source recipe")
    return namespace


def _deployment_relative_path_v1(value: object) -> str:
    path = _catalog_relative_path_v1(value, "deployment relative path")
    if (
        unicodedata.normalize("NFC", path) != path
        or path.split("/", 1)[0] == RECEIVED_SOURCE_DESCRIPTOR_BASENAME_V1
    ):
        raise _invalid("deployment relative path")
    return path


def _deployment_absolute_path_v1(
    value: object, detail: str, *, allow_root: bool = False,
) -> str:
    path = _catalog_absolute_path_v1(value, detail)
    if path == "/" and not allow_root:
        raise _invalid(detail)
    if path.startswith("//") or unicodedata.normalize("NFC", path) != path:
        raise _invalid(detail)
    return path


def _positive_identity_v1(value: object, detail: str) -> int:
    if type(value) is not int or not 0 < value < 2 ** 31:
        raise _invalid(detail)
    return value


def _positive_release_sequence_v1(value: object) -> int:
    if type(value) is not int or not 0 < value <= 2 ** 63 - 1:
        raise _invalid("release sequence")
    return value


def _bounded_file_size_v1(value: object, detail: str) -> int:
    if type(value) is not int or not 0 <= value <= 2 ** 63 - 1:
        raise _invalid(detail)
    return value


def _deployment_document_id_v1(
    domain: bytes, value: Mapping[str, object], field: str,
) -> str:
    return _digest(
        domain,
        _canonical_json({
            key: item for key, item in value.items() if key != field
        }),
    )


def _parse_deployment_artifact_v1(value: object) -> _DeploymentArtifactV1:
    if (
        not isinstance(value, dict)
        or set(value) != _DEPLOYMENT_ARTIFACT_KEYS_V1
    ):
        raise _invalid("deployment artifact schema")
    source = _deployment_relative_path_v1(value.get("source_path"))
    destination = _deployment_absolute_path_v1(
        value.get("destination_path"), "deployment artifact destination",
    )
    kind = value.get("kind")
    if source == DEPLOYMENT_DESCRIPTOR_PATH_V1:
        raise _invalid("deployment descriptor self reference")
    if not isinstance(kind, str) or kind not in _DEPLOYMENT_ARTIFACT_KINDS_V1:
        raise _invalid("deployment artifact kind")
    if kind == "administrative_program":
        phase = "group6_admin"
        mode = 0o755
        relative = destination.removeprefix(ADMINISTRATIVE_ROOT_TEXT_V1 + "/")
        if (
            relative != "preflight.py"
            or source != "deployment/admin/preflight.py"
        ):
            raise _invalid("deployment administrative artifact binding")
    else:
        phase = "group7_cutover"
        mode = 0o644
        relative = destination.removeprefix(SYSTEM_UNIT_ROOT_TEXT_V1 + "/")
        if (
            relative == destination or "/" in relative
            or source != f"deployment/systemd/{relative}"
            or len(relative.encode("utf-8")) > 192
            or _PREDECESSOR_UNIT_RE_V1.fullmatch(relative) is None
        ):
            raise _invalid("deployment unit artifact binding")
        expected_suffix = {
            "service_unit": ".service",
            "timer_unit": ".timer",
            "target_unit": ".target",
            "stop_only_unit": ".service",
        }[kind]
        if not relative.endswith(expected_suffix):
            raise _invalid("deployment unit artifact kind")
    if value.get("install_phase") != phase:
        raise _invalid("deployment artifact phase")
    size = _bounded_file_size_v1(value.get("size"), "deployment artifact size")
    if size > MAX_DISTRIBUTION_FILE_BYTES:
        raise _invalid("deployment artifact size")
    content_hash = _require_digest(
        value.get("content_hash"), "deployment artifact hash",
    )
    if (
        type(value.get("mode")) is not int or value.get("mode") != mode
        or type(value.get("uid")) is not int or value.get("uid") != 0
        or type(value.get("gid")) is not int or value.get("gid") != 0
    ):
        raise _invalid("deployment artifact metadata")
    return _DeploymentArtifactV1(
        source, destination, kind, phase, size, content_hash, mode, 0, 0,
    )


def _decode_deployment_descriptor_v1(
    encoded: bytes,
) -> _DecodedDeploymentDescriptorV1:
    value = decode_canonical_json_v1(
        encoded, MAX_DEPLOYMENT_DESCRIPTOR_BYTES_V1,
    )
    if (
        not isinstance(value, dict)
        or set(value) != _DEPLOYMENT_DESCRIPTOR_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("deployment descriptor schema")
    sequence = _positive_release_sequence_v1(value.get("release_sequence"))
    expected_installation_root = (
        RELEASE_ROOT / f"{sequence:020d}"
    ).as_posix()
    if value.get("installation_root") != expected_installation_root:
        raise _invalid("deployment installation root")
    service_user = value.get("service_user")
    if (
        not isinstance(service_user, str)
        or _SERVICE_ACCOUNT_RE_V1.fullmatch(service_user) is None
    ):
        raise _invalid("deployment service user")
    service_uid = _positive_identity_v1(
        value.get("service_uid"), "deployment service uid",
    )
    service_gid = _positive_identity_v1(
        value.get("service_gid"), "deployment service gid",
    )
    raw_supplementary = value.get("service_supplementary_gids")
    if not isinstance(raw_supplementary, list):
        raise _invalid("deployment supplementary gids")
    supplementary = tuple(
        _positive_identity_v1(item, "deployment supplementary gid")
        for item in raw_supplementary
    )
    if (
        supplementary != tuple(sorted(set(supplementary)))
        or service_gid not in supplementary
    ):
        raise _invalid("deployment supplementary gids")
    service_home = _deployment_absolute_path_v1(
        value.get("service_home"), "deployment service home",
    )
    service_shell = _deployment_absolute_path_v1(
        value.get("service_shell"), "deployment service shell",
    )
    if PurePosixPath(service_shell).name not in {"nologin", "false"}:
        raise _invalid("deployment service shell")
    if (
        value.get("administrative_root") != ADMINISTRATIVE_ROOT_TEXT_V1
        or value.get("system_unit_root") != SYSTEM_UNIT_ROOT_TEXT_V1
    ):
        raise _invalid("deployment fixed roots")
    raw_artifacts = value.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise _invalid("deployment artifacts")
    artifacts = tuple(_parse_deployment_artifact_v1(item) for item in raw_artifacts)
    if (
        not artifacts or len(artifacts) > MAX_DEPLOYMENT_ARTIFACTS_V1
        or sum(item.kind == "administrative_program" for item in artifacts) != 1
        or not any(item.kind != "administrative_program" for item in artifacts)
    ):
        raise _invalid("deployment artifact coverage")
    destinations = tuple(item.destination_path for item in artifacts)
    sources = tuple(item.source_path for item in artifacts)
    if (
        destinations != tuple(sorted(
            destinations, key=lambda item: item.encode("utf-8"),
        ))
        or len(destinations) != len(set(destinations))
        or len(sources) != len(set(sources))
    ):
        raise _invalid("deployment artifact order")
    catalog_id = _require_digest(
        value.get("service_catalog_id"), "deployment catalog id",
    )
    coverage_hash = _require_digest(
        value.get("service_coverage_hash"), "deployment coverage hash",
    )
    executables = tuple(
        _deployment_absolute_path_v1(value.get(field), detail)
        for field, detail in (
            ("python_executable", "deployment Python executable"),
            ("openssl_executable", "deployment OpenSSL executable"),
            ("systemctl_executable", "deployment systemctl executable"),
            (
                "systemd_analyze_executable",
                "deployment systemd-analyze executable",
            ),
        )
    )
    descriptor_id = _require_digest(
        value.get("descriptor_id"), "deployment descriptor id",
    )
    if descriptor_id != _deployment_document_id_v1(
        DEPLOYMENT_DESCRIPTOR_ID_DOMAIN_V1, value, "descriptor_id",
    ):
        raise _invalid("deployment descriptor id")
    return _DecodedDeploymentDescriptorV1(
        descriptor_id, sequence, expected_installation_root, service_user,
        service_uid, service_gid, supplementary, service_home, service_shell,
        ADMINISTRATIVE_ROOT_TEXT_V1, SYSTEM_UNIT_ROOT_TEXT_V1, artifacts,
        catalog_id, coverage_hash, executables[0], executables[1],
        executables[2], executables[3],
    )


def _decode_startup_prerequisite_v1(
    encoded: bytes,
) -> _DecodedStartupPrerequisiteV1:
    value = decode_canonical_json_v1(
        encoded, MAX_STARTUP_PREREQUISITE_BYTES_V1,
    )
    if (
        not isinstance(value, dict)
        or set(value) != _STARTUP_PREREQUISITE_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("startup prerequisite schema")
    version = value.get("systemd_manager_version")
    if (
        not isinstance(version, str) or not version
        or len(version.encode("utf-8")) > 128
        or (version_match := _SYSTEMD_VERSION_RE_V1.fullmatch(version)) is None
        or version_match.group("major") != "255"
    ):
        raise _invalid("startup prerequisite systemd version")
    sequence = _positive_release_sequence_v1(value.get("release_sequence"))
    digest_fields = (
        "prerequisite_id", "request_id", "closed_build_id",
        "deployment_descriptor_id", "predecessor_id",
        "administrative_bundle_hash", "python_binary_hash",
        "openssl_binary_hash", "openssl_tcb_hash", "systemctl_binary_hash",
        "systemd_analyze_binary_hash", "service_catalog_id",
        "service_coverage_hash", "candidate_units_hash",
        "effective_units_hash",
    )
    digests = {
        field: _require_digest(value.get(field), "startup " + field)
        for field in digest_fields
    }
    if digests["prerequisite_id"] != _deployment_document_id_v1(
        STARTUP_PREREQUISITE_ID_DOMAIN_V1, value, "prerequisite_id",
    ):
        raise _invalid("startup prerequisite id")
    return _DecodedStartupPrerequisiteV1(
        digests["prerequisite_id"], digests["request_id"],
        digests["closed_build_id"], sequence,
        digests["deployment_descriptor_id"], digests["predecessor_id"],
        digests["administrative_bundle_hash"],
        digests["python_binary_hash"], digests["openssl_binary_hash"],
        digests["openssl_tcb_hash"], digests["systemctl_binary_hash"],
        digests["systemd_analyze_binary_hash"],
        digests["service_catalog_id"], digests["service_coverage_hash"],
        version, digests["candidate_units_hash"],
        digests["effective_units_hash"],
    )


def _startup_prerequisite_digest_v1(encoded: bytes) -> str:
    """Journal evidence digest of the complete canonical prerequisite bytes."""
    _decode_startup_prerequisite_v1(encoded)
    return _raw_sha256_v1(encoded)


def _administrative_bundle_hash_v1(
    descriptor: object,
) -> str:
    from executor_birth_distribution_assembler import (
        DeploymentDescriptorV1, encode_deployment_descriptor_v1,
    )

    if type(descriptor) is DeploymentDescriptorV1:
        try:
            encode_deployment_descriptor_v1(descriptor)
        except Exception as exc:
            raise _invalid("administrative bundle descriptor") from exc
    elif type(descriptor) is not _DecodedDeploymentDescriptorV1:
        raise _invalid("administrative bundle descriptor")
    material = bytearray(_u64be_v1(len(descriptor.artifacts)))
    for artifact in descriptor.artifacts:
        destination = artifact.destination_path.encode("utf-8")
        kind = artifact.kind.encode("ascii")
        phase = artifact.install_phase.encode("ascii")
        material.extend(_u64be_v1(len(destination)))
        material.extend(destination)
        material.extend(_u64be_v1(len(kind)))
        material.extend(kind)
        material.extend(_u64be_v1(len(phase)))
        material.extend(phase)
        material.extend(artifact.mode.to_bytes(4, "big", signed=False))
        material.extend(_u64be_v1(artifact.size))
        material.extend(bytes.fromhex(artifact.content_hash.removeprefix("sha256:")))
    return _digest(ADMINISTRATIVE_BUNDLE_DOMAIN_V1, bytes(material))


def _candidate_enablement_links_v1(
    unit_name: str, directives: tuple[_ServiceDirectiveV1, ...],
) -> tuple[_EnablementLinkV1, ...]:
    validate_unit_name_v1(unit_name)
    links: list[_EnablementLinkV1] = []
    for directive in directives:
        if directive.section != "Install" or directive.name not in {
            "WantedBy", "RequiredBy",
        }:
            continue
        relation = "wants" if directive.name == "WantedBy" else "requires"
        for target_unit in directive.values:
            validate_unit_name_v1(target_unit)
            path = (
                f"{SYSTEM_UNIT_ROOT_TEXT_V1}/{target_unit}.{relation}/"
                f"{unit_name}"
            )
            target = "../" + unit_name
            parent = PurePosixPath(path).parent
            resolved = parent.parent / unit_name
            if (
                not path.startswith(SYSTEM_UNIT_ROOT_TEXT_V1 + "/")
                or resolved.as_posix()
                != f"{SYSTEM_UNIT_ROOT_TEXT_V1}/{unit_name}"
                or target != "../" + unit_name
            ):
                raise _invalid("candidate enablement link")
            links.append(_EnablementLinkV1(path, target))
    links.sort(key=lambda item: item.path.encode("utf-8"))
    paths = tuple(item.path for item in links)
    if len(paths) != len(set(paths)):
        raise _invalid("candidate enablement link collision")
    return tuple(links)


def _compile_candidate_units_v1(
    catalog: _DecodedServiceCatalogV1,
) -> _CandidateUnitsSnapshotV1:
    """Compile the signed candidate graph without observing live systemd."""
    if type(catalog) is not _DecodedServiceCatalogV1:
        raise _invalid("candidate catalog")
    entries: list[_CandidateUnitV1] = []
    all_link_paths: set[str] = set()
    catalog_unit_names = {
        name for item in catalog.entries
        for name in (item.unit_name, item.external_unit_name)
        if name is not None
    }
    for entry in catalog.entries:
        if entry.unit_spec is None:
            continue
        assert entry.unit_name is not None
        if any(
            value not in catalog_unit_names
            for directive in entry.unit_spec.directives
            if directive.section == "Install"
            and directive.name in {"WantedBy", "RequiredBy"}
            for value in directive.values
        ):
            raise _invalid("candidate enablement target")
        rendered = _render_service_directives_v1(entry.unit_spec.directives)
        reparsed = _parse_service_unit_fragment_v1(entry.unit_name, rendered)
        if reparsed != entry.unit_spec:
            raise _invalid("candidate unit round trip")
        links = _candidate_enablement_links_v1(
            entry.unit_name, entry.unit_spec.directives,
        )
        for link in links:
            if link.path in all_link_paths:
                raise _invalid("candidate global link collision")
            all_link_paths.add(link.path)
        entries.append(_CandidateUnitV1(
            entry.entry_id, entry.unit_name, entry.unit_spec.fragment_hash,
            entry.unit_spec.directives, links,
        ))
    entry_ids = tuple(item.entry_id for item in entries)
    if entry_ids != tuple(sorted(
        entry_ids, key=lambda item: item.encode("utf-8"),
    )):
        raise _invalid("candidate entry order")
    observed_links = tuple(sorted(
        (
            (link.path, link.target)
            for item in entries for link in item.enablement_links
        ),
        key=lambda item: item[0].encode("utf-8"),
    ))
    if (
        observed_links != _EXPECTED_PRODUCT_ENABLEMENT_LINKS_V1
        and not (
            not observed_links
            and _isolated_g6c_namespace_v1(catalog) is not None
        )
    ):
        raise _invalid("candidate enablement topology")
    document = {
        "schema_version": 1,
        "entries": [item.as_value() for item in entries],
    }
    encoded = _canonical_json(document)
    return _CandidateUnitsSnapshotV1(
        tuple(entries), encoded, _digest(CANDIDATE_UNITS_DOMAIN_V1, encoded),
    )


def _installed_tree_hash_v1(files: tuple[DistributionFileV1, ...]) -> str:
    if (
        not isinstance(files, tuple) or not files
        or any(type(item) is not DistributionFileV1 for item in files)
    ):
        raise _invalid("installed tree files")
    paths = tuple(item.path for item in files)
    if (
        paths != tuple(sorted(paths, key=lambda item: item.encode("utf-8")))
        or len(paths) != len(set(paths))
    ):
        raise _invalid("installed tree file order")
    material = bytearray(_u64be_v1(len(files)))
    for item in files:
        encoded_path = item.path.encode("utf-8")
        material.extend(_u64be_v1(len(encoded_path)))
        material.extend(encoded_path)
        material.extend(_u64be_v1(item.size))
        material.extend(bytes.fromhex(item.content_hash.removeprefix("sha256:")))
    return _digest(INSTALLED_TREE_DOMAIN_V1, bytes(material))


def _required_material_capture_paths_v1(
    distribution: _AuthenticatedDistributionObjectV1,
    catalog: _DecodedServiceCatalogV1,
) -> frozenset[str]:
    paths = {
        SERVICE_CATALOG_PATH_V1,
        DEPLOYMENT_DESCRIPTOR_PATH_V1,
        *(
            f"deployment/systemd/{item.unit_name}"
            for item in catalog.entries if item.unit_spec is not None
        ),
    }
    installation_root = PurePosixPath(distribution.facts.installation_root)
    for entry in catalog.entries:
        if entry.target_executable is None:
            continue
        try:
            relative = PurePosixPath(entry.target_executable).relative_to(
                installation_root,
            ).as_posix()
        except ValueError:
            continue
        paths.add(relative)
    return frozenset(paths)


def _bind_candidate_cutover_materials_core_v1(
    distribution: _AuthenticatedDistributionObjectV1,
    transaction: _DecodedCoordinatorRecordV2,
    predecessor: _DecodedPredecessorDescriptorV1,
    captured: Mapping[str, bytes],
) -> _CandidateCutoverMaterialsV1:
    """Cross-bind the reusable signed candidate before its live measurement."""
    if (
        type(distribution) is not _AuthenticatedDistributionObjectV1
        or type(transaction) is not _DecodedCoordinatorRecordV2
        or type(predecessor) is not _DecodedPredecessorDescriptorV1
        or not isinstance(captured, Mapping)
        or any(
            type(key) is not str or type(value) is not bytes
            for key, value in captured.items()
        )
        or transaction.sequence < 1
    ):
        raise _invalid("preflight material arguments")
    manifest_value, manifest_files = _parse_distribution_manifest_v1(
        distribution.encoded,
    )
    if (
        _distribution_facts_v1(manifest_value) != distribution.facts
        or manifest_files != distribution.files
        or type(distribution.signature) is not bytes
        or len(distribution.signature) != 64
        or transaction.closed_build_id != distribution.facts.closed_build_id
        or transaction.release_sequence != distribution.facts.release_sequence
    ):
        raise _invalid("preflight distribution binding")
    catalog_encoded = captured.get(SERVICE_CATALOG_PATH_V1)
    descriptor_encoded = captured.get(DEPLOYMENT_DESCRIPTOR_PATH_V1)
    if type(catalog_encoded) is not bytes or type(descriptor_encoded) is not bytes:
        raise _invalid("preflight material capture")
    catalog = _decode_service_catalog_v1(catalog_encoded)
    descriptor = _decode_deployment_descriptor_v1(descriptor_encoded)
    candidate = _compile_candidate_units_v1(catalog)
    _service_source_identity_v1(catalog, descriptor)
    bundle_hash = _administrative_bundle_hash_v1(descriptor)
    installed_tree_hash = _installed_tree_hash_v1(manifest_files)
    files_by_path = {item.path: item for item in manifest_files}
    for path, role, content in (
        (SERVICE_CATALOG_PATH_V1, "service_catalog", catalog_encoded),
        (DEPLOYMENT_DESCRIPTOR_PATH_V1, "deployment_descriptor", descriptor_encoded),
    ):
        manifest_file = files_by_path.get(path)
        if (
            manifest_file is None or manifest_file.role != role
            or manifest_file.size != len(content)
            or distribution_file_hash_v1(path, content)
            != manifest_file.content_hash
        ):
            raise _invalid("preflight signed material binding")
    if (
        descriptor.release_sequence != distribution.facts.release_sequence
        or descriptor.installation_root != distribution.facts.installation_root
        or descriptor.descriptor_id != transaction.deployment_descriptor_id
        or descriptor.service_catalog_id != catalog.catalog_id
        or descriptor.service_coverage_hash != catalog.service_coverage_hash
        or transaction.service_coverage_hash != catalog.service_coverage_hash
        or transaction.administrative_bundle_hash != bundle_hash
        or predecessor.administrative_bundle_hash != bundle_hash
        or (
            transaction.release_sequence == 1
            and (
                predecessor.service_catalog_id != catalog.catalog_id
                or predecessor.service_coverage_hash
                != catalog.service_coverage_hash
            )
        )
        or (
            transaction.sequence >= 4
            and transaction.installed_tree_hash != installed_tree_hash
        )
    ):
        raise _invalid("preflight material cross binding")

    service_files = tuple(
        item for item in manifest_files if item.role == "service_unit"
    )
    expected_unit_paths = {
        f"deployment/systemd/{item.unit_name}"
        for item in catalog.entries if item.unit_spec is not None
    }
    if {item.path for item in service_files} != expected_unit_paths:
        raise _invalid("preflight service unit coverage")
    expected_artifact_sources = {
        "deployment/admin/preflight.py", *expected_unit_paths,
    }
    if (
        {item.source_path for item in descriptor.artifacts}
        != expected_artifact_sources
    ):
        raise _invalid("preflight deployment artifact coverage")
    entries_by_unit = {
        str(item.unit_name): item
        for item in catalog.entries if item.unit_spec is not None
    }
    expected_kinds = {
        "gated_service": "service_unit",
        "gated_timer": "timer_unit",
        "stop_only": "stop_only_unit",
        "target": "target_unit",
    }
    for artifact in descriptor.artifacts:
        manifest_file = files_by_path.get(artifact.source_path)
        artifact_bytes = captured.get(artifact.source_path)
        if (
            manifest_file is None or type(artifact_bytes) is not bytes
            or len(artifact_bytes) != artifact.size
            or manifest_file.size != artifact.size
            or manifest_file.content_hash != artifact.content_hash
            or distribution_file_hash_v1(
                artifact.source_path, artifact_bytes,
            ) != artifact.content_hash
        ):
            raise _invalid("preflight deployment artifact file binding")
        if artifact.kind == "administrative_program":
            if manifest_file.role != "preflight":
                raise _invalid("preflight administrative artifact role")
            continue
        entry = entries_by_unit.get(PurePosixPath(artifact.source_path).name)
        if (
            manifest_file.role != "service_unit" or entry is None
            or expected_kinds.get(entry.class_name) != artifact.kind
        ):
            raise _invalid("preflight unit artifact kind")

    fragments: list[tuple[str, bytes]] = []
    for unit_name, entry in entries_by_unit.items():
        path = f"deployment/systemd/{unit_name}"
        fragment = captured.get(path)
        manifest_file = files_by_path[path]
        if (
            type(fragment) is not bytes
            or distribution_file_hash_v1(path, fragment)
            != manifest_file.content_hash
        ):
            raise _invalid("preflight unit fragment manifest binding")
        parsed = _parse_service_unit_fragment_v1(unit_name, fragment)
        if (
            parsed != entry.unit_spec
            or _render_service_directives_v1(parsed.directives) != fragment
        ):
            raise _invalid("preflight unit fragment catalog binding")
        fragments.append((unit_name, fragment))

    installation_root = PurePosixPath(descriptor.installation_root)
    for entry in catalog.entries:
        if (
            entry.execution_kind == "python_module"
            and entry.target_executable != descriptor.python_executable
        ):
            raise _invalid("preflight Python executable binding")
        if (
            entry.execution_kind == "systemctl_stop"
            and entry.target_executable != descriptor.systemctl_executable
        ):
            raise _invalid("preflight systemctl executable binding")
        if entry.execution_kind in {"python_module", "native_executable"}:
            assert entry.target_working_directory is not None
            try:
                PurePosixPath(entry.target_working_directory).relative_to(
                    installation_root,
                )
            except ValueError as exc:
                raise _invalid("preflight target working directory") from exc
        if entry.target_executable is None:
            continue
        try:
            relative = PurePosixPath(entry.target_executable).relative_to(
                installation_root,
            ).as_posix()
        except ValueError:
            continue
        executable = captured.get(relative)
        manifest_file = files_by_path.get(relative)
        if (
            type(executable) is not bytes or manifest_file is None
            or manifest_file.size != len(executable)
            or distribution_file_hash_v1(relative, executable)
            != manifest_file.content_hash
            or _target_executable_hash_v1(entry.target_executable, executable)
            != entry.target_executable_hash
        ):
            raise _invalid("preflight distribution target executable")

    required_paths = _required_material_capture_paths_v1(
        distribution, catalog,
    ) | frozenset(item.source_path for item in descriptor.artifacts)
    if any(path not in captured for path in required_paths):
        raise _invalid("preflight captured material coverage")
    return _CandidateCutoverMaterialsV1(
        distribution, transaction, predecessor, catalog, descriptor,
        candidate, tuple(sorted(fragments)), bundle_hash, installed_tree_hash,
    )


def _bind_preflight_materials_core_v1(
    distribution: _AuthenticatedDistributionObjectV1,
    transaction: _DecodedCoordinatorRecordV2,
    predecessor: _DecodedPredecessorDescriptorV1,
    captured: Mapping[str, bytes], prerequisite_encoded: bytes,
) -> _BoundPreflightMaterialsV1:
    """Add the published prerequisite to one reusable signed candidate."""
    if type(prerequisite_encoded) is not bytes or transaction.sequence < 2:
        raise _invalid("preflight material arguments")
    candidate = _bind_candidate_cutover_materials_core_v1(
        distribution, transaction, predecessor, captured,
    )
    prerequisite = _decode_startup_prerequisite_v1(prerequisite_encoded)
    if (
        prerequisite.request_id != transaction.request_id
        or prerequisite.closed_build_id != transaction.closed_build_id
        or prerequisite.release_sequence != transaction.release_sequence
        or prerequisite.deployment_descriptor_id
        != candidate.descriptor.descriptor_id
        or prerequisite.predecessor_id != predecessor.predecessor_id
        or prerequisite.administrative_bundle_hash
        != candidate.administrative_bundle_hash
        or prerequisite.service_catalog_id != candidate.catalog.catalog_id
        or prerequisite.service_coverage_hash
        != candidate.catalog.service_coverage_hash
        or prerequisite.candidate_units_hash
        != candidate.candidate_units.candidate_units_hash
        or prerequisite.systemd_manager_version not in SUPPORTED_SYSTEMD_VERSIONS
        or transaction.startup_prerequisite_id != prerequisite.prerequisite_id
        or transaction.startup_prerequisite_digest
        != _startup_prerequisite_digest_v1(prerequisite_encoded)
    ):
        raise _invalid("preflight material cross binding")
    return _BoundPreflightMaterialsV1(
        distribution, transaction, candidate.catalog, candidate.descriptor,
        prerequisite, candidate.candidate_units, candidate.unit_fragments,
        candidate.administrative_bundle_hash, candidate.installed_tree_hash,
    )


def _bind_preflight_materials_for_test_v1(
    distribution: _AuthenticatedDistributionObjectV1,
    transaction: _DecodedCoordinatorRecordV2,
    predecessor: _DecodedPredecessorDescriptorV1,
    captured: Mapping[str, bytes], prerequisite_encoded: bytes,
) -> _BoundPreflightMaterialsForTestV1:
    """Nominal test seam; its result cannot enter productive dispatch."""
    return _BoundPreflightMaterialsForTestV1(
        _bind_preflight_materials_core_v1(
            distribution, transaction, predecessor, captured,
            prerequisite_encoded,
        )
    )


def _select_cutover_candidate_from_snapshot_v2(
    snapshot: _ReconciledFixedOwnershipSnapshotV1, *,
    complete_encoded: bytes, request_id: str, closed_build_id: str,
    release_sequence: int, distribution_encoded: bytes,
    distribution_signature: bytes,
) -> tuple[
    _AuthenticatedDistributionObjectV1,
    _DecodedCoordinatorRecordV2,
    _DecodedPredecessorDescriptorV1,
]:
    """Select the exact pending transaction without consulting required-head."""
    if (
        type(snapshot) is not _ReconciledFixedOwnershipSnapshotV1
        or type(complete_encoded) is not bytes
        or type(request_id) is not str
        or type(closed_build_id) is not str
        or type(release_sequence) is not int
        or type(distribution_encoded) is not bytes
        or type(distribution_signature) is not bytes
    ):
        raise _invalid("cutover candidate selection")
    transactions = tuple(
        item for item in snapshot.transactions
        if item.claim.request_id == request_id
    )
    builds = tuple(
        item for item in snapshot.builds
        if item.facts.closed_build_id == closed_build_id
        and item.facts.release_sequence == release_sequence
    )
    predecessor = snapshot.predecessor
    if (
        len(transactions) != 1
        or len(builds) != 1
        or type(predecessor) is not _DecodedPredecessorDescriptorV1
    ):
        raise _recovery("cutover candidate selection")
    transaction = transactions[0]
    if len(transaction.prefix.records) < 2:
        raise _recovery("cutover candidate binding")
    complete = transaction.prefix.records[1]
    build = builds[0]
    if (
        transaction.prefix.encoded_records[1] != complete_encoded
        or complete.sequence != 1
        or complete.state != "RECEIPTS_COMPLETE"
        or transaction.claim.closed_build_id != closed_build_id
        or transaction.claim.release_sequence != release_sequence
        or complete.request_id != request_id
        or complete.closed_build_id != closed_build_id
        or complete.release_sequence != release_sequence
        or build.encoded != distribution_encoded
        or build.signature != distribution_signature
    ):
        raise _recovery("cutover candidate binding")
    return build, complete, predecessor


def _prepare_cutover_candidate_v2(
    complete: object, distribution: object,
) -> _PreparedCutoverCandidateV2:
    """Capture one signed candidate and the TCB before live topology changes."""
    from executor_birth_distribution_manifest import (
        VerifiedDistribution,
        verify_current_installation_distribution_v1,
    )
    from executor_birth_ownership_coordinator import (
        OwnershipCoordinatorRecordV2,
        OwnershipCoordinatorStateV1,
    )

    if (
        type(complete) is not OwnershipCoordinatorRecordV2
        or complete.sequence != 1
        or complete.state is not OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE
        or type(distribution) is not VerifiedDistribution
    ):
        raise _invalid("cutover candidate input")
    verified = verify_current_installation_distribution_v1(
        distribution.encoded, distribution.signature,
    )
    if verified != distribution:
        raise _recovery("cutover distribution changed")
    authenticated = _authenticate_fixed_ownership_snapshot_v1()
    build, transaction, predecessor = _select_cutover_candidate_from_snapshot_v2(
        authenticated.snapshot,
        complete_encoded=complete.encode(),
        request_id=complete.request_id,
        closed_build_id=complete.closed_build_id,
        release_sequence=complete.release_sequence,
        distribution_encoded=distribution.encoded,
        distribution_signature=distribution.signature,
    )
    root = RELEASE_ROOT / f"{complete.release_sequence:020d}"
    if build.facts.installation_root != root.as_posix():
        raise _invalid("cutover installation root")
    probe_paths = frozenset({
        SERVICE_CATALOG_PATH_V1, DEPLOYMENT_DESCRIPTOR_PATH_V1,
    })
    probe = _snapshot_exact_distribution_tree_v1(
        root, build.files, uid=0, gid=0, chain_stop=None,
        capture_paths=probe_paths,
    )
    catalog = _decode_service_catalog_v1(probe[SERVICE_CATALOG_PATH_V1])
    descriptor = _decode_deployment_descriptor_v1(
        probe[DEPLOYMENT_DESCRIPTOR_PATH_V1],
    )
    capture_paths = (
        _required_material_capture_paths_v1(build, catalog)
        | frozenset(item.source_path for item in descriptor.artifacts)
    )
    captured = _capture_verified_distribution_tree_v1(
        build.facts, build.files, root,
        expected_type=_AuthenticatedDistributionObjectV1,
        uid=0, gid=0, chain_stop=None,
        extra_capture_paths=capture_paths, require_compiled_review=True,
    )
    materials = _bind_candidate_cutover_materials_core_v1(
        build, transaction, predecessor, captured,
    )
    _revalidate_captured_administrative_tcb_v1(
        authenticated.administrative_tcb.capture,
        _administrative_links_v1(), uid=0, gid=0, chain_stop=None,
    )
    repeated = _authenticate_fixed_ownership_snapshot_v1()
    if repeated.snapshot != authenticated.snapshot:
        raise _recovery("fixed ownership changed")
    repeated_distribution = verify_current_installation_distribution_v1(
        distribution.encoded, distribution.signature,
    )
    if repeated_distribution != distribution:
        raise _recovery("cutover distribution changed")
    return _PreparedCutoverCandidateV2(
        materials, authenticated.administrative_tcb.capture,
        _PREPARED_CUTOVER_CANDIDATE_SEAL_V2,
    )


def _capture_cutover_effective_systemd_v2(
    prepared: _PreparedCutoverCandidateV2,
) -> _CapturedEffectiveSystemdUnitsV1:
    """Measure the installed candidate through the fixed product manager."""
    if (
        type(prepared) is not _PreparedCutoverCandidateV2
        or prepared._seal is not _PREPARED_CUTOVER_CANDIDATE_SEAL_V2
    ):
        raise _invalid("prepared cutover candidate")
    materials = prepared.materials
    captured = _capture_effective_systemd_units_core_v1(
        materials,
        systemctl_executable=materials.descriptor.systemctl_executable,
        live_root=Path("/"), uid=0, gid=0,
    )
    _revalidate_captured_effective_systemd_v1(
        captured, live_root=Path("/"), uid=0, gid=0,
    )
    return captured


def _build_startup_prerequisite_for_cutover_v2(
    prepared: _PreparedCutoverCandidateV2,
    effective: _CapturedEffectiveSystemdUnitsV1,
):
    """Build the prerequisite only from authenticated and freshly read facts."""
    from executor_birth_distribution_assembler import (
        build_startup_prerequisite_v1,
    )

    if (
        type(prepared) is not _PreparedCutoverCandidateV2
        or prepared._seal is not _PREPARED_CUTOVER_CANDIDATE_SEAL_V2
        or type(effective) is not _CapturedEffectiveSystemdUnitsV1
    ):
        raise _invalid("cutover prerequisite input")
    materials = prepared.materials
    tcb = prepared.administrative_tcb
    _revalidate_captured_administrative_tcb_v1(
        tcb, _administrative_links_v1(), uid=0, gid=0, chain_stop=None,
    )
    _revalidate_captured_effective_systemd_v1(
        effective, live_root=Path("/"), uid=0, gid=0,
    )
    prerequisite = build_startup_prerequisite_v1(
        request_id=materials.transaction.request_id,
        closed_build_id=materials.transaction.closed_build_id,
        release_sequence=materials.transaction.release_sequence,
        deployment_descriptor_id=materials.descriptor.descriptor_id,
        predecessor_id=materials.predecessor.predecessor_id,
        administrative_bundle_hash=materials.administrative_bundle_hash,
        python_binary_hash=tcb.executables.python_binary_hash,
        openssl_binary_hash=tcb.executables.openssl_binary_hash,
        openssl_tcb_hash=tcb.openssl_tcb.openssl_tcb_hash,
        systemctl_binary_hash=tcb.executables.systemctl_binary_hash,
        systemd_analyze_binary_hash=(
            tcb.executables.systemd_analyze_binary_hash
        ),
        service_catalog_id=materials.catalog.catalog_id,
        service_coverage_hash=materials.catalog.service_coverage_hash,
        systemd_manager_version=effective.manager_version,
        candidate_units_hash=materials.candidate_units.candidate_units_hash,
        effective_units_hash=effective.snapshot.effective_units_hash,
    )
    _revalidate_captured_administrative_tcb_v1(
        tcb, _administrative_links_v1(), uid=0, gid=0, chain_stop=None,
    )
    _revalidate_captured_effective_systemd_v1(
        effective, live_root=Path("/"), uid=0, gid=0,
    )
    return prerequisite


def _decode_ownership_cutover_v1(
    encoded: bytes, signature: bytes,
) -> _DecodedOwnershipCutoverV1:
    """Decode structural facts only; this does not authenticate the signature."""
    if type(signature) is not bytes or len(signature) != 64:
        raise _invalid("ownership cutover signature")
    value = decode_canonical_json_v1(encoded, MAX_CUTOVER_BYTES_V1)
    if (
        not isinstance(value, dict) or set(value) != _CUTOVER_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("ownership cutover schema")
    key_id = value.get("signing_key_id")
    count = value.get("current_count")
    raw_receipts = value.get("current_receipts")
    if (
        not isinstance(key_id, str)
        or _OWNERSHIP_KEY_RE.fullmatch(key_id) is None
        or type(count) is not int or count < 0
        or not isinstance(raw_receipts, list) or count != len(raw_receipts)
    ):
        raise _invalid("ownership cutover header")
    receipts: list[OwnershipReceiptFactsV1] = []
    identities: list[tuple[str, str]] = []
    for raw in raw_receipts:
        if not isinstance(raw, dict) or set(raw) != _CUTOVER_RECEIPT_KEYS_V1:
            raise _invalid("ownership cutover receipt")
        contract_id = raw.get("contract_id")
        if (
            not isinstance(contract_id, str) or not contract_id
            or "\0" in contract_id
        ):
            raise _invalid("ownership cutover contract")
        generation_id = _require_digest(
            raw.get("generation_id"), "ownership cutover generation",
        )
        receipt_hash = _require_digest(
            raw.get("receipt_hash"), "ownership cutover receipt hash",
        )
        identities.append((contract_id, generation_id))
        receipts.append(OwnershipReceiptFactsV1(
            contract_id, generation_id, receipt_hash,
        ))
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise _invalid("ownership cutover receipt order")
    cutover_id = _require_digest(value.get("cutover_id"), "cutover_id")
    previous_cutover_id = _nullable_digest_v1(
        value.get("previous_cutover_id"), "previous_cutover_id",
    )
    request_id = _require_digest(value.get("request_id"), "request_id")
    catalog_id = _require_digest(value.get("catalog_id"), "catalog_id")
    maintenance_evidence_hash = _require_digest(
        value.get("maintenance_evidence_hash"), "maintenance_evidence_hash",
    )
    boundary_inventory_hash = _require_digest(
        value.get("boundary_inventory_hash"), "boundary_inventory_hash",
    )
    closed_build_id = _require_digest(
        value.get("closed_build_id"), "closed_build_id",
    )
    context_transition_id = _require_digest(
        value.get("context_transition_id"), "context_transition_id",
    )
    dominant_startup_receipt = _require_digest(
        value.get("dominant_startup_receipt"), "dominant_startup_receipt",
    )
    guard_version = value.get("boundary_guard_version")
    if (
        not isinstance(guard_version, str) or not guard_version
        or "\0" in guard_version
        or len(guard_version.encode("utf-8")) > 128
    ):
        raise _invalid("boundary_guard_version")
    receipt_values = [
        {
            "contract_id": item.contract_id,
            "generation_id": item.generation_id,
            "receipt_hash": item.receipt_hash,
        }
        for item in receipts
    ]
    if catalog_id != _cutover_catalog_id_v1(receipt_values):
        raise _invalid("cutover catalog_id")
    unsigned = {key: item for key, item in value.items() if key != "cutover_id"}
    if cutover_id != _digest(CUTOVER_ID_DOMAIN_V1, _canonical_json(unsigned)):
        raise _invalid("cutover_id")
    return _DecodedOwnershipCutoverV1(
        cutover_id, previous_cutover_id, request_id, key_id, catalog_id,
        tuple(receipts), maintenance_evidence_hash, boundary_inventory_hash,
        guard_version, closed_build_id, context_transition_id,
        dominant_startup_receipt, bytes(encoded), bytes(signature),
    )


def _decode_ownership_head_v1(
    encoded: bytes, signature: bytes,
) -> _DecodedOwnershipHeadV1:
    """Decode structural facts only; this does not authenticate the signature."""
    if type(signature) is not bytes or len(signature) != 64:
        raise _invalid("ownership head signature")
    value = decode_canonical_json_v1(encoded, MAX_HEAD_BYTES_V1)
    if not isinstance(value, dict) or set(value) != _HEAD_KEYS_V1:
        raise _invalid("ownership head schema")
    sequence = value.get("release_sequence")
    key_id = value.get("signing_key_id")
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or type(sequence) is not int or sequence <= 0
        or not isinstance(key_id, str)
        or _OWNERSHIP_KEY_RE.fullmatch(key_id) is None
    ):
        raise _invalid("ownership head header")
    cutover_id = _require_digest(value.get("cutover_id"), "cutover_id")
    closed_build_id = _require_digest(
        value.get("closed_build_id"), "closed_build_id",
    )
    previous_head_id = _nullable_digest_v1(
        value.get("previous_head_id"), "previous_head_id",
    )
    if (sequence == 1) != (previous_head_id is None):
        raise _invalid("previous_head_id")
    head_id = _require_digest(value.get("head_id"), "head_id")
    unsigned = {key: item for key, item in value.items() if key != "head_id"}
    if head_id != _digest(HEAD_ID_DOMAIN_V1, _canonical_json(unsigned)):
        raise _invalid("head_id")
    return _DecodedOwnershipHeadV1(
        sequence, cutover_id, closed_build_id, previous_head_id, head_id,
        key_id, bytes(encoded), bytes(signature),
    )


def _required_head_frame_parts_v1(framed: bytes) -> tuple[bytes, bytes]:
    """Split exact framing; callers choose candidate or fixed-root semantics."""
    if type(framed) is not bytes or len(framed) > MAX_REQUIRED_HEAD_BYTES_V1:
        raise _invalid("required head size")
    prefix = len(REQUIRED_HEAD_MAGIC_V1)
    if (
        len(framed) < prefix + 4 + 64
        or framed[:prefix] != REQUIRED_HEAD_MAGIC_V1
    ):
        raise _invalid("required head magic")
    payload_length = int.from_bytes(framed[prefix:prefix + 4], "big")
    expected_length = prefix + 4 + payload_length + 64
    if payload_length > MAX_HEAD_BYTES_V1 or len(framed) != expected_length:
        raise _invalid("required head framing")
    payload_start = prefix + 4
    encoded = framed[payload_start:payload_start + payload_length]
    signature = framed[payload_start + payload_length:]
    return encoded, signature


def _decode_required_head_frame_v1(framed: bytes) -> _DecodedOwnershipHeadV1:
    """Decode an unauthenticated required-head candidate with exact framing."""
    encoded, signature = _required_head_frame_parts_v1(framed)
    return _decode_ownership_head_v1(encoded, signature)


def _decode_fixed_required_head_frame_v1(
    framed: bytes,
) -> _DecodedOwnershipHeadV1:
    """Decode the durable pointer, classifying damaged framing as recovery."""
    try:
        encoded, signature = _required_head_frame_parts_v1(framed)
    except PreflightError as exc:
        raise _recovery("required head framing") from exc
    return _decode_ownership_head_v1(encoded, signature)


def _decode_successor_claim_v1(encoded: bytes) -> _DecodedSuccessorClaimV1:
    """Decode claim facts only; fixed-root ownership is established elsewhere."""
    value = decode_canonical_json_v1(
        encoded, MAX_COORDINATOR_CONTROL_BYTES_V2,
    )
    if (
        not isinstance(value, dict) or set(value) != _SUCCESSOR_CLAIM_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("successor claim schema")
    sequence = value.get("release_sequence")
    if type(sequence) is not int or sequence <= 0:
        raise _invalid("successor claim release_sequence")
    previous_head_id = _nullable_digest_v1(
        value.get("previous_head_id"), "successor claim previous_head_id",
    )
    if (sequence == 1) != (previous_head_id is None):
        raise _invalid("successor claim previous_head_id")
    claim_id = _require_digest(value.get("claim_id"), "successor claim id")
    request_id = _require_digest(
        value.get("request_id"), "successor claim request_id",
    )
    source_id = _require_digest(
        value.get("source_id"), "successor claim source_id",
    )
    closed_build_id = _require_digest(
        value.get("closed_build_id"), "successor claim closed_build_id",
    )
    unsigned = {key: item for key, item in value.items() if key != "claim_id"}
    if claim_id != _digest(
        SUCCESSOR_CLAIM_ID_DOMAIN_V1, _canonical_json(unsigned),
    ):
        raise _invalid("successor claim id")
    return _DecodedSuccessorClaimV1(
        claim_id, previous_head_id, sequence, request_id, source_id,
        closed_build_id,
    )


def _decode_current_receipts_v1(
    raw_receipts: object,
) -> tuple[OwnershipReceiptFactsV1, ...]:
    if not isinstance(raw_receipts, list):
        raise _invalid("coordinator current_receipts")
    receipts: list[OwnershipReceiptFactsV1] = []
    identities: list[tuple[str, str]] = []
    for raw in raw_receipts:
        if not isinstance(raw, dict) or set(raw) != _CUTOVER_RECEIPT_KEYS_V1:
            raise _invalid("coordinator receipt schema")
        contract_id = raw.get("contract_id")
        if (
            not isinstance(contract_id, str) or not contract_id
            or "\0" in contract_id
        ):
            raise _invalid("coordinator contract_id")
        generation_id = _require_digest(
            raw.get("generation_id"), "coordinator generation_id",
        )
        receipt_hash = _require_digest(
            raw.get("receipt_hash"), "coordinator receipt_hash",
        )
        identities.append((contract_id, generation_id))
        receipts.append(OwnershipReceiptFactsV1(
            contract_id, generation_id, receipt_hash,
        ))
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise _invalid("coordinator receipt order")
    return tuple(receipts)


def _current_inventory_hash_from_receipts_v1(
    receipts: tuple[OwnershipReceiptFactsV1, ...],
) -> str:
    if (
        type(receipts) is not tuple
        or any(type(item) is not OwnershipReceiptFactsV1 for item in receipts)
    ):
        raise _invalid("coordinator current inventory")
    encoded = _canonical_json([{
        "contract_id": item.contract_id,
        "generation_id": item.generation_id,
    } for item in receipts])
    return _digest(CURRENT_INVENTORY_DOMAIN_V1, encoded)


def _maintenance_evidence_hash_v1(encoded: bytes) -> str:
    value = decode_canonical_json_v1(encoded, MAX_CUTOVER_BYTES_V1)
    source = value.get("source") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != _MAINTENANCE_PROOF_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or not isinstance(source, str)
        or source not in _MAINTENANCE_SOURCES_V1
        or not isinstance(value.get("units"), list)
    ):
        raise _invalid("maintenance proof schema")
    identities: list[tuple[str, str]] = []
    for raw in value["units"]:
        if not isinstance(raw, dict) or set(raw) != _MAINTENANCE_UNIT_KEYS_V1:
            raise _invalid("maintenance unit schema")
        scope = raw.get("scope")
        unit = raw.get("unit")
        try:
            unit_size = len(unit.encode("utf-8")) if isinstance(unit, str) else -1
        except UnicodeEncodeError as exc:
            raise _invalid("maintenance unit name") from exc
        if (
            not isinstance(scope, str) or scope not in {"system", "user"}
            or not isinstance(unit, str) or not unit or "\0" in unit
            or unit_size > 256
            or raw.get("load_state") != "loaded"
            or not isinstance(raw.get("active_state"), str)
            or raw.get("active_state") not in {"inactive", "failed"}
            or type(raw.get("main_pid")) is not int
            or raw.get("main_pid") != 0
        ):
            raise _invalid("maintenance unit state")
        identities.append((scope, unit))
    if tuple(identities) != _MAINTENANCE_TARGETS_V1:
        raise _invalid("maintenance unit order")
    return _digest(MAINTENANCE_PROOF_DOMAIN_V1, encoded)


def _install_transaction_id_v1(value: dict[str, object]) -> str:
    if (
        type(value) is not dict or set(value) != _INSTALL_TRANSACTION_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("install transaction schema")
    return _digest(INSTALL_TRANSACTION_ID_DOMAIN_V1, _canonical_json(value))


def _decode_coordinator_record_v2(
    encoded: bytes,
) -> _DecodedCoordinatorRecordV2:
    """Decode one non-authorizing V2 journal record through HEAD_REQUIRED."""
    value = decode_canonical_json_v1(encoded, MAX_COORDINATOR_RECORD_BYTES_V2)
    if (
        not isinstance(value, dict)
        or set(value) != _COORDINATOR_RECORD_KEYS_V2
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 2
    ):
        raise _invalid("coordinator record schema")
    sequence = value.get("sequence")
    if (
        type(sequence) is not int or not 0 <= sequence < len(_COORDINATOR_STATES_V1)
        or value.get("state") != _COORDINATOR_STATES_V1[sequence]
    ):
        raise _invalid("coordinator state sequence")

    required_digests = {}
    for field in (
        "request_id", "closed_build_id", "distribution_payload_hash",
        "distribution_signature_hash", "boundary_inventory_hash", "source_id",
        "successor_claim_id", "deployment_descriptor_id",
        "install_transaction_id", "service_coverage_hash",
        "administrative_bundle_hash", "previous_admission_context_id",
        "previous_context_epoch", "target_admission_context_id",
        "target_context_epoch", "context_transition_id",
        "current_inventory_hash",
    ):
        required_digests[field] = _require_digest(
            value.get(field), "coordinator " + field,
        )
    nullable_digests = {}
    for field in (
        "previous_record_sha256", "previous_closed_build_id",
        "previous_cutover_id", "maintenance_before_hash",
        "maintenance_after_hash", "startup_prerequisite_id",
        "startup_prerequisite_digest", "cutover_id", "catalog_id",
        "certificate_payload_hash", "certificate_signature_hash",
        "installed_tree_hash", "previous_head_id", "head_id", "head_payload_hash",
        "head_signature_hash", "required_head_frame_hash",
        "verified_chain_head_id", "preflight_attestation_hash",
        "dominant_startup_receipt",
    ):
        nullable_digests[field] = _nullable_digest_v1(
            value.get(field), "coordinator " + field,
        )
    if (sequence == 0) != (
        nullable_digests["previous_record_sha256"] is None
    ):
        raise _invalid("coordinator previous_record_sha256")
    release_sequence = value.get("release_sequence")
    if type(release_sequence) is not int or release_sequence <= 0:
        raise _invalid("coordinator release_sequence")
    previous_head_id = nullable_digests["previous_head_id"]
    if (release_sequence == 1) != (previous_head_id is None):
        raise _invalid("coordinator previous_head_id")
    guard_version = value.get("boundary_guard_version")
    if (
        not isinstance(guard_version, str) or not guard_version
        or "\0" in guard_version
    ):
        raise _invalid("coordinator boundary_guard_version")
    provisioning_transaction_id = value.get("provisioning_transaction_id")
    if (
        type(provisioning_transaction_id) is not str
        or _PROVISIONING_TRANSACTION_RE_V2.fullmatch(
            provisioning_transaction_id,
        ) is None
    ):
        raise _invalid("coordinator provisioning_transaction_id")
    hex_fields = {}
    for field in (
        "previous_set_id", "target_set_id", "target_context_material_sha256",
        "target_set_json_sha256",
    ):
        item = value.get(field)
        if type(item) is not str or _HEX_SHA256_RE_V2.fullmatch(item) is None:
            raise _invalid("coordinator " + field)
        hex_fields[field] = item

    raw_proof = value.get("maintenance_proof_b64")
    if sequence == 0:
        if (
            value.get("current_receipts") != [] or raw_proof is not None
            or nullable_digests["maintenance_before_hash"] is not None
            or nullable_digests["maintenance_after_hash"] is not None
        ):
            raise _invalid("coordinator prepared fields")
        receipts: tuple[OwnershipReceiptFactsV1, ...] = ()
        maintenance_proof = None
    else:
        receipts = _decode_current_receipts_v1(value.get("current_receipts"))
        if (
            not isinstance(raw_proof, str)
            or nullable_digests["maintenance_before_hash"] is None
            or nullable_digests["maintenance_after_hash"] is None
        ):
            raise _invalid("coordinator maintenance fields")
        try:
            maintenance_proof = base64.b64decode(raw_proof, validate=True)
        except (TypeError, ValueError) as exc:
            raise _invalid("coordinator maintenance base64") from exc
        if (
            not maintenance_proof
            or base64.b64encode(maintenance_proof).decode("ascii") != raw_proof
        ):
            raise _invalid("coordinator maintenance base64")
        observed_hash = _maintenance_evidence_hash_v1(maintenance_proof)
        if (
            nullable_digests["maintenance_before_hash"] != observed_hash
            or nullable_digests["maintenance_after_hash"] != observed_hash
        ):
            raise _invalid("coordinator maintenance binding")
        if required_digests["current_inventory_hash"] != (
            _current_inventory_hash_from_receipts_v1(receipts)
        ):
            raise _invalid("coordinator current inventory binding")

    certificate_fields = (
        "startup_prerequisite_id", "startup_prerequisite_digest",
        "cutover_id", "catalog_id", "certificate_payload_hash",
        "certificate_signature_hash", "dominant_startup_receipt",
    )
    if (
        sequence >= 2
        and any(nullable_digests[field] is None for field in certificate_fields)
    ) or (
        sequence < 2
        and any(nullable_digests[field] is not None for field in certificate_fields)
    ):
        raise _invalid("coordinator certificate threshold")
    if (sequence >= 4) != (
        nullable_digests["installed_tree_hash"] is not None
    ):
        raise _invalid("coordinator installed tree threshold")
    head_fields = (
        "head_id", "head_payload_hash", "head_signature_hash",
        "required_head_frame_hash", "verified_chain_head_id",
    )
    if (
        sequence >= 5
        and any(nullable_digests[field] is None for field in head_fields)
    ) or (
        sequence < 5
        and any(nullable_digests[field] is not None for field in head_fields)
    ):
        raise _invalid("coordinator head threshold")
    if (
        sequence >= 5
        and nullable_digests["verified_chain_head_id"]
        != nullable_digests["head_id"]
    ):
        raise _invalid("coordinator verified chain head")
    if (sequence >= 6) != (
        nullable_digests["preflight_attestation_hash"] is not None
    ):
        raise _invalid("coordinator preflight threshold")

    install_value = {
        "schema_version": 1,
        "request_id": required_digests["request_id"],
        "source_id": required_digests["source_id"],
        "closed_build_id": required_digests["closed_build_id"],
        "release_sequence": release_sequence,
        "previous_head_id": previous_head_id,
        "successor_claim_id": required_digests["successor_claim_id"],
        "deployment_descriptor_id": required_digests["deployment_descriptor_id"],
        "service_coverage_hash": required_digests["service_coverage_hash"],
        "administrative_bundle_hash": required_digests[
            "administrative_bundle_hash"
        ],
    }
    if required_digests["install_transaction_id"] != _install_transaction_id_v1(
        install_value,
    ):
        raise _invalid("coordinator install_transaction_id")

    decoded = _DecodedCoordinatorRecordV2(
        sequence, value["state"], nullable_digests["previous_record_sha256"],
        required_digests["request_id"],
        nullable_digests["previous_closed_build_id"],
        nullable_digests["previous_cutover_id"],
        required_digests["closed_build_id"],
        required_digests["distribution_payload_hash"],
        required_digests["distribution_signature_hash"],
        required_digests["boundary_inventory_hash"], guard_version, receipts,
        nullable_digests["maintenance_before_hash"],
        nullable_digests["maintenance_after_hash"], maintenance_proof,
        nullable_digests["startup_prerequisite_id"],
        nullable_digests["startup_prerequisite_digest"],
        nullable_digests["cutover_id"], nullable_digests["catalog_id"],
        nullable_digests["certificate_payload_hash"],
        nullable_digests["certificate_signature_hash"],
        nullable_digests["dominant_startup_receipt"],
        required_digests["source_id"], required_digests["successor_claim_id"],
        required_digests["deployment_descriptor_id"],
        required_digests["install_transaction_id"],
        nullable_digests["installed_tree_hash"], release_sequence,
        previous_head_id, nullable_digests["head_id"],
        nullable_digests["head_payload_hash"],
        nullable_digests["head_signature_hash"],
        nullable_digests["required_head_frame_hash"],
        nullable_digests["verified_chain_head_id"],
        nullable_digests["preflight_attestation_hash"],
        required_digests["service_coverage_hash"],
        required_digests["administrative_bundle_hash"],
        provisioning_transaction_id,
        hex_fields["previous_set_id"],
        required_digests["previous_admission_context_id"],
        required_digests["previous_context_epoch"],
        hex_fields["target_set_id"],
        required_digests["target_admission_context_id"],
        required_digests["target_context_epoch"],
        hex_fields["target_context_material_sha256"],
        hex_fields["target_set_json_sha256"],
        required_digests["context_transition_id"],
        required_digests["current_inventory_hash"],
    )
    if decoded.as_value() != value:
        raise _invalid("coordinator record binding")
    return decoded


def _coordinator_record_hash_v2(encoded: bytes) -> str:
    if type(encoded) is not bytes:
        raise _invalid("coordinator record hash")
    return _digest(COORDINATOR_RECORD_DOMAIN_V2, encoded)


def _decode_coordinator_prefix_v2(
    encoded_records: tuple[bytes, ...],
) -> _DecodedCoordinatorPrefixV2:
    """Decode a contiguous non-authorizing record prefix from 000 through 006."""
    if (
        type(encoded_records) is not tuple
        or not 1 <= len(encoded_records) <= len(_COORDINATOR_STATES_V1)
        or any(type(encoded) is not bytes for encoded in encoded_records)
    ):
        raise _invalid("coordinator prefix")
    records: list[_DecodedCoordinatorRecordV2] = []
    accepted_bytes: list[bytes] = []
    previous_hash = None
    for sequence, encoded in enumerate(encoded_records):
        record = _decode_coordinator_record_v2(encoded)
        if (
            record.sequence != sequence
            or record.previous_record_sha256 != previous_hash
        ):
            raise _invalid("coordinator prefix chain")
        if records:
            first_value = records[0].as_value()
            current_value = record.as_value()
            if any(
                current_value[key] != first_value[key]
                for key in _COORDINATOR_CARRY_KEYS_V2
            ):
                raise _invalid("coordinator prefix carry")
            for threshold, keys in _COORDINATOR_THRESHOLD_KEYS_V2:
                if sequence > threshold:
                    threshold_value = records[threshold].as_value()
                    if any(
                        current_value[key] != threshold_value[key]
                        for key in keys
                    ):
                        raise _invalid("coordinator prefix threshold carry")
        records.append(record)
        accepted_bytes.append(encoded)
        previous_hash = _coordinator_record_hash_v2(encoded)
    return _DecodedCoordinatorPrefixV2(
        tuple(records), tuple(accepted_bytes),
    )


def _raw_sha256_v1(payload: bytes) -> str:
    if type(payload) is not bytes:
        raise _invalid("raw digest")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _framed_sha256_v1(domain: bytes, payload: bytes) -> str:
    if type(domain) is not bytes or not domain or type(payload) is not bytes:
        raise _invalid("framed digest")
    return _raw_sha256_v1(
        domain + len(payload).to_bytes(8, "big") + payload,
    )


def _coordinator_request_id_v1(
    closed_build_id: object, previous_closed_build_id: object,
    previous_cutover_id: object,
) -> str:
    closed = _require_digest(closed_build_id, "coordinator closed_build_id")
    previous_build = _nullable_digest_v1(
        previous_closed_build_id, "coordinator previous_closed_build_id",
    )
    previous_cutover = _nullable_digest_v1(
        previous_cutover_id, "coordinator previous_cutover_id",
    )
    framed = bytearray(COORDINATOR_REQUEST_DOMAIN_V1)
    for value in (closed, previous_build or "none", previous_cutover or "none"):
        encoded = value.encode("ascii")
        framed.extend(len(encoded).to_bytes(8, "big"))
        framed.extend(encoded)
    return _raw_sha256_v1(bytes(framed))


def _decode_legacy_coordinator_record_v1(
    encoded: bytes,
) -> _DecodedLegacyCoordinatorRecordV1:
    """Decode the immutable V1 bridge without continuing it as V2."""
    value = decode_canonical_json_v1(encoded, MAX_COORDINATOR_RECORD_BYTES_V2)
    if (
        not isinstance(value, dict)
        or set(value) != _LEGACY_COORDINATOR_RECORD_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("legacy coordinator record schema")
    sequence = value.get("sequence")
    if (
        type(sequence) is not int or sequence not in {0, 1}
        or value.get("state") != _COORDINATOR_STATES_V1[sequence]
    ):
        raise _invalid("legacy coordinator state")

    required = {}
    for field in (
        "request_id", "closed_build_id", "distribution_payload_hash",
        "distribution_signature_hash", "boundary_inventory_hash",
    ):
        required[field] = _require_digest(
            value.get(field), "legacy coordinator " + field,
        )
    nullable = {}
    for field in (
        "previous_record_sha256", "previous_closed_build_id",
        "previous_cutover_id", "maintenance_before_hash",
        "maintenance_after_hash", "startup_prerequisite_id",
        "startup_prerequisite_digest", "cutover_id", "catalog_id",
        "certificate_payload_hash", "certificate_signature_hash",
    ):
        nullable[field] = _nullable_digest_v1(
            value.get(field), "legacy coordinator " + field,
        )
    if (sequence == 0) != (nullable["previous_record_sha256"] is None):
        raise _invalid("legacy previous_record_sha256")
    guard_version = value.get("boundary_guard_version")
    if (
        not isinstance(guard_version, str) or not guard_version
        or "\0" in guard_version
    ):
        raise _invalid("legacy boundary_guard_version")

    raw_proof = value.get("maintenance_proof_b64")
    if sequence == 0:
        if (
            value.get("current_receipts") != [] or raw_proof is not None
            or nullable["maintenance_before_hash"] is not None
            or nullable["maintenance_after_hash"] is not None
        ):
            raise _invalid("legacy prepared fields")
        receipts: tuple[OwnershipReceiptFactsV1, ...] = ()
        maintenance_proof = None
    else:
        receipts = _decode_current_receipts_v1(value.get("current_receipts"))
        if (
            not isinstance(raw_proof, str)
            or nullable["maintenance_before_hash"] is None
            or nullable["maintenance_after_hash"] is None
        ):
            raise _invalid("legacy maintenance fields")
        try:
            maintenance_proof = base64.b64decode(raw_proof, validate=True)
        except (TypeError, ValueError) as exc:
            raise _invalid("legacy maintenance base64") from exc
        if (
            not maintenance_proof
            or base64.b64encode(maintenance_proof).decode("ascii") != raw_proof
        ):
            raise _invalid("legacy maintenance base64")
        observed_hash = _maintenance_evidence_hash_v1(maintenance_proof)
        if (
            nullable["maintenance_before_hash"] != observed_hash
            or nullable["maintenance_after_hash"] != observed_hash
        ):
            raise _invalid("legacy maintenance binding")
    if any(nullable[field] is not None for field in (
        "startup_prerequisite_id", "startup_prerequisite_digest",
        "cutover_id", "catalog_id", "certificate_payload_hash",
        "certificate_signature_hash",
    )):
        raise _invalid("legacy premature certificate fields")

    decoded = _DecodedLegacyCoordinatorRecordV1(
        sequence, value["state"], nullable["previous_record_sha256"],
        required["request_id"], nullable["previous_closed_build_id"],
        nullable["previous_cutover_id"], required["closed_build_id"],
        required["distribution_payload_hash"],
        required["distribution_signature_hash"],
        required["boundary_inventory_hash"], guard_version, receipts,
        nullable["maintenance_before_hash"],
        nullable["maintenance_after_hash"], maintenance_proof,
        nullable["startup_prerequisite_id"],
        nullable["startup_prerequisite_digest"], nullable["cutover_id"],
        nullable["catalog_id"], nullable["certificate_payload_hash"],
        nullable["certificate_signature_hash"],
    )
    if decoded.as_value() != value:
        raise _invalid("legacy coordinator record binding")
    return decoded


def _legacy_coordinator_record_hash_v1(encoded: bytes) -> str:
    if type(encoded) is not bytes:
        raise _invalid("legacy coordinator record hash")
    return _digest(LEGACY_COORDINATOR_RECORD_DOMAIN_V1, encoded)


def _decode_legacy_coordinator_prefix_v1(
    encoded_records: tuple[bytes, ...],
) -> _DecodedLegacyCoordinatorPrefixV1:
    if (
        type(encoded_records) is not tuple
        or not 1 <= len(encoded_records) <= 2
        or any(type(encoded) is not bytes for encoded in encoded_records)
    ):
        raise _invalid("legacy coordinator prefix")
    records: list[_DecodedLegacyCoordinatorRecordV1] = []
    previous_hash = None
    for sequence, encoded in enumerate(encoded_records):
        record = _decode_legacy_coordinator_record_v1(encoded)
        if (
            record.sequence != sequence
            or record.previous_record_sha256 != previous_hash
        ):
            raise _invalid("legacy coordinator chain")
        if records:
            first = records[0].as_value()
            current = record.as_value()
            if any(
                current[key] != first[key]
                for key in _LEGACY_COORDINATOR_CARRY_KEYS_V1
            ):
                raise _invalid("legacy coordinator carry")
        records.append(record)
        previous_hash = _legacy_coordinator_record_hash_v1(encoded)
    if records[0].request_id != _coordinator_request_id_v1(
        records[0].closed_build_id,
        records[0].previous_closed_build_id,
        records[0].previous_cutover_id,
    ):
        raise _invalid("legacy coordinator request")
    return _DecodedLegacyCoordinatorPrefixV1(records=tuple(records), encoded_records=encoded_records)


def _legacy_journal_hash_v2(encoded_records: tuple[bytes, ...]) -> str:
    if (
        type(encoded_records) is not tuple
        or not 1 <= len(encoded_records) <= 2
        or any(
            type(encoded) is not bytes or not encoded
            or len(encoded) > MAX_COORDINATOR_RECORD_BYTES_V2
            for encoded in encoded_records
        )
    ):
        raise _invalid("legacy journal bytes")
    framed = bytearray(LEGACY_JOURNAL_DOMAIN_V2)
    framed.extend(len(encoded_records).to_bytes(8, "big"))
    for encoded in encoded_records:
        framed.extend(len(encoded).to_bytes(8, "big"))
        framed.extend(encoded)
    return _raw_sha256_v1(bytes(framed))


def _decode_legacy_disposition_v2(encoded: bytes) -> _DecodedLegacyDispositionV2:
    value = decode_canonical_json_v1(
        encoded, MAX_COORDINATOR_CONTROL_BYTES_V2,
    )
    if (
        not isinstance(value, dict) or set(value) != _LEGACY_DISPOSITION_KEYS_V2
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 2
    ):
        raise _invalid("legacy disposition schema")
    disposition_id = _require_digest(
        value.get("disposition_id"), "legacy disposition_id",
    )
    legacy_journal_hash = _require_digest(
        value.get("legacy_journal_hash"), "legacy journal hash",
    )
    legacy_request_id = _require_digest(
        value.get("legacy_request_id"), "legacy request_id",
    )
    successor_request_id = _require_digest(
        value.get("successor_request_id"), "successor request_id",
    )
    state = value.get("legacy_state")
    reason = value.get("reason")
    if (
        not isinstance(state, str)
        or state not in {"PREPARED", "RECEIPTS_COMPLETE"}
        or reason != _LEGACY_DISPOSITION_REASON_V2
    ):
        raise _invalid("legacy disposition fields")
    unsigned = {key: item for key, item in value.items() if key != "disposition_id"}
    if disposition_id != _digest(
        LEGACY_DISPOSITION_DOMAIN_V2, _canonical_json(unsigned),
    ):
        raise _invalid("legacy disposition_id")
    return _DecodedLegacyDispositionV2(
        disposition_id, legacy_journal_hash, legacy_request_id, state,
        successor_request_id, reason,
    )


def _predecessor_digest_v1(value: object, field: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise _invalid(field)
    return value


def _predecessor_relative_path_v1(value: object) -> str:
    """Mirror the assembler predecessor path contract without extra policy."""
    if (
        type(value) is not str or not value
        or unicodedata.normalize("NFC", value) != value
        or "\\" in value or "\0" in value or value.startswith("/")
    ):
        raise _invalid("predecessor file path")
    parts = value.split("/")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or len(parts) > MAX_PREDECESSOR_PATH_DEPTH_V1
        or PurePosixPath(value).as_posix() != value
        or parts[0] == RECEIVED_SOURCE_DESCRIPTOR_BASENAME_V1
    ):
        raise _invalid("predecessor file path")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid("predecessor file path") from exc
    return value


def _predecessor_absolute_path_v1(
    value: object, field: str, *, allow_root: bool = False,
) -> str:
    """Mirror the assembler absolute-path contract without the admin cap."""
    if (
        type(value) is not str or not value.startswith("/")
        or value.startswith("//") or "\\" in value or "\0" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise _invalid(field)
    if value == "/":
        if allow_root:
            return value
        raise _invalid(field)
    parts = value.split("/")[1:]
    if (
        not parts or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(value).as_posix() != value
    ):
        raise _invalid(field)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(field) from exc
    return value


def _predecessor_text_v1(
    value: object, field: str, *, maximum: int, allow_empty: bool = False,
) -> str:
    if type(value) is not str or "\0" in value:
        raise _invalid(field)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(field) from exc
    if (not allow_empty and not encoded) or len(encoded) > maximum:
        raise _invalid(field)
    return value


def _decode_predecessor_environment_v1(
    raw_environment: object,
) -> tuple[_DecodedPredecessorEnvironmentV1, ...]:
    if type(raw_environment) is not list or len(raw_environment) > 256:
        raise _invalid("predecessor service command environment")
    environment: list[_DecodedPredecessorEnvironmentV1] = []
    for raw in raw_environment:
        if (
            type(raw) is not dict
            or frozenset(raw) != _PREDECESSOR_ENVIRONMENT_KEYS_V1
        ):
            raise _invalid("predecessor service command environment keys")
        name = raw.get("name")
        if (
            type(name) is not str
            or _PREDECESSOR_ENVIRONMENT_RE_V1.fullmatch(name) is None
            or name in _PREDECESSOR_FORBIDDEN_ENVIRONMENT_NAMES_V1
            or name.startswith(("PYTHON", "LD_", "DYLD_", "OPENSSL_"))
        ):
            raise _invalid("predecessor service command environment name")
        environment.append(_DecodedPredecessorEnvironmentV1(
            name,
            _predecessor_text_v1(
                raw.get("value"),
                "predecessor service command environment value",
                maximum=16 * 1024, allow_empty=True,
            ),
        ))
    names = [item.name for item in environment]
    if names != sorted(names) or len(names) != len(set(names)):
        raise _invalid("predecessor service command environment order")
    return tuple(environment)


def _decode_predecessor_service_command_v1(
    raw: object,
) -> _DecodedPredecessorServiceCommandV1:
    if (
        type(raw) is not dict
        or frozenset(raw) != _PREDECESSOR_SERVICE_COMMAND_KEYS_V1
    ):
        raise _invalid("predecessor service command keys")
    entry_id = raw.get("entry_id")
    if type(entry_id) is not str or _ENTRY_ID_RE.fullmatch(entry_id) is None:
        raise _invalid("predecessor service command entry id")
    kind = raw.get("execution_kind")
    if type(kind) is not str or kind not in _PREDECESSOR_EXECUTION_KINDS_V1:
        raise _invalid("predecessor service command execution kind")
    raw_arguments = raw.get("target_args")
    if type(raw_arguments) is not list or len(raw_arguments) > 28:
        raise _invalid("predecessor service command target arguments")
    arguments = tuple(
        _predecessor_text_v1(
            item, "predecessor service command target argument",
            maximum=4096, allow_empty=True,
        )
        for item in raw_arguments
    )
    environment = _decode_predecessor_environment_v1(
        raw.get("target_environment"),
    )
    if kind == "none":
        if (
            any(raw.get(field) is not None for field in (
                "target_executable", "target_executable_hash",
                "python_module", "target_working_directory",
            ))
            or arguments or environment
        ):
            raise _invalid("predecessor empty service command binding")
        executable = executable_hash = module = working_directory = None
    else:
        executable = _predecessor_absolute_path_v1(
            raw.get("target_executable"),
            "predecessor service command executable",
        )
        executable_hash = _predecessor_digest_v1(
            raw.get("target_executable_hash"),
            "predecessor service command executable hash",
        )
        working_directory = _predecessor_absolute_path_v1(
            raw.get("target_working_directory"),
            "predecessor service command working directory", allow_root=True,
        )
        if kind == "python_module":
            module_value = raw.get("python_module")
            try:
                module_size = (
                    len(module_value.encode("utf-8"))
                    if type(module_value) is str else -1
                )
            except UnicodeEncodeError as exc:
                raise _invalid("predecessor service command python module") from exc
            if (
                type(module_value) is not str or module_size > 255
                or _PREDECESSOR_MODULE_RE_V1.fullmatch(module_value) is None
            ):
                raise _invalid("predecessor service command python module")
            module = module_value
        else:
            if raw.get("python_module") is not None:
                raise _invalid("predecessor service command python module")
            module = None
        if kind == "systemctl_stop" and (
            working_directory != "/" or len(arguments) < 2
            or arguments[0] != "stop"
            or any(
                _PREDECESSOR_UNIT_RE_V1.fullmatch(item) is None
                for item in arguments[1:]
            )
            or arguments[1:] != tuple(sorted(set(arguments[1:])))
            or environment
        ):
            raise _invalid("predecessor service command systemctl stop")
    return _DecodedPredecessorServiceCommandV1(
        entry_id, kind, executable, executable_hash, module, arguments,
        working_directory, environment,
    )


def _decode_predecessor_descriptor_v1(
    encoded: bytes,
) -> _DecodedPredecessorDescriptorV1:
    """Decode structural predecessor facts; this does not establish authority."""
    value = decode_canonical_json_v1(
        encoded, MAX_PREDECESSOR_DESCRIPTOR_BYTES_V1,
    )
    if (
        type(value) is not dict or frozenset(value) != _PREDECESSOR_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("predecessor descriptor schema")

    raw_files = value.get("files")
    if (
        type(raw_files) is not list
        or not 1 <= len(raw_files) <= MAX_PREDECESSOR_FILES_V1
    ):
        raise _invalid("predecessor file count")
    files: list[_DecodedPredecessorFileV1] = []
    for raw in raw_files:
        if type(raw) is not dict or frozenset(raw) != _PREDECESSOR_FILE_KEYS_V1:
            raise _invalid("predecessor file keys")
        size = raw.get("size")
        if type(size) is not int or not 0 <= size <= 2 ** 63 - 1:
            raise _invalid("predecessor file size")
        files.append(_DecodedPredecessorFileV1(
            _predecessor_relative_path_v1(raw.get("path")), size,
            _predecessor_digest_v1(
                raw.get("content_hash"), "predecessor file hash",
            ),
        ))
    paths = [item.path for item in files]
    if (
        paths != sorted(paths, key=lambda item: item.encode("utf-8"))
        or len(paths) != len(set(paths))
    ):
        raise _invalid("predecessor file order")

    raw_commands = value.get("service_commands")
    if (
        type(raw_commands) is not list
        or not 1 <= len(raw_commands) <= MAX_PREDECESSOR_SERVICE_COMMANDS_V1
    ):
        raise _invalid("predecessor service command count")
    commands = tuple(
        _decode_predecessor_service_command_v1(raw) for raw in raw_commands
    )
    entry_ids = [item.entry_id for item in commands]
    if (
        entry_ids != sorted(entry_ids)
        or len(entry_ids) != len(set(entry_ids))
    ):
        raise _invalid("predecessor service command order")

    predecessor_id = _predecessor_digest_v1(
        value.get("predecessor_id"), "predecessor descriptor id",
    )
    unsigned = {
        key: item for key, item in value.items() if key != "predecessor_id"
    }
    if predecessor_id != _digest(
        PREDECESSOR_DESCRIPTOR_ID_DOMAIN_V1, _canonical_json(unsigned),
    ):
        raise _invalid("predecessor descriptor id")
    decoded = _DecodedPredecessorDescriptorV1(
        predecessor_id,
        _predecessor_digest_v1(
            value.get("transaction_id"), "predecessor transaction id",
        ),
        _predecessor_absolute_path_v1(
            value.get("installation_root"), "predecessor installation root",
        ),
        tuple(files), commands,
        _predecessor_digest_v1(
            value.get("administrative_bundle_hash"),
            "predecessor administrative bundle hash",
        ),
        _predecessor_digest_v1(
            value.get("service_catalog_id"),
            "predecessor service catalog id",
        ),
        _predecessor_digest_v1(
            value.get("service_coverage_hash"),
            "predecessor service coverage hash",
        ),
    )
    if decoded.as_value() != value:
        raise _invalid("predecessor descriptor binding")
    return decoded


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
    require_single_link: bool = True,
) -> bytes:
    """Read one immutable regular file through a no-follow stable handle."""
    if (
        not isinstance(path, Path) or not path.is_absolute()
        or type(maximum) is not int or maximum < 0
        or type(uid) is not int or type(gid) is not int
        or (mode is not None and type(mode) is not int)
        or type(require_single_link) is not bool
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
        or before.st_uid != uid or before.st_gid != gid or before.st_nlink < 1
        or (require_single_link and before.st_nlink != 1)
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


class _OpenControlDirectoryV1(NamedTuple):
    key: str
    descriptor: int
    parent_descriptor: int | None
    basename: str | None
    identity: tuple[int, ...]
    tracked_names: tuple[str, ...]
    strict_inventory: bool


class _ControlFileV1(NamedTuple):
    key: str
    parent_descriptor: int
    basename: str
    identity: tuple[int, ...]
    maximum: int
    mode: int
    exact_size: int | None


def _control_names_v1(descriptor: int) -> tuple[str, ...]:
    try:
        names = os.listdir(descriptor)
    except OSError as exc:
        raise _recovery("control directory inventory") from exc
    if len(names) != len(set(names)):
        raise _recovery("duplicate control name")
    for name in names:
        try:
            valid = (
                isinstance(name, str) and name not in {"", ".", ".."}
                and "/" not in name and "\0" not in name
                and unicodedata.normalize("NFC", name) == name
                and bool(name.encode("utf-8"))
            )
        except UnicodeEncodeError as exc:
            raise _recovery("control name") from exc
        if not valid:
            raise _recovery("control name")
    return tuple(sorted(names, key=lambda value: value.encode("utf-8")))


def _control_directory_chain_snapshot_v1(
    directory: Path, *, uid: int, gid: int, stop: Path | None,
) -> tuple[tuple[Path, tuple[int, ...]], ...]:
    """Snapshot every trusted ancestor so policy still holds at return."""
    current = directory
    stop_value = stop.as_posix() if stop is not None else None
    checked_stop = stop is None
    observed: list[tuple[Path, tuple[int, ...]]] = []
    while True:
        try:
            info = current.lstat()
        except OSError as exc:
            raise _recovery("control directory chain") from exc
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o022
        ):
            raise _recovery("unsafe control directory chain")
        observed.append((current, _metadata_identity_v1(info)))
        if stop_value is not None and current.as_posix() == stop_value:
            checked_stop = True
            break
        if current.parent == current:
            break
        current = current.parent
    if not checked_stop:
        raise _recovery("control directory outside trusted root")
    return tuple(observed)


def _require_unchanged_control_directory_chain_v1(
    snapshot: tuple[tuple[Path, tuple[int, ...]], ...], *, uid: int, gid: int,
) -> None:
    for path, identity in snapshot:
        try:
            info = path.lstat()
        except OSError as exc:
            raise _recovery("control directory chain changed") from exc
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o022
            or _metadata_identity_v1(info) != identity
        ):
            raise _recovery("control directory chain changed")


def _require_control_directory_v1(
    info: os.stat_result, *, uid: int, gid: int,
) -> None:
    if (
        not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != uid or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != 0o755
    ):
        raise _recovery("control directory metadata")


def _open_control_child_directory_v1(
    parent_descriptor: int, basename: str, key: str, *, uid: int, gid: int,
) -> tuple[int, tuple[int, ...]]:
    try:
        before = os.stat(
            basename, dir_fd=parent_descriptor, follow_symlinks=False,
        )
        _require_control_directory_v1(before, uid=uid, gid=gid)
        descriptor = os.open(
            basename, _snapshot_open_flags_v1(True), dir_fd=parent_descriptor,
        )
    except PreflightError:
        raise
    except OSError as exc:
        raise _recovery("control directory open: " + key) from exc
    try:
        opened = os.fstat(descriptor)
        if _metadata_identity_v1(opened) != _metadata_identity_v1(before):
            raise _recovery("control directory replaced: " + key)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, _metadata_identity_v1(opened)


def _register_control_file_v1(
    parent_descriptor: int, basename: str, key: str, *,
    uid: int, gid: int, maximum: int, mode: int,
    exact_size: int | None = None,
) -> _ControlFileV1:
    try:
        info = os.stat(
            basename, dir_fd=parent_descriptor, follow_symlinks=False,
        )
    except OSError as exc:
        raise _recovery("control file metadata: " + key) from exc
    if (
        not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != uid or info.st_gid != gid or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != mode or info.st_size > maximum
        or (exact_size is not None and info.st_size != exact_size)
    ):
        raise _recovery("control file metadata: " + key)
    return _ControlFileV1(
        key, parent_descriptor, basename, _metadata_identity_v1(info),
        maximum, mode, exact_size,
    )


def _read_control_file_v1(file: _ControlFileV1) -> bytes:
    try:
        descriptor = os.open(
            file.basename, _snapshot_open_flags_v1(False),
            dir_fd=file.parent_descriptor,
        )
    except OSError as exc:
        raise _recovery("control file open: " + file.key) from exc
    try:
        before = os.fstat(descriptor)
        if _metadata_identity_v1(before) != file.identity:
            raise _recovery("control file replaced: " + file.key)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(
                descriptor, min(1024 * 1024, file.maximum + 1 - size),
            )
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > file.maximum:
                raise _recovery("control file size: " + file.key)
        after = os.fstat(descriptor)
        try:
            live = os.stat(
                file.basename, dir_fd=file.parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise _recovery("control file rebound: " + file.key) from exc
        if (
            size != before.st_size
            or _metadata_identity_v1(after) != file.identity
            or _metadata_identity_v1(live) != file.identity
        ):
            raise _recovery("control file changed: " + file.key)
        return b"".join(chunks)
    except PreflightError:
        raise
    except OSError as exc:
        raise _recovery("control file read: " + file.key) from exc
    finally:
        os.close(descriptor)


def _authority_checkpoint_v1(index: int) -> bytes:
    completed = list(_AUTHORITY_KINDS_V1[:min(index, 3)])
    state = "verified" if index == 4 else "preparing"
    if index == 3:
        state = "complete"
    return _canonical_json({
        "schema_version": 1, "checkpoint_sequence": index,
        "state": state, "completed": completed,
    })


def _paired_control_stems_v1(
    names: tuple[str, ...], *, pattern: re.Pattern[str], label: str,
) -> tuple[str, ...]:
    if any(
        name.startswith(".")
        or re.fullmatch(r".+\.(?:json|sig)", name) is None
        for name in names
    ):
        raise _recovery("unexpected " + label + " object")
    stems = tuple(sorted(
        {name.rsplit(".", 1)[0] for name in names},
        key=lambda value: value.encode("utf-8"),
    ))
    if (
        any(pattern.fullmatch(stem) is None for stem in stems)
        or len(names) != len(stems) * 2
        or any(
            f"{stem}.json" not in names or f"{stem}.sig" not in names
            for stem in stems
        )
    ):
        raise _recovery("incomplete " + label + " pair")
    return stems


def _capture_fixed_ownership_state_core_v1(
    ownership_root: Path, *, uid: int, gid: int,
    chain_stop: Path | None,
    between_for_test: Callable[[], None] | None = None,
) -> _CapturedFixedOwnershipStateCandidateV1:
    """Capture one handle-bound fixed-store candidate without authorizing it."""
    if (
        not isinstance(ownership_root, Path) or not ownership_root.is_absolute()
        or type(uid) is not int or type(gid) is not int
        or (
            chain_stop is not None and (
                not isinstance(chain_stop, Path) or not chain_stop.is_absolute()
                or not ownership_root.is_relative_to(chain_stop)
            )
        )
        or (between_for_test is not None and not callable(between_for_test))
    ):
        raise _invalid("ownership capture arguments")

    directories: list[_OpenControlDirectoryV1] = []
    files: dict[str, _ControlFileV1] = {}
    root_descriptor = -1

    def add_file(
        parent: int, name: str, key: str, *, maximum: int,
        mode: int = 0o644, exact_size: int | None = None,
    ) -> _ControlFileV1:
        if key in files:
            raise _recovery("duplicate control path")
        item = _register_control_file_v1(
            parent, name, key, uid=uid, gid=gid, maximum=maximum,
            mode=mode, exact_size=exact_size,
        )
        files[key] = item
        return item

    def add_directory(
        parent: int, name: str, key: str, *, strict: bool = True,
    ) -> tuple[int, tuple[str, ...]]:
        descriptor, identity = _open_control_child_directory_v1(
            parent, name, key, uid=uid, gid=gid,
        )
        registered = False
        try:
            names = _control_names_v1(descriptor)
            directories.append(_OpenControlDirectoryV1(
                key, descriptor, parent, name, identity, names, strict,
            ))
            registered = True
        except BaseException:
            if not registered:
                os.close(descriptor)
            raise
        return descriptor, names

    def decode_durable(callable_, *arguments):
        try:
            return callable_(*arguments)
        except PreflightError as exc:
            raise _recovery("durable ownership object") from exc

    try:
        directory_chain_snapshot = _control_directory_chain_snapshot_v1(
            ownership_root.parent, uid=uid, gid=gid, stop=chain_stop,
        )
        try:
            path_before = ownership_root.lstat()
            _require_control_directory_v1(path_before, uid=uid, gid=gid)
            root_descriptor = os.open(
                ownership_root, _snapshot_open_flags_v1(True),
            )
            root_opened = os.fstat(root_descriptor)
        except PreflightError:
            raise
        except OSError as exc:
            raise _recovery("ownership root open") from exc
        if _metadata_identity_v1(path_before) != _metadata_identity_v1(root_opened):
            raise _recovery("ownership root replaced")
        root_names = _control_names_v1(root_descriptor)
        relevant_root_names = frozenset({
            "authorities-v1", "chain-v1", "coordinator-v1",
            "ownership-cutover-v1.json", "ownership-cutover-v1.sig",
            "predecessor-v1.json", "preflight-attestations-v1",
        })
        anchor_like_names = tuple(
            name for name in root_names
            if name.lstrip(".").startswith("ownership-cutover-v1.")
        )
        if any(name not in relevant_root_names for name in anchor_like_names):
            raise _recovery("unexpected anchor object")
        tracked_root_names = tuple(
            name for name in root_names if name in relevant_root_names
        )
        if not {"authorities-v1", "chain-v1", "coordinator-v1"}.issubset(
            tracked_root_names
        ):
            raise _recovery("ownership root inventory")
        directories.append(_OpenControlDirectoryV1(
            ".", root_descriptor, None, None,
            _metadata_identity_v1(root_opened), tracked_root_names, False,
        ))

        authority_fd, authority_names = add_directory(
            root_descriptor, "authorities-v1", "authorities-v1",
        )
        expected_authority_names = frozenset({
            *_AUTHORITY_REGISTRY_BASENAMES_V1,
            *_AUTHORITY_PRIVATE_BASENAMES_V1,
            *_AUTHORITY_CHECKPOINT_BASENAMES_V1,
        })
        if frozenset(authority_names) != expected_authority_names:
            raise _recovery("authority inventory")
        for name in _AUTHORITY_REGISTRY_BASENAMES_V1:
            add_file(
                authority_fd, name, "authorities-v1/" + name,
                maximum=MAX_REGISTRY_BYTES,
            )
        for name in _AUTHORITY_PRIVATE_BASENAMES_V1:
            add_file(
                authority_fd, name, "authorities-v1/" + name,
                maximum=32, mode=0o600, exact_size=32,
            )
        for name in _AUTHORITY_CHECKPOINT_BASENAMES_V1:
            add_file(
                authority_fd, name, "authorities-v1/" + name,
                maximum=MAX_AUTHORITY_CHECKPOINT_BYTES_V1,
            )

        chain_fd, chain_names = add_directory(
            root_descriptor, "chain-v1", "chain-v1",
        )
        allowed_chain_names = frozenset({
            "builds-v1", "cutovers-v1", "heads-v1",
            "context-transitions-v1",
            "required-head-v1.bin", ".required-head-v1.lock",
        })
        if (
            not {
                "builds-v1", "cutovers-v1", "heads-v1",
                "context-transitions-v1",
            }.issubset(chain_names)
            or any(name not in allowed_chain_names for name in chain_names)
        ):
            raise _recovery("chain inventory")
        build_fd, build_names = add_directory(
            chain_fd, "builds-v1", "chain-v1/builds-v1",
        )
        cutover_fd, cutover_names = add_directory(
            chain_fd, "cutovers-v1", "chain-v1/cutovers-v1",
        )
        head_fd, head_names = add_directory(
            chain_fd, "heads-v1", "chain-v1/heads-v1",
        )
        context_transition_fd, context_transition_names = add_directory(
            chain_fd, "context-transitions-v1",
            "chain-v1/context-transitions-v1",
        )
        if (
            len(context_transition_names) > MAX_CONTEXT_TRANSITIONS_V1
            or any(
                _CONTEXT_TRANSITION_BASENAME_RE_V1.fullmatch(name) is None
                for name in context_transition_names
            )
        ):
            raise _recovery("context transition inventory")
        for name in context_transition_names:
            add_file(
                context_transition_fd, name,
                "chain-v1/context-transitions-v1/" + name,
                maximum=MAX_CONTEXT_TRANSITION_BYTES_V1,
            )
        build_stems = _paired_control_stems_v1(
            build_names, pattern=_ARCHIVED_DIGEST_STEM_RE_V1, label="build",
        )
        cutover_stems = _paired_control_stems_v1(
            cutover_names, pattern=_ARCHIVED_DIGEST_STEM_RE_V1,
            label="cutover",
        )
        head_stems = _paired_control_stems_v1(
            head_names, pattern=_ARCHIVED_HEAD_STEM_RE_V1, label="head",
        )
        for descriptor, prefix, stems, maximum in (
            (build_fd, "chain-v1/builds-v1", build_stems, MAX_MANIFEST_BYTES),
            (cutover_fd, "chain-v1/cutovers-v1", cutover_stems,
             MAX_CUTOVER_BYTES_V1),
            (head_fd, "chain-v1/heads-v1", head_stems, MAX_HEAD_BYTES_V1),
        ):
            for stem in stems:
                add_file(
                    descriptor, stem + ".json", f"{prefix}/{stem}.json",
                    maximum=maximum,
                )
                add_file(
                    descriptor, stem + ".sig", f"{prefix}/{stem}.sig",
                    maximum=64, exact_size=64,
                )
        if "required-head-v1.bin" in chain_names:
            add_file(
                chain_fd, "required-head-v1.bin",
                "chain-v1/required-head-v1.bin",
                maximum=MAX_REQUIRED_HEAD_BYTES_V1,
            )
        if ".required-head-v1.lock" in chain_names:
            add_file(
                chain_fd, ".required-head-v1.lock",
                "chain-v1/.required-head-v1.lock", maximum=1,
                mode=0o600, exact_size=1,
            )

        anchor_names = {
            "ownership-cutover-v1.json", "ownership-cutover-v1.sig",
        } & set(tracked_root_names)
        if anchor_names not in (set(), {
            "ownership-cutover-v1.json", "ownership-cutover-v1.sig",
        }):
            raise _recovery("anchor pair")
        if anchor_names:
            add_file(
                root_descriptor, "ownership-cutover-v1.json",
                "ownership-cutover-v1.json", maximum=MAX_CUTOVER_BYTES_V1,
            )
            add_file(
                root_descriptor, "ownership-cutover-v1.sig",
                "ownership-cutover-v1.sig", maximum=64, exact_size=64,
            )
        if "predecessor-v1.json" in tracked_root_names:
            add_file(
                root_descriptor, "predecessor-v1.json", "predecessor-v1.json",
                maximum=MAX_PREDECESSOR_DESCRIPTOR_BYTES_V1,
            )

        preflight_attestation_names: tuple[str, ...] = ()
        if "preflight-attestations-v1" in tracked_root_names:
            attestation_fd, preflight_attestation_names = add_directory(
                root_descriptor, "preflight-attestations-v1",
                "preflight-attestations-v1",
            )
            if (
                len(preflight_attestation_names)
                > MAX_PREFLIGHT_ATTESTATIONS_V1
                or any(
                    _PREFLIGHT_ATTESTATION_BASENAME_RE_V1.fullmatch(name)
                    is None
                    for name in preflight_attestation_names
                )
            ):
                raise _recovery("preflight attestation inventory")
            for name in preflight_attestation_names:
                add_file(
                    attestation_fd, name,
                    "preflight-attestations-v1/" + name,
                    maximum=MAX_PREFLIGHT_ATTESTATION_BYTES_V1,
                )

        coordinator_fd, coordinator_names = add_directory(
            root_descriptor, "coordinator-v1", "coordinator-v1",
        )
        legacy_names = tuple(
            name for name in coordinator_names
            if _LEGACY_RECORD_RE_V1.fullmatch(name) is not None
        )
        allowed_coordinator = {
            *legacy_names, "successor-claims-v1", "transactions-v2",
            "legacy-disposition-v2.json",
        }
        if any(name not in allowed_coordinator for name in coordinator_names):
            raise _recovery("coordinator inventory")
        legacy_indices = tuple(
            int(_LEGACY_RECORD_RE_V1.fullmatch(name).group(1))
            for name in legacy_names
        )
        if legacy_indices != tuple(range(len(legacy_indices))) or len(legacy_indices) > 2:
            raise _recovery("legacy journal inventory")
        for name in legacy_names:
            add_file(
                coordinator_fd, name, "coordinator-v1/" + name,
                maximum=MAX_COORDINATOR_RECORD_BYTES_V2,
            )
        if "legacy-disposition-v2.json" in coordinator_names:
            add_file(
                coordinator_fd, "legacy-disposition-v2.json",
                "coordinator-v1/legacy-disposition-v2.json",
                maximum=MAX_COORDINATOR_CONTROL_BYTES_V2,
            )

        claim_names: tuple[str, ...] = ()
        claim_fd = -1
        if "successor-claims-v1" in coordinator_names:
            claim_fd, claim_names = add_directory(
                coordinator_fd, "successor-claims-v1",
                "coordinator-v1/successor-claims-v1",
            )
            if any(
                _SUCCESSOR_CLAIM_BASENAME_RE_V1.fullmatch(name) is None
                for name in claim_names
            ):
                raise _recovery("claim inventory")
            for name in claim_names:
                add_file(
                    claim_fd, name,
                    "coordinator-v1/successor-claims-v1/" + name,
                    maximum=MAX_COORDINATOR_CONTROL_BYTES_V2,
                )

        transaction_names: tuple[str, ...] = ()
        transaction_root_fd = -1
        transaction_record_names: dict[str, tuple[str, ...]] = {}
        if "transactions-v2" in coordinator_names:
            transaction_root_fd, transaction_names = add_directory(
                coordinator_fd, "transactions-v2",
                "coordinator-v1/transactions-v2",
            )
            if any(
                _TRANSACTION_DIRECTORY_RE_V2.fullmatch(name) is None
                for name in transaction_names
            ):
                raise _recovery("transaction inventory")
            for request_id in transaction_names:
                transaction_fd, record_names = add_directory(
                    transaction_root_fd, request_id,
                    "coordinator-v1/transactions-v2/" + request_id,
                )
                indices = []
                for name in record_names:
                    matched = _TRANSACTION_RECORD_RE_V2.fullmatch(name)
                    if matched is None:
                        raise _recovery("transaction record inventory")
                    indices.append(int(matched.group(1)))
                if (
                    not 1 <= len(indices) <= 7
                    or tuple(indices) != tuple(range(len(indices)))
                ):
                    raise _recovery("transaction record sequence")
                transaction_record_names[request_id] = record_names
                for name in record_names:
                    add_file(
                        transaction_fd, name,
                        f"coordinator-v1/transactions-v2/{request_id}/{name}",
                        maximum=MAX_COORDINATOR_RECORD_BYTES_V2,
                    )

        registry_bytes = tuple(
            _read_control_file_v1(files["authorities-v1/" + name])
            for name in _AUTHORITY_REGISTRY_BASENAMES_V1
        )
        registries = decode_durable(
            _decode_ownership_registry_set_v1, *registry_bytes,
        )
        for index, name in enumerate(_AUTHORITY_CHECKPOINT_BASENAMES_V1):
            observed = _read_control_file_v1(files["authorities-v1/" + name])
            if observed != _authority_checkpoint_v1(index):
                raise _recovery("authority checkpoint")

        builds: list[_CapturedSignedObjectCandidateV1] = []
        cutovers: list[_CapturedSignedObjectCandidateV1] = []
        heads: list[_CapturedSignedObjectCandidateV1] = []
        for stem in build_stems:
            encoded = _read_control_file_v1(
                files[f"chain-v1/builds-v1/{stem}.json"],
            )
            signature = _read_control_file_v1(
                files[f"chain-v1/builds-v1/{stem}.sig"],
            )
            value, _manifest_files = decode_durable(
                _parse_distribution_manifest_v1, encoded,
            )
            if value["closed_build_id"].removeprefix("sha256:") != stem:
                raise _recovery("build name binding")
            builds.append(_CapturedSignedObjectCandidateV1(
                stem, encoded, signature,
            ))
        for stem in cutover_stems:
            encoded = _read_control_file_v1(
                files[f"chain-v1/cutovers-v1/{stem}.json"],
            )
            signature = _read_control_file_v1(
                files[f"chain-v1/cutovers-v1/{stem}.sig"],
            )
            decoded = decode_durable(
                _decode_ownership_cutover_v1, encoded, signature,
            )
            if decoded.cutover_id.removeprefix("sha256:") != stem:
                raise _recovery("cutover name binding")
            cutovers.append(_CapturedSignedObjectCandidateV1(
                stem, encoded, signature,
            ))
        for stem in head_stems:
            encoded = _read_control_file_v1(
                files[f"chain-v1/heads-v1/{stem}.json"],
            )
            signature = _read_control_file_v1(
                files[f"chain-v1/heads-v1/{stem}.sig"],
            )
            decoded = decode_durable(
                _decode_ownership_head_v1, encoded, signature,
            )
            expected_stem = (
                f"{decoded.release_sequence:020d}-"
                f"{decoded.cutover_id.removeprefix('sha256:')}"
            )
            if expected_stem != stem:
                raise _recovery("head name binding")
            heads.append(_CapturedSignedObjectCandidateV1(
                stem, encoded, signature,
            ))

        anchor = None
        if anchor_names:
            anchor = decode_durable(
                _decode_ownership_cutover_v1,
                _read_control_file_v1(files["ownership-cutover-v1.json"]),
                _read_control_file_v1(files["ownership-cutover-v1.sig"]),
            )
        required_head = None
        if "required-head-v1.bin" in chain_names:
            required_head = decode_durable(
                _decode_fixed_required_head_frame_v1,
                _read_control_file_v1(
                    files["chain-v1/required-head-v1.bin"],
                ),
            )
        empty_chain = (
            anchor is None and required_head is None
            and not builds and not cutovers and not heads
        )
        if empty_chain and "chain-v1/.required-head-v1.lock" in files:
            raise _recovery("partial ownership chain")
        if anchor is None and (
            required_head is not None or builds or cutovers or heads
        ):
            raise _recovery("partial ownership chain")
        if required_head is not None and (
            anchor is None or not builds or not cutovers or not heads
        ):
            raise _recovery("partial ownership chain")
        if "chain-v1/.required-head-v1.lock" in files and _read_control_file_v1(
            files["chain-v1/.required-head-v1.lock"],
        ) != b"\0":
            raise _recovery("required head lock")

        claims: list[_CapturedClaimCandidateV1] = []
        seen_claim_ids: set[str] = set()
        seen_request_ids: set[str] = set()
        for name in claim_names:
            encoded = _read_control_file_v1(files[
                "coordinator-v1/successor-claims-v1/" + name
            ])
            decoded = decode_durable(_decode_successor_claim_v1, encoded)
            expected_name = (
                "initial.json" if decoded.release_sequence == 1
                else decoded.previous_head_id.removeprefix("sha256:") + ".json"
            )
            if (
                name != expected_name or decoded.claim_id in seen_claim_ids
                or decoded.request_id in seen_request_ids
            ):
                raise _recovery("claim binding")
            seen_claim_ids.add(decoded.claim_id)
            seen_request_ids.add(decoded.request_id)
            claims.append(_CapturedClaimCandidateV1(name, encoded, decoded))
        claims.sort(key=lambda item: item.decoded.release_sequence)
        if tuple(item.decoded.release_sequence for item in claims) != tuple(
            range(1, len(claims) + 1)
        ):
            raise _recovery("claim sequence")

        transactions: list[_CapturedTransactionCandidateV2] = []
        for request_id in transaction_names:
            encoded_records = tuple(
                _read_control_file_v1(files[
                    f"coordinator-v1/transactions-v2/{request_id}/{name}"
                ])
                for name in transaction_record_names[request_id]
            )
            decoded_prefix = None
            if len(encoded_records) <= len(_COORDINATOR_STATES_V1):
                decoded_prefix = decode_durable(
                    _decode_coordinator_prefix_v2, encoded_records,
                )
                if decoded_prefix.records[0].request_id != request_id:
                    raise _recovery("transaction request binding")
            transactions.append(_CapturedTransactionCandidateV2(
                request_id, encoded_records, decoded_prefix,
            ))

        legacy_records = tuple(
            (name, _read_control_file_v1(files["coordinator-v1/" + name]))
            for name in legacy_names
        )
        legacy_disposition = (
            _read_control_file_v1(
                files["coordinator-v1/legacy-disposition-v2.json"],
            )
            if "coordinator-v1/legacy-disposition-v2.json" in files else None
        )
        predecessor = (
            decode_durable(
                _decode_predecessor_descriptor_v1,
                _read_control_file_v1(files["predecessor-v1.json"]),
            )
            if "predecessor-v1.json" in files else None
        )
        context_transitions = tuple(
            _CapturedContextTransitionCandidateV1(
                name, _read_control_file_v1(
                    files["chain-v1/context-transitions-v1/" + name],
                ),
            )
            for name in context_transition_names
        )
        preflight_attestations = tuple(
            _CapturedPreflightAttestationCandidateV1(
                name, _read_control_file_v1(
                    files["preflight-attestations-v1/" + name],
                ),
            )
            for name in preflight_attestation_names
        )

        if between_for_test is not None:
            between_for_test()

        current_root_names = _control_names_v1(root_descriptor)
        relevant_root_now = tuple(
            name for name in current_root_names if name in relevant_root_names
        )
        if any(
            name not in relevant_root_names
            for name in current_root_names
            if name.lstrip(".").startswith("ownership-cutover-v1.")
        ):
            raise _recovery("unexpected anchor object")
        if relevant_root_now != tracked_root_names:
            raise _recovery("ownership root inventory changed")
        for directory in directories:
            if _metadata_identity_v1(os.fstat(directory.descriptor)) != directory.identity:
                raise _recovery("control directory changed: " + directory.key)
            current_names = _control_names_v1(directory.descriptor)
            if directory.strict_inventory and current_names != directory.tracked_names:
                raise _recovery("control inventory changed: " + directory.key)
            if directory.parent_descriptor is not None:
                try:
                    live = os.stat(
                        directory.basename, dir_fd=directory.parent_descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise _recovery(
                        "control directory rebound: " + directory.key,
                    ) from exc
                if _metadata_identity_v1(live) != directory.identity:
                    raise _recovery("control directory rebound: " + directory.key)
        for file in files.values():
            try:
                live = os.stat(
                    file.basename, dir_fd=file.parent_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise _recovery("control file rebound: " + file.key) from exc
            if _metadata_identity_v1(live) != file.identity:
                raise _recovery("control file changed: " + file.key)
        try:
            path_after = ownership_root.lstat()
        except OSError as exc:
            raise _recovery("ownership root rebound") from exc
        if _metadata_identity_v1(path_after) != directories[0].identity:
            raise _recovery("ownership root rebound")
        _require_unchanged_control_directory_chain_v1(
            directory_chain_snapshot, uid=uid, gid=gid,
        )

        return _CapturedFixedOwnershipStateCandidateV1(
            registries, anchor, required_head, tuple(builds), tuple(cutovers),
            tuple(heads), tuple(claims), tuple(transactions),
            context_transitions,
            preflight_attestations, legacy_records, legacy_disposition,
            predecessor,
        )
    except PreflightError as exc:
        if exc.code == CODE_RECOVERY:
            raise
        raise _recovery("fixed ownership capture") from exc
    except (OSError, ValueError, TypeError, MemoryError) as exc:
        raise _recovery("fixed ownership capture") from exc
    finally:
        closed: set[int] = set()
        for directory in reversed(directories):
            if directory.descriptor not in closed:
                os.close(directory.descriptor)
                closed.add(directory.descriptor)
        if root_descriptor >= 0 and root_descriptor not in closed:
            os.close(root_descriptor)


def _capture_fixed_ownership_state_v1() -> _CapturedFixedOwnershipStateCandidateV1:
    """Observe only the fixed root-owned productive store."""
    require_linux_before_io_v1()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise _invalid("effective identity")
    return _capture_fixed_ownership_state_core_v1(
        OWNERSHIP_ROOT, uid=0, gid=0, chain_stop=None,
    )


def _capture_fixed_ownership_state_for_test_v1(
    ownership_root: Path, *, between_for_test: Callable[[], None] | None = None,
) -> _CapturedFixedOwnershipStateForTestV1:
    """Portable nominal seam; its result cannot enter productive authority."""
    candidate = _capture_fixed_ownership_state_core_v1(
        Path(ownership_root), uid=os.getuid(), gid=os.getgid(),
        chain_stop=Path(ownership_root).parent,
        between_for_test=between_for_test,
    )
    return _CapturedFixedOwnershipStateForTestV1(candidate)


def _authenticate_fixed_ownership_snapshot_core_v1(
    candidate: _CapturedFixedOwnershipStateCandidateV1, *,
    openssl_executable: Path, temporary_root: Path,
    temporary_uid: int, temporary_gid: int, chain_stop: Path | None,
) -> _ReconciledFixedOwnershipSnapshotV1:
    """Authenticate one coherent snapshot without asserting live effects.

    The candidate bytes are the only authority input.  In particular this
    function never reopens a registry or ownership object by pathname.  The
    The result intentionally does not attest installed-tree or live systemd.
    It does authenticate every durable preflight document and its record-006
    reference; the later operational pass independently repeats live checks.
    """
    if type(candidate) is not _CapturedFixedOwnershipStateCandidateV1:
        raise _invalid("fixed ownership candidate type")
    registries = {item.authority: item for item in candidate.registries}
    if set(registries) != set(_AUTHORITY_KINDS_V1):
        raise _invalid("fixed ownership registry set")

    def durable(callable_, *arguments, **keywords):
        try:
            return callable_(*arguments, **keywords)
        except PreflightError as exc:
            if exc.code == CODE_RECOVERY:
                raise
            raise _recovery("fixed ownership authentication") from exc

    def verify_signature(
        registry: OwnershipPublicKeyFactsV1, *, signing_key_id: str,
        domain: bytes, encoded: bytes, signature: bytes,
    ) -> None:
        if signing_key_id != registry.key_id:
            raise _recovery("fixed ownership signing key")
        durable(
            _verify_ed25519_openssl_core_v1,
            registry.raw_public_key, domain + encoded, signature,
            openssl_executable=openssl_executable,
            temporary_root=temporary_root,
            temporary_uid=temporary_uid, temporary_gid=temporary_gid,
            chain_stop=chain_stop,
        )

    try:
        builds: list[_AuthenticatedDistributionObjectV1] = []
        builds_by_id: dict[str, _AuthenticatedDistributionObjectV1] = {}
        build_sequences: set[int] = set()
        for captured in candidate.builds:
            value, files = durable(
                _parse_distribution_manifest_v1, captured.encoded,
            )
            if value["signing_key_id"] != registries["distribution"].key_id:
                raise _recovery("distribution signing key")
            durable(
                _verify_ed25519_openssl_core_v1,
                registries["distribution"].raw_public_key,
                SIGNATURE_DOMAIN + captured.encoded, captured.signature,
                openssl_executable=openssl_executable,
                temporary_root=temporary_root,
                temporary_uid=temporary_uid, temporary_gid=temporary_gid,
                chain_stop=chain_stop,
            )
            facts = _distribution_facts_v1(value)
            if (
                facts.closed_build_id.removeprefix("sha256:") != captured.stem
                or facts.closed_build_id in builds_by_id
                or facts.release_sequence in build_sequences
            ):
                raise _recovery("distribution archive fork")
            authenticated = _AuthenticatedDistributionObjectV1(
                facts, files, captured.encoded, captured.signature,
            )
            builds.append(authenticated)
            builds_by_id[facts.closed_build_id] = authenticated
            build_sequences.add(facts.release_sequence)

        cutovers: list[_DecodedOwnershipCutoverV1] = []
        cutovers_by_id: dict[str, _DecodedOwnershipCutoverV1] = {}
        for captured in candidate.cutovers:
            decoded = durable(
                _decode_ownership_cutover_v1,
                captured.encoded, captured.signature,
            )
            verify_signature(
                registries["cutover"], signing_key_id=decoded.signing_key_id,
                domain=CUTOVER_SIGNATURE_DOMAIN_V1,
                encoded=decoded.encoded, signature=decoded.signature,
            )
            if (
                decoded.cutover_id.removeprefix("sha256:") != captured.stem
                or decoded.cutover_id in cutovers_by_id
            ):
                raise _recovery("cutover archive fork")
            cutovers.append(decoded)
            cutovers_by_id[decoded.cutover_id] = decoded

        heads: list[_DecodedOwnershipHeadV1] = []
        heads_by_sequence: dict[int, _DecodedOwnershipHeadV1] = {}
        for captured in candidate.heads:
            decoded = durable(
                _decode_ownership_head_v1,
                captured.encoded, captured.signature,
            )
            verify_signature(
                registries["head"], signing_key_id=decoded.signing_key_id,
                domain=HEAD_SIGNATURE_DOMAIN_V1,
                encoded=decoded.encoded, signature=decoded.signature,
            )
            expected_stem = (
                f"{decoded.release_sequence:020d}-"
                f"{decoded.cutover_id.removeprefix('sha256:')}"
            )
            if (
                expected_stem != captured.stem
                or decoded.release_sequence in heads_by_sequence
            ):
                raise _recovery("head archive fork")
            heads.append(decoded)
            heads_by_sequence[decoded.release_sequence] = decoded

        anchor = candidate.anchor
        required_head = candidate.required_head
        empty_chain = anchor is None and required_head is None and not heads
        if empty_chain:
            if builds or cutovers:
                raise _recovery("partial authenticated ownership chain")
        else:
            if anchor is None:
                raise _recovery("partial authenticated ownership chain")
            verify_signature(
                registries["cutover"], signing_key_id=anchor.signing_key_id,
                domain=CUTOVER_SIGNATURE_DOMAIN_V1,
                encoded=anchor.encoded, signature=anchor.signature,
            )
            archived_anchor = cutovers_by_id.get(anchor.cutover_id)
            if (
                archived_anchor is not None
                and (
                    archived_anchor.encoded != anchor.encoded
                    or archived_anchor.signature != anchor.signature
                )
            ):
                raise _recovery("anchor archive copy")
            if required_head is not None:
                verify_signature(
                    registries["head"],
                    signing_key_id=required_head.signing_key_id,
                    domain=HEAD_SIGNATURE_DOMAIN_V1,
                    encoded=required_head.encoded,
                    signature=required_head.signature,
                )
                archived_required = heads_by_sequence.get(
                    required_head.release_sequence,
                )
                if (
                    archived_required is None
                    or archived_required.encoded != required_head.encoded
                    or archived_required.signature != required_head.signature
                    or archived_required.head_id != required_head.head_id
                ):
                    raise _recovery("required head archive copy")
            if not heads:
                maximum_sequence = 0
            else:
                maximum_sequence = max(heads_by_sequence)
            if set(heads_by_sequence) != set(range(1, maximum_sequence + 1)):
                raise _recovery("ownership head gap")

            previous_head = None
            previous_build = None
            for sequence in range(1, maximum_sequence + 1):
                head = heads_by_sequence[sequence]
                build = builds_by_id.get(head.closed_build_id)
                cutover = cutovers_by_id.get(head.cutover_id)
                if build is None or cutover is None:
                    raise _recovery("ownership chain missing object")
                if (
                    build.facts.release_sequence != sequence
                    or cutover.closed_build_id != head.closed_build_id
                ):
                    raise _recovery("ownership chain object binding")
                if previous_head is None:
                    if (
                        head.previous_head_id is not None
                        or head.cutover_id != anchor.cutover_id
                        or head.closed_build_id != anchor.closed_build_id
                        or build.facts.previous_closed_build_id is not None
                    ):
                        raise _recovery("ownership anchor link")
                elif (
                    head.previous_head_id != previous_head.head_id
                    or cutover.previous_cutover_id != previous_head.cutover_id
                    or build.facts.previous_closed_build_id
                    != previous_build.facts.closed_build_id
                ):
                    raise _recovery("ownership predecessor link")
                previous_head = head
                previous_build = build
            # A successor build or certificate may already be durably appended
            # while the older required head remains authoritative.  All such
            # objects are authenticated above, but only a head may select them.

        legacy_prefix = None
        if candidate.legacy_records:
            legacy_prefix = durable(
                _decode_legacy_coordinator_prefix_v1,
                tuple(encoded for _name, encoded in candidate.legacy_records),
            )
        legacy_disposition = (
            durable(_decode_legacy_disposition_v2, candidate.legacy_disposition)
            if candidate.legacy_disposition is not None else None
        )

        claims = tuple(item.decoded for item in candidate.claims)
        claims_by_id = {claim.claim_id: claim for claim in claims}
        if len(claims_by_id) != len(claims):
            raise _recovery("duplicate successor claim")
        transaction_by_claim: dict[str, _AuthenticatedTransactionSnapshotV2] = {}
        transactions: list[_AuthenticatedTransactionSnapshotV2] = []
        for captured in candidate.transactions:
            prefix = captured.decoded_prefix
            if prefix is None or not prefix.records:
                raise _recovery("transaction prefix unavailable")
            first = prefix.records[0]
            claim = claims_by_id.get(first.successor_claim_id)
            if (
                claim is None or claim.claim_id in transaction_by_claim
                or captured.request_id != first.request_id
                or claim.request_id != first.request_id
                or claim.source_id != first.source_id
                or claim.closed_build_id != first.closed_build_id
                or claim.release_sequence != first.release_sequence
                or claim.previous_head_id != first.previous_head_id
                or first.request_id != _coordinator_request_id_v1(
                    first.closed_build_id, first.previous_closed_build_id,
                    first.previous_cutover_id,
                )
            ):
                raise _recovery("claim transaction binding")
            resolved = _AuthenticatedTransactionSnapshotV2(claim, prefix)
            transaction_by_claim[claim.claim_id] = resolved
            transactions.append(resolved)
        transactions.sort(key=lambda item: item.claim.release_sequence)

        pending_claims: list[_DecodedSuccessorClaimV1] = []
        previous_transaction = None
        for index, claim in enumerate(claims):
            transaction = transaction_by_claim.get(claim.claim_id)
            if claim.release_sequence == 1:
                expected_request_id = _coordinator_request_id_v1(
                    claim.closed_build_id, None, None,
                )
            else:
                if (
                    previous_transaction is None
                    or previous_transaction.prefix.records[-1].sequence != 6
                    or claim.previous_head_id
                    != previous_transaction.prefix.records[-1].head_id
                ):
                    raise _recovery("claim predecessor")
                previous_first = previous_transaction.prefix.records[0]
                previous_latest = previous_transaction.prefix.records[-1]
                expected_request_id = _coordinator_request_id_v1(
                    claim.closed_build_id, previous_first.closed_build_id,
                    previous_latest.cutover_id,
                )
            if claim.request_id != expected_request_id:
                raise _recovery("claim request")
            if transaction is None:
                if index != len(claims) - 1:
                    raise _recovery("nonterminal pending claim")
                pending_claims.append(claim)
                continue
            first = transaction.prefix.records[0]
            if claim.release_sequence == 1:
                if (
                    first.previous_closed_build_id is not None
                    or first.previous_cutover_id is not None
                ):
                    raise _recovery("initial transaction predecessor")
            else:
                previous_first = previous_transaction.prefix.records[0]
                previous_latest = previous_transaction.prefix.records[-1]
                if (
                    first.previous_closed_build_id != previous_first.closed_build_id
                    or first.previous_cutover_id != previous_latest.cutover_id
                    or first.previous_head_id != previous_latest.head_id
                ):
                    raise _recovery("successor transaction predecessor")
            previous_transaction = transaction

        if legacy_prefix is not None:
            latest_legacy = legacy_prefix.records[-1]
            if legacy_disposition is None:
                if transactions or len(claims) > 1:
                    raise _recovery("legacy prefix order")
            else:
                successor = tuple(
                    claim for claim in claims
                    if claim.request_id == legacy_disposition.successor_request_id
                )
                if (
                    len(successor) != 1
                    or successor[0].release_sequence != 1
                    or legacy_disposition.legacy_journal_hash
                    != _legacy_journal_hash_v2(legacy_prefix.encoded_records)
                    or legacy_disposition.legacy_request_id
                    != latest_legacy.request_id
                    or legacy_disposition.legacy_state != latest_legacy.state
                ):
                    raise _recovery("legacy disposition binding")
        elif legacy_disposition is not None:
            raise _recovery("orphan legacy disposition")

        completed_by_sequence: dict[int, _AuthenticatedTransactionSnapshotV2] = {}
        for transaction in transactions:
            first = transaction.prefix.records[0]
            latest = transaction.prefix.records[-1]
            build = builds_by_id.get(first.closed_build_id)
            if latest.sequence >= 5 and build is None:
                raise _recovery("transaction build missing")
            if build is not None and (
                first.distribution_payload_hash != _raw_sha256_v1(build.encoded)
                or first.distribution_signature_hash
                != _raw_sha256_v1(build.signature)
                or first.boundary_inventory_hash
                != build.facts.boundary_inventory_hash
                or first.boundary_guard_version
                != build.facts.boundary_guard_version
                or first.previous_closed_build_id
                != build.facts.previous_closed_build_id
                or first.release_sequence != build.facts.release_sequence
            ):
                raise _recovery("transaction distribution binding")
            if latest.sequence >= 2:
                cutover = cutovers_by_id.get(latest.cutover_id)
                if (
                    cutover is None and anchor is not None
                    and anchor.cutover_id == latest.cutover_id
                ):
                    cutover = anchor
                if latest.sequence >= 3 and cutover is None:
                    raise _recovery("transaction cutover missing")
                if cutover is not None and (
                    cutover.request_id != first.request_id
                    or cutover.previous_cutover_id != first.previous_cutover_id
                    or cutover.closed_build_id != first.closed_build_id
                    or cutover.catalog_id != latest.catalog_id
                    or cutover.current_receipts != latest.current_receipts
                    or cutover.maintenance_evidence_hash
                    != latest.maintenance_after_hash
                    or cutover.boundary_inventory_hash
                    != first.boundary_inventory_hash
                    or cutover.boundary_guard_version
                    != first.boundary_guard_version
                    or cutover.context_transition_id
                    != first.context_transition_id
                    or cutover.dominant_startup_receipt
                    != latest.dominant_startup_receipt
                    or latest.certificate_payload_hash
                    != _raw_sha256_v1(cutover.encoded)
                    or latest.certificate_signature_hash
                    != _raw_sha256_v1(cutover.signature)
                ):
                    raise _recovery("transaction cutover binding")
            if latest.sequence >= 5:
                head = heads_by_sequence.get(first.release_sequence)
                if head is None:
                    raise _recovery("transaction head missing")
                framed_head = (
                    REQUIRED_HEAD_MAGIC_V1
                    + len(head.encoded).to_bytes(4, "big")
                    + head.encoded + head.signature
                )
                # A conjunction that refuses without saying which of its
                # seven bindings disagreed costs one round per hypothesis, and
                # this path runs only under a real manager. The names are
                # structural; the values they compare stay out.
                disagreed = [
                    name for name, matches in (
                        ("head_id", head.head_id == latest.head_id),
                        ("cutover_id", head.cutover_id == latest.cutover_id),
                        ("closed_build_id",
                         head.closed_build_id == first.closed_build_id),
                        ("previous_head_id",
                         head.previous_head_id == first.previous_head_id),
                        ("head_payload_hash",
                         latest.head_payload_hash == _framed_sha256_v1(
                             HEAD_PAYLOAD_HASH_DOMAIN_V2, head.encoded)),
                        ("head_signature_hash",
                         latest.head_signature_hash == _framed_sha256_v1(
                             HEAD_SIGNATURE_HASH_DOMAIN_V2, head.signature)),
                        ("required_head_frame_hash",
                         latest.required_head_frame_hash == _framed_sha256_v1(
                             REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2, framed_head)),
                        ("verified_chain_head_id",
                         latest.verified_chain_head_id == head.head_id),
                    ) if not matches
                ]
                if disagreed:
                    raise _recovery(
                        "transaction head binding " + ",".join(disagreed)
                    )
                completed_by_sequence[first.release_sequence] = transaction

        transitions_by_id: dict[str, _DecodedContextTransitionV1] = {}
        for captured in candidate.context_transitions:
            decoded = durable(_decode_context_transition_v1, captured.encoded)
            expected_basename = (
                decoded.transition_id.removeprefix("sha256:") + ".json"
            )
            if (
                captured.basename != expected_basename
                or decoded.transition_id in transitions_by_id
            ):
                raise _recovery("context transition name binding")
            transitions_by_id[decoded.transition_id] = decoded

        for transaction in transactions:
            first = transaction.prefix.records[0]
            latest = transaction.prefix.records[-1]
            transition = transitions_by_id.pop(
                first.context_transition_id, None,
            )
            if latest.sequence >= 2 and transition is None:
                raise _recovery("context transition missing")
            if transition is None:
                continue
            if (
                transition.request_id != first.request_id
                or transition.closed_build_id != first.closed_build_id
                or transition.previous_cutover_id != first.previous_cutover_id
                or transition.previous_set_id != first.previous_set_id
                or transition.previous_admission_context_id
                != first.previous_admission_context_id
                or transition.previous_context_epoch
                != first.previous_context_epoch
                or transition.set_id != first.target_set_id
                or transition.prepared_admission_context_id
                != first.target_admission_context_id
                or transition.prepared_context_epoch
                != first.target_context_epoch
                or transition.context_material_sha256
                != first.target_context_material_sha256
                or transition.set_json_sha256 != first.target_set_json_sha256
                or transition.current_inventory_hash
                != first.current_inventory_hash
            ):
                raise _recovery("context transition journal binding")
        if transitions_by_id:
            raise _recovery("orphan context transition")

        attestations_by_request: dict[
            str, tuple[_DecodedPreflightAttestationV1, bytes]
        ] = {}
        for captured in candidate.preflight_attestations:
            decoded = durable(
                _decode_preflight_attestation_v1, captured.encoded,
            )
            expected_basename = decoded.request_id + ".json"
            if (
                captured.basename != expected_basename
                or decoded.request_id in attestations_by_request
            ):
                raise _recovery("preflight attestation name binding")
            attestations_by_request[decoded.request_id] = (
                decoded, captured.encoded,
            )

        for transaction in transactions:
            first = transaction.prefix.records[0]
            latest = transaction.prefix.records[-1]
            captured_attestation = attestations_by_request.pop(
                first.request_id, None,
            )
            if latest.sequence == 6 and captured_attestation is None:
                raise _recovery("preflight attestation missing")
            if captured_attestation is None:
                continue
            if latest.sequence not in {5, 6}:
                raise _recovery("preflight attestation order")
            attestation, encoded_attestation = captured_attestation
            if (
                attestation.request_id != first.request_id
                or attestation.closed_build_id != first.closed_build_id
                or attestation.release_sequence != first.release_sequence
                or attestation.head_id != latest.head_id
                or attestation.required_head_frame_hash
                != latest.required_head_frame_hash
                or attestation.deployment_descriptor_id
                != first.deployment_descriptor_id
                or attestation.service_coverage_hash
                != first.service_coverage_hash
                or attestation.administrative_bundle_hash
                != first.administrative_bundle_hash
                or (
                    latest.sequence == 6
                    and latest.preflight_attestation_hash
                    != _digest(
                        PREFLIGHT_ATTESTATION_RECORD_DOMAIN_V1,
                        encoded_attestation,
                    )
                )
            ):
                raise _recovery("preflight attestation journal binding")
        if attestations_by_request:
            raise _recovery("orphan preflight attestation")

        transactions_by_build = {
            transaction.prefix.records[0].closed_build_id: transaction
            for transaction in transactions
        }
        if len(transactions_by_build) != len(transactions):
            raise _recovery("transaction build fork")
        if anchor is not None:
            anchor_transaction = transactions_by_build.get(
                anchor.closed_build_id,
            )
            if (
                anchor_transaction is None
                or anchor_transaction.prefix.records[-1].sequence < 2
                or anchor_transaction.prefix.records[-1].cutover_id
                != anchor.cutover_id
            ):
                raise _recovery("orphan ownership anchor")
        for build in builds:
            transaction = transactions_by_build.get(build.facts.closed_build_id)
            if (
                transaction is None
                or transaction.prefix.records[-1].sequence < 4
            ):
                raise _recovery("orphan build archive")
        for cutover in cutovers:
            transaction = transactions_by_build.get(cutover.closed_build_id)
            if (
                transaction is None
                or transaction.prefix.records[-1].sequence < 2
                or transaction.prefix.records[-1].cutover_id != cutover.cutover_id
            ):
                raise _recovery("orphan cutover archive")
        for head in heads:
            transaction = transactions_by_build.get(head.closed_build_id)
            if (
                transaction is None
                or transaction.claim.release_sequence != head.release_sequence
                or transaction.prefix.records[-1].sequence < 4
            ):
                raise _recovery("orphan head archive")

        if required_head is not None:
            required_sequence = required_head.release_sequence
            completed_sequences = set(completed_by_sequence)
            stable_sequences = set(range(1, required_sequence + 1))
            cas_predecessor_sequences = set(range(1, required_sequence))
            required_prefix = next((
                transaction for transaction in transactions
                if transaction.claim.release_sequence == required_sequence
            ), None)
            stable = completed_sequences == stable_sequences
            cas_before_record = (
                completed_sequences == cas_predecessor_sequences
                and required_prefix is not None
                and required_prefix.prefix.records[-1].sequence == 4
            )
            if not stable and not cas_before_record:
                raise _recovery("coordinator required chain coverage")
        elif completed_by_sequence:
            raise _recovery("head record without required pointer")

        initial_transaction = next((
            transaction for transaction in transactions
            if transaction.claim.release_sequence == 1
        ), None)
        if initial_transaction is not None:
            initial_record = initial_transaction.prefix.records[0]
            initial_latest = initial_transaction.prefix.records[-1]
            predecessor = candidate.predecessor
            if predecessor is None:
                if initial_latest.sequence >= 1:
                    raise _recovery("predecessor transaction missing")
            elif (
                predecessor.transaction_id != initial_record.install_transaction_id
                or predecessor.administrative_bundle_hash
                != initial_record.administrative_bundle_hash
                or predecessor.service_coverage_hash
                != initial_record.service_coverage_hash
            ):
                raise _recovery("predecessor transaction binding")
            if any(
                transaction.prefix.records[0].administrative_bundle_hash
                != initial_record.administrative_bundle_hash
                for transaction in transactions
            ):
                raise _recovery("administrative bundle changed")
        elif candidate.predecessor is not None:
            raise _recovery("orphan predecessor")

        return _ReconciledFixedOwnershipSnapshotV1(
            candidate.registries, anchor, required_head,
            tuple(sorted(builds, key=lambda item: item.facts.release_sequence)),
            tuple(sorted(cutovers, key=lambda item: item.cutover_id)),
            tuple(heads_by_sequence[index] for index in sorted(heads_by_sequence)),
            claims, tuple(transactions), tuple(pending_claims), legacy_prefix,
            legacy_disposition, candidate.predecessor,
        )
    except PreflightError as exc:
        if exc.code == CODE_RECOVERY:
            raise
        raise _recovery("fixed ownership authentication") from exc
    except (OSError, ValueError, TypeError, MemoryError) as exc:
        raise _recovery("fixed ownership authentication") from exc


def _authenticate_fixed_ownership_snapshot_v1(
) -> _AuthenticatedFixedOwnershipSnapshotV1:
    """Authenticate only the fixed productive snapshot captured internally."""
    administrative_tcb = _capture_administrative_tcb_v1()
    candidate = _capture_fixed_ownership_state_v1()
    snapshot = _authenticate_fixed_ownership_snapshot_core_v1(
        candidate, openssl_executable=Path(
            administrative_tcb.capture.executables.openssl.resolved.canonical_path
        ),
        temporary_root=RUNTIME_ROOT, temporary_uid=0, temporary_gid=0,
        chain_stop=None,
    )
    _revalidate_captured_administrative_tcb_v1(
        administrative_tcb.capture, _administrative_links_v1(),
        uid=0, gid=0, chain_stop=None,
    )
    return _AuthenticatedFixedOwnershipSnapshotV1(snapshot, administrative_tcb)


def _authenticate_fixed_ownership_snapshot_for_test_v1(
    ownership_root: Path, *, openssl_executable: Path, temporary_root: Path,
) -> _AuthenticatedFixedOwnershipSnapshotForTestV1:
    """Portable seam whose result cannot enter the productive wrapper."""
    captured = _capture_fixed_ownership_state_for_test_v1(ownership_root)
    uid, gid = os.getuid(), os.getgid()
    snapshot = _authenticate_fixed_ownership_snapshot_core_v1(
        captured.candidate, openssl_executable=openssl_executable,
        temporary_root=temporary_root, temporary_uid=uid, temporary_gid=gid,
        chain_stop=temporary_root,
    )
    return _AuthenticatedFixedOwnershipSnapshotForTestV1(snapshot)


def _require_no_posix_access_acl_v1(path: Path) -> None:
    """Fail closed on ACLs that mode bits alone cannot evaluate."""
    try:
        os.getxattr(path, "system.posix_acl_access", follow_symlinks=False)
    except OSError as exc:
        if exc.errno not in {
            errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA),
            errno.ENOTSUP, errno.EOPNOTSUPP,
        }:
            raise _invalid("trusted path ACL") from exc
    else:
        raise _invalid("trusted path ACL")


def _trusted_path_start_v1(
    path: Path, chain_stop: Path | None,
) -> tuple[Path, list[str]]:
    if not isinstance(path, Path) or not path.is_absolute():
        raise _invalid("trusted path")
    if chain_stop is None:
        start = Path("/")
    elif (
        not isinstance(chain_stop, Path) or not chain_stop.is_absolute()
        or not path.is_relative_to(chain_stop)
    ):
        raise _invalid("trusted path root")
    else:
        start = chain_stop
    try:
        relative = path.relative_to(start)
    except ValueError as exc:
        raise _invalid("trusted path root") from exc
    parts = list(relative.parts)
    if len(parts) > 512 or any(part in {"", ".", ".."} for part in parts):
        raise _invalid("trusted path components")
    return start, parts


def _require_trusted_directory_info_v1(
    path: Path, info: os.stat_result, *, uid: int, gid: int,
) -> None:
    if (
        not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o022
    ):
        raise _invalid("unsafe trusted directory")
    _require_no_posix_access_acl_v1(path)


def _resolve_trusted_path_core_v1(
    path: Path, *, kind: str, executable: bool, uid: int, gid: int,
    chain_stop: Path | None, require_single_link: bool,
) -> _TrustedResolvedPathV1:
    """Resolve all path-component links with a bounded authenticated trace."""
    if (
        kind not in {"file", "directory"} or type(executable) is not bool
        or type(uid) is not int or type(gid) is not int
        or type(require_single_link) is not bool
        or (kind == "directory" and executable)
    ):
        raise _invalid("trusted path arguments")
    start, pending = _trusted_path_start_v1(path, chain_stop)
    try:
        start_info = start.lstat()
    except FileNotFoundError as exc:
        raise _missing("trusted path root") from exc
    except OSError as exc:
        raise _invalid("trusted path root") from exc
    _require_trusted_directory_info_v1(
        start, start_info, uid=uid, gid=gid,
    )
    trace: list[_TrustedPathComponentV1] = [
        _TrustedPathComponentV1(
            start.as_posix(), _metadata_identity_v1(start_info), None,
        )
    ]
    current = start
    link_count = 0
    observations = 0
    while pending:
        component = pending.pop(0)
        candidate = current / component
        observations += 1
        if observations > 1024:
            raise _invalid("trusted path traversal bound")
        try:
            info = candidate.lstat()
        except FileNotFoundError as exc:
            raise _missing("trusted path") from exc
        except OSError as exc:
            raise _invalid("trusted path") from exc
        if stat.S_ISLNK(info.st_mode):
            if info.st_uid != uid or info.st_gid != gid or link_count == 8:
                raise _invalid("trusted path link")
            _require_no_posix_access_acl_v1(candidate)
            try:
                target_text = os.readlink(candidate)
                target_size = len(target_text.encode("utf-8"))
            except (OSError, UnicodeEncodeError) as exc:
                raise _invalid("trusted path link") from exc
            if not target_text or "\0" in target_text or target_size > 4096:
                raise _invalid("trusted path link target")
            trace.append(_TrustedPathComponentV1(
                candidate.as_posix(), _metadata_identity_v1(info), target_text,
            ))
            target = Path(target_text)
            if not target.is_absolute():
                target = current / target
            target = Path(os.path.normpath(target.as_posix()))
            if not target.is_absolute():
                raise _invalid("trusted path link target")
            target_start, target_parts = _trusted_path_start_v1(
                target, chain_stop,
            )
            if target_start != start:
                raise _invalid("trusted path link root")
            pending = target_parts + pending
            current = start
            link_count += 1
            continue
        final = not pending
        if not final:
            _require_trusted_directory_info_v1(
                candidate, info, uid=uid, gid=gid,
            )
        elif kind == "directory":
            _require_trusted_directory_info_v1(
                candidate, info, uid=uid, gid=gid,
            )
        else:
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != uid or info.st_gid != gid
                or info.st_nlink < 1
                or (require_single_link and info.st_nlink != 1)
                or info.st_mode & 0o022
                or (executable and not info.st_mode & 0o111)
            ):
                raise _invalid("unsafe trusted file")
            _require_no_posix_access_acl_v1(candidate)
        trace.append(_TrustedPathComponentV1(
            candidate.as_posix(), _metadata_identity_v1(info), None,
        ))
        current = candidate
    if kind != "directory" and current == start:
        raise _invalid("trusted file root")
    return _TrustedResolvedPathV1(
        path.as_posix(), current.as_posix(), kind, tuple(trace),
    )


def _capture_trusted_file_v1(
    requested_path: Path, *, executable: bool, uid: int, gid: int,
    chain_stop: Path | None, maximum: int,
    require_single_link: bool,
) -> _CapturedTrustedFileV1:
    resolved = _resolve_trusted_path_core_v1(
        requested_path, kind="file", executable=executable, uid=uid, gid=gid,
        chain_stop=chain_stop, require_single_link=require_single_link,
    )
    canonical = Path(resolved.canonical_path)
    try:
        before = canonical.lstat()
    except OSError as exc:
        raise _invalid("trusted file capture") from exc
    content = _read_bounded_regular_v1(
        canonical, maximum, uid=uid, gid=gid, chain_stop=chain_stop,
        require_single_link=require_single_link,
    )
    try:
        after = canonical.lstat()
    except OSError as exc:
        raise _invalid("trusted file capture") from exc
    repeated = _resolve_trusted_path_core_v1(
        requested_path, kind="file", executable=executable, uid=uid, gid=gid,
        chain_stop=chain_stop, require_single_link=require_single_link,
    )
    identity = _metadata_identity_v1(before)
    if (
        identity != _metadata_identity_v1(after)
        or repeated != resolved
        or identity != resolved.components[-1].identity
        or len(content) != before.st_size
    ):
        raise _invalid("trusted file changed")
    return _CapturedTrustedFileV1(resolved, identity, content)


def _revalidate_captured_file_v1(
    captured: _CapturedTrustedFileV1, *, executable: bool, uid: int, gid: int,
    chain_stop: Path | None, maximum: int, require_single_link: bool,
) -> None:
    if type(captured) is not _CapturedTrustedFileV1:
        raise _invalid("trusted file revalidation")
    repeated = _capture_trusted_file_v1(
        Path(captured.resolved.requested_path), executable=executable,
        uid=uid, gid=gid, chain_stop=chain_stop, maximum=maximum,
        require_single_link=require_single_link,
    )
    if repeated != captured:
        raise _invalid("trusted file revalidation")


def _resolve_root_executable_v1(path: Path) -> Path:
    """Resolve at most eight root-owned component links without PATH lookup."""
    return Path(_resolve_trusted_path_core_v1(
        path, kind="file", executable=True, uid=0, gid=0,
        chain_stop=None, require_single_link=True,
    ).canonical_path)


_ELF64_HEADER_V1 = struct.Struct("<16sHHIQQQIHHHHHH")
_ELF64_PROGRAM_HEADER_V1 = struct.Struct("<IIQQQQQQ")
_LOADER_NAMED_LINE_RE_V1 = re.compile(
    r"(?P<name>(?:[A-Za-z0-9][A-Za-z0-9_.+:-]{0,255}|"
    r"/[^\s()]{1,4095})) => "
    r"(?P<path>/[^\s()]{1,4095}) \((?P<address>0x[0-9A-Fa-f]+)\)\Z"
)
_LOADER_DIRECT_LINE_RE_V1 = re.compile(
    r"(?P<value>(?:/[^\s()]{1,4095}|linux-vdso\.so\.1)) "
    r"\((?P<address>0x[0-9A-Fa-f]+)\)\Z"
)
_OPENSSL_MODULE_DIRECTORY_RE_V1 = re.compile(
    r'MODULESDIR: "(?P<path>/[^"\s]{1,4095})"\n\Z'
)


def _parse_elf64_interpreter_v1(content: bytes, architecture: str) -> str:
    """Extract the sole PT_INTERP from one bounded G6 OpenSSL ELF."""
    machine = {"x86_64": 62, "aarch64": 183}.get(architecture)
    if type(content) is not bytes or machine is None or len(content) < 64:
        raise _invalid("OpenSSL ELF")
    try:
        (
            ident, elf_type, observed_machine, version, _entry, phoff,
            _shoff, _flags, ehsize, phentsize, phnum, _shentsize, _shnum,
            _shstrndx,
        ) = _ELF64_HEADER_V1.unpack_from(content)
    except struct.error as exc:
        raise _invalid("OpenSSL ELF header") from exc
    if (
        ident[:4] != b"\x7fELF" or ident[4] != 2 or ident[5] != 1
        or ident[6] != 1 or elf_type not in {2, 3}
        or observed_machine != machine or version != 1 or ehsize != 64
        or phentsize != _ELF64_PROGRAM_HEADER_V1.size
        or not 0 < phnum <= MAX_ELF_PROGRAM_HEADERS_V1
        or phoff < ehsize or phoff > len(content)
        or phnum > (len(content) - phoff) // phentsize
    ):
        raise _invalid("OpenSSL ELF header")
    interpreters: list[str] = []
    for index in range(phnum):
        offset = phoff + index * phentsize
        try:
            (
                program_type, _program_flags, file_offset, _virtual_address,
                _physical_address, file_size, memory_size, _alignment,
            ) = _ELF64_PROGRAM_HEADER_V1.unpack_from(content, offset)
        except struct.error as exc:
            raise _invalid("OpenSSL ELF program header") from exc
        if program_type != 3:
            continue
        if (
            not 2 <= file_size <= MAX_ELF_INTERPRETER_BYTES_V1
            or memory_size < file_size or file_offset > len(content)
            or file_size > len(content) - file_offset
        ):
            raise _invalid("OpenSSL ELF interpreter")
        payload = content[file_offset:file_offset + file_size]
        if not payload.endswith(b"\0") or b"\0" in payload[:-1]:
            raise _invalid("OpenSSL ELF interpreter")
        try:
            interpreter = payload[:-1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _invalid("OpenSSL ELF interpreter") from exc
        interpreter = _catalog_absolute_path_v1(
            interpreter, "OpenSSL ELF interpreter",
        )
        if interpreter == "/":
            raise _invalid("OpenSSL ELF interpreter")
        interpreters.append(interpreter)
    if len(interpreters) != 1:
        raise _invalid("OpenSSL ELF interpreter coverage")
    return interpreters[0]


def _parse_loader_list_v1(
    stdout: bytes, architecture: str,
) -> tuple[_LoaderDependencyV1, ...]:
    if (
        type(stdout) is not bytes or architecture not in {"x86_64", "aarch64"}
        or not stdout or len(stdout) > MAX_TCB_SUBPROCESS_STREAM_BYTES_V1
        or not stdout.endswith(b"\n") or b"\r" in stdout or b"\0" in stdout
    ):
        raise _invalid("OpenSSL loader output")
    try:
        lines = stdout[:-1].decode("utf-8").split("\n")
    except UnicodeDecodeError as exc:
        raise _invalid("OpenSSL loader output") from exc
    if (
        not lines or len(lines) > MAX_OPENSSL_LOADER_ENTRIES_V1
        or any(not line for line in lines)
    ):
        raise _invalid("OpenSSL loader output")
    result: list[_LoaderDependencyV1] = []
    for raw_line in lines:
        line = raw_line.lstrip(" \t")
        if not line or line != line.rstrip(" \t"):
            raise _invalid("OpenSSL loader line")
        named = _LOADER_NAMED_LINE_RE_V1.fullmatch(line)
        if named is not None:
            name = named.group("name")
            if name.startswith("/"):
                name = _catalog_absolute_path_v1(
                    name, "OpenSSL loader name path",
                )
            path = _catalog_absolute_path_v1(
                named.group("path"), "OpenSSL loader path",
            )
            result.append(_LoaderDependencyV1(name, path))
            continue
        direct = _LOADER_DIRECT_LINE_RE_V1.fullmatch(line)
        if direct is None:
            raise _invalid("OpenSSL loader line")
        value = direct.group("value")
        if value == "linux-vdso.so.1":
            result.append(_LoaderDependencyV1(value, None))
        else:
            result.append(_LoaderDependencyV1(
                None, _catalog_absolute_path_v1(value, "OpenSSL loader path"),
            ))
    return tuple(result)


def _parse_openssl_module_directory_v1(stdout: bytes) -> str:
    if type(stdout) is not bytes or len(stdout) > MAX_TCB_SUBPROCESS_STREAM_BYTES_V1:
        raise _invalid("OpenSSL module directory output")
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid("OpenSSL module directory output") from exc
    match = _OPENSSL_MODULE_DIRECTORY_RE_V1.fullmatch(text)
    if match is None:
        raise _invalid("OpenSSL module directory output")
    path = _catalog_absolute_path_v1(
        match.group("path"), "OpenSSL module directory",
    )
    if path == "/":
        raise _invalid("OpenSSL module directory")
    return path


def _administrative_links_v1() -> tuple[Path, Path, Path, Path]:
    return PYTHON_LINK, OPENSSL_LINK, SYSTEMCTL_LINK, SYSTEMD_ANALYZE_LINK


def _capture_administrative_executables_core_v1(
    links: tuple[Path, Path, Path, Path], *, uid: int, gid: int,
    chain_stop: Path | None,
) -> _AdministrativeExecutableSnapshotV1:
    if (
        type(links) is not tuple or len(links) != 4
        or any(not isinstance(item, Path) for item in links)
    ):
        raise _invalid("administrative executable links")
    captured = tuple(
        _capture_trusted_file_v1(
            link, executable=True, uid=uid, gid=gid, chain_stop=chain_stop,
            maximum=MAX_DISTRIBUTION_FILE_BYTES, require_single_link=True,
        )
        for link in links
    )
    hashes = tuple(
        _administrative_executable_hash_v1(
            item.resolved.canonical_path, item.content,
        )
        for item in captured
    )
    return _AdministrativeExecutableSnapshotV1(
        *captured, *hashes,
    )


def _revalidate_administrative_executables_v1(
    snapshot: _AdministrativeExecutableSnapshotV1,
    links: tuple[Path, Path, Path, Path], *, uid: int, gid: int,
    chain_stop: Path | None,
) -> None:
    if type(snapshot) is not _AdministrativeExecutableSnapshotV1:
        raise _invalid("administrative executable snapshot")
    repeated = _capture_administrative_executables_core_v1(
        links, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    if repeated != snapshot:
        raise _invalid("administrative executable changed")


def _run_checked_tcb_command_v1(argv: tuple[str, ...]) -> bytes:
    returncode, stdout, stderr = _run_openssl_bounded_v1(
        argv, maximum=MAX_TCB_SUBPROCESS_STREAM_BYTES_V1,
    )
    if returncode != 0 or stderr:
        raise _invalid("OpenSSL TCB command")
    return stdout


def _capture_loader_closure_v1(
    dependencies: tuple[_LoaderDependencyV1, ...], *, uid: int, gid: int,
    chain_stop: Path | None,
) -> tuple[
    tuple[_CapturedTrustedFileV1, ...], tuple[tuple[str, str], ...],
]:
    name_paths: dict[str, str] = {}
    captures: dict[str, _CapturedTrustedFileV1] = {}
    identities: list[tuple[str, str]] = []
    for dependency in dependencies:
        if dependency.path is None:
            if dependency.name != "linux-vdso.so.1":
                raise _invalid("OpenSSL loader virtual dependency")
            identities.append((dependency.name, ""))
            continue
        captured = _capture_trusted_file_v1(
            Path(dependency.path), executable=False, uid=uid, gid=gid,
            chain_stop=chain_stop, maximum=MAX_DISTRIBUTION_FILE_BYTES,
            require_single_link=False,
        )
        canonical = captured.resolved.canonical_path
        if dependency.name is not None:
            previous = name_paths.setdefault(dependency.name, canonical)
            if previous != canonical:
                raise _invalid("OpenSSL loader name collision")
            name = dependency.name
        else:
            name = ""
        identities.append((name, canonical))
        present = captures.setdefault(canonical, captured)
        if (
            present.identity != captured.identity
            or present.content != captured.content
        ):
            raise _invalid("OpenSSL loader alias divergence")
    signature = tuple(sorted(set(identities), key=lambda item: (
        item[0].encode("utf-8"), item[1].encode("utf-8"),
    )))
    return tuple(captures[path] for path in sorted(
        captures, key=lambda item: item.encode("utf-8"),
    )), signature


def _require_same_canonical_captures_v1(
    before: tuple[_CapturedTrustedFileV1, ...],
    after: tuple[_CapturedTrustedFileV1, ...], detail: str,
) -> None:
    before_by_path = {
        item.resolved.canonical_path: (item.identity, item.content)
        for item in before
    }
    after_by_path = {
        item.resolved.canonical_path: (item.identity, item.content)
        for item in after
    }
    if before_by_path != after_by_path:
        raise _invalid(detail)


def _capture_module_inventory_v1(
    directory_resolution: _TrustedResolvedPathV1, *, uid: int, gid: int,
    chain_stop: Path | None,
) -> tuple[_CapturedTrustedFileV1, ...]:
    if (
        type(directory_resolution) is not _TrustedResolvedPathV1
        or directory_resolution.kind != "directory"
    ):
        raise _invalid("OpenSSL module directory snapshot")
    directory = Path(directory_resolution.canonical_path)
    flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(directory, flags)
        before = os.fstat(descriptor)
        if _metadata_identity_v1(before) != directory_resolution.components[-1].identity:
            raise _invalid("OpenSSL module directory rebound")
        raw_entries: list[tuple[str, tuple[int, ...], int]] = []
        total_size = 0
        with os.scandir(descriptor) as entries:
            for entry in entries:
                try:
                    encoded_name = entry.name.encode("utf-8")
                    info = entry.stat(follow_symlinks=False)
                except (OSError, UnicodeEncodeError) as exc:
                    raise _invalid("OpenSSL module inventory") from exc
                if (
                    not encoded_name or b"/" in encoded_name or b"\0" in encoded_name
                    or entry.name in {".", ".."}
                    or not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or info.st_uid != uid or info.st_gid != gid
                    or info.st_nlink < 1 or info.st_mode & 0o022
                ):
                    raise _invalid("OpenSSL module entry")
                total_size += info.st_size
                raw_entries.append((
                    entry.name, _metadata_identity_v1(info), info.st_size,
                ))
                if (
                    len(raw_entries) > MAX_OPENSSL_MODULE_FILES_V1
                    or total_size > MAX_OPENSSL_MODULE_BYTES_V1
                ):
                    raise _invalid("OpenSSL module inventory bound")
        after = os.fstat(descriptor)
    except PreflightError:
        raise
    except OSError as exc:
        raise _invalid("OpenSSL module inventory") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _metadata_identity_v1(after) != _metadata_identity_v1(before):
        raise _invalid("OpenSSL module directory changed")
    repeated_directory = _resolve_trusted_path_core_v1(
        Path(directory_resolution.requested_path), kind="directory",
        executable=False, uid=uid, gid=gid, chain_stop=chain_stop,
        require_single_link=False,
    )
    if repeated_directory != directory_resolution:
        raise _invalid("OpenSSL module directory changed")
    captures: list[_CapturedTrustedFileV1] = []
    for name, identity, size in sorted(
        raw_entries, key=lambda item: item[0].encode("utf-8"),
    ):
        captured = _capture_trusted_file_v1(
            directory / name, executable=False, uid=uid, gid=gid,
            chain_stop=chain_stop, maximum=size, require_single_link=False,
        )
        if captured.identity != identity or len(captured.content) != size:
            raise _invalid("OpenSSL module entry changed")
        captures.append(captured)
    return tuple(captures)


def _openssl_tcb_document_v1(
    elf_loader: str, module_directory: str,
    files: tuple[_OpenSslTcbFileV1, ...],
) -> tuple[bytes, str]:
    loader = _catalog_absolute_path_v1(elf_loader, "OpenSSL TCB loader")
    modules = _catalog_absolute_path_v1(
        module_directory, "OpenSSL TCB module directory",
    )
    if (
        type(files) is not tuple or not files
        or any(type(item) is not _OpenSslTcbFileV1 for item in files)
    ):
        raise _invalid("OpenSSL TCB files")
    paths = tuple(item.path for item in files)
    if (
        paths != tuple(sorted(paths, key=lambda item: item.encode("utf-8")))
        or len(paths) != len(set(paths))
    ):
        raise _invalid("OpenSSL TCB file order")
    document = {
        "schema_version": 1,
        "command_profile": "ed25519-pkeyutl-v1",
        "config_path": "/dev/null",
        "provider": "default",
        "elf_loader": loader,
        "module_directory": modules,
        "files": [item.as_value() for item in files],
    }
    encoded = _canonical_json(document)
    return encoded, _digest(OPENSSL_TCB_DOMAIN_V1, encoded)


def _capture_openssl_tcb_core_v1(
    executables: _AdministrativeExecutableSnapshotV1,
    links: tuple[Path, Path, Path, Path], *, architecture: str,
    uid: int, gid: int, chain_stop: Path | None,
) -> _OpenSslTcbSnapshotV1:
    if (
        type(executables) is not _AdministrativeExecutableSnapshotV1
        or architecture not in {"x86_64", "aarch64"}
    ):
        raise _invalid("OpenSSL TCB arguments")
    interpreter = _parse_elf64_interpreter_v1(
        executables.openssl.content, architecture,
    )
    loader = _capture_trusted_file_v1(
        Path(interpreter), executable=True, uid=uid, gid=gid,
        chain_stop=chain_stop, maximum=MAX_DISTRIBUTION_FILE_BYTES,
        require_single_link=False,
    )
    loader_argv = (
        loader.resolved.canonical_path, "--list",
        executables.openssl.resolved.canonical_path,
    )

    first_output = _run_checked_tcb_command_v1(loader_argv)
    _revalidate_administrative_executables_v1(
        executables, links, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    _revalidate_captured_file_v1(
        loader, executable=True, uid=uid, gid=gid, chain_stop=chain_stop,
        maximum=MAX_DISTRIBUTION_FILE_BYTES, require_single_link=False,
    )
    first_dependencies = _parse_loader_list_v1(first_output, architecture)
    first_closure, first_signature = _capture_loader_closure_v1(
        first_dependencies, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    for item in first_closure:
        _revalidate_captured_file_v1(
            item, executable=False, uid=uid, gid=gid, chain_stop=chain_stop,
            maximum=MAX_DISTRIBUTION_FILE_BYTES, require_single_link=False,
        )

    second_output = _run_checked_tcb_command_v1(loader_argv)
    _revalidate_administrative_executables_v1(
        executables, links, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    _revalidate_captured_file_v1(
        loader, executable=True, uid=uid, gid=gid, chain_stop=chain_stop,
        maximum=MAX_DISTRIBUTION_FILE_BYTES, require_single_link=False,
    )
    second_dependencies = _parse_loader_list_v1(second_output, architecture)
    second_closure, second_signature = _capture_loader_closure_v1(
        second_dependencies, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    if first_signature != second_signature:
        raise _invalid("OpenSSL loader closure changed")
    _require_same_canonical_captures_v1(
        first_closure, second_closure, "OpenSSL loader files changed",
    )

    for item in second_closure:
        _revalidate_captured_file_v1(
            item, executable=False, uid=uid, gid=gid, chain_stop=chain_stop,
            maximum=MAX_DISTRIBUTION_FILE_BYTES, require_single_link=False,
        )
    module_output = _run_checked_tcb_command_v1((
        executables.openssl.resolved.canonical_path, "version", "-m",
    ))
    _revalidate_administrative_executables_v1(
        executables, links, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    _revalidate_captured_file_v1(
        loader, executable=True, uid=uid, gid=gid, chain_stop=chain_stop,
        maximum=MAX_DISTRIBUTION_FILE_BYTES, require_single_link=False,
    )
    for item in second_closure:
        _revalidate_captured_file_v1(
            item, executable=False, uid=uid, gid=gid, chain_stop=chain_stop,
            maximum=MAX_DISTRIBUTION_FILE_BYTES, require_single_link=False,
        )
    module_directory_text = _parse_openssl_module_directory_v1(module_output)
    module_directory = _resolve_trusted_path_core_v1(
        Path(module_directory_text), kind="directory", executable=False,
        uid=uid, gid=gid, chain_stop=chain_stop, require_single_link=False,
    )
    first_modules = _capture_module_inventory_v1(
        module_directory, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    second_modules = _capture_module_inventory_v1(
        module_directory, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    if first_modules != second_modules:
        raise _invalid("OpenSSL module inventory changed")

    captures_by_path: dict[str, _CapturedTrustedFileV1] = {}
    for item in (
        executables.openssl, loader, *second_closure, *second_modules,
    ):
        canonical = item.resolved.canonical_path
        previous = captures_by_path.setdefault(canonical, item)
        if previous.identity != item.identity or previous.content != item.content:
            raise _invalid("OpenSSL TCB alias divergence")
    captures = tuple(captures_by_path[path] for path in sorted(
        captures_by_path, key=lambda item: item.encode("utf-8"),
    ))
    files = tuple(
        _OpenSslTcbFileV1(
            item.resolved.canonical_path, len(item.content),
            _openssl_tcb_file_hash_v1(
                item.resolved.canonical_path, item.content,
            ),
        )
        for item in captures
    )
    encoded, tcb_hash = _openssl_tcb_document_v1(
        loader.resolved.canonical_path,
        module_directory.canonical_path, files,
    )
    snapshot = _OpenSslTcbSnapshotV1(
        architecture, executables.openssl.resolved.canonical_path,
        loader.resolved.canonical_path,
        module_directory.canonical_path, files, encoded, tcb_hash,
        captures, second_modules, module_directory,
    )
    _revalidate_openssl_tcb_v1(
        snapshot, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    return snapshot


def _revalidate_openssl_tcb_v1(
    snapshot: _OpenSslTcbSnapshotV1, *, uid: int, gid: int,
    chain_stop: Path | None,
) -> None:
    if type(snapshot) is not _OpenSslTcbSnapshotV1:
        raise _invalid("OpenSSL TCB snapshot")
    for item in snapshot.captures:
        _revalidate_captured_file_v1(
            item,
            executable=(item.resolved.canonical_path in {
                snapshot.openssl_executable, snapshot.elf_loader,
            }),
            uid=uid, gid=gid, chain_stop=chain_stop,
            maximum=MAX_DISTRIBUTION_FILE_BYTES, require_single_link=False,
        )
    repeated_directory = _resolve_trusted_path_core_v1(
        Path(snapshot.module_directory_resolution.requested_path),
        kind="directory", executable=False, uid=uid, gid=gid,
        chain_stop=chain_stop, require_single_link=False,
    )
    if repeated_directory != snapshot.module_directory_resolution:
        raise _invalid("OpenSSL module directory revalidation")
    repeated_modules = _capture_module_inventory_v1(
        repeated_directory, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    if repeated_modules != snapshot.module_captures:
        raise _invalid("OpenSSL module inventory revalidation")


def _capture_administrative_tcb_core_v1(
    links: tuple[Path, Path, Path, Path], *, architecture: str,
    uid: int, gid: int, chain_stop: Path | None,
) -> _CapturedAdministrativeTcbV1:
    executables = _capture_administrative_executables_core_v1(
        links, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    openssl_tcb = _capture_openssl_tcb_core_v1(
        executables, links, architecture=architecture,
        uid=uid, gid=gid, chain_stop=chain_stop,
    )
    _revalidate_administrative_executables_v1(
        executables, links, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    return _CapturedAdministrativeTcbV1(executables, openssl_tcb)


def _local_g6_architecture_v1() -> str:
    architecture = {
        "x86_64": "x86_64", "amd64": "x86_64",
        "aarch64": "aarch64", "arm64": "aarch64",
    }.get(platform.machine().lower())
    if architecture is None:
        raise _invalid("G6 architecture")
    return architecture


def _capture_administrative_tcb_v1() -> _CapturedAdministrativeTcbProductV1:
    """Measure the fixed product TCB before it authenticates any snapshot."""
    require_linux_before_io_v1()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise _invalid("effective identity")
    return _CapturedAdministrativeTcbProductV1(
        _capture_administrative_tcb_core_v1(
            _administrative_links_v1(),
            architecture=_local_g6_architecture_v1(),
            uid=0, gid=0, chain_stop=None,
        )
    )


def _capture_administrative_tcb_for_test_v1(
    links: tuple[Path, Path, Path, Path], *, architecture: str,
    trusted_root: Path,
) -> _CapturedAdministrativeTcbForTestV1:
    """Portable filesystem seam whose result cannot enter product auth."""
    if (
        not isinstance(trusted_root, Path) or not trusted_root.is_absolute()
        or type(links) is not tuple or len(links) != 4
        or any(
            not isinstance(item, Path) or not item.is_absolute()
            or not item.is_relative_to(trusted_root)
            for item in links
        )
    ):
        raise _invalid("test administrative TCB root")
    return _CapturedAdministrativeTcbForTestV1(
        _capture_administrative_tcb_core_v1(
            links, architecture=architecture, uid=os.getuid(), gid=os.getgid(),
            chain_stop=trusted_root,
        )
    )


def _revalidate_captured_administrative_tcb_v1(
    capture: _CapturedAdministrativeTcbV1,
    links: tuple[Path, Path, Path, Path], *, uid: int, gid: int,
    chain_stop: Path | None,
) -> None:
    if type(capture) is not _CapturedAdministrativeTcbV1:
        raise _invalid("administrative TCB capture")
    _revalidate_administrative_executables_v1(
        capture.executables, links, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    _revalidate_openssl_tcb_v1(
        capture.openssl_tcb, uid=uid, gid=gid, chain_stop=chain_stop,
    )


def _bind_administrative_tcb_core_v1(
    materials: _BoundPreflightMaterialsV1,
    capture: _CapturedAdministrativeTcbV1,
    links: tuple[Path, Path, Path, Path], *, uid: int, gid: int,
    chain_stop: Path | None,
) -> _ObservedAdministrativeTcbV1:
    if (
        type(materials) is not _BoundPreflightMaterialsV1
        or type(capture) is not _CapturedAdministrativeTcbV1
    ):
        raise _invalid("administrative TCB binding arguments")
    descriptor = materials.descriptor
    prerequisite = materials.prerequisite
    executables = capture.executables
    observed_paths = (
        executables.python.resolved.canonical_path,
        executables.openssl.resolved.canonical_path,
        executables.systemctl.resolved.canonical_path,
        executables.systemd_analyze.resolved.canonical_path,
    )
    expected_paths = (
        descriptor.python_executable, descriptor.openssl_executable,
        descriptor.systemctl_executable, descriptor.systemd_analyze_executable,
    )
    if (
        observed_paths != expected_paths
        or materials.distribution.facts.architecture
        != capture.openssl_tcb.architecture
        or capture.openssl_tcb.openssl_executable
        != descriptor.openssl_executable
        or prerequisite.python_binary_hash != executables.python_binary_hash
        or prerequisite.openssl_binary_hash != executables.openssl_binary_hash
        or prerequisite.systemctl_binary_hash != executables.systemctl_binary_hash
        or prerequisite.systemd_analyze_binary_hash
        != executables.systemd_analyze_binary_hash
        or prerequisite.openssl_tcb_hash
        != capture.openssl_tcb.openssl_tcb_hash
    ):
        raise _invalid("administrative TCB signed binding")
    _revalidate_captured_administrative_tcb_v1(
        capture, links, uid=uid, gid=gid, chain_stop=chain_stop,
    )

    admin_by_path = {
        item.resolved.canonical_path: item
        for item in (
            executables.python, executables.openssl, executables.systemctl,
            executables.systemd_analyze,
        )
    }
    expected_target_hashes: dict[str, str] = {}
    for entry in materials.catalog.entries:
        if entry.target_executable is None:
            continue
        try:
            PurePosixPath(entry.target_executable).relative_to(
                PurePosixPath(descriptor.installation_root),
            )
        except ValueError:
            pass
        else:
            continue
        if entry.target_executable_hash is None:
            raise _invalid("external target executable hash")
        previous = expected_target_hashes.setdefault(
            entry.target_executable, entry.target_executable_hash,
        )
        if previous != entry.target_executable_hash:
            raise _invalid("external target hash alias")

    external: list[_ExternalTargetMeasurementV1] = []
    for declared_path in sorted(
        expected_target_hashes, key=lambda item: item.encode("utf-8"),
    ):
        captured = admin_by_path.get(declared_path)
        if captured is None:
            captured = _capture_trusted_file_v1(
                Path(declared_path), executable=True, uid=uid, gid=gid,
                chain_stop=chain_stop, maximum=MAX_DISTRIBUTION_FILE_BYTES,
                require_single_link=False,
            )
        target_hash = _target_executable_hash_v1(
            declared_path, captured.content,
        )
        if target_hash != expected_target_hashes[declared_path]:
            raise _invalid("external target executable binding")
        external.append(_ExternalTargetMeasurementV1(
            declared_path, target_hash, captured,
        ))
    for item in external:
        _revalidate_captured_file_v1(
            item.captured, executable=True, uid=uid, gid=gid,
            chain_stop=chain_stop, maximum=MAX_DISTRIBUTION_FILE_BYTES,
            require_single_link=(
                item.captured.resolved.canonical_path in admin_by_path
            ),
        )
    _revalidate_captured_administrative_tcb_v1(
        capture, links, uid=uid, gid=gid, chain_stop=chain_stop,
    )
    return _ObservedAdministrativeTcbV1(
        materials, capture, tuple(external),
    )


def _select_ownership_epoch_v1(
    snapshot: _ReconciledFixedOwnershipSnapshotV1,
) -> _SelectedOwnershipEpochV1:
    """Select the one authoritative durable epoch, ignoring pending successors."""
    if type(snapshot) is not _ReconciledFixedOwnershipSnapshotV1:
        raise _invalid("administrative TCB ownership selection")
    required_head = snapshot.required_head
    predecessor = snapshot.predecessor
    if required_head is None or predecessor is None:
        raise _invalid("administrative TCB ownership selection")
    builds = tuple(
        item for item in snapshot.builds
        if item.facts.closed_build_id == required_head.closed_build_id
        and item.facts.release_sequence == required_head.release_sequence
    )
    transactions = tuple(
        item for item in snapshot.transactions
        if item.claim.release_sequence == required_head.release_sequence
        and item.prefix.records[0].closed_build_id
        == required_head.closed_build_id
        and item.prefix.records[-1].head_id == required_head.head_id
        and item.prefix.records[-1].sequence >= 5
    )
    if (
        len(builds) != 1 or len(transactions) != 1
    ):
        raise _invalid("administrative TCB ownership selection")
    return _SelectedOwnershipEpochV1(
        snapshot.registries, snapshot.anchor, required_head, builds[0],
        transactions[0], predecessor,
    )


def _require_materials_selected_by_snapshot_v1(
    snapshot: _ReconciledFixedOwnershipSnapshotV1,
    materials: _BoundPreflightMaterialsV1,
) -> _SelectedOwnershipEpochV1:
    """Cross-bind materials to the one authoritative durable epoch."""
    if type(materials) is not _BoundPreflightMaterialsV1:
        raise _invalid("administrative TCB ownership selection")
    selected = _select_ownership_epoch_v1(snapshot)
    if (
        materials.distribution != selected.build
        or materials.transaction != selected.transaction.prefix.records[-1]
        or materials.prerequisite.predecessor_id
        != selected.predecessor.predecessor_id
    ):
        raise _invalid("administrative TCB ownership selection")
    return selected


def _bind_administrative_tcb_v1(
    authenticated: _AuthenticatedFixedOwnershipSnapshotV1,
    materials: _BoundPreflightMaterialsV1,
) -> _ObservedAdministrativeTcbProductV1:
    """Bind only the graph selected by the same product authentication."""
    if (
        type(authenticated) is not _AuthenticatedFixedOwnershipSnapshotV1
        or type(materials) is not _BoundPreflightMaterialsV1
    ):
        raise _invalid("product administrative TCB")
    _require_materials_selected_by_snapshot_v1(
        authenticated.snapshot, materials,
    )
    captured = authenticated.administrative_tcb
    if type(captured) is not _CapturedAdministrativeTcbProductV1:
        raise _invalid("product administrative TCB")
    return _ObservedAdministrativeTcbProductV1(
        _bind_administrative_tcb_core_v1(
            materials, captured.capture, _administrative_links_v1(),
            uid=0, gid=0, chain_stop=None,
        )
    )


def _bind_administrative_tcb_for_test_v1(
    materials: _BoundPreflightMaterialsForTestV1,
    captured: _CapturedAdministrativeTcbForTestV1,
    links: tuple[Path, Path, Path, Path], *, trusted_root: Path,
) -> _ObservedAdministrativeTcbForTestV1:
    if (
        type(materials) is not _BoundPreflightMaterialsForTestV1
        or type(captured) is not _CapturedAdministrativeTcbForTestV1
        or not isinstance(trusted_root, Path) or not trusted_root.is_absolute()
    ):
        raise _invalid("test administrative TCB binding")
    return _ObservedAdministrativeTcbForTestV1(
        _bind_administrative_tcb_core_v1(
            materials.materials, captured.capture, links,
            uid=os.getuid(), gid=os.getgid(), chain_stop=trusted_root,
        )
    )


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


def _run_openssl_bounded_v1(
    argv: tuple[str, ...], *, maximum: int = MAX_OPENSSL_STREAM_BYTES,
) -> tuple[int, bytes, bytes]:
    if (
        type(argv) is not tuple or not argv
        or any(type(item) is not str or not item or "\0" in item for item in argv)
        or not Path(argv[0]).is_absolute()
        or type(maximum) is not int
        or not 0 < maximum <= MAX_TCB_SUBPROCESS_STREAM_BYTES_V1
    ):
        raise _invalid("OpenSSL command")
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
                        key.fileobj.fileno(), maximum + 1,
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = output if key.data == "stdout" else error
                target.extend(chunk)
                if len(target) > maximum:
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
        "_publish_control_no_replace_v2": ("store_write",),
        "_reserve_transition_edge_core_v2": ("store_write",),
        "_reserve_transition_edge_locked_for_test_v2": ("store_write",),
        "_reserve_transition_edge_locked_v2": ("store_write",),
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
        "_load_received_source_locked_core_v1": ("store_write",),
        "_load_received_source_with_product_session_v1": ("store_write",),
        "_load_received_source_with_test_session_v1": ("store_write",),
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
    "executor_birth_transition": {
        "<module>": ("store_write",),
        "deploy_source_v1": ("store_write",),
        "main": ("store_write",),
    },
    "executor_birth_systemd": {
        "_install_group6_administrative_for_test_v1": ("store_write",),
        "_install_locked_core_v1": ("store_write",),
        "_install_signed_isolated_systemd_for_test_v1": ("store_write",),
        "_open_parent_v1": ("store_write",),
        "_publish_administrative_tree_v1": ("store_write",),
        "_publish_isolated_units_for_test_v1": ("store_write",),
        "install_group6_administrative_v1": ("store_write",),
    },
    "executor_birth_admin_preflight": {
        "_publish_preflight_attestation_core_v1": ("store_write",),
        "_publish_preflight_attestation_for_test_v1": ("store_write",),
        "_publish_preflight_attestation_v1": ("store_write",),
        "_write_all_exact_v1": ("store_write",),
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
    "executor_birth_transition": frozenset({
        "install.executor_birth_transition",
    }),
    "executor_birth_systemd": frozenset({
        "install.executor_birth_systemd",
    }),
    "executor_birth_admin_preflight": frozenset({
        "executor_birth_admin_preflight",
        "runtime.executor_birth_admin_preflight",
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
    "install/executor_birth_transition.py": "executor_birth_transition",
    "install/executor_birth_systemd.py": "executor_birth_systemd",
    "runtime/executor_birth_admin_preflight.py": (
        "executor_birth_admin_preflight"
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
AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE = (
    "runtime/executor_birth_admin_preflight.py", "_launch_python_target_v1",
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


def _is_authenticated_preflight_runpy_v1(
    call: ast.Call, path: str, scope: str,
    aliases: Mapping[str, str], nodes: Sequence[ast.AST],
) -> bool:
    """Recognize the sole exact runpy door bound by the signed launch plan."""
    if (
        (path, scope) != AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE
        or not isinstance(call.func, ast.Attribute)
        or not isinstance(call.func.value, ast.Name)
        or call.func.value.id != "runpy" or call.func.attr != "run_module"
        or aliases.get("runpy") != "runpy"
        or len(call.args) != 1
        or not isinstance(call.args[0], ast.Attribute)
        or not isinstance(call.args[0].value, ast.Name)
        or call.args[0].value.id != "plan"
        or call.args[0].attr != "python_module"
        or len(call.keywords) != 2
        or any(item.arg is None for item in call.keywords)
    ):
        return False
    keywords = {item.arg: item.value for item in call.keywords}
    if set(keywords) != {"run_name", "alter_sys"}:
        return False
    run_name = keywords["run_name"]
    alter_sys = keywords["alter_sys"]
    if (
        not isinstance(run_name, ast.Constant) or run_name.value != "__main__"
        or not isinstance(alter_sys, ast.Constant) or alter_sys.value is not False
    ):
        return False
    for node in nodes:
        targets: set[str] = set()
        if isinstance(node, ast.Assign):
            for target in node.targets:
                targets.update(_target_names(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets.update(_target_names(node.target))
        if targets & {"runpy", "plan"}:
            return False
    return True


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
        if canonical in local_callables and (
            isinstance(item.func, ast.Name)
            or isinstance(item.func, ast.Attribute)
            and isinstance(item.func.value, ast.Name)
        ):
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
            (
                _is_dynamic_code_loader_call(item.func, canonical)
                or _may_resolve_dynamic_loader_callable(item.func, aliases)
            )
            and not _is_authenticated_preflight_runpy_v1(
                item, path, scope, aliases, nodes,
            )
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

            def authenticated_preflight_runpy(call: ast.Call) -> bool:
                current: ast.AST = call
                while (parent := parents.get(id(current))) is not None:
                    if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        return _is_authenticated_preflight_runpy_v1(
                            call,
                            (
                                "runtime/executor_birth_admin_preflight.py"
                                if item.path == _BOUNDARY_PREFLIGHT_ENTRYPOINT_V1
                                else item.path
                            ),
                            parent.name, aliases,
                            list(ast.walk(parent)),
                        )
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
                    and not authenticated_preflight_runpy(node)
                    or _may_resolve_dynamic_loader_callable(node.func, aliases)
                    and not authenticated_preflight_runpy(node)
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
    _capture_verified_distribution_tree_v1(
        record.facts, record.files, root, expected_type=expected_type,
        uid=uid, gid=gid, chain_stop=chain_stop,
        extra_capture_paths=frozenset(), require_compiled_review=(
            expected_type is AuthenticatedDistributionV1
        ),
    )


def _capture_verified_distribution_tree_v1(
    facts: DistributionFactsV1,
    files: tuple[DistributionFileV1, ...],
    root: Path, *, expected_type: type,
    uid: int, gid: int, chain_stop: Path | None,
    extra_capture_paths: frozenset[str], require_compiled_review: bool,
) -> dict[str, bytes]:
    """Verify one exact tree and retain only explicitly required live bytes."""
    if (
        type(facts) is not DistributionFactsV1
        or not isinstance(files, tuple)
        or any(type(item) is not DistributionFileV1 for item in files)
        or expected_type not in {
            AuthenticatedDistributionV1,
            _AuthenticatedDistributionForTestV1,
            _AuthenticatedDistributionObjectV1,
        }
        or type(extra_capture_paths) is not frozenset
        or any(type(item) is not str for item in extra_capture_paths)
        or type(require_compiled_review) is not bool
    ):
        raise _invalid("verified distribution capture")
    value = {
        "boundary_inventory_path": facts.boundary_inventory_path,
        "boundary_inventory_hash": facts.boundary_inventory_hash,
        "boundary_guard_version": facts.boundary_guard_version,
        "product_version": facts.product_version,
    }
    inventory_path = facts.boundary_inventory_path
    available_paths = frozenset(item.path for item in files)
    if not extra_capture_paths.issubset(available_paths):
        raise _invalid("distribution capture path")
    capture_paths = frozenset(
        item.path for item in files
        if item.path.endswith(".py") or item.path == inventory_path
    ) | extra_capture_paths

    def verify_semantics(verified: dict[str, bytes]) -> None:
        inventory = verified[inventory_path]
        if (
            _digest(BOUNDARY_INVENTORY_DOMAIN, inventory)
            != value["boundary_inventory_hash"]
        ):
            raise _invalid("boundary inventory hash")
        inventory_value = _validate_boundary_inventory_v1(inventory)
        _require_birth_closed_sources_v1(verified, inventory_value)
        if require_compiled_review:
            _require_compiled_source_review_v1(verified)
        if value["boundary_guard_version"] != _BIRTH_CLOSED_GUARD_VERSION:
            raise _invalid("boundary guard version")
        if _product_version_from_source_v1(
            verified["runtime/__version__.py"]
        ) != value["product_version"]:
            raise _invalid("product version")
        _verify_local_import_closure_v1(root, files, verified)

    return _snapshot_exact_distribution_tree_v1(
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


def _load_bound_preflight_materials_v1(
    authenticated: _AuthenticatedFixedOwnershipSnapshotV1,
) -> tuple[_SelectedOwnershipEpochV1, _BoundPreflightMaterialsV1]:
    """Read and bind the installed tree selected only by fixed ownership state."""
    if type(authenticated) is not _AuthenticatedFixedOwnershipSnapshotV1:
        raise _invalid("product preflight materials")
    selected = _select_ownership_epoch_v1(authenticated.snapshot)
    build = selected.build
    root = RELEASE_ROOT / f"{build.facts.release_sequence:020d}"
    architecture = _local_g6_architecture_v1()
    if (
        build.facts.installation_root != root.as_posix()
        or build.facts.platform != "linux"
        or build.facts.architecture != architecture
    ):
        raise _invalid("product preflight distribution")

    probe_paths = frozenset({
        SERVICE_CATALOG_PATH_V1, DEPLOYMENT_DESCRIPTOR_PATH_V1,
    })
    probe = _snapshot_exact_distribution_tree_v1(
        root, build.files, uid=0, gid=0, chain_stop=None,
        capture_paths=probe_paths,
    )
    catalog = _decode_service_catalog_v1(probe[SERVICE_CATALOG_PATH_V1])
    descriptor = _decode_deployment_descriptor_v1(
        probe[DEPLOYMENT_DESCRIPTOR_PATH_V1],
    )
    capture_paths = (
        _required_material_capture_paths_v1(build, catalog)
        | frozenset(item.source_path for item in descriptor.artifacts)
    )
    captured = _capture_verified_distribution_tree_v1(
        build.facts, build.files, root,
        expected_type=_AuthenticatedDistributionObjectV1,
        uid=0, gid=0, chain_stop=None,
        extra_capture_paths=capture_paths, require_compiled_review=True,
    )
    latest = selected.transaction.prefix.records[-1]
    prerequisite_path = (
        OWNERSHIP_ROOT / "startup-prerequisites-v1"
        / f"{latest.request_id}.json"
    )
    prerequisite = _capture_trusted_file_v1(
        prerequisite_path, executable=False, uid=0, gid=0,
        chain_stop=None, maximum=MAX_STARTUP_PREREQUISITE_BYTES_V1,
        require_single_link=True,
    )
    if stat.S_IMODE(prerequisite.identity[2]) != 0o644:
        raise _invalid("startup prerequisite mode")
    materials = _bind_preflight_materials_core_v1(
        build, latest, selected.predecessor, captured, prerequisite.content,
    )
    repeated = _require_materials_selected_by_snapshot_v1(
        authenticated.snapshot, materials,
    )
    if repeated != selected:
        raise _invalid("product preflight ownership selection")
    return selected, materials


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


_PUBLIC_FAILURE_EXIT_V1 = {
    CODE_MISSING: EXIT_MISSING,
    CODE_INVALID: EXIT_INVALID,
    CODE_HEAD_MISMATCH: EXIT_HEAD_MISMATCH,
    CODE_PLATFORM: EXIT_PLATFORM,
    CODE_RECOVERY: EXIT_RECOVERY,
}


def _run_operational_command_v1(command: CliCommandV1) -> None:
    """Run only fixed-root checks; launch remains closed until its bootstrap."""
    if type(command) is not CliCommandV1:
        raise _invalid("operational command")
    if command.command == "check-all":
        if command.entry_id is not None:
            raise _invalid("operational command")
        _publish_preflight_attestation_v1(
            _attest_operational_preflight_v1(),
        )
        return
    if command.command not in {"check", "launch"} or command.entry_id is None:
        raise _invalid("operational command")
    lease = _LaunchGateLeaseV1(_acquire_startup_gate_shared_v1())
    try:
        operational = _attest_operational_preflight_v1()
        entry = _require_preflight_entry_v1(operational, command.entry_id)
        if command.command == "launch":
            if entry.class_name != "gated_service":
                raise _missing("B3 entrypoint supervision is incomplete")
            _launch_gated_service_v1(
                _make_launch_plan_v1(operational, entry), lease,
            )
    finally:
        lease.close()


def _public_failure_v1(error: BaseException) -> tuple[str, int]:
    """Reduce every internal failure to one closed public code/exit pair."""
    if isinstance(error, PreflightError):
        expected = _PUBLIC_FAILURE_EXIT_V1.get(error.code)
        if expected is not None and error.exit_status == expected:
            return error.code, expected
    return CODE_RECOVERY, EXIT_RECOVERY


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch the closed administrative CLI without exposing diagnostics."""
    try:
        raw_argv = list(sys.argv[1:] if argv is None else argv)
        command = parse_cli_v1(raw_argv)
        # These checks precede every operational read, lock or subprocess.
        require_linux_before_io_v1()
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise _invalid("effective identity")
        _run_operational_command_v1(command)
        # The current residual always denies. Retain the success shape for the
        # complete B3 implementation without adding a caller-controlled switch.
        return 0
    except Exception as error:  # fail closed; target SystemExit stays authoritative
        code, exit_status = _public_failure_v1(error)
        try:
            sys.stderr.write(code + "\n")
        except BaseException:
            return EXIT_RECOVERY
        return exit_status


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


def _teardown_systemctl_process_v1(process: subprocess.Popen[bytes]) -> None:
    failed = False
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        failed = True
    try:
        process.wait(timeout=SYSTEMCTL_TEARDOWN_TIMEOUT_SECONDS_V1)
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
        raise _recovery("systemctl process teardown")


def _run_systemctl_bounded_v1(
    argv: tuple[str, ...],
) -> tuple[int, bytes, bytes]:
    """Run the sole closed systemctl protocol with asymmetric stream caps."""
    if (
        type(argv) is not tuple or not argv
        or any(type(item) is not str or not item or "\0" in item for item in argv)
        or not Path(argv[0]).is_absolute()
    ):
        raise _invalid("systemctl command")
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
            raise _invalid("systemctl pipes")
        for stream, label in (
            (process.stdout, "stdout"), (process.stderr, "stderr"),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        deadline = time.monotonic() + SYSTEMCTL_TIMEOUT_SECONDS_V1
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    argv, SYSTEMCTL_TIMEOUT_SECONDS_V1,
                )
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(
                    argv, SYSTEMCTL_TIMEOUT_SECONDS_V1,
                )
            for key, _mask in events:
                target = output if key.data == "stdout" else error
                maximum = (
                    MAX_SYSTEMCTL_STDOUT_BYTES_V1
                    if key.data == "stdout"
                    else MAX_SYSTEMCTL_STDERR_BYTES_V1
                )
                try:
                    chunk = os.read(
                        key.fileobj.fileno(), maximum + 1 - len(target),
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target.extend(chunk)
                if len(target) > maximum:
                    raise _invalid("systemctl output bound")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(
                argv, SYSTEMCTL_TIMEOUT_SECONDS_V1,
            )
        returncode = process.wait(timeout=remaining)
        return returncode, bytes(output), bytes(error)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _invalid("systemctl execution") from exc
    finally:
        active_failure = sys.exception()
        teardown_failure: PreflightError | None = None
        try:
            selector.close()
        except OSError:
            teardown_failure = _recovery("systemctl selector teardown")
        if process is not None:
            try:
                _teardown_systemctl_process_v1(process)
            except PreflightError as exc:
                teardown_failure = exc
        if teardown_failure is not None:
            if active_failure is not None:
                raise teardown_failure from active_failure
            raise teardown_failure


def _run_systemctl_show_v1(
    systemctl_executable: str, unit_name: str | None,
    properties: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    argv = systemctl_show_argv_v1(
        systemctl_executable, unit_name, properties,
    )
    returncode, stdout, stderr = _run_systemctl_bounded_v1(argv)
    if returncode != 0 or stderr:
        raise _invalid("systemctl show command")
    return parse_systemctl_show_v1(stdout, properties)


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


def normalize_systemd_duration_usec_v1(
    value: str, *, observed: bool = False,
) -> str:
    """Normalize one duration; `observed` accepts what systemd itself renders.

    A signed directive and a live property are not the same input. What a
    catalog may DECLARE stays narrow: `infinity` remains refused there, because
    an unbounded timeout is a policy an author must not be able to sign.  What
    systemd REPORTS is not a choice: measured on 255.4 a zero duration renders
    without a suffix (`RandomizedDelayUSec=0`, `WatchdogUSec=0`) and an
    unbounded one as `infinity` (`JobTimeoutUSec`), on properties no unit ever
    set. Both are canonical single tokens the component grammar cannot parse,
    so refusing them on the observed side denied every real unit.

    `infinity` is returned verbatim: it has no microsecond value, and inventing
    one would compare unequal to the rendering that produced it.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise _invalid("duration")
    if value == "0":
        return "0"
    if observed and value == "infinity":
        return "infinity"
    total = 0
    for component in value.split(" "):
        match = _DURATION_COMPONENT_RE.fullmatch(component)
        if match is None:
            raise _invalid(f"duration component {component!r} in {value!r}")
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
        result[name] = normalize_systemd_duration_usec_v1(duration, observed=True)
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


def _validate_configured_directives_v1(
    directives: object,
) -> tuple[_ServiceDirectiveV1, ...]:
    if (
        type(directives) is not tuple or not directives
        or any(type(item) is not _ServiceDirectiveV1 for item in directives)
    ):
        raise _invalid("configured directives")
    rebuilt = tuple(
        _validate_service_directive_v1(
            item.section, item.name, item.value_type, list(item.values),
        )
        for item in directives
    )
    keys = tuple((item.section, item.name) for item in rebuilt)
    if (
        rebuilt != directives
        or rebuilt != tuple(sorted(rebuilt, key=_service_directive_sort_key_v1))
        or len(keys) != len(set(keys))
    ):
        raise _invalid("configured directives")
    return rebuilt


def _configured_directives_hash_v1(
    directives: tuple[_ServiceDirectiveV1, ...],
) -> str:
    """Hash the independently parsed, complete configured directive list."""
    validated = _validate_configured_directives_v1(directives)
    encoded = _canonical_json({
        "schema_version": 1,
        "directives": [item.as_value() for item in validated],
    })
    return _digest(SYSTEMD_CONFIGURED_DIRECTIVES_DOMAIN_V1, encoded)


def _configured_directives_hash_from_fragment_v1(
    unit_name: str, fragment: bytes,
) -> str:
    parsed = _parse_service_unit_fragment_v1(unit_name, fragment)
    return _configured_directives_hash_v1(parsed.directives)


def _systemd_applicable_directives_v1(
    class_name: str,
) -> tuple[tuple[str, str, str], ...]:
    section = {
        "gated_service": "Service",
        "stop_only": "Service",
        "gated_timer": "Timer",
        "target": None,
    }.get(class_name)
    if class_name not in {"gated_service", "stop_only", "gated_timer", "target"}:
        raise _invalid("systemd property class")
    result = tuple(
        (candidate_section, name, value_type)
        for (candidate_section, name), value_type
        in _SERVICE_CATALOG_DIRECTIVE_TYPES_V1.items()
        if candidate_section == "Unit" or candidate_section == section
    )
    names = tuple(item[1] for item in result)
    if len(names) != len(set(names)):
        raise _invalid("systemd projection property collision")
    return result


def _systemd_manager_properties_for_directive_v1(
    section: str, name: str,
) -> tuple[str, ...]:
    if (section, name) not in _SERVICE_CATALOG_DIRECTIVE_TYPES_V1:
        raise _invalid("systemd directive property")
    if section == "Install":
        raise _invalid("systemd Install projection")
    return _SYSTEMD_DIRECTIVE_PROPERTY_REMAP_V1.get(
        (section, name), (name,),
    )


def _systemd_property_plan_v1(
    entry: _ServiceCatalogEntryV1,
) -> _SystemdPropertyPlanV1:
    """Compile the exact property request and output cardinalities for a unit."""
    if (
        type(entry) is not _ServiceCatalogEntryV1 or entry.unit_spec is None
        or entry.unit_name is None
    ):
        raise _invalid("systemd property entry")
    directives = _validate_configured_directives_v1(entry.unit_spec.directives)
    if _service_fragment_hash_v1(
        entry.unit_name, _render_service_directives_v1(directives),
    ) != entry.unit_spec.fragment_hash:
        raise _invalid("systemd property unit specification")
    applicable = _systemd_applicable_directives_v1(entry.class_name)
    applicable_keys = frozenset((section, name) for section, name, _ in applicable)
    configured = _service_directive_index_v1(entry.unit_spec)
    configured_projected = frozenset(
        key for key in configured if key[0] != "Install"
    )
    if not configured_projected.issubset(applicable_keys):
        raise _invalid("systemd unrepresentable directive")

    requested = set(_SYSTEMD_BASE_PROPERTIES_V1)
    requested.update(_SYSTEMD_ADDED_EDGE_RELATIONS_V1)
    for section, name, _value_type in applicable:
        requested.update(_systemd_manager_properties_for_directive_v1(
            section, name,
        ))
    requested_properties = tuple(sorted(requested))
    _validate_property_request_v1(requested_properties)

    cardinalities = {name: 1 for name in requested_properties}
    for directive_name, pair in _SYSTEMD_EXEC_PROPERTY_PAIRS_V1.items():
        if entry.class_name not in {"gated_service", "stop_only"}:
            continue
        count = int(("Service", directive_name) in configured)
        cardinalities[pair[0]] = count
        cardinalities[pair[1]] = count
    if entry.class_name == "gated_timer":
        monotonic_count = sum(
            ("Timer", name) in configured
            for name in ("OnBootSec", "OnActiveSec", "OnUnitActiveSec")
        )
        calendar_count = int(("Timer", "OnCalendar") in configured)
        # Measured on systemd 255.4 with the exact argv this module builds
        # (`--no-pager --plain --all show --property=...`): a timer collection
        # with no entries is OMITTED, it does not render as an empty line.
        # Only scalar and list properties render empty. Claiming one value for
        # an unset collection put a name in the expected set that can never be
        # observed, and denied every real timer without OnCalendar.
        cardinalities["TimersMonotonic"] = monotonic_count
        cardinalities["TimersCalendar"] = calendar_count
    return _SystemdPropertyPlanV1(
        entry.class_name, requested_properties,
        tuple(sorted(cardinalities.items())),
    )


def _systemd_set_detail_v1(
    reason: str, expected: set[str], observed: set[str],
) -> str:
    """Name the two sides of a set difference, deterministically and bounded."""
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    return (
        f"{reason} missing={','.join(missing[:12]) or '-'} "
        f"unexpected={','.join(unexpected[:12]) or '-'}"
    )


def _validate_systemd_property_cardinality_v1(
    plan: _SystemdPropertyPlanV1,
    observed: Mapping[str, tuple[str, ...]],
) -> None:
    if (
        type(plan) is not _SystemdPropertyPlanV1
        or not isinstance(observed, Mapping)
        or any(
            type(name) is not str or type(values) is not tuple
            or any(type(value) is not str for value in values)
            for name, values in observed.items()
        )
    ):
        raise _invalid("systemd property cardinality")
    cardinalities = dict(plan.cardinalities)
    expected_names = {
        name for name, count in cardinalities.items() if count != 0
    }
    if set(observed) != expected_names:
        # The detail names the difference. It never reaches stderr, and the
        # interface systemd exposes has already moved once under us: a denial
        # that only says "the set differs" costs a full CI round trip to learn
        # which name it was. Property names are not payload; values are, and
        # stay out.
        raise _invalid(_systemd_set_detail_v1(
            "systemd property set", expected_names, set(observed),
        ))
    for name, count in cardinalities.items():
        if len(observed.get(name, ())) != count:
            raise _invalid(
                "systemd property cardinality "
                f"{name} expected={count} observed={len(observed.get(name, ()))}"
            )


def _systemd_single_property_v1(
    observed: Mapping[str, tuple[str, ...]], name: str,
) -> str:
    values = observed.get(name)
    if type(values) is not tuple or len(values) != 1:
        raise _invalid("systemd single property")
    return values[0]


def _normalize_systemd_boolean_v1(value: str) -> str:
    if value not in {"yes", "no"}:
        raise _invalid("systemd boolean")
    return value


def _normalize_systemd_integer_v1(
    value: str, *, signed: bool = False, infinity: bool = False,
) -> str:
    if infinity and value == "infinity":
        return value
    pattern = r"(?:0|-?[1-9][0-9]*)\Z" if signed else r"(?:0|[1-9][0-9]*)\Z"
    if type(value) is not str or re.fullmatch(pattern, value) is None:
        raise _invalid("systemd integer")
    number = int(value)
    if abs(number) > (1 << 63) - 1:
        raise _invalid("systemd integer overflow")
    return str(number)


def _normalize_systemd_signal_v1(value: str) -> str:
    if type(value) is not str:
        raise _invalid("systemd signal")
    if _INTEGER_RE.fullmatch(value) is not None:
        name = _SYSTEMD_SIGNAL_NAMES_V1.get(int(value))
    else:
        name = value if value in _SYSTEMD_SIGNAL_NAMES_V1.values() else None
    if name is None:
        raise _invalid("systemd signal")
    return name


def _normalize_systemd_word_set_v1(
    values: Iterable[str], *, detail: str,
    validator: Callable[[str], bool], numeric: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, Iterable):
        raise _invalid(detail)
    items = tuple(values)
    if any(type(item) is not str or not item or not validator(item) for item in items):
        raise _invalid(detail)
    key = (lambda item: int(item)) if numeric else (lambda item: item.encode("utf-8"))
    ordered = tuple(sorted(items, key=key))
    if len(ordered) != len(set(ordered)):
        raise _invalid(detail)
    return ordered


def _systemd_scalar_set_v1(values: tuple[str, ...]) -> tuple[str, ...]:
    return () if not values else (" ".join(values),)


def _normalize_systemd_capabilities_v1(value: str) -> tuple[str, ...]:
    words = tokenize_systemd_words_v1(value)
    normalized = _normalize_systemd_word_set_v1(
        (item.upper() for item in words), detail="systemd capabilities",
        validator=lambda item: item in _SYSTEMD_CAPABILITY_NAMES_V1,
    )
    return _systemd_scalar_set_v1(normalized)


def _normalize_systemd_supplementary_groups_v1(value: str) -> tuple[str, ...]:
    normalized = _normalize_systemd_word_set_v1(
        tokenize_systemd_words_v1(value), detail="systemd supplementary groups",
        validator=lambda item: _INTEGER_RE.fullmatch(item) is not None,
        numeric=True,
    )
    return _systemd_scalar_set_v1(normalized)


def _normalize_systemd_named_set_v1(
    value: str, *, pattern: str, detail: str,
) -> tuple[str, ...]:
    """Normalize one named set, keeping systemd's negation marker intact.

    Measured on systemd 255.4: a unit that restricts nothing renders the
    property as a bare `~` — the empty NEGATED set, "deny nothing" — and a
    unit that restricts something renders the plain names. Rejecting the
    marker denied every unit that had set no restriction, which is most of
    them. The marker is kept as its own first token rather than dropped:
    `~` and an empty set are opposite meanings, and collapsing them would let
    a later drift from one to the other pass unnoticed.
    """
    words = tokenize_systemd_words_v1(value)
    negated = bool(words) and words[0].startswith("~")
    if negated:
        head = words[0][1:]
        words = ((head,) if head else ()) + words[1:]
    normalized = _normalize_systemd_word_set_v1(
        words, detail=detail,
        validator=lambda item: re.fullmatch(pattern, item) is not None,
    )
    scalar = _systemd_scalar_set_v1(normalized)
    return ("~", *scalar) if negated else scalar


def _normalize_systemd_unit_list_v1(value: str) -> tuple[str, ...]:
    return _normalize_systemd_word_set_v1(
        tokenize_systemd_words_v1(value), detail="systemd unit list",
        validator=lambda item: (
            _OBSERVED_UNIT_RE.fullmatch(item) is not None
            and len(item.encode("utf-8")) <= 255
        ),
    )


def _normalize_systemd_path_list_v1(value: str) -> tuple[str, ...]:
    return _normalize_systemd_word_set_v1(
        tokenize_systemd_words_v1(value), detail="systemd path list",
        validator=lambda item: (
            _catalog_absolute_path_v1(item, "systemd path list") == item
        ),
    )


def _normalize_systemd_environment_v1(value: str) -> tuple[str, ...]:
    items = tokenize_systemd_words_v1(value)
    by_name: dict[str, str] = {}
    for item in items:
        name, separator, _raw = item.partition("=")
        if separator != "=" or not name:
            raise _invalid("systemd environment")
        _validate_catalog_environment_name_v1(name, target=False)
        if name in by_name:
            raise _invalid("systemd environment duplicate")
        by_name[name] = item
    return tuple(
        by_name[name]
        for name in sorted(by_name, key=lambda item: item.encode("utf-8"))
    )


def _normalize_systemd_memory_v1(value: str) -> str:
    if type(value) is not str:
        raise _invalid("systemd memory")
    if value == "infinity":
        return value
    match = re.fullmatch(r"(0|[1-9][0-9]*)([KMGT]?)", value)
    if match is None:
        raise _invalid("systemd memory")
    number = int(match.group(1))
    suffix = match.group(2)
    if suffix:
        number *= 1024 ** ("KMGT".index(suffix) + 1)
    if number > (1 << 64) - 1:
        raise _invalid("systemd memory overflow")
    return str(number)


def _normalize_systemd_umask_v1(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-7]{4}", value) is None:
        raise _invalid("systemd UMask")
    return value


def _normalize_systemd_success_status_v1(value: str) -> tuple[str, ...]:
    codes: list[str] = []
    signals: list[str] = []
    for item in tokenize_systemd_words_v1(value):
        if _INTEGER_RE.fullmatch(item) is not None:
            number = int(item)
            if number > 255:
                raise _invalid("systemd success status")
            codes.append(str(number))
        else:
            signals.append(_normalize_systemd_signal_v1(item))
    if len(codes) != len(set(codes)) or len(signals) != len(set(signals)):
        raise _invalid("systemd success status duplicate")
    normalized = tuple(sorted(codes, key=int)) + tuple(
        sorted(signals, key=lambda item: item.encode("utf-8"))
    )
    return _systemd_scalar_set_v1(normalized)


def _normalize_systemd_scalar_v1(value: str) -> tuple[str, ...]:
    if (
        type(value) is not str or "\0" in value or "\n" in value or "\r" in value
        or len(value.encode("utf-8")) > 64 * 1024
    ):
        raise _invalid("systemd scalar")
    return () if value == "" else (value,)


def _normalize_signed_systemd_directive_v1(
    directive: _ServiceDirectiveV1,
) -> tuple[str, ...]:
    section, name, value_type = (
        directive.section, directive.name, directive.value_type,
    )
    values = directive.values
    if value_type == "boolean":
        return (_normalize_systemd_boolean_v1(values[0]),)
    if value_type == "duration":
        return (normalize_systemd_duration_usec_v1(values[0]),)
    if value_type == "integer":
        normalized_integer = _normalize_systemd_integer_v1(
            values[0], signed=(name == "Nice"), infinity=(name == "TasksMax"),
        )
        # One declared `LimitNOFILE` sets both the hard and the soft limit;
        # the observed side reports the pair, so the signed side declares it.
        if name == "LimitNOFILE":
            return (normalized_integer, normalized_integer)
        return (normalized_integer,)
    if value_type == "unit_list":
        return _normalize_systemd_word_set_v1(
            values, detail="signed systemd unit list",
            validator=lambda item: _PREDECESSOR_UNIT_RE_V1.fullmatch(item) is not None,
        )
    if value_type == "path_list":
        return _normalize_systemd_word_set_v1(
            values, detail="signed systemd path list",
            validator=lambda item: (
                _catalog_absolute_path_v1(item, "signed systemd path") == item
            ),
        )
    if value_type == "environment":
        return _normalize_systemd_environment_v1(" ".join(values))
    if value_type == "argv":
        first = values[0]
        return ((first[1:] if first.startswith("!") else first), *values[1:])
    if section == "Service" and name == "SupplementaryGroups":
        return _normalize_systemd_supplementary_groups_v1(values[0])
    if section == "Service" and name in {
        "CapabilityBoundingSet", "AmbientCapabilities",
    }:
        return _normalize_systemd_capabilities_v1(values[0])
    if section == "Service" and name == "KillSignal":
        return (_normalize_systemd_signal_v1(values[0]),)
    if section == "Service" and name == "SuccessExitStatus":
        return _normalize_systemd_success_status_v1(values[0])
    if section == "Service" and name == "UMask":
        return (_normalize_systemd_umask_v1(values[0]),)
    if section == "Service" and name in {"MemoryHigh", "MemoryMax"}:
        return (_normalize_systemd_memory_v1(values[0]),)
    if section == "Service" and name == "RestrictAddressFamilies":
        return _normalize_systemd_named_set_v1(
            values[0], pattern=r"AF_[A-Z0-9_]+", detail="address families",
        )
    if section == "Service" and name == "SystemCallArchitectures":
        return _normalize_systemd_named_set_v1(
            values[0], pattern=r"[A-Za-z0-9_-]+", detail="architectures",
        )
    return values


def _normalize_manager_directive_v1(
    section: str, name: str, value_type: str,
    observed: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    manager_properties = _systemd_manager_properties_for_directive_v1(
        section, name,
    )
    if name in _SYSTEMD_EXEC_PROPERTY_PAIRS_V1:
        historical_name, extended_name = _SYSTEMD_EXEC_PROPERTY_PAIRS_V1[name]
        historical = observed.get(historical_name, ())
        extended = observed.get(extended_name, ())
        if not historical and not extended:
            return ()
        raise _invalid("systemd Exec requires signed context")
    if name in _SYSTEMD_TIMER_BASE_PROPERTIES_V1:
        raise _invalid("systemd timer requires grouped context")
    raw = _systemd_single_property_v1(observed, manager_properties[0])
    if name == "LimitNOFILE":
        # systemd reports the HARD limit in `LimitNOFILE` and the SOFT one in
        # `LimitNOFILESoft`, and by default they differ: measured 1048576 and
        # 1024 on a unit that configured neither. Requiring equality denied
        # every unit that left the limit alone. A unit that sets the directive
        # sets both, so the signed side normalizes one value to the same pair
        # and the two shapes stay comparable.
        soft = _systemd_single_property_v1(observed, manager_properties[1])
        return (
            _normalize_systemd_integer_v1(raw),
            _normalize_systemd_integer_v1(soft),
        )
        return (normalized,)
    if value_type == "boolean":
        return (_normalize_systemd_boolean_v1(raw),)
    if value_type == "duration":
        if name == "WatchdogSec" and raw == "0":
            return ("0",)
        return (normalize_systemd_duration_usec_v1(raw, observed=True),)
    if value_type == "integer":
        return (_normalize_systemd_integer_v1(
            raw, signed=(name == "Nice"), infinity=(name == "TasksMax"),
        ),)
    if value_type == "unit_list":
        return _normalize_systemd_unit_list_v1(raw)
    if value_type == "path_list":
        if name == "WorkingDirectory":
            if raw == "":
                return ()
            # systemd prefixes the rendered directory with its own marker when
            # the unit did not name a plain path: measured `!/home/user` on
            # a unit that asked for the account home. The marker is part of
            # what the manager reports, not a malformed path, and it is kept
            # as its own token so a later change between marked and plain
            # cannot pass unnoticed.
            marker = raw[0] if raw[0] in "!-" else ""
            path = raw[1:] if marker else raw
            _catalog_absolute_path_v1(path, "systemd working directory")
            return (marker, path) if marker else (path,)
        return _normalize_systemd_path_list_v1(raw)
    if value_type == "environment":
        return _normalize_systemd_environment_v1(raw)
    if section == "Service" and name == "SupplementaryGroups":
        return _normalize_systemd_supplementary_groups_v1(raw)
    if section == "Service" and name in {
        "CapabilityBoundingSet", "AmbientCapabilities",
    }:
        return _normalize_systemd_capabilities_v1(raw)
    if section == "Service" and name == "KillSignal":
        return (_normalize_systemd_signal_v1(raw),)
    if section == "Service" and name == "SuccessExitStatus":
        return _normalize_systemd_success_status_v1(raw)
    if section == "Service" and name == "UMask":
        return (_normalize_systemd_umask_v1(raw),)
    if section == "Service" and name in {"MemoryHigh", "MemoryMax"}:
        return (_normalize_systemd_memory_v1(raw),)
    if section == "Service" and name == "RestrictAddressFamilies":
        return _normalize_systemd_named_set_v1(
            raw, pattern=r"AF_[A-Z0-9_]+", detail="address families",
        )
    if section == "Service" and name == "SystemCallArchitectures":
        return _normalize_systemd_named_set_v1(
            raw, pattern=r"[A-Za-z0-9_-]+", detail="architectures",
        )
    return _normalize_systemd_scalar_v1(raw)


def _compile_systemd_manager_projection_v1(
    entry: _ServiceCatalogEntryV1,
    observed: Mapping[str, tuple[str, ...]],
) -> _SystemdManagerProjectionV1:
    """Validate one closed show result and project it onto signed names."""
    plan = _systemd_property_plan_v1(entry)
    _validate_systemd_property_cardinality_v1(plan, observed)
    assert entry.unit_spec is not None
    configured = _service_directive_index_v1(entry.unit_spec)
    applicable = _systemd_applicable_directives_v1(entry.class_name)

    exec_values: dict[str, tuple[str, ...]] = {}
    for name, pair in _SYSTEMD_EXEC_PROPERTY_PAIRS_V1.items():
        key = ("Service", name)
        if key not in {(section, item_name) for section, item_name, _ in applicable}:
            continue
        directive = configured.get(key)
        if directive is None:
            exec_values[name] = ()
            continue
        expected_flags = (
            ("no-setuid",) if directive.values[0].startswith("!") else ()
        )
        parsed = validate_exec_property_pair_v1(
            observed[pair[0]], observed[pair[1]], expected_flags,
        )
        if len(parsed) != 1:
            raise _invalid("systemd Exec cardinality")
        exec_values[name] = tuple(parsed[0]["argv"])

    timer_values: dict[str, tuple[str, ...]] = {}
    if entry.class_name == "gated_timer":
        # A timer collection with no entries is OMITTED by systemd, not
        # rendered empty, so the property is simply absent from the
        # observation. The `("",)` form is kept for the manager that does
        # render an empty line; both mean the same empty collection.
        monotonic_raw = observed.get("TimersMonotonic", ())
        calendar_raw = observed.get("TimersCalendar", ())
        monotonic = () if monotonic_raw == ("",) else monotonic_raw
        calendar = () if calendar_raw == ("",) else calendar_raw
        parsed_timers = parse_systemd_timer_properties_v1(monotonic, calendar)
        expected_bases = {
            base
            for name, (_manager_name, base) in _SYSTEMD_TIMER_BASE_PROPERTIES_V1.items()
            if ("Timer", name) in configured
        }
        if set(parsed_timers) != expected_bases:
            raise _invalid("systemd timer base set")
        for name, (_manager_name, base) in _SYSTEMD_TIMER_BASE_PROPERTIES_V1.items():
            value = parsed_timers.get(base)
            timer_values[name] = () if value is None else (value,)

    properties: list[_SystemdManagerPropertyV1] = []
    for section, name, value_type in applicable:
        if name in exec_values:
            normalized = exec_values[name]
        elif name in timer_values:
            normalized = timer_values[name]
        else:
            normalized = _normalize_manager_directive_v1(
                section, name, value_type, observed,
            )
        directive = configured.get((section, name))
        if directive is not None:
            signed = _normalize_signed_systemd_directive_v1(directive)
            if name in _SYSTEMD_DIRECT_RELATIONS_V1:
                if not set(signed).issubset(normalized):
                    raise _invalid("systemd direct relation")
            elif signed != normalized:
                raise _invalid("systemd configured directive")
        elif section == "Unit" and name == "Documentation" and normalized:
            raise _invalid("systemd Documentation default")
        elif (
            section == "Service" and name == "WatchdogSec"
            and normalized not in (("0",), ("infinity",))
        ):
            # A watchdog that was never configured is reported as disabled in
            # two equivalent ways. Measured on systemd 255.4: most units render
            # `WatchdogUSec=0`, others render `infinity` (observed on
            # `launchpadlib-cache-clean.service`). Both mean "no watchdog";
            # pinning only the first denied a unit that configured nothing.
            raise _invalid(f"systemd Watchdog default {normalized!r}")
        properties.append(_SystemdManagerPropertyV1(
            name, value_type, normalized,
        ))
    properties.sort(key=lambda item: item.name.encode("utf-8"))
    names = tuple(item.name for item in properties)
    if len(names) != len(set(names)):
        raise _invalid("systemd projection property collision")
    return _SystemdManagerProjectionV1(tuple(properties))


def _compile_systemd_added_edge_pairs_v1(
    entry: _ServiceCatalogEntryV1,
    observed: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[str, str], ...]:
    plan = _systemd_property_plan_v1(entry)
    _validate_systemd_property_cardinality_v1(plan, observed)
    assert entry.unit_spec is not None
    configured = _service_directive_index_v1(entry.unit_spec)
    residual: list[tuple[str, str]] = []
    for relation in sorted(_SYSTEMD_ADDED_EDGE_RELATIONS_V1):
        values = _normalize_systemd_unit_list_v1(
            _systemd_single_property_v1(observed, relation),
        )
        direct = configured.get(("Unit", relation))
        explicit = set(() if direct is None else direct.values)
        if not explicit.issubset(values):
            raise _invalid("systemd direct relation")
        residual.extend(
            (relation, unit_name) for unit_name in values
            if unit_name not in explicit
        )
    residual.sort(key=lambda item: (
        item[0].encode("utf-8"), item[1].encode("utf-8"),
    ))
    if (
        len(residual) > MAX_SYSTEMD_ADDED_EDGES_PER_UNIT_V1
        or len(residual) != len(set(residual))
    ):
        raise _invalid("systemd added edge bound")
    return tuple(residual)


def _systemd_origin_file_hash_v1(path: str, content: bytes) -> str:
    return _framed_system_file_hash_v1(
        SYSTEMD_ORIGIN_FILE_DOMAIN_V1, path, content, "systemd origin file",
    )


def _systemd_origin_source_hash_v1(path: str, content: bytes) -> str:
    return _framed_system_file_hash_v1(
        SYSTEMD_ORIGIN_SOURCE_DOMAIN_V1, path, content,
        "systemd origin source",
    )


def _strict_systemd_child_v1(path: str, roots: tuple[str, ...]) -> bool:
    try:
        candidate = PurePosixPath(_catalog_absolute_path_v1(
            path, "systemd origin path",
        ))
    except PreflightError:
        return False
    for root_text in roots:
        root = PurePosixPath(root_text)
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if relative.parts:
            return True
    return False


def _validate_systemd_file_claim_v1(
    *, path: object, size: object, content_hash: object,
    uid: object, gid: object, mode: object, roots: tuple[str, ...],
    detail: str,
) -> None:
    if (
        not isinstance(path, str) or not _strict_systemd_child_v1(path, roots)
        or type(size) is not int or not 0 <= size <= MAX_SYSTEMD_ORIGIN_BYTES_V1
        or not isinstance(content_hash, str) or _DIGEST_RE.fullmatch(content_hash) is None
        or type(uid) is not int or uid != 0
        or type(gid) is not int or gid != 0
        or type(mode) is not int or not 0 <= mode <= 0o7777 or mode & 0o022
    ):
        raise _invalid(detail)


def _validate_systemd_manager_added_edge_v1(
    edge: _SystemdManagerAddedEdgeV1,
) -> None:
    if (
        type(edge) is not _SystemdManagerAddedEdgeV1
        or edge.relation not in _SYSTEMD_ADDED_EDGE_RELATIONS_V1
        or not isinstance(edge.unit_name, str)
        or _OBSERVED_UNIT_RE.fullmatch(edge.unit_name) is None
        or len(edge.unit_name.encode("utf-8")) > 255
        or edge.origin_kind not in {
            "root_fragment", "root_generator", "manager_virtual",
            "absent_unit",
        }
        or edge.load_state != (
            "not-found" if edge.origin_kind == "absent_unit" else "loaded"
        )
    ):
        raise _invalid("systemd added edge")
    file_fields = (
        edge.fragment_path, edge.size, edge.content_hash,
        edge.uid, edge.gid, edge.mode,
    )
    source_fields = (
        edge.source_path, edge.source_size, edge.source_content_hash,
        edge.source_uid, edge.source_gid, edge.source_mode,
    )
    if edge.origin_kind == "manager_virtual":
        if (
            edge.unit_name not in _SYSTEMD_MANAGER_VIRTUAL_UNITS_V1
            or any(value is not None for value in (*file_fields, *source_fields))
            or edge.unit_file_state is not None
        ):
            raise _invalid("systemd manager virtual origin")
        return

    if edge.origin_kind == "absent_unit":
        # A relation may name a unit that does not exist: systemd keeps the
        # edge and reports the target as not-found. That is an ordinary,
        # observable fact on any real system, not tampering, and denying it
        # made the canonical graph impossible to build. The node is recorded
        # as absent and carries no file, no owner and no state, so its later
        # appearance changes the effective photograph instead of hiding in it.
        if (
            any(value is not None for value in (*file_fields, *source_fields))
            or edge.unit_file_state is not None
        ):
            raise _invalid("systemd absent origin")
        return

    roots = (
        _SYSTEMD_ROOT_FRAGMENT_ROOTS_V1
        if edge.origin_kind == "root_fragment"
        else _SYSTEMD_ROOT_GENERATOR_ROOTS_V1
    )
    _validate_systemd_file_claim_v1(
        path=edge.fragment_path, size=edge.size,
        content_hash=edge.content_hash, uid=edge.uid, gid=edge.gid,
        mode=edge.mode, roots=roots, detail="systemd origin fragment",
    )
    if edge.origin_kind == "root_fragment":
        if (
            any(value is not None for value in source_fields)
            or edge.unit_file_state not in _SYSTEMD_ROOT_FRAGMENT_STATES_V1
        ):
            raise _invalid("systemd root fragment origin")
        return
    if edge.unit_file_state != "generated":
        raise _invalid("systemd generator state")
    _validate_systemd_file_claim_v1(
        path=edge.source_path, size=edge.source_size,
        content_hash=edge.source_content_hash, uid=edge.source_uid,
        gid=edge.source_gid, mode=edge.source_mode,
        roots=("/etc", "/usr"), detail="systemd generator source",
    )


def _validate_systemd_manager_projection_v1(
    projection: _SystemdManagerProjectionV1,
) -> None:
    if (
        type(projection) is not _SystemdManagerProjectionV1
        or type(projection.properties) is not tuple
        or any(
            type(item) is not _SystemdManagerPropertyV1
            or ("Unit", item.name) not in _SERVICE_CATALOG_DIRECTIVE_TYPES_V1
            and ("Service", item.name) not in _SERVICE_CATALOG_DIRECTIVE_TYPES_V1
            and ("Timer", item.name) not in _SERVICE_CATALOG_DIRECTIVE_TYPES_V1
            or item.value_type not in {
                "scalar", "boolean", "duration", "integer", "argv",
                "environment", "unit_list", "path_list",
            }
            or type(item.values) is not tuple
            or any(
                type(value) is not str or "\0" in value or "\n" in value
                or "\r" in value or len(value.encode("utf-8")) > 64 * 1024
                for value in item.values
            )
            for item in projection.properties
        )
    ):
        raise _invalid("systemd manager projection")
    names = tuple(item.name for item in projection.properties)
    if (
        names != tuple(sorted(names, key=lambda item: item.encode("utf-8")))
        or len(names) != len(set(names))
    ):
        raise _invalid("systemd manager projection order")


def _validate_enablement_link_claim_v1(link: _EnablementLinkV1) -> None:
    if type(link) is not _EnablementLinkV1:
        raise _invalid("effective enablement link")
    path = _catalog_absolute_path_v1(link.path, "effective enablement path")
    target = link.target
    if (
        not path.startswith(SYSTEM_UNIT_ROOT_TEXT_V1 + "/")
        or not isinstance(target, str) or not target.startswith("../")
        or target.count("/") != 1 or target in {"../", ".."}
    ):
        raise _invalid("effective enablement link")
    unit_name = target[3:]
    validate_unit_name_v1(unit_name)
    if (
        PurePosixPath(path).parent.parent / unit_name
        != PurePosixPath(SYSTEM_UNIT_ROOT_TEXT_V1) / unit_name
    ):
        raise _invalid("effective enablement target")


def _validate_effective_systemd_unit_v1(
    unit: _EffectiveSystemdUnitV1,
) -> None:
    if (
        type(unit) is not _EffectiveSystemdUnitV1
        or validate_entry_id_v1(unit.entry_id) != unit.entry_id
        or validate_unit_name_v1(unit.unit_name) != unit.unit_name
        or unit.fragment_path
        != f"{SYSTEM_UNIT_ROOT_TEXT_V1}/{unit.unit_name}"
        or not isinstance(unit.fragment_hash, str)
        or _DIGEST_RE.fullmatch(unit.fragment_hash) is None
        or type(unit.fragment_uid) is not int or unit.fragment_uid != 0
        or type(unit.fragment_gid) is not int or unit.fragment_gid != 0
        or type(unit.fragment_mode) is not int
        or not 0 <= unit.fragment_mode <= 0o7777
        or unit.fragment_mode & 0o022
        or type(unit.dropins) is not tuple or unit.dropins
        or type(unit.enablement_links) is not tuple
        or unit.load_state != "loaded"
        or unit.unit_file_state not in _SYSTEMD_ROOT_FRAGMENT_STATES_V1
        or unit.need_daemon_reload != "no"
        or not isinstance(unit.configured_directives_hash, str)
        or _DIGEST_RE.fullmatch(unit.configured_directives_hash) is None
        or type(unit.manager_added_edges) is not tuple
        or len(unit.manager_added_edges) > MAX_SYSTEMD_ADDED_EDGES_PER_UNIT_V1
    ):
        raise _invalid("effective systemd unit")
    for link in unit.enablement_links:
        _validate_enablement_link_claim_v1(link)
    link_paths = tuple(link.path for link in unit.enablement_links)
    if (
        link_paths != tuple(sorted(
            link_paths, key=lambda item: item.encode("utf-8"),
        ))
        or len(link_paths) != len(set(link_paths))
    ):
        raise _invalid("effective enablement link order")
    _validate_systemd_manager_projection_v1(unit.manager_projection)
    for edge in unit.manager_added_edges:
        _validate_systemd_manager_added_edge_v1(edge)
    edge_keys = tuple(
        (edge.relation, edge.unit_name) for edge in unit.manager_added_edges
    )
    if (
        edge_keys != tuple(sorted(edge_keys, key=lambda item: (
            item[0].encode("utf-8"), item[1].encode("utf-8"),
        )))
        or len(edge_keys) != len(set(edge_keys))
    ):
        raise _invalid("systemd added edge order")


def _make_effective_systemd_units_snapshot_v1(
    entries: tuple[_EffectiveSystemdUnitV1, ...],
) -> _EffectiveSystemdUnitsSnapshotV1:
    """Encode and hash a complete pure effective-systemd observation."""
    if (
        type(entries) is not tuple or not entries
        or any(type(item) is not _EffectiveSystemdUnitV1 for item in entries)
    ):
        raise _invalid("effective systemd entries")
    for item in entries:
        _validate_effective_systemd_unit_v1(item)
    entry_ids = tuple(item.entry_id for item in entries)
    unit_names = tuple(item.unit_name for item in entries)
    all_link_paths = tuple(
        link.path for item in entries for link in item.enablement_links
    )
    edge_count = sum(len(item.manager_added_edges) for item in entries)
    if (
        entry_ids != tuple(sorted(
            entry_ids, key=lambda item: item.encode("utf-8"),
        ))
        or len(entry_ids) != len(set(entry_ids))
        or len(unit_names) != len(set(unit_names))
        or len(all_link_paths) != len(set(all_link_paths))
        or edge_count > MAX_SYSTEMD_ADDED_EDGES_TOTAL_V1
    ):
        raise _invalid("effective systemd coverage")
    encoded = _canonical_json({
        "schema_version": 1,
        "entries": [item.as_value() for item in entries],
    })
    return _EffectiveSystemdUnitsSnapshotV1(
        entries, encoded, _digest(EFFECTIVE_UNITS_DOMAIN_V1, encoded),
    )


def _systemd_live_path_v1(logical_path: str, live_root: Path) -> Path:
    logical = _catalog_absolute_path_v1(logical_path, "systemd live path")
    if (
        not isinstance(live_root, Path) or not live_root.is_absolute()
        or str(live_root) != live_root.as_posix()
        or Path(os.path.normpath(live_root.as_posix())) != live_root
    ):
        raise _invalid("systemd live root")
    if live_root == Path("/"):
        return Path(logical)
    return live_root.joinpath(*PurePosixPath(logical).parts[1:])


def _capture_exact_systemd_file_v1(
    logical_path: str, *, live_root: Path, uid: int, gid: int,
    maximum: int,
) -> _CapturedSystemdFileV1:
    actual = _systemd_live_path_v1(logical_path, live_root)
    captured = _capture_trusted_file_v1(
        actual, executable=False, uid=uid, gid=gid,
        chain_stop=None if live_root == Path("/") else live_root,
        maximum=maximum, require_single_link=True,
    )
    if (
        captured.resolved.requested_path != actual.as_posix()
        or captured.resolved.canonical_path != actual.as_posix()
        or any(
            component.link_target is not None
            for component in captured.resolved.components
        )
    ):
        raise _invalid("systemd exact file path")
    return _CapturedSystemdFileV1(logical_path, maximum, captured)


def _capture_systemd_enablement_link_v1(
    link: _EnablementLinkV1, *, live_root: Path, uid: int, gid: int,
) -> _CapturedSystemdLinkV1:
    _validate_enablement_link_claim_v1(link)
    actual = _systemd_live_path_v1(link.path, live_root)
    chain_stop = None if live_root == Path("/") else live_root
    parent = _resolve_trusted_path_core_v1(
        actual.parent, kind="directory", executable=False, uid=uid, gid=gid,
        chain_stop=chain_stop, require_single_link=False,
    )
    if (
        parent.requested_path != actual.parent.as_posix()
        or parent.canonical_path != actual.parent.as_posix()
        or any(item.link_target is not None for item in parent.components)
    ):
        raise _invalid("systemd enablement parent")
    try:
        before = actual.lstat()
        target = os.readlink(actual)
        target_size = len(target.encode("utf-8"))
    except FileNotFoundError as exc:
        raise _missing("systemd enablement link") from exc
    except (OSError, UnicodeError) as exc:
        raise _invalid("systemd enablement link") from exc
    if (
        not stat.S_ISLNK(before.st_mode) or before.st_uid != uid
        or before.st_gid != gid or before.st_nlink != 1
        or not target or "\0" in target
        or target_size > 4096 or target != link.target
    ):
        raise _invalid("systemd enablement link")
    _require_no_posix_access_acl_v1(actual)
    try:
        after = actual.lstat()
        repeated_target = os.readlink(actual)
    except (OSError, UnicodeError) as exc:
        raise _invalid("systemd enablement link") from exc
    repeated_parent = _resolve_trusted_path_core_v1(
        actual.parent, kind="directory", executable=False, uid=uid, gid=gid,
        chain_stop=chain_stop, require_single_link=False,
    )
    identity = _metadata_identity_v1(before)
    if (
        _metadata_identity_v1(after) != identity
        or repeated_target != target or repeated_parent != parent
    ):
        raise _invalid("systemd enablement link changed")
    return _CapturedSystemdLinkV1(
        link.path, actual.as_posix(), parent, identity, target,
    )


def _systemd_file_facts_v1(
    captured: _CapturedSystemdFileV1,
) -> tuple[int, str, int, int, int]:
    if type(captured) is not _CapturedSystemdFileV1:
        raise _invalid("systemd file facts")
    identity = captured.captured.identity
    content = captured.captured.content
    if len(identity) != 9 or identity[6] != len(content):
        raise _invalid("systemd file facts")
    return (
        len(content), captured.logical_path, identity[3], identity[4],
        stat.S_IMODE(identity[2]),
    )


def _systemd_origin_edge_v1(
    relation: str, origin: _SystemdOriginObservationV1,
) -> _SystemdManagerAddedEdgeV1:
    edge = _SystemdManagerAddedEdgeV1(
        relation, origin.unit_name, origin.origin_kind,
        origin.fragment_path, origin.source_path, origin.source_size,
        origin.source_content_hash, origin.source_uid, origin.source_gid,
        origin.source_mode, origin.size, origin.content_hash, origin.uid,
        origin.gid, origin.mode, origin.load_state, origin.unit_file_state,
    )
    _validate_systemd_manager_added_edge_v1(edge)
    return edge


def _capture_systemd_origin_v1(
    unit_name: str, *, systemctl_executable: str,
    capture_file: Callable[[str, int], _CapturedSystemdFileV1],
) -> _SystemdOriginObservationV1:
    observed = _run_systemctl_show_v1(
        systemctl_executable, unit_name, _SYSTEMD_ORIGIN_PROPERTIES_V1,
    )
    if set(observed) != set(_SYSTEMD_ORIGIN_PROPERTIES_V1) or any(
        len(observed[name]) != 1 for name in _SYSTEMD_ORIGIN_PROPERTIES_V1
    ):
        raise _invalid("systemd origin property set")
    observed_id = _systemd_single_property_v1(observed, "Id")
    observed_load = _systemd_single_property_v1(observed, "LoadState")
    observed_transient = _systemd_single_property_v1(observed, "Transient")
    if (
        observed_id != unit_name
        or observed_load not in {"loaded", "not-found"}
        or observed_transient != "no"
    ):
        # Name the unit and the three observations. Unit names and load state
        # are not payload, and a mute denial here costs a CI round trip per
        # hypothesis: the same blindness already cost two on this cell.
        raise _invalid(
            f"systemd origin identity requested={unit_name} "
            f"id={observed_id} load={observed_load} "
            f"transient={observed_transient}"
        )
    fragment_path = _systemd_single_property_v1(observed, "FragmentPath")
    source_path = _systemd_single_property_v1(observed, "SourcePath")
    unit_file_state = _systemd_single_property_v1(observed, "UnitFileState")

    if observed_load == "not-found":
        if fragment_path or source_path or unit_file_state:
            raise _invalid("systemd absent observation")
        return _SystemdOriginObservationV1(
            unit_name, "absent_unit", None, None,
            None, None, None, None, None, None, None, None, None, None,
            "not-found", None,
        )

    if unit_name in _SYSTEMD_MANAGER_VIRTUAL_UNITS_V1:
        if fragment_path or source_path or unit_file_state:
            raise _invalid("systemd manager virtual observation")
        return _SystemdOriginObservationV1(
            unit_name, "manager_virtual", None, None,
            None, None, None, None, None, None, None, None, None, None,
            "loaded", None,
        )

    if _strict_systemd_child_v1(
        fragment_path, _SYSTEMD_ROOT_GENERATOR_ROOTS_V1,
    ):
        origin_kind = "root_generator"
        if unit_file_state != "generated":
            raise _invalid("systemd origin unit file state")
    elif _strict_systemd_child_v1(
        fragment_path, _SYSTEMD_ROOT_FRAGMENT_ROOTS_V1,
    ):
        origin_kind = "root_fragment"
        if unit_file_state not in _SYSTEMD_ROOT_FRAGMENT_STATES_V1:
            raise _invalid("systemd origin unit file state")
    else:
        raise _invalid("systemd origin fragment path")
    fragment = capture_file(fragment_path, MAX_SYSTEMD_ORIGIN_BYTES_V1)
    size, logical_fragment, _fragment_uid, _fragment_gid, fragment_mode = (
        _systemd_file_facts_v1(fragment)
    )
    fragment_hash = _systemd_origin_file_hash_v1(
        logical_fragment, fragment.captured.content,
    )
    if origin_kind == "root_fragment":
        if source_path:
            raise _invalid("systemd root fragment source")
        source_facts: tuple[object, ...] = (None,) * 6
    else:
        if not _strict_systemd_child_v1(source_path, ("/etc", "/usr")):
            raise _invalid("systemd generator source path")
        source = capture_file(source_path, MAX_SYSTEMD_ORIGIN_BYTES_V1)
        (
            source_size, logical_source, _source_uid, _source_gid, source_mode,
        ) = _systemd_file_facts_v1(source)
        source_facts = (
            logical_source, source_size,
            _systemd_origin_source_hash_v1(
                logical_source, source.captured.content,
            ),
            0, 0, source_mode,
        )
    return _SystemdOriginObservationV1(
        unit_name, origin_kind, logical_fragment, source_facts[0],
        source_facts[1], source_facts[2], source_facts[3], source_facts[4],
        source_facts[5], size, fragment_hash, 0, 0,
        fragment_mode, "loaded", unit_file_state,
    )


def _capture_effective_systemd_units_core_v1(
    materials: _BoundPreflightMaterialsV1 | _CandidateCutoverMaterialsV1, *,
    systemctl_executable: str,
    live_root: Path, uid: int, gid: int,
) -> _CapturedEffectiveSystemdUnitsV1:
    """Build one complete, non-authorizing effective-systemd observation."""
    if (
        type(materials) not in {
            _BoundPreflightMaterialsV1, _CandidateCutoverMaterialsV1,
        }
        or type(uid) is not int or type(gid) is not int
        or systemctl_executable != materials.descriptor.systemctl_executable
    ):
        raise _invalid("effective systemd arguments")
    version_observed = _run_systemctl_show_v1(
        systemctl_executable, None, ("Version",),
    )
    if set(version_observed) != {"Version"} or len(
        version_observed["Version"]
    ) != 1:
        raise _invalid("manager Version")
    manager_version = version_observed["Version"][0]
    if manager_version not in SUPPORTED_SYSTEMD_VERSIONS:
        raise _invalid("unsupported manager Version")

    candidates_by_id = {
        item.entry_id: item for item in materials.candidate_units.entries
    }
    fragments_by_unit = dict(materials.unit_fragments)
    catalog_entries = tuple(
        item for item in materials.catalog.entries if item.unit_spec is not None
    )
    if (
        tuple(item.entry_id for item in catalog_entries)
        != tuple(item.entry_id for item in materials.candidate_units.entries)
        or len(fragments_by_unit) != len(materials.unit_fragments)
    ):
        raise _invalid("effective systemd candidate coverage")

    file_captures: dict[str, _CapturedSystemdFileV1] = {}
    link_captures: dict[str, _CapturedSystemdLinkV1] = {}

    def capture_file(
        logical_path: str, maximum: int,
    ) -> _CapturedSystemdFileV1:
        present = file_captures.get(logical_path)
        if present is not None:
            if present.maximum > maximum or len(
                present.captured.content
            ) > maximum:
                raise _invalid("systemd file bound disagreement")
            return present
        captured = _capture_exact_systemd_file_v1(
            logical_path, live_root=live_root, uid=uid, gid=gid,
            maximum=maximum,
        )
        file_captures[logical_path] = captured
        return captured

    unit_observations: list[tuple[
        _ServiceCatalogEntryV1, _CandidateUnitV1,
        dict[str, tuple[str, ...]], tuple[tuple[str, str], ...],
    ]] = []
    origin_names: set[str] = set()
    total_edges = 0
    for entry in catalog_entries:
        candidate = candidates_by_id.get(entry.entry_id)
        if candidate is None or entry.unit_name != candidate.unit_name:
            raise _invalid("effective systemd candidate binding")
        plan = _systemd_property_plan_v1(entry)
        observed = _run_systemctl_show_v1(
            systemctl_executable, candidate.unit_name,
            plan.requested_properties,
        )
        _validate_systemd_property_cardinality_v1(plan, observed)
        edge_pairs = _compile_systemd_added_edge_pairs_v1(entry, observed)
        total_edges += len(edge_pairs)
        if total_edges > MAX_SYSTEMD_ADDED_EDGES_TOTAL_V1:
            raise _invalid("systemd added edge total")
        origin_names.update(name for _relation, name in edge_pairs)
        unit_observations.append((entry, candidate, observed, edge_pairs))

        logical_fragment = f"{SYSTEM_UNIT_ROOT_TEXT_V1}/{candidate.unit_name}"
        signed_fragment = fragments_by_unit.get(candidate.unit_name)
        if type(signed_fragment) is not bytes:
            raise _invalid("effective systemd fragment binding")
        fragment = capture_file(logical_fragment, MAX_UNIT_FRAGMENT_BYTES_V1)
        if (
            fragment.captured.content != signed_fragment
            or _service_fragment_hash_v1(
                candidate.unit_name, fragment.captured.content,
            ) != candidate.fragment_hash
        ):
            raise _invalid("effective systemd fragment binding")
        for link in candidate.enablement_links:
            captured_link = _capture_systemd_enablement_link_v1(
                link, live_root=live_root, uid=uid, gid=gid,
            )
            if link.path in link_captures:
                raise _invalid("effective systemd link collision")
            link_captures[link.path] = captured_link

    origins = {
        unit_name: _capture_systemd_origin_v1(
            unit_name, systemctl_executable=systemctl_executable,
            capture_file=capture_file,
        )
        for unit_name in sorted(origin_names, key=lambda item: item.encode("utf-8"))
    }
    effective_entries: list[_EffectiveSystemdUnitV1] = []
    for entry, candidate, observed, edge_pairs in unit_observations:
        fragment = file_captures[
            f"{SYSTEM_UNIT_ROOT_TEXT_V1}/{candidate.unit_name}"
        ]
        _size, logical_path, _fragment_uid, _fragment_gid, fragment_mode = (
            _systemd_file_facts_v1(fragment)
        )
        if (
            _systemd_single_property_v1(observed, "FragmentPath")
            != logical_path
            or _systemd_single_property_v1(observed, "DropInPaths") != ""
            or _systemd_single_property_v1(observed, "LoadState") != "loaded"
            or _systemd_single_property_v1(
                observed, "UnitFileState",
            ) not in _SYSTEMD_ROOT_FRAGMENT_STATES_V1
            or _systemd_single_property_v1(
                observed, "NeedDaemonReload",
            ) != "no"
        ):
            raise _invalid("effective systemd base properties")
        unit_file_state = _systemd_single_property_v1(
            observed, "UnitFileState",
        )
        manager_edges = tuple(
            _systemd_origin_edge_v1(relation, origins[unit_name])
            for relation, unit_name in edge_pairs
        )
        effective_entries.append(_EffectiveSystemdUnitV1(
            entry.entry_id, candidate.unit_name, logical_path,
            candidate.fragment_hash, 0, 0,
            fragment_mode, (), candidate.enablement_links, "loaded",
            unit_file_state, "no",
            _configured_directives_hash_from_fragment_v1(
                candidate.unit_name, fragment.captured.content,
            ),
            _compile_systemd_manager_projection_v1(entry, observed),
            manager_edges,
        ))
    snapshot = _make_effective_systemd_units_snapshot_v1(
        tuple(effective_entries),
    )
    return _CapturedEffectiveSystemdUnitsV1(
        manager_version, snapshot,
        tuple(file_captures[path] for path in sorted(
            file_captures, key=lambda item: item.encode("utf-8"),
        )),
        tuple(link_captures[path] for path in sorted(
            link_captures, key=lambda item: item.encode("utf-8"),
        )),
    )


def _revalidate_captured_effective_systemd_v1(
    captured: _CapturedEffectiveSystemdUnitsV1, *, live_root: Path,
    uid: int, gid: int,
) -> None:
    if type(captured) is not _CapturedEffectiveSystemdUnitsV1:
        raise _invalid("effective systemd revalidation")
    for item in captured.files:
        repeated = _capture_exact_systemd_file_v1(
            item.logical_path, live_root=live_root, uid=uid, gid=gid,
            maximum=item.maximum,
        )
        if repeated != item:
            raise _invalid("effective systemd file changed")
    for item in captured.links:
        repeated = _capture_systemd_enablement_link_v1(
            _EnablementLinkV1(item.logical_path, item.target),
            live_root=live_root, uid=uid, gid=gid,
        )
        if repeated != item:
            raise _invalid("effective systemd link changed")


def _capture_startup_prerequisite_file_v1(
    materials: _BoundPreflightMaterialsV1, *, ownership_root: Path,
    uid: int, gid: int, chain_stop: Path | None,
) -> _CapturedTrustedFileV1:
    if (
        type(materials) is not _BoundPreflightMaterialsV1
        or not isinstance(ownership_root, Path) or not ownership_root.is_absolute()
    ):
        raise _invalid("startup prerequisite capture")
    request_id = _require_digest(
        materials.prerequisite.request_id, "startup prerequisite request",
    )
    path = (
        ownership_root / "startup-prerequisites-v1"
        / f"{request_id}.json"
    )
    captured = _capture_trusted_file_v1(
        path, executable=False, uid=uid, gid=gid, chain_stop=chain_stop,
        maximum=MAX_STARTUP_PREREQUISITE_BYTES_V1,
        require_single_link=True,
    )
    try:
        decoded = _decode_startup_prerequisite_v1(captured.content)
        evidence_digest = _startup_prerequisite_digest_v1(captured.content)
    except PreflightError as exc:
        raise _invalid("startup prerequisite capture") from exc
    if (
        captured.resolved.requested_path != path.as_posix()
        or captured.resolved.canonical_path != path.as_posix()
        or any(
            component.link_target is not None
            for component in captured.resolved.components
        )
        or stat.S_IMODE(captured.identity[2]) != 0o644
        or decoded != materials.prerequisite
        or evidence_digest
        != materials.transaction.startup_prerequisite_digest
        or materials.transaction.startup_prerequisite_id
        != materials.prerequisite.prerequisite_id
    ):
        raise _invalid("startup prerequisite capture")
    return captured


def _observe_effective_systemd_core_v1(
    administrative_tcb: _ObservedAdministrativeTcbV1, *,
    ownership_root: Path, ownership_chain_stop: Path | None,
    live_root: Path, uid: int, gid: int,
    administrative_links: tuple[Path, Path, Path, Path],
    administrative_chain_stop: Path | None,
    between_for_test: Callable[[], None] | None = None,
) -> _ObservedEffectiveSystemdV1:
    """Perform P0/S0/P1/S1/P2 without creating an operational capability."""
    if (
        type(administrative_tcb) is not _ObservedAdministrativeTcbV1
        or type(administrative_links) is not tuple
        or len(administrative_links) != 4
        or any(not isinstance(item, Path) for item in administrative_links)
        or between_for_test is not None and not callable(between_for_test)
    ):
        raise _invalid("effective systemd observation arguments")
    materials = administrative_tcb.materials
    systemctl_executable = (
        administrative_tcb.capture.executables.systemctl.resolved.canonical_path
    )
    if systemctl_executable != materials.descriptor.systemctl_executable:
        raise _invalid("effective systemd executable binding")

    prerequisite_0 = _capture_startup_prerequisite_file_v1(
        materials, ownership_root=ownership_root, uid=uid, gid=gid,
        chain_stop=ownership_chain_stop,
    )
    snapshot_0 = _capture_effective_systemd_units_core_v1(
        materials, systemctl_executable=systemctl_executable,
        live_root=live_root, uid=uid, gid=gid,
    )
    if between_for_test is not None:
        between_for_test()
    prerequisite_1 = _capture_startup_prerequisite_file_v1(
        materials, ownership_root=ownership_root, uid=uid, gid=gid,
        chain_stop=ownership_chain_stop,
    )
    snapshot_1 = _capture_effective_systemd_units_core_v1(
        materials, systemctl_executable=systemctl_executable,
        live_root=live_root, uid=uid, gid=gid,
    )
    prerequisite_2 = _capture_startup_prerequisite_file_v1(
        materials, ownership_root=ownership_root, uid=uid, gid=gid,
        chain_stop=ownership_chain_stop,
    )
    if not prerequisite_0 == prerequisite_1 == prerequisite_2:
        raise _invalid("startup prerequisite changed")
    if snapshot_0 != snapshot_1:
        raise _invalid("effective systemd A/B mismatch")
    prerequisite = materials.prerequisite
    if (
        snapshot_1.manager_version != prerequisite.systemd_manager_version
        or snapshot_1.snapshot.effective_units_hash
        != prerequisite.effective_units_hash
    ):
        # Two very different faults share this refusal: a manager that is not
        # the one the prerequisite was signed against, and a topology that
        # moved since. Saying which — and, for the topology, both digests —
        # is the difference between one round and several under a real
        # manager. Digests and a version string are not payload.
        raise _invalid(
            "effective systemd signed binding "
            + (
                f"version observed={snapshot_1.manager_version} "
                f"signed={prerequisite.systemd_manager_version}"
                if snapshot_1.manager_version
                != prerequisite.systemd_manager_version
                else
                f"units observed={snapshot_1.snapshot.effective_units_hash} "
                f"signed={prerequisite.effective_units_hash}"
            )
        )
    _revalidate_captured_effective_systemd_v1(
        snapshot_1, live_root=live_root, uid=uid, gid=gid,
    )
    _revalidate_captured_administrative_tcb_v1(
        administrative_tcb.capture, administrative_links,
        uid=uid, gid=gid, chain_stop=administrative_chain_stop,
    )
    prerequisite_final = _capture_startup_prerequisite_file_v1(
        materials, ownership_root=ownership_root, uid=uid, gid=gid,
        chain_stop=ownership_chain_stop,
    )
    if prerequisite_final != prerequisite_2:
        raise _invalid("startup prerequisite changed")
    return _ObservedEffectiveSystemdV1(
        administrative_tcb, prerequisite_2, snapshot_1,
    )


def _observe_effective_systemd_v1(
    authenticated: _AuthenticatedFixedOwnershipSnapshotV1,
    administrative_tcb: _ObservedAdministrativeTcbProductV1,
) -> _ObservedEffectiveSystemdProductV1:
    """Product observation with a final independent ownership reread."""
    require_linux_before_io_v1()
    if (
        not hasattr(os, "geteuid") or os.geteuid() != 0
        or type(authenticated) is not _AuthenticatedFixedOwnershipSnapshotV1
        or type(administrative_tcb) is not _ObservedAdministrativeTcbProductV1
        or administrative_tcb.observation.capture
        != authenticated.administrative_tcb.capture
    ):
        raise _invalid("product effective systemd observation")
    observation = administrative_tcb.observation
    selected_epoch = _require_materials_selected_by_snapshot_v1(
        authenticated.snapshot, observation.materials,
    )
    result = _observe_effective_systemd_core_v1(
        observation, ownership_root=OWNERSHIP_ROOT,
        ownership_chain_stop=None, live_root=Path("/"), uid=0, gid=0,
        administrative_links=_administrative_links_v1(),
        administrative_chain_stop=None,
    )
    repeated_ownership = _authenticate_fixed_ownership_snapshot_v1()
    repeated_epoch = _require_materials_selected_by_snapshot_v1(
        repeated_ownership.snapshot, observation.materials,
    )
    if repeated_epoch != selected_epoch:
        raise _invalid("selected ownership epoch changed")
    _revalidate_captured_effective_systemd_v1(
        result.effective_systemd, live_root=Path("/"), uid=0, gid=0,
    )
    _revalidate_captured_administrative_tcb_v1(
        observation.capture, _administrative_links_v1(),
        uid=0, gid=0, chain_stop=None,
    )
    repeated_prerequisite = _capture_startup_prerequisite_file_v1(
        observation.materials, ownership_root=OWNERSHIP_ROOT,
        uid=0, gid=0, chain_stop=None,
    )
    if repeated_prerequisite != result.prerequisite:
        raise _invalid("startup prerequisite changed")
    return _ObservedEffectiveSystemdProductV1(result)


def _observe_effective_systemd_for_test_v1(
    administrative_tcb: _ObservedAdministrativeTcbForTestV1, *,
    ownership_root: Path, live_root: Path,
    administrative_links: tuple[Path, Path, Path, Path],
    administrative_root: Path,
    between_for_test: Callable[[], None] | None = None,
) -> _ObservedEffectiveSystemdForTestV1:
    """Portable nominal seam; its result cannot enter productive dispatch."""
    if (
        type(administrative_tcb) is not _ObservedAdministrativeTcbForTestV1
        or not isinstance(ownership_root, Path)
        or not ownership_root.is_absolute()
        or not isinstance(live_root, Path) or not live_root.is_absolute()
        or not isinstance(administrative_root, Path)
        or not administrative_root.is_absolute()
    ):
        raise _invalid("test effective systemd observation")
    result = _observe_effective_systemd_core_v1(
        administrative_tcb.observation,
        ownership_root=ownership_root,
        ownership_chain_stop=ownership_root.parent,
        live_root=live_root, uid=os.getuid(), gid=os.getgid(),
        administrative_links=administrative_links,
        administrative_chain_stop=administrative_root,
        between_for_test=between_for_test,
    )
    return _ObservedEffectiveSystemdForTestV1(result)


def _decode_context_transition_v1(
    encoded: bytes,
) -> _DecodedContextTransitionV1:
    """Decode one exact content-addressed authority-context transition."""
    value = decode_canonical_json_v1(encoded, MAX_CONTEXT_TRANSITION_BYTES_V1)
    if (
        type(value) is not dict
        or set(value) != _CONTEXT_TRANSITION_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("context transition schema")
    transition_id = _require_digest(
        value.get("transition_id"), "context transition_id",
    )
    request_id = _require_digest(
        value.get("request_id"), "context request_id",
    )
    closed_build_id = _require_digest(
        value.get("closed_build_id"), "context closed_build_id",
    )
    previous_cutover_id = _nullable_digest_v1(
        value.get("previous_cutover_id"), "context previous_cutover_id",
    )
    previous_admission_context_id = _require_digest(
        value.get("previous_admission_context_id"),
        "context previous_admission_context_id",
    )
    previous_context_epoch = _require_digest(
        value.get("previous_context_epoch"), "context previous_context_epoch",
    )
    prepared_admission_context_id = _require_digest(
        value.get("prepared_admission_context_id"),
        "context prepared_admission_context_id",
    )
    prepared_context_epoch = _require_digest(
        value.get("prepared_context_epoch"), "context prepared_context_epoch",
    )
    current_inventory_hash = _require_digest(
        value.get("current_inventory_hash"), "context current_inventory_hash",
    )
    hex_fields = {}
    for field in (
        "previous_set_id", "set_id", "context_material_sha256",
        "set_json_sha256",
    ):
        item = value.get(field)
        if type(item) is not str or _HEX_SHA256_RE_V2.fullmatch(item) is None:
            raise _invalid("context " + field)
        hex_fields[field] = item
    expected_id = _digest(
        CONTEXT_TRANSITION_ID_DOMAIN_V1,
        _canonical_json({
            key: item for key, item in value.items()
            if key != "transition_id"
        }),
    )
    if transition_id != expected_id:
        raise _invalid("context transition_id binding")
    return _DecodedContextTransitionV1(
        transition_id, request_id, closed_build_id, previous_cutover_id,
        hex_fields["previous_set_id"], previous_admission_context_id,
        previous_context_epoch, hex_fields["set_id"],
        prepared_admission_context_id, prepared_context_epoch,
        hex_fields["context_material_sha256"],
        hex_fields["set_json_sha256"], current_inventory_hash,
    )


def _decode_preflight_attestation_v1(
    encoded: bytes,
) -> _DecodedPreflightAttestationV1:
    """Decode the exact durable attestation consumed by journal record 006."""
    value = decode_canonical_json_v1(
        encoded, MAX_PREFLIGHT_ATTESTATION_BYTES_V1,
    )
    if (
        type(value) is not dict
        or set(value) != _PREFLIGHT_ATTESTATION_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise _invalid("preflight attestation schema")
    digest_fields = {}
    for field in (
        "attestation_id", "request_id", "closed_build_id", "head_id",
        "required_head_frame_hash", "deployment_descriptor_id",
        "service_catalog_id", "service_coverage_hash",
        "candidate_units_hash", "administrative_bundle_hash",
        "python_binary_hash", "openssl_binary_hash", "openssl_tcb_hash",
        "systemctl_binary_hash", "systemd_analyze_binary_hash",
        "effective_units_hash",
    ):
        digest_fields[field] = _require_digest(
            value.get(field), "preflight attestation " + field,
        )
    release_sequence = _positive_release_sequence_v1(
        value.get("release_sequence"),
    )
    raw_entry_ids = value.get("checked_entry_ids")
    if type(raw_entry_ids) is not list or not raw_entry_ids:
        raise _invalid("preflight attestation coverage")
    checked_entry_ids = tuple(
        validate_entry_id_v1(item) for item in raw_entry_ids
    )
    if (
        len(checked_entry_ids) != len(set(checked_entry_ids))
        or checked_entry_ids != tuple(sorted(
            checked_entry_ids, key=lambda item: item.encode("utf-8"),
        ))
    ):
        raise _invalid("preflight attestation coverage")
    decoded = _DecodedPreflightAttestationV1(
        digest_fields["attestation_id"], digest_fields["request_id"],
        digest_fields["closed_build_id"], release_sequence,
        digest_fields["head_id"], digest_fields["required_head_frame_hash"],
        digest_fields["deployment_descriptor_id"],
        digest_fields["service_catalog_id"],
        digest_fields["service_coverage_hash"],
        digest_fields["candidate_units_hash"],
        digest_fields["administrative_bundle_hash"],
        digest_fields["python_binary_hash"],
        digest_fields["openssl_binary_hash"],
        digest_fields["openssl_tcb_hash"],
        digest_fields["systemctl_binary_hash"],
        digest_fields["systemd_analyze_binary_hash"],
        digest_fields["effective_units_hash"], checked_entry_ids,
    )
    if (
        decoded.as_value() != value
        or decoded.attestation_id != _deployment_document_id_v1(
            PREFLIGHT_ATTESTATION_DOMAIN_V1, value, "attestation_id",
        )
    ):
        raise _invalid("preflight attestation binding")
    return decoded


def _decode_preflight_attestation_record_v1(
    encoded: bytes,
) -> tuple[_DecodedPreflightAttestationV1, str]:
    """Decode once and return the exact digest carried by record 006."""
    decoded = _decode_preflight_attestation_v1(encoded)
    return decoded, _digest(PREFLIGHT_ATTESTATION_RECORD_DOMAIN_V1, encoded)


def _preflight_attestation_record_hash_v1(encoded: bytes) -> str:
    """Return the domain-separated digest carried by journal record 006."""
    return _decode_preflight_attestation_record_v1(encoded)[1]


def _preflight_attestation_bytes_v1(
    selected: _SelectedOwnershipEpochV1,
    observation: _ObservedEffectiveSystemdV1,
) -> bytes:
    """Encode the complete, exact check-all result without publishing it."""
    if (
        type(selected) is not _SelectedOwnershipEpochV1
        or type(observation) is not _ObservedEffectiveSystemdV1
    ):
        raise _invalid("preflight attestation arguments")
    materials = observation.administrative_tcb.materials
    if (
        materials.distribution != selected.build
        or materials.transaction != selected.transaction.prefix.records[-1]
        or materials.prerequisite.predecessor_id
        != selected.predecessor.predecessor_id
    ):
        raise _invalid("preflight attestation ownership")
    latest = selected.transaction.prefix.records[-1]
    prerequisite = materials.prerequisite
    checked_entry_ids = tuple(sorted(
        (
            entry.entry_id for entry in materials.catalog.entries
            if entry.requires_preflight or entry.unit_spec is not None
        ),
        key=lambda item: item.encode("utf-8"),
    ))
    if not checked_entry_ids or len(checked_entry_ids) != len(set(checked_entry_ids)):
        raise _invalid("preflight attestation coverage")
    value: dict[str, object] = {
        "schema_version": 1,
        "attestation_id": None,
        "request_id": latest.request_id,
        "closed_build_id": selected.required_head.closed_build_id,
        "release_sequence": selected.required_head.release_sequence,
        "head_id": selected.required_head.head_id,
        "required_head_frame_hash": latest.required_head_frame_hash,
        "deployment_descriptor_id": materials.descriptor.descriptor_id,
        "service_catalog_id": materials.catalog.catalog_id,
        "service_coverage_hash": materials.catalog.service_coverage_hash,
        "candidate_units_hash": materials.candidate_units.candidate_units_hash,
        "administrative_bundle_hash": materials.administrative_bundle_hash,
        "python_binary_hash": prerequisite.python_binary_hash,
        "openssl_binary_hash": prerequisite.openssl_binary_hash,
        "openssl_tcb_hash": prerequisite.openssl_tcb_hash,
        "systemctl_binary_hash": prerequisite.systemctl_binary_hash,
        "systemd_analyze_binary_hash": prerequisite.systemd_analyze_binary_hash,
        "effective_units_hash": (
            observation.effective_systemd.snapshot.effective_units_hash
        ),
        "checked_entry_ids": list(checked_entry_ids),
    }
    value["attestation_id"] = _deployment_document_id_v1(
        PREFLIGHT_ATTESTATION_DOMAIN_V1, value, "attestation_id",
    )
    encoded = _canonical_json(value)
    if len(encoded) > MAX_PREFLIGHT_ATTESTATION_BYTES_V1:
        raise _invalid("preflight attestation size")
    return encoded


def _write_all_exact_v1(descriptor: int, content: bytes) -> None:
    if type(descriptor) is not int or descriptor < 0 or type(content) is not bytes:
        raise _invalid("preflight attestation write")
    offset = 0
    while offset < len(content):
        try:
            written = os.write(descriptor, content[offset:])
        except OSError as exc:
            raise _recovery("preflight attestation write") from exc
        if written <= 0:
            raise _recovery("preflight attestation write")
        offset += written


def _publish_preflight_attestation_core_v1(
    encoded: bytes, request_id: str, *, root: Path,
    uid: int, gid: int, chain_stop: Path | None,
) -> None:
    """Publish one attestation by no-replace link under a locked directory."""
    if (
        type(encoded) is not bytes
        or len(encoded) > MAX_PREFLIGHT_ATTESTATION_BYTES_V1
        or _require_digest(request_id, "preflight request") != request_id
        or not isinstance(root, Path) or not root.is_absolute()
    ):
        raise _invalid("preflight attestation publication")
    decoded = _decode_preflight_attestation_v1(encoded)
    if decoded.request_id != request_id:
        raise _invalid("preflight attestation publication")
    _require_safe_directory_chain_v1(root, uid=uid, gid=gid, stop=chain_stop)
    try:
        before = root.lstat()
        if (
            not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode)
            or before.st_uid != uid or before.st_gid != gid
            or stat.S_IMODE(before.st_mode) != 0o755
        ):
            raise _invalid("preflight attestation directory")
        directory = os.open(
            root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except PreflightError:
        raise
    except OSError as exc:
        raise _missing("preflight attestation directory") from exc
    temporary_created = False
    basename = request_id + ".json"
    temporary = "." + request_id.removeprefix("sha256:") + ".tmp"
    try:
        opened = os.fstat(directory)
        if _metadata_identity_v1(before) != _metadata_identity_v1(opened):
            raise _recovery("preflight attestation directory replaced")
        fcntl.flock(directory, fcntl.LOCK_EX)
        names = tuple(sorted(os.listdir(directory), key=lambda item: item.encode("utf-8")))
        if temporary in names:
            raise _recovery("preflight attestation partial state")
        if basename in names:
            try:
                existing = _read_bounded_regular_v1(
                    root / basename, MAX_PREFLIGHT_ATTESTATION_BYTES_V1,
                    uid=uid, gid=gid, mode=0o644, chain_stop=chain_stop,
                )
            except PreflightError as exc:
                raise _recovery("preflight attestation existing state") from exc
            if existing != encoded:
                raise _recovery("preflight attestation conflict")
            return
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            output = os.open(temporary, flags, 0o600, dir_fd=directory)
            temporary_created = True
        except OSError as exc:
            raise _recovery("preflight attestation staging") from exc
        try:
            os.fchown(output, uid, gid)
            os.fchmod(output, 0o644)
            _write_all_exact_v1(output, encoded)
            os.fsync(output)
        except PreflightError:
            raise
        except OSError as exc:
            raise _recovery("preflight attestation staging") from exc
        finally:
            os.close(output)
        try:
            os.link(
                temporary, basename, src_dir_fd=directory,
                dst_dir_fd=directory, follow_symlinks=False,
            )
            os.fsync(directory)
            os.unlink(temporary, dir_fd=directory)
            temporary_created = False
            os.fsync(directory)
        except FileExistsError:
            raise _recovery("preflight attestation conflict")
        except OSError as exc:
            raise _recovery("preflight attestation publication") from exc
        observed = _read_bounded_regular_v1(
            root / basename, MAX_PREFLIGHT_ATTESTATION_BYTES_V1,
            uid=uid, gid=gid, mode=0o644, chain_stop=chain_stop,
        )
        if observed != encoded:
            raise _recovery("preflight attestation reread")
        after = root.lstat()
        if (
            before.st_dev, before.st_ino, before.st_mode,
            before.st_uid, before.st_gid, before.st_nlink,
        ) != (
            after.st_dev, after.st_ino, after.st_mode,
            after.st_uid, after.st_gid, after.st_nlink,
        ):
            raise _recovery("preflight attestation directory changed")
    finally:
        # A failed durable transition is evidence for recovery; never erase it.
        if temporary_created:
            pass
        try:
            fcntl.flock(directory, fcntl.LOCK_UN)
        finally:
            os.close(directory)


def _read_preflight_attestation_core_v1(
    request_id: str, *, root: Path, uid: int, gid: int,
    chain_stop: Path | None,
) -> bytes:
    """Reread one exact published attestation through trusted path checks."""
    if (
        _require_digest(request_id, "preflight request") != request_id
        or not isinstance(root, Path) or not root.is_absolute()
    ):
        raise _invalid("preflight attestation reread")
    try:
        encoded = _read_bounded_regular_v1(
            root / (request_id + ".json"),
            MAX_PREFLIGHT_ATTESTATION_BYTES_V1,
            uid=uid, gid=gid, mode=0o644, chain_stop=chain_stop,
        )
        decoded = _decode_preflight_attestation_v1(encoded)
    except PreflightError as exc:
        if exc.code == CODE_RECOVERY:
            raise
        raise _recovery("preflight attestation durable state") from exc
    if decoded.request_id != request_id:
        raise _recovery("preflight attestation request binding")
    return encoded


def _read_preflight_attestation_v1(request_id: str) -> bytes:
    """Product reread from the single fixed root-owned attestation store."""
    return _read_preflight_attestation_core_v1(
        request_id, root=PREFLIGHT_ATTESTATION_ROOT_V1,
        uid=0, gid=0, chain_stop=None,
    )


def _read_preflight_attestation_for_test_v1(
    request_id: str, root: Path,
) -> bytes:
    """Portable nominal reread; it cannot select the productive root."""
    root = Path(root)
    return _read_preflight_attestation_core_v1(
        request_id, root=root, uid=os.getuid(), gid=os.getgid(),
        chain_stop=root.parent,
    )


def _publish_preflight_attestation_v1(
    operational: _OperationalPreflightV1,
) -> bytes:
    if type(operational) is not _OperationalPreflightV1:
        raise _invalid("product preflight attestation")
    encoded = _preflight_attestation_bytes_v1(
        operational.selected, operational.observation.observation,
    )
    _publish_preflight_attestation_core_v1(
        encoded, operational.selected.transaction.prefix.records[-1].request_id,
        root=PREFLIGHT_ATTESTATION_ROOT_V1, uid=0, gid=0, chain_stop=None,
    )
    observed = _read_preflight_attestation_core_v1(
        operational.selected.transaction.prefix.records[-1].request_id,
        root=PREFLIGHT_ATTESTATION_ROOT_V1, uid=0, gid=0, chain_stop=None,
    )
    if observed != encoded:
        raise _recovery("preflight attestation publication reread")
    return observed


def _publish_preflight_attestation_for_test_v1(
    operational: _OperationalPreflightForTestV1, root: Path,
) -> bytes:
    if type(operational) is not _OperationalPreflightForTestV1:
        raise _invalid("test preflight attestation")
    encoded = _preflight_attestation_bytes_v1(
        operational.selected, operational.observation.observation,
    )
    _publish_preflight_attestation_core_v1(
        encoded, operational.selected.transaction.prefix.records[-1].request_id,
        root=root, uid=os.getuid(), gid=os.getgid(), chain_stop=root.parent,
    )
    observed = _read_preflight_attestation_core_v1(
        operational.selected.transaction.prefix.records[-1].request_id,
        root=root, uid=os.getuid(), gid=os.getgid(), chain_stop=root.parent,
    )
    if observed != encoded:
        raise _recovery("preflight attestation publication reread")
    return observed


def _attest_operational_preflight_v1() -> _OperationalPreflightV1:
    """Compose every fixed-root proof; callers cannot inject any authority."""
    authenticated = _authenticate_fixed_ownership_snapshot_v1()
    selected, materials = _load_bound_preflight_materials_v1(authenticated)
    administrative = _bind_administrative_tcb_v1(authenticated, materials)
    observation = _observe_effective_systemd_v1(
        authenticated, administrative,
    )
    final_selected, final_materials = _load_bound_preflight_materials_v1(
        authenticated,
    )
    if (
        observation.observation.administrative_tcb.materials != materials
        or final_selected != selected or final_materials != materials
    ):
        raise _invalid("product operational preflight")
    return _OperationalPreflightV1(authenticated, selected, observation)


def _require_preflight_entry_v1(
    operational: _OperationalPreflightV1, entry_id: str,
) -> _ServiceCatalogEntryV1:
    if type(operational) is not _OperationalPreflightV1:
        raise _invalid("product preflight entry")
    identifier = validate_entry_id_v1(entry_id)
    entries = tuple(
        item for item in operational.observation.observation.administrative_tcb
        .materials.catalog.entries
        if item.entry_id == identifier
    )
    if len(entries) != 1 or not entries[0].requires_preflight:
        raise _invalid("product preflight entry")
    return entries[0]


def _trusted_python_path_v1(
    installation_root: str, working_directory: str,
) -> tuple[str, ...]:
    root = PurePosixPath(validate_absolute_path_v1(installation_root))
    working = PurePosixPath(validate_absolute_path_v1(working_directory))
    try:
        working.relative_to(root)
    except ValueError as exc:
        raise _invalid("launch Python root") from exc
    retained: list[str] = [working.as_posix()]
    for raw in sys.path:
        if not isinstance(raw, str) or not raw or not raw.startswith("/"):
            continue
        try:
            candidate = Path(validate_absolute_path_v1(raw))
        except PreflightError:
            continue
        if candidate.as_posix() == working.as_posix():
            continue
        try:
            candidate.relative_to(Path(root.as_posix()))
        except ValueError:
            pass
        else:
            continue
        try:
            resolved = _resolve_trusted_path_core_v1(
                candidate, kind="directory", executable=False,
                uid=0, gid=0, chain_stop=None, require_single_link=False,
            )
        except PreflightError:
            continue
        canonical = resolved.canonical_path
        if canonical not in retained:
            retained.append(canonical)
    if len(retained) < 2:
        raise _invalid("launch standard library path")
    return tuple(retained)


def _launch_dynamic_environment_v1(
    entry: _ServiceCatalogEntryV1,
) -> tuple[tuple[str, str], ...]:
    directives = _service_directive_index_v1(entry.unit_spec)
    service_type = directives.get(("Service", "Type"))
    notify = service_type is not None and service_type.values == ("notify",)
    watchdog = ("Service", "WatchdogSec") in directives
    dynamic: dict[str, str] = {}
    if notify:
        socket = os.environ.get("NOTIFY_SOCKET")
        if (
            not isinstance(socket, str) or not socket
            or "\0" in socket or len(socket.encode("utf-8")) > 4096
            or not (socket.startswith("/") or socket.startswith("@"))
        ):
            raise _invalid("launch notify socket")
        dynamic["NOTIFY_SOCKET"] = socket
    if watchdog:
        usec = os.environ.get("WATCHDOG_USEC")
        pid = os.environ.get("WATCHDOG_PID")
        if (
            not isinstance(usec, str) or _INTEGER_RE.fullmatch(usec) is None
            or usec == "0" or not isinstance(pid, str)
            or _INTEGER_RE.fullmatch(pid) is None
            or int(pid) != os.getpid()
        ):
            raise _invalid("launch watchdog environment")
        dynamic["WATCHDOG_USEC"] = usec
        dynamic["WATCHDOG_PID"] = pid
    return tuple(sorted(dynamic.items(), key=lambda item: item[0].encode("ascii")))


def _make_launch_plan_v1(
    operational: _OperationalPreflightV1,
    entry: _ServiceCatalogEntryV1,
) -> _LaunchPlanV1:
    if (
        type(operational) is not _OperationalPreflightV1
        or type(entry) is not _ServiceCatalogEntryV1
        or entry.class_name != "gated_service"
        or entry.execution_kind not in {"python_module", "native_executable"}
        or entry.target_executable is None
        or entry.target_working_directory is None
    ):
        raise _invalid("launch plan")
    materials = operational.observation.observation.administrative_tcb.materials
    capture = operational.observation.observation.administrative_tcb.capture
    descriptor = materials.descriptor
    directives = _service_directive_index_v1(entry.unit_spec)
    user = directives.get(("Service", "User"))
    group = directives.get(("Service", "Group"))
    supplementary = directives.get(("Service", "SupplementaryGroups"))
    observed_groups = () if supplementary is None else tuple(
        int(item) for item in supplementary.values[0].split(" ")
    )
    try:
        running_executable = validate_absolute_path_v1(
            os.readlink("/proc/self/exe"),
        )
        captured_python = capture.executables.python.resolved.canonical_path
    except (AttributeError, OSError) as exc:
        raise _invalid("launch Python identity") from exc
    if (
        user is None or user.values != (descriptor.service_user,)
        or group is None or group.values != (str(descriptor.service_gid),)
        or observed_groups != descriptor.service_supplementary_gids
        or entry.target_executable != (
            descriptor.python_executable
            if entry.execution_kind == "python_module"
            else entry.target_executable
        )
        or running_executable != descriptor.python_executable
        or captured_python != descriptor.python_executable
    ):
        raise _invalid("launch identity binding")
    environment = {
        "HOME": descriptor.service_home,
        "LOGNAME": descriptor.service_user,
        "SHELL": descriptor.service_shell,
        "USER": descriptor.service_user,
    }
    for item in entry.target_environment:
        if item.name in environment:
            raise _invalid("launch environment collision")
        environment[item.name] = item.value
    for name, value in _launch_dynamic_environment_v1(entry):
        if name in environment:
            raise _invalid("launch environment collision")
        environment[name] = value
    return _LaunchPlanV1(
        entry, descriptor.installation_root, descriptor.service_user,
        descriptor.service_uid, descriptor.service_gid,
        descriptor.service_supplementary_gids, descriptor.service_home,
        descriptor.service_shell, entry.python_module, entry.target_args,
        entry.target_working_directory,
        tuple(sorted(environment.items(), key=lambda item: item[0].encode("ascii"))),
        _trusted_python_path_v1(
            descriptor.installation_root, entry.target_working_directory,
        ) if entry.execution_kind == "python_module" else (),
        0o027,
    )


def _prctl_v1(option: int, argument: int = 0) -> None:
    if type(option) is not int or type(argument) is not int:
        raise _invalid("launch prctl")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = (
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_ulong, ctypes.c_ulong,
    )
    prctl.restype = ctypes.c_int
    if prctl(option, argument, 0, 0, 0) != 0:
        raise _invalid("launch prctl")


def _read_proc_status_v1() -> dict[str, str]:
    try:
        encoded = Path("/proc/self/status").read_bytes()
    except OSError as exc:
        raise _invalid("launch process status") from exc
    if (
        not encoded or len(encoded) > MAX_PROC_STATUS_BYTES_V1
        or b"\0" in encoded or b"\r" in encoded
    ):
        raise _invalid("launch process status")
    try:
        lines = encoded.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise _invalid("launch process status") from exc
    result: dict[str, str] = {}
    for line in lines:
        name, separator, value = line.partition(":")
        if separator and name in {
            "Uid", "Gid", "Groups", "NoNewPrivs",
            "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb",
        }:
            if name in result:
                raise _invalid("launch process status")
            result[name] = value.strip()
    return result


def _drop_service_privileges_v1(plan: _LaunchPlanV1) -> None:
    if type(plan) is not _LaunchPlanV1 or plan.entry.class_name != "gated_service":
        raise _invalid("launch privilege plan")
    try:
        os.setgroups(list(plan.service_supplementary_gids))
        os.setgid(plan.service_gid)
        for capability in _LAUNCHER_BOUNDING_CAPABILITIES_V1:
            _prctl_v1(_PR_CAPBSET_DROP_V1, capability)
        _prctl_v1(_PR_CAP_AMBIENT_V1, _PR_CAP_AMBIENT_CLEAR_ALL_V1)
        os.setuid(plan.service_uid)
        _prctl_v1(_PR_SET_NO_NEW_PRIVS_V1, 1)
        os.umask(plan.umask)
        os.chdir(plan.target_working_directory)
        os.environ.clear()
        os.environ.update(dict(plan.environment))
    except PreflightError:
        raise
    except (OSError, ValueError) as exc:
        raise _invalid("launch privilege transition") from exc
    status = _read_proc_status_v1()
    expected_uid = "\t".join((str(plan.service_uid),) * 4)
    expected_gid = "\t".join((str(plan.service_gid),) * 4)
    observed_groups = tuple(int(item) for item in status.get("Groups", "").split())
    if (
        status.get("Uid") != expected_uid or status.get("Gid") != expected_gid
        or observed_groups != plan.service_supplementary_gids
        or status.get("NoNewPrivs") != "1"
        or any(status.get(name) != "0000000000000000" for name in (
            "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb",
        ))
        or os.getuid() != plan.service_uid or os.geteuid() != plan.service_uid
        or os.getgid() != plan.service_gid or os.getegid() != plan.service_gid
        or tuple(os.getgroups()) != plan.service_supplementary_gids
    ):
        raise _invalid("launch privilege postcondition")


def _close_launch_descriptors_v1(keep: int | None) -> None:
    try:
        raw_names = os.listdir("/proc/self/fd")
    except OSError as exc:
        raise _invalid("launch descriptor inventory") from exc
    descriptors: list[int] = []
    for raw in raw_names:
        if not raw.isascii() or not raw.isdigit():
            raise _invalid("launch descriptor inventory")
        descriptor = int(raw)
        if descriptor > 2 and descriptor != keep:
            descriptors.append(descriptor)
    for descriptor in sorted(set(descriptors)):
        try:
            os.close(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise _invalid("launch descriptor close") from exc
    try:
        remaining_items: list[int] = []
        for raw in os.listdir("/proc/self/fd"):
            if not raw.isascii() or not raw.isdigit():
                raise _invalid("launch descriptor inventory")
            descriptor = int(raw)
            if descriptor <= 2:
                continue
            try:
                os.fstat(descriptor)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    continue
                raise
            remaining_items.append(descriptor)
        remaining = tuple(sorted(remaining_items))
    except OSError as exc:
        raise _invalid("launch descriptor inventory") from exc
    expected = () if keep is None else (keep,)
    if remaining != expected:
        raise _invalid("launch descriptor postcondition")


def _launch_python_target_v1(plan: _LaunchPlanV1) -> None:
    if (
        type(plan) is not _LaunchPlanV1
        or plan.entry.execution_kind != "python_module"
        or plan.python_module is None or not plan.python_path
    ):
        raise _invalid("launch Python target")
    sys.path[:] = list(plan.python_path)
    sys.argv[:] = [plan.python_module, *plan.target_args]
    runpy.run_module(plan.python_module, run_name="__main__", alter_sys=False)


def _launch_gated_service_v1(
    plan: _LaunchPlanV1, lease: _LaunchGateLeaseV1,
) -> None:
    if type(plan) is not _LaunchPlanV1 or type(lease) is not _LaunchGateLeaseV1:
        raise _invalid("launch gated service")
    _drop_service_privileges_v1(plan)
    executable = plan.entry.target_executable
    assert executable is not None
    if plan.entry.execution_kind == "native_executable":
        if lease.descriptor < 3:
            raise _invalid("launch startup gate")
        os.set_inheritable(lease.descriptor, False)
        _close_launch_descriptors_v1(lease.descriptor)
        try:
            os.execve(
                executable, [executable, *plan.target_args],
                dict(plan.environment),
            )
        except OSError as exc:
            raise _invalid("launch native target") from exc
        raise _recovery("launch native target returned")
    lease.close()
    _close_launch_descriptors_v1(None)
    _launch_python_target_v1(plan)


def _acquire_startup_gate_shared_v1() -> int:
    """Acquire the fixed root-owned startup gate without creating it."""
    _require_safe_directory_chain_v1(
        STARTUP_GATE_PATH_V1.parent, uid=0, gid=0, stop=None,
    )
    try:
        before = STARTUP_GATE_PATH_V1.lstat()
        if (
            not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
            or before.st_uid != 0 or before.st_gid != 0
            or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
        ):
            raise _invalid("startup gate")
        # Read-only, because a SHARED holder never writes: `flock` places its
        # advisory lock on the descriptor whatever the access mode, unlike a
        # POSIX record lock. Asking for write access would make the gate
        # unopenable exactly where it must work — a `ProtectSystem=strict`
        # unit sees the whole hierarchy read-only, so `O_RDWR` returns EROFS
        # and the launch refuses.
        #
        # NOTE, so the next reader is not misled: no exclusive holder of this
        # gate exists anywhere in the product yet. Today the shared lock
        # serializes nothing; the writer that will hold it during a birth
        # transition is still to be written. This open mode is correct for a
        # reader either way, but it is not, on its own, a protection.
        descriptor = os.open(
            STARTUP_GATE_PATH_V1,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except PreflightError:
        raise
    except FileNotFoundError as exc:
        raise _missing("startup gate") from exc
    except OSError as exc:
        raise _invalid("startup gate") from exc
    try:
        opened = os.fstat(descriptor)
        if _metadata_identity_v1(before) != _metadata_identity_v1(opened):
            raise _invalid("startup gate replaced")
        deadline = time.monotonic() + 30.0
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise _recovery("startup gate timeout")
                time.sleep(0.01)
        after = STARTUP_GATE_PATH_V1.lstat()
        if _metadata_identity_v1(before) != _metadata_identity_v1(after):
            raise _invalid("startup gate changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _release_startup_gate_v1(descriptor: int) -> None:
    if type(descriptor) is not int or descriptor < 0:
        raise _invalid("startup gate descriptor")
    failed = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        failed = True
    try:
        os.close(descriptor)
    except OSError:
        failed = True
    if failed:
        raise _recovery("startup gate release")


if __name__ == "__main__":
    raise SystemExit(main())
