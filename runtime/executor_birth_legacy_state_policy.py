"""Pure legacy-adoption grammar; store owners authenticate publication bytes."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import PurePosixPath
import re

from executor_birth_canonical import encode_canonical_ascii_v1
from executor_birth_crypto_framing import framed_sha256_v1, is_framed_sha256_v1
from executor_birth_host_provisioning_evidence import (
    host_provisioning_policy_sha256_v1,
)
from executor_birth_legacy_state_request import (
    LegacyStateError,
    LegacyStateRequestV1,
    is_legacy_state_utf8_path_v1,
    require_canonical_legacy_state_request_v1,
)


LEGACY_STATE_PROTOCOL_V1 = "metnos.executor-birth.legacy-state/v1"
MAX_LEGACY_STATE_ENTRIES_V1 = 4096
MAX_LEGACY_STATE_DEPTH_V1 = 32
MAX_LEGACY_STATE_FILE_BYTES_V1 = 8 * 1024 * 1024
MAX_LEGACY_STATE_TOTAL_BYTES_V1 = 64 * 1024 * 1024
LEGACY_STATE_RESERVED_TOP_LEVEL_V1 = (
    PurePosixPath("contract-authoring"),
    PurePosixPath("contract-publications"),
    PurePosixPath("contract-publications-shadow"),
    PurePosixPath("contract-publications.ACTIVE"),
    PurePosixPath("contract-publications.audit.jsonl"),
    PurePosixPath(".contract-publications-v1.catalog-admission.lock"),
    PurePosixPath(".contract-publications-shadow-v1.catalog-admission.lock"),
    PurePosixPath(".contract-publications.audit.jsonl.unique.lock"),
)
_AUTHORING = "contract-authoring"
_PUBLICATION_DIRS = frozenset({
    "contract-publications", "contract-publications-shadow",
})
_ORIGINS = frozenset({"core", "builtin", "builtin_skill", "retired"})
_CONTROL_RE = re.compile(r"\.(.+)\.birth-control-([0-9a-f]{64})\Z")
_TRANSACTION_RE = re.compile(r"\.birth-(?:stage|backup)-[0-9a-f]{64}\Z")
_CONTROL_LEAF_SIZES = {"authoring.lock": 1, "version.json": None}
_SCAFFOLD_ROOTS = (_AUTHORING, *tuple(sorted(_PUBLICATION_DIRS)))
LEGACY_STATE_FSM_V1 = (
    ("PLANNED", "INVENTORY"),
    ("INVENTORIED", "ADOPT_AUTHORING"),
    ("AUTHORING_ADOPTED", "CONVERGE_CONTRACTS_AND_VERIFY"),
    ("LEGACY_STATE_READY", None),
)
_POLICY_DOMAIN = b"metnos.executor-birth.legacy-state-policy/v1\0"
_OBSERVATION_DOMAIN = b"metnos.executor-birth.legacy-state-observation/v1\0"
_FILE_DOMAIN = b"metnos.executor-birth.legacy-state-file/v1\0"
class LegacyStateDispositionV1(str, Enum):
    fresh = "fresh"
    exact_service = "exact-service"
    root_adoption_required = "root-adoption-required"
    invalid = "invalid"


class LegacyNodeKindV1(str, Enum):
    directory = "directory"
    regular_file = "regular-file"
    other = "other"


def _invalid(detail: str) -> LegacyStateError:
    return LegacyStateError(detail)


def _digest(domain: bytes, value: object) -> str:
    return framed_sha256_v1(domain, encode_canonical_ascii_v1(value))


def legacy_state_file_sha256_v1(payload: bytes) -> str:
    if type(payload) is not bytes or len(payload) > MAX_LEGACY_STATE_FILE_BYTES_V1:
        raise _invalid("file_payload")
    return framed_sha256_v1(_FILE_DOMAIN, payload)


@dataclass(frozen=True, slots=True)
class LegacyPathObservationV1:
    relative_path: PurePosixPath
    node_kind: LegacyNodeKindV1
    uid: int
    gid: int
    mode: int
    nlink: int
    size: int | None
    content_sha256: str | None
    has_access_acl: bool = False
    has_default_acl: bool = False
    device: int = 0
    inode: int = 0

    def __post_init__(self) -> None:
        path = self.relative_path
        regular = self.node_kind is LegacyNodeKindV1.regular_file
        valid_size = (
            type(self.size) is int
            and 0 <= self.size <= MAX_LEGACY_STATE_FILE_BYTES_V1
        )
        if (
            type(self.node_kind) is not LegacyNodeKindV1
            or type(path) is not PurePosixPath or path.is_absolute() or not path.parts
            or path.as_posix() != str(path) or ".." in path.parts
            or not is_legacy_state_utf8_path_v1(path)
            or type(self.uid) is not int or self.uid < 0
            or type(self.gid) is not int or self.gid < 0
            or type(self.mode) is not int or not 0 <= self.mode <= 0o7777
            or type(self.nlink) is not int or self.nlink < 1
            or type(self.has_access_acl) is not bool
            or type(self.has_default_acl) is not bool
            or type(self.device) is not int or self.device < 0
            or type(self.inode) is not int or self.inode < 0
            or (regular and not valid_size)
            or (not regular and self.size is not None)
            or (regular and not is_framed_sha256_v1(self.content_sha256))
            or (not regular and self.content_sha256 is not None)
        ):
            raise _invalid("observation_entry")


@dataclass(frozen=True, slots=True)
class LegacyStateObservationV1:
    entries: tuple[LegacyPathObservationV1, ...]

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or len(self.entries) > MAX_LEGACY_STATE_ENTRIES_V1:
            raise _invalid("observation_entries")
        if any(type(item) is not LegacyPathObservationV1 for item in self.entries):
            raise _invalid("observation_entries")
        paths = tuple(item.relative_path.as_posix() for item in self.entries)
        total = sum(item.size or 0 for item in self.entries)
        if (
            len(set(paths)) != len(paths)
            or paths != tuple(sorted(paths, key=str.encode))
            or any(len(item.relative_path.parts) > MAX_LEGACY_STATE_DEPTH_V1 for item in self.entries)
            or total > MAX_LEGACY_STATE_TOTAL_BYTES_V1
        ):
            raise _invalid("observation_order")

    @property
    def observation_sha256(self) -> str:
        return _digest(
            _OBSERVATION_DOMAIN,
            [_entry_value(item) for item in self.entries],
        )


def _entry_value(entry: LegacyPathObservationV1) -> dict[str, object]:
    return {
        "content_sha256": entry.content_sha256, "gid": entry.gid,
        "has_access_acl": entry.has_access_acl,
        "has_default_acl": entry.has_default_acl, "mode": entry.mode,
        "nlink": entry.nlink, "node_kind": entry.node_kind.value,
        "relative_path": entry.relative_path.as_posix(), "size": entry.size,
        "uid": entry.uid, "device": entry.device, "inode": entry.inode,
    }


def _closed_parents(entries: dict[str, LegacyPathObservationV1]) -> bool:
    reserved = {item.name for item in LEGACY_STATE_RESERVED_TOP_LEVEL_V1}
    for name, entry in entries.items():
        if entry.relative_path.parts[0] not in reserved:
            return False
        for parent in PurePosixPath(name).parents:
            if parent == PurePosixPath("."):
                break
            found = entries.get(parent.as_posix())
            if found is None or found.node_kind is not LegacyNodeKindV1.directory:
                return False
    return True


def _metadata_valid(entry: LegacyPathObservationV1) -> bool:
    mode = 0o700 if entry.node_kind is LegacyNodeKindV1.directory else 0o600
    return (
        entry.node_kind is not LegacyNodeKindV1.other and entry.mode == mode
        and not entry.has_access_acl and not entry.has_default_acl
        and (entry.node_kind is LegacyNodeKindV1.directory or entry.nlink == 1)
    )


def _control_path_valid(
    parts: tuple[str, ...], dot_index: int, entry: LegacyPathObservationV1,
) -> bool:
    match = _CONTROL_RE.fullmatch(parts[dot_index])
    if match is None or dot_index < 3 or len(parts) not in {dot_index + 1, dot_index + 2}:
        return False
    manifest = "/".join((*parts[3:dot_index], match.group(1), "manifest.toml"))
    contract_id = f"{parts[2]}:{manifest}"
    if hashlib.sha256(contract_id.encode("utf-8")).hexdigest() != match.group(2):
        return False
    if len(parts) == dot_index + 1:
        return entry.node_kind is LegacyNodeKindV1.directory
    if parts[-1] not in _CONTROL_LEAF_SIZES:
        return False
    if entry.node_kind is not LegacyNodeKindV1.regular_file:
        return False
    required_size = _CONTROL_LEAF_SIZES[parts[-1]]
    return required_size is None or entry.size == required_size


def _authoring_shape_valid(entries: dict[str, LegacyPathObservationV1]) -> bool:
    relevant = {
        name: item for name, item in entries.items()
        if name == _AUTHORING or name.startswith(_AUTHORING + "/")
    }
    if not relevant:
        return True
    root = relevant.get(_AUTHORING)
    if root is None or root.node_kind is not LegacyNodeKindV1.directory:
        return False
    for name, entry in relevant.items():
        parts = PurePosixPath(name).parts
        if len(parts) == 2 and parts[1] != "v1":
            return False
        if len(parts) >= 3 and (parts[1] != "v1" or parts[2] not in _ORIGINS):
            return False
        if any(_TRANSACTION_RE.fullmatch(part) for part in parts):
            return False
        dots = [index for index, part in enumerate(parts) if part.startswith(".")]
        if dots and (
            len(dots) != 1 or not _control_path_valid(parts, dots[0], entry)
        ):
            return False
    return True


def _topology_valid(entries: dict[str, LegacyPathObservationV1]) -> bool:
    for name, entry in entries.items():
        parts = PurePosixPath(name).parts
        if len(parts) == 1:
            directory = name == _AUTHORING or name in _PUBLICATION_DIRS
            if directory != (entry.node_kind is LegacyNodeKindV1.directory):
                return False
        elif parts[0] in _PUBLICATION_DIRS and (
            len(parts) == 2 and parts[1] != "v1"
        ):
            return False
        if any(_TRANSACTION_RE.fullmatch(part) for part in parts):
            return False
    for root in _SCAFFOLD_ROOTS:
        if root in entries:
            scaffold = entries.get(root + "/v1")
            if scaffold is None or scaffold.node_kind is not LegacyNodeKindV1.directory:
                return False
    return _authoring_shape_valid(entries)


def _classify_legacy_state_core_v1(
    request: LegacyStateRequestV1,
    observation: LegacyStateObservationV1,
) -> LegacyStateDispositionV1:
    """Classify structure/metadata; store owners authenticate opaque bytes."""
    if type(request) is not LegacyStateRequestV1 or type(observation) is not LegacyStateObservationV1:
        raise _invalid("classification_type")
    if not observation.entries:
        return LegacyStateDispositionV1.fresh
    entries = {item.relative_path.as_posix(): item for item in observation.entries}
    if not _closed_parents(entries) or not _topology_valid(entries):
        return LegacyStateDispositionV1.invalid
    service = (request.service_uid, request.service_gid)
    root_seen = False
    for name, entry in entries.items():
        if not _metadata_valid(entry):
            return LegacyStateDispositionV1.invalid
        owner = (entry.uid, entry.gid)
        if name == _AUTHORING or name.startswith(_AUTHORING + "/"):
            if owner not in {service, (0, 0)}:
                return LegacyStateDispositionV1.invalid
            root_seen |= owner == (0, 0)
        elif owner != service:
            return LegacyStateDispositionV1.invalid
    return (
        LegacyStateDispositionV1.root_adoption_required
        if root_seen else LegacyStateDispositionV1.exact_service
    )


def classify_legacy_state_v1(
    request: LegacyStateRequestV1,
    observation: LegacyStateObservationV1,
) -> LegacyStateDispositionV1:
    require_canonical_legacy_state_request_v1(request)
    return _classify_legacy_state_core_v1(request, observation)


def _classify_legacy_state_for_test_v1(
    request: LegacyStateRequestV1,
    observation: LegacyStateObservationV1,
) -> LegacyStateDispositionV1:
    if type(request) is not LegacyStateRequestV1 or request._canonical is not False:
        raise _invalid("test_classification_request")
    return _classify_legacy_state_core_v1(request, observation)


def _project_adoption_entry_v1(
    request: LegacyStateRequestV1, entry: LegacyPathObservationV1,
) -> LegacyPathObservationV1:
    authoring = entry.relative_path.parts[0] == _AUTHORING
    owner = (entry.uid, entry.gid)
    if authoring and owner == (0, 0):
        owner = (request.service_uid, request.service_gid)
    return LegacyPathObservationV1(
        entry.relative_path, entry.node_kind, *owner, entry.mode, entry.nlink,
        entry.size, entry.content_sha256,
        entry.has_access_acl, entry.has_default_acl, entry.device, entry.inode,
    )


def project_legacy_state_adoption_v1(
    request: LegacyStateRequestV1,
    observation: LegacyStateObservationV1,
) -> LegacyStateObservationV1:
    """Project any valid partial adoption to its sole permitted final state."""
    if (
        type(request) is not LegacyStateRequestV1
        or type(observation) is not LegacyStateObservationV1
        or classify_legacy_state_v1(request, observation)
        is LegacyStateDispositionV1.invalid
    ):
        raise _invalid("adoption_projection")
    return LegacyStateObservationV1(tuple(
        _project_adoption_entry_v1(request, entry)
        for entry in observation.entries
    ))


def legacy_state_adoption_target_sha256_v1(
    request: LegacyStateRequestV1,
    observation: LegacyStateObservationV1,
) -> str:
    return project_legacy_state_adoption_v1(
        request, observation,
    ).observation_sha256


def legacy_state_policy_sha256_v1() -> str:
    return _digest(_POLICY_DOMAIN, {
        "adoption_delta": "crash-resumable-authoring-root-owner-to-service-only",
        "authoring_control_leaves": sorted(_CONTROL_LEAF_SIZES),
        "authoring_control_leaf_sizes": _CONTROL_LEAF_SIZES,
        "authoring_control_pattern": _CONTROL_RE.pattern,
        "dispositions": [item.value for item in LegacyStateDispositionV1],
        "directory_mode": 0o700, "file_mode": 0o600,
        "identity_binding": "device+inode",
        "fsm": [list(item) for item in LEGACY_STATE_FSM_V1],
        "host_provisioning_policy_sha256": host_provisioning_policy_sha256_v1(),
        "max_depth": MAX_LEGACY_STATE_DEPTH_V1,
        "max_entries": MAX_LEGACY_STATE_ENTRIES_V1,
        "max_file_bytes": MAX_LEGACY_STATE_FILE_BYTES_V1,
        "max_total_bytes": MAX_LEGACY_STATE_TOTAL_BYTES_V1,
        "node_kinds": [item.value for item in LegacyNodeKindV1],
        "origins": sorted(_ORIGINS), "posix_acl": "absent",
        "protocol": LEGACY_STATE_PROTOCOL_V1,
        "publication_payloads": "opaque-existing-owner",
        "ready_state": "exact-service-after-contract-convergence",
        "request_binding": "canonical-host-layout-state-role+typed-account",
        "reserved": [item.as_posix() for item in LEGACY_STATE_RESERVED_TOP_LEVEL_V1],
        "scaffolds": [root + "/v1" for root in _SCAFFOLD_ROOTS],
        "transaction_pattern": _TRANSACTION_RE.pattern,
        "transactions": "reject",
    })


__all__ = [
    "LEGACY_STATE_FSM_V1", "LEGACY_STATE_PROTOCOL_V1",
    "LEGACY_STATE_RESERVED_TOP_LEVEL_V1",
    "MAX_LEGACY_STATE_DEPTH_V1", "MAX_LEGACY_STATE_ENTRIES_V1",
    "MAX_LEGACY_STATE_FILE_BYTES_V1",
    "MAX_LEGACY_STATE_TOTAL_BYTES_V1", "LegacyNodeKindV1",
    "LegacyPathObservationV1", "LegacyStateDispositionV1",
    "LegacyStateObservationV1", "classify_legacy_state_v1",
    "legacy_state_adoption_target_sha256_v1",
    "legacy_state_file_sha256_v1",
    "legacy_state_policy_sha256_v1", "project_legacy_state_adoption_v1",
]
