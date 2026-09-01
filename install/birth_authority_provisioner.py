"""The Birth authority provisioner: journal, author root and recovery (2B).

This module is installer-side by construction.  Section 4.1 gives the layout
to the installer, and the mutating capability of increment 2A must not gain a
second door in the runtime: the provisioner runs while an installation is being
prepared, never while Metnos serves a turn.

Sections 4.3 and 8 of the group 2 analysis fix the journal bytes.  The header and
every checkpoint are immutable documents with a closed schema, a canonical
encoding and a digest bound to a domain, so a resumed run can tell an expected
state from a tampered one without interpreting anything written by a candidate.

The journal provides consistency and recovery, not authentication: before
groups 5 and 6 a process that controls the transitional root can fabricate a
coherent journal, so none of these documents is an authorisation decision.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Sequence
import sys

_RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
if str(_RUNTIME) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(_RUNTIME))

from executor_birth_prepared_set import (
    PreparedAuthoritySetV2, _PREPARED_AUTHORITY_SET_SEAL_V2,
    _prepared_authority_set_binding_v2, is_prepared_authority_set_v2,
)

TRANSACTION_PROTOCOL_V1 = "birth-authority-provisioning-v1"
TRANSACTION_PROTOCOL_V2 = "birth-authority-provisioning-v2"
CHECKPOINT_DIGEST_DOMAIN_V1 = b"metnos.executor-birth.provisioning-checkpoint/v1\0"
SOURCE_INVENTORY_DIGEST_DOMAIN_V2 = (
    b"metnos.executor-birth.provisioning-source-inventory/v2\0"
)
MATERIAL_PLAN_DIGEST_DOMAIN_V2 = (
    b"metnos.executor-birth.provisioning-material-plan/v2\0"
)
TRANSACTION_HEADER_BASENAME_V1 = "transaction-v1.json"
TRANSACTION_HEADER_BASENAME_V2 = "transaction-v2.json"
MATERIAL_PLAN_BASENAME_V2 = "material-plan-v2.json"
MATERIAL_PLAN_PENDING_PREFIX_V2 = ".material-plan-v2.pending."
CHECKPOINTS_BASENAME_V1 = "checkpoints-v1"
MAXIMUM_CHECKPOINT_SEQUENCE_V1 = 8191
MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1 = 1024 * 1024

_HEX_DIGITS = frozenset("0123456789abcdef")


class BirthProvisioningError(RuntimeError):
    """Stable provisioning failure without a path, key byte or ACL detail.

    The public chain is empty by construction, exactly as in the filesystem
    capability: section 11 forbids personal paths, security descriptors and
    platform diagnostics in the public message, and the originating error is
    kept privately for diagnosis.
    """

    def __init__(self, code: str, cause: BaseException | None = None) -> None:
        self.code = code
        self._internal_cause = cause
        super().__init__(code)
        self.__suppress_context__ = True

    @property
    def __context__(self) -> None:
        return None

    @__context__.setter
    def __context__(self, value: BaseException | None) -> None:
        if value is not None and self._internal_cause is None:
            self._internal_cause = value

    @property
    def __cause__(self) -> None:
        return None

    @__cause__.setter
    def __cause__(self, value: BaseException | None) -> None:
        if value is not None and self._internal_cause is None:
            self._internal_cause = value


class ProvisioningStateV1(str, Enum):
    """Closed and monotone sequence of durable provisioning states.

    The declaration order is normative: a checkpoint may repeat the previous
    state or advance, never go back (section 4.3).
    """

    created = "created"
    author_staged = "author_staged"
    inputs_staged = "inputs_staged"
    authorities_staged = "authorities_staged"
    context_staged = "context_staged"
    verified = "verified"
    author_installed = "author_installed"
    set_installed = "set_installed"
    marker_installed = "marker_installed"


_STATE_ORDER_V1: dict[ProvisioningStateV1, int] = {
    state: index for index, state in enumerate(ProvisioningStateV1)
}


def state_rank_v1(state: ProvisioningStateV1) -> int:
    """Position of one state in the closed sequence."""
    return _STATE_ORDER_V1[state]


class PayloadObjectTypeV1(str, Enum):
    file = "file"
    directory = "directory"


class PayloadConfidentialityV1(str, Enum):
    confidential = "confidential"
    integrity_only = "integrity_only"


@dataclass(frozen=True, slots=True)
class MaterialPlanEntryV2:
    """One exact object that a recoverable V2 staging pass must produce."""

    relative_path: str
    object_type: PayloadObjectTypeV1
    confidentiality: PayloadConfidentialityV1
    payload: bytes | None = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _is_canonical_relative_path(self.relative_path)
            or not (
                self.relative_path == "authority-set"
                or self.relative_path.startswith("authority-set/")
            )
            or not isinstance(self.object_type, PayloadObjectTypeV1)
            or not isinstance(self.confidentiality, PayloadConfidentialityV1)
        ):
            raise _conflict()
        if self.object_type is PayloadObjectTypeV1.directory:
            if self.payload is not None:
                raise _conflict()
        elif not isinstance(self.payload, bytes):
            raise _conflict()

    @property
    def sort_key(self) -> bytes:
        return self.relative_path.encode("utf-8")

    def to_document(self) -> dict[str, object]:
        payload = self.payload
        return {
            "relative_path": self.relative_path,
            "object_type": self.object_type.value,
            "confidentiality": self.confidentiality.value,
            "size": None if payload is None else len(payload),
            "sha256": (
                None if payload is None else hashlib.sha256(payload).hexdigest()
            ),
            "payload_hex": None if payload is None else payload.hex(),
        }

    @staticmethod
    def from_document(value: object) -> "MaterialPlanEntryV2":
        if not isinstance(value, dict) or set(value) != {
            "relative_path", "object_type", "confidentiality", "size",
            "sha256", "payload_hex",
        }:
            raise _conflict()
        try:
            object_type = PayloadObjectTypeV1(value["object_type"])
            confidentiality = PayloadConfidentialityV1(value["confidentiality"])
        except ValueError as exc:
            raise _conflict(exc) from None
        if object_type is PayloadObjectTypeV1.directory:
            if any(value[field] is not None for field in (
                "size", "sha256", "payload_hex",
            )):
                raise _conflict()
            payload = None
        else:
            encoded = value["payload_hex"]
            if (
                not isinstance(encoded, str)
                or len(encoded) % 2
                or set(encoded) - _HEX_DIGITS
            ):
                raise _conflict()
            payload = bytes.fromhex(encoded)
            if (
                value["size"] != len(payload)
                or value["sha256"] != hashlib.sha256(payload).hexdigest()
            ):
                raise _conflict()
        return MaterialPlanEntryV2(
            relative_path=value["relative_path"],
            object_type=object_type,
            confidentiality=confidentiality,
            payload=payload,
        )


@dataclass(frozen=True, slots=True)
class MaterialPlanV2:
    """Closed confidential inventory that makes V2 staging restartable."""

    transaction_id: str
    transaction_header_sha256: str
    entries: tuple[MaterialPlanEntryV2, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.entries, key=lambda item: item.sort_key))
        paths = {entry.relative_path for entry in ordered}
        if (
            not _is_hex(self.transaction_id, 32)
            or not _is_hex(self.transaction_header_sha256, 64)
            or not ordered
            or ordered != self.entries
            or len(paths) != len(ordered)
            or "authority-set" not in paths
        ):
            raise _conflict()
        kinds = {entry.relative_path: entry.object_type for entry in ordered}
        if kinds["authority-set"] is not PayloadObjectTypeV1.directory:
            raise _conflict()
        for entry in ordered:
            parts = entry.relative_path.split("/")
            for index in range(1, len(parts)):
                parent = "/".join(parts[:index])
                if kinds.get(parent) is not PayloadObjectTypeV1.directory:
                    raise _conflict()

    def _document(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "protocol": TRANSACTION_PROTOCOL_V2,
            "transaction_id": self.transaction_id,
            "transaction_header_sha256": self.transaction_header_sha256,
            "objects": [entry.to_document() for entry in self.entries],
        }

    def digest(self) -> str:
        return hashlib.sha256(
            MATERIAL_PLAN_DIGEST_DOMAIN_V2
            + encode_canonical_document_v1(self._document())
        ).hexdigest()

    def encode(self) -> bytes:
        value = self._document()
        value["material_plan_sha256"] = self.digest()
        encoded = encode_canonical_document_v1(value)
        if len(encoded) > MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1:
            raise _conflict()
        return encoded


def decode_material_plan_v2(raw: bytes) -> MaterialPlanV2:
    """Decode one exact V2 plan and verify its closed object inventory."""
    value = decode_canonical_document_v1(raw)
    if set(value) != {
        "schema_version", "protocol", "transaction_id",
        "transaction_header_sha256", "objects", "material_plan_sha256",
    } or (
        value["schema_version"] != 2
        or value["protocol"] != TRANSACTION_PROTOCOL_V2
    ):
        raise _conflict()
    objects = value["objects"]
    if not isinstance(objects, list):
        raise _conflict()
    plan = MaterialPlanV2(
        transaction_id=value["transaction_id"],
        transaction_header_sha256=value["transaction_header_sha256"],
        entries=tuple(MaterialPlanEntryV2.from_document(item) for item in objects),
    )
    if value["material_plan_sha256"] != plan.digest() or plan.encode() != raw:
        raise _conflict()
    return plan


def _reject(code: str, cause: BaseException | None = None) -> BirthProvisioningError:
    return BirthProvisioningError(code, cause)


def _conflict(cause: BaseException | None = None) -> BirthProvisioningError:
    return _reject("birth_provisioning_transaction_conflict", cause)


def _is_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and not (set(value) - _HEX_DIGITS)
    )


def _is_exact_int(value: object) -> bool:
    return type(value) is int


def _is_size(value: object) -> bool:
    return _is_exact_int(value) and value >= 0


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise _conflict()
        seen[key] = value
    return seen


def encode_canonical_document_v1(value: object) -> bytes:
    """Serialise one journal document in the single admitted encoding."""
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _conflict(exc) from None


def decode_canonical_document_v1(raw: bytes) -> dict[str, object]:
    """Read one journal document and refuse every non-canonical encoding."""
    if not isinstance(raw, (bytes, bytearray)):
        raise _conflict()
    raw = bytes(raw)
    if len(raw) > MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1:
        raise _conflict()
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs_without_duplicates,
        )
    except BirthProvisioningError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _conflict(exc) from None
    if not isinstance(value, dict) or encode_canonical_document_v1(value) != raw:
        raise _conflict()
    return value


@dataclass(frozen=True, slots=True)
class PlatformIdentityV1:
    """Typed identity of one payload object on the running platform."""

    platform: str
    device: int | None = None
    inode: int | None = None
    volume_serial: str | None = None
    file_id: str | None = None

    def __post_init__(self) -> None:
        if self.platform == "posix":
            if (
                not _is_size(self.device) or not _is_size(self.inode)
                or self.volume_serial is not None or self.file_id is not None
            ):
                raise _conflict()
        elif self.platform == "windows":
            if (
                not _is_hex(self.volume_serial, 16) or not _is_hex(self.file_id, 32)
                or self.device is not None or self.inode is not None
            ):
                raise _conflict()
        else:
            raise _conflict()

    def to_document(self) -> dict[str, object]:
        if self.platform == "posix":
            return {"platform": "posix", "device": self.device, "inode": self.inode}
        return {
            "platform": "windows", "volume_serial": self.volume_serial,
            "file_id": self.file_id,
        }

    @staticmethod
    def from_document(value: object) -> "PlatformIdentityV1":
        if not isinstance(value, dict):
            raise _conflict()
        if set(value) == {"platform", "device", "inode"} and value["platform"] == "posix":
            return PlatformIdentityV1(
                "posix", device=value["device"], inode=value["inode"],
            )
        if (
            set(value) == {"platform", "volume_serial", "file_id"}
            and value["platform"] == "windows"
        ):
            return PlatformIdentityV1(
                "windows", volume_serial=value["volume_serial"],
                file_id=value["file_id"],
            )
        raise _conflict()


def _is_canonical_relative_path(value: object) -> bool:
    """A relative POSIX path with no dot, parent, backslash or empty part."""
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    if "\\" in value or "//" in value or value.endswith("/"):
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


@dataclass(frozen=True, slots=True)
class PayloadRecordV1:
    """One object destined to a final location, as the journal records it."""

    relative_path: str
    object_type: PayloadObjectTypeV1
    confidentiality: PayloadConfidentialityV1
    size: int | None
    sha256: str | None
    platform_identity: PlatformIdentityV1

    def __post_init__(self) -> None:
        if not _is_canonical_relative_path(self.relative_path):
            raise _conflict()
        if not isinstance(self.object_type, PayloadObjectTypeV1):
            raise _conflict()
        if not isinstance(self.confidentiality, PayloadConfidentialityV1):
            raise _conflict()
        if not isinstance(self.platform_identity, PlatformIdentityV1):
            raise _conflict()
        if self.object_type is PayloadObjectTypeV1.file:
            if not _is_size(self.size) or not _is_hex(self.sha256, 64):
                raise _conflict()
        elif self.size is not None or self.sha256 is not None:
            raise _conflict()

    @property
    def sort_key(self) -> bytes:
        return self.relative_path.encode("utf-8")

    def to_document(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "object_type": self.object_type.value,
            "confidentiality": self.confidentiality.value,
            "size": self.size,
            "sha256": self.sha256,
            "platform_identity": self.platform_identity.to_document(),
        }

    @staticmethod
    def from_document(value: object) -> "PayloadRecordV1":
        if not isinstance(value, dict) or set(value) != {
            "relative_path", "object_type", "confidentiality", "size", "sha256",
            "platform_identity",
        }:
            raise _conflict()
        try:
            object_type = PayloadObjectTypeV1(value["object_type"])
            confidentiality = PayloadConfidentialityV1(value["confidentiality"])
        except ValueError as exc:
            raise _conflict(exc) from None
        return PayloadRecordV1(
            relative_path=value["relative_path"],
            object_type=object_type,
            confidentiality=confidentiality,
            size=value["size"],
            sha256=value["sha256"],
            platform_identity=PlatformIdentityV1.from_document(
                value["platform_identity"]
            ),
        )


def _ordered_payload_records(
    records: Sequence[PayloadRecordV1],
) -> tuple[PayloadRecordV1, ...]:
    """Order the inventory by UTF-8 bytes and refuse a repeated path.

    The order is a property of the inventory, not of the caller: normalising it
    here means a checkpoint read back from disk equals the one that produced
    those bytes.
    """
    if not isinstance(records, (tuple, list)) or any(
        not isinstance(record, PayloadRecordV1) for record in records
    ):
        raise _conflict()
    ordered = tuple(sorted(records, key=lambda record: record.sort_key))
    if len({record.relative_path for record in ordered}) != len(ordered):
        raise _conflict()
    return ordered


@dataclass(frozen=True, slots=True)
class TransactionHeaderV1:
    """The immutable identity of one provisioning transaction."""

    transaction_id: str
    provisioner_build_id: str

    def __post_init__(self) -> None:
        if not _is_hex(self.transaction_id, 32):
            raise _conflict()
        if not isinstance(self.provisioner_build_id, str) or not self.provisioner_build_id:
            raise _conflict()

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "transaction_id": self.transaction_id,
            "protocol": TRANSACTION_PROTOCOL_V1,
            "provisioner_build_id": self.provisioner_build_id,
        }

    def encode(self) -> bytes:
        return encode_canonical_document_v1(self.to_document())


def decode_transaction_header_v1(raw: bytes) -> TransactionHeaderV1:
    """Read the header and refuse another protocol or schema."""
    value = decode_canonical_document_v1(raw)
    if set(value) != {
        "schema_version", "transaction_id", "protocol", "provisioner_build_id"
    } or value["schema_version"] != 1 or value["protocol"] != TRANSACTION_PROTOCOL_V1:
        raise _conflict()
    return TransactionHeaderV1(
        transaction_id=value["transaction_id"],
        provisioner_build_id=value["provisioner_build_id"],
    )


def _is_digest_v2(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and _is_hex(value[7:], 64)
    )


@dataclass(frozen=True, slots=True)
class TransactionHeaderV2:
    """Immutable identity of one transition provisioning transaction."""

    transaction_id: str
    provisioner_build_id: str
    request_id: str
    closed_build_id: str
    previous_set_id: str
    distribution_payload_hash: str
    distribution_signature_hash: str
    source_inventory_hash: str

    def __post_init__(self) -> None:
        if (
            not _is_hex(self.transaction_id, 32)
            or not isinstance(self.provisioner_build_id, str)
            or not self.provisioner_build_id
            or "\0" in self.provisioner_build_id
            or not _is_hex(self.previous_set_id, 64)
            or any(not _is_digest_v2(value) for value in (
                self.request_id,
                self.closed_build_id,
                self.distribution_payload_hash,
                self.distribution_signature_hash,
                self.source_inventory_hash,
            ))
        ):
            raise _conflict()

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "protocol": TRANSACTION_PROTOCOL_V2,
            "transaction_id": self.transaction_id,
            "provisioner_build_id": self.provisioner_build_id,
            "request_id": self.request_id,
            "closed_build_id": self.closed_build_id,
            "previous_set_id": self.previous_set_id,
            "distribution_payload_hash": self.distribution_payload_hash,
            "distribution_signature_hash": self.distribution_signature_hash,
            "source_inventory_hash": self.source_inventory_hash,
        }

    def encode(self) -> bytes:
        return encode_canonical_document_v1(self.to_document())


def decode_transaction_header_v2(raw: bytes) -> TransactionHeaderV2:
    """Decode the closed V2 header without accepting a V1 interpretation."""
    value = decode_canonical_document_v1(raw)
    if set(value) != {
        "schema_version", "protocol", "transaction_id",
        "provisioner_build_id", "request_id", "closed_build_id",
        "previous_set_id", "distribution_payload_hash",
        "distribution_signature_hash", "source_inventory_hash",
    } or value["schema_version"] != 2 or value["protocol"] != TRANSACTION_PROTOCOL_V2:
        raise _conflict()
    return TransactionHeaderV2(
        transaction_id=value["transaction_id"],
        provisioner_build_id=value["provisioner_build_id"],
        request_id=value["request_id"],
        closed_build_id=value["closed_build_id"],
        previous_set_id=value["previous_set_id"],
        distribution_payload_hash=value["distribution_payload_hash"],
        distribution_signature_hash=value["distribution_signature_hash"],
        source_inventory_hash=value["source_inventory_hash"],
    )


def provisioning_source_inventory_hash_v2(distribution: object) -> str:
    """Bind the ordered authenticated file inventory of one distribution."""
    from executor_birth_distribution_manifest import is_verified_distribution

    if not is_verified_distribution(distribution):
        raise _conflict()
    files = tuple(sorted(
        distribution.files, key=lambda item: item.path.encode("utf-8"),
    ))
    if len({item.path for item in files}) != len(files):
        raise _conflict()
    value = [{
        "path": item.path,
        "size": item.size,
        "content_hash": item.content_hash,
        "role": item.role,
    } for item in files]
    encoded = encode_canonical_document_v1({"files": value})
    return "sha256:" + hashlib.sha256(
        SOURCE_INVENTORY_DIGEST_DOMAIN_V2 + encoded,
    ).hexdigest()


def _build_transaction_header_v2(
    *, transaction_id: str, provisioner_build_id: str,
    claim: object, distribution: object, previous_set: object,
) -> TransactionHeaderV2:
    """Derive a V2 header only from nominally authenticated transition facts."""
    from executor_birth_distribution_manifest import is_verified_distribution
    from executor_birth_ownership_coordinator import SuccessorClaimV1
    from executor_birth_prepared_set import is_prepared_set_v1

    if (
        not isinstance(claim, SuccessorClaimV1)
        or not is_verified_distribution(distribution)
        or not is_prepared_set_v1(previous_set)
        or claim.closed_build_id != distribution.identity.closed_build_id
        or claim.release_sequence != distribution.release_sequence
    ):
        raise _conflict()
    return TransactionHeaderV2(
        transaction_id=transaction_id,
        provisioner_build_id=provisioner_build_id,
        request_id=claim.request_id,
        closed_build_id=distribution.identity.closed_build_id,
        previous_set_id=previous_set.set_id,
        distribution_payload_hash=(
            "sha256:" + hashlib.sha256(distribution.encoded).hexdigest()
        ),
        distribution_signature_hash=(
            "sha256:" + hashlib.sha256(distribution.signature).hexdigest()
        ),
        source_inventory_hash=provisioning_source_inventory_hash_v2(
            distribution,
        ),
    )


_JOURNAL_FORMAT_SEAL = object()


@dataclass(frozen=True, slots=True)
class _TransactionJournalFormat:
    root_prefix: str
    header_basename: str
    header_pending_prefix: str
    decode_header: Callable[[bytes], object]
    material_plan_basename: str | None
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _JOURNAL_FORMAT_SEAL
            or not self.root_prefix.startswith(".birth-provisioning-")
            or not self.header_basename.startswith("transaction-v")
            or not self.header_basename.endswith(".json")
            or not self.header_pending_prefix.startswith(".transaction-v")
            or not callable(self.decode_header)
            or (
                self.material_plan_basename is not None
                and self.material_plan_basename != MATERIAL_PLAN_BASENAME_V2
            )
        ):
            raise _conflict()


_ACQUIRED_DIGEST_FIELDS_V1 = (
    "author_source_public_inventory_sha256",
    "approval_input_sha256",
    "semantic_input_sha256",
    "producer_catalog_sha256",
    "context_source_inventory_sha256",
)

_PRODUCED_DIGEST_FIELDS_V1 = (
    "author_store_public_inventory_sha256",
    "set_json_sha256",
    "context_material_sha256",
)


@dataclass(frozen=True, slots=True)
class CheckpointV1:
    """One immutable durable step of the transaction."""

    transaction_id: str
    checkpoint_sequence: int
    previous_checkpoint_sha256: str | None
    state: ProvisioningStateV1
    payload_inventory: tuple[PayloadRecordV1, ...]
    digests: Mapping[str, str | None]
    set_id: str | None

    def __post_init__(self) -> None:
        if not _is_hex(self.transaction_id, 32):
            raise _conflict()
        if (
            not _is_exact_int(self.checkpoint_sequence)
            or not 0 <= self.checkpoint_sequence <= MAXIMUM_CHECKPOINT_SEQUENCE_V1
        ):
            raise _conflict()
        first = self.checkpoint_sequence == 0
        if first != (self.previous_checkpoint_sha256 is None):
            raise _conflict()
        if not first and not _is_hex(self.previous_checkpoint_sha256, 64):
            raise _conflict()
        if not isinstance(self.state, ProvisioningStateV1):
            raise _conflict()
        if self.set_id is not None and not _is_hex(self.set_id, 64):
            raise _conflict()
        known = set(_ACQUIRED_DIGEST_FIELDS_V1) | set(_PRODUCED_DIGEST_FIELDS_V1)
        if set(self.digests) != known:
            raise _conflict()
        if any(
            value is not None and not _is_hex(value, 64)
            for value in self.digests.values()
        ):
            raise _conflict()
        object.__setattr__(
            self, "payload_inventory",
            _ordered_payload_records(self.payload_inventory),
        )
        # The journal is immutable once built, so the caller keeps no writable
        # reference to the digests it passed in.
        object.__setattr__(self, "digests", MappingProxyType(dict(self.digests)))

    def to_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "schema_version": 1,
            "transaction_id": self.transaction_id,
            "checkpoint_sequence": self.checkpoint_sequence,
            "previous_checkpoint_sha256": self.previous_checkpoint_sha256,
            "state": self.state.value,
            "set_id": self.set_id,
            "payload_inventory": [
                record.to_document()
                for record in self.payload_inventory
            ],
        }
        document.update(dict(self.digests))
        document["checkpoint_sha256"] = self.digest()
        return document

    def digest(self) -> str:
        """Digest of this checkpoint without the field that carries it."""
        document: dict[str, object] = {
            "schema_version": 1,
            "transaction_id": self.transaction_id,
            "checkpoint_sequence": self.checkpoint_sequence,
            "previous_checkpoint_sha256": self.previous_checkpoint_sha256,
            "state": self.state.value,
            "set_id": self.set_id,
            "payload_inventory": [
                record.to_document()
                for record in self.payload_inventory
            ],
        }
        document.update(dict(self.digests))
        return hashlib.sha256(
            CHECKPOINT_DIGEST_DOMAIN_V1 + encode_canonical_document_v1(document)
        ).hexdigest()

    def encode(self) -> bytes:
        return encode_canonical_document_v1(self.to_document())

    def name(self) -> str:
        return checkpoint_name_v1(self.checkpoint_sequence)


def _sequence_component_v1(sequence: int) -> str:
    """The single 20-digit form every sequenced name of the layout shares."""
    if (
        not _is_exact_int(sequence)
        or not 0 <= sequence <= MAXIMUM_CHECKPOINT_SEQUENCE_V1
    ):
        raise _conflict()
    return f"{sequence:020d}"


def checkpoint_name_v1(sequence: int) -> str:
    """The only authoritative name of one checkpoint."""
    return _sequence_component_v1(sequence) + ".json"


def empty_digests_v1() -> dict[str, str | None]:
    """Every digest field, still unacquired and unproduced."""
    return {
        field: None
        for field in _ACQUIRED_DIGEST_FIELDS_V1 + _PRODUCED_DIGEST_FIELDS_V1
    }


def decode_checkpoint_v1(raw: bytes) -> CheckpointV1:
    """Read one checkpoint and verify its own digest before returning it."""
    value = decode_canonical_document_v1(raw)
    expected = {
        "schema_version", "transaction_id", "checkpoint_sequence",
        "previous_checkpoint_sha256", "state", "set_id", "payload_inventory",
        "checkpoint_sha256",
    } | set(_ACQUIRED_DIGEST_FIELDS_V1) | set(_PRODUCED_DIGEST_FIELDS_V1)
    if set(value) != expected or value["schema_version"] != 1:
        raise _conflict()
    try:
        state = ProvisioningStateV1(value["state"])
    except ValueError as exc:
        raise _conflict(exc) from None
    inventory = value["payload_inventory"]
    if not isinstance(inventory, list):
        raise _conflict()
    records = tuple(PayloadRecordV1.from_document(item) for item in inventory)
    checkpoint = CheckpointV1(
        transaction_id=value["transaction_id"],
        checkpoint_sequence=value["checkpoint_sequence"],
        previous_checkpoint_sha256=value["previous_checkpoint_sha256"],
        state=state,
        payload_inventory=records,
        digests={
            field: value[field]
            for field in _ACQUIRED_DIGEST_FIELDS_V1 + _PRODUCED_DIGEST_FIELDS_V1
        },
        set_id=value["set_id"],
    )
    # The inventory is re-ordered on the way out, so a document whose list was
    # shuffled cannot round-trip: comparing the whole encoding also proves the
    # order, not only the digest of the fields.
    if not _is_hex(value["checkpoint_sha256"], 64):
        raise _conflict()
    if checkpoint.encode() != raw:
        raise _conflict()
    return checkpoint


TRANSACTION_PREFIX_V1 = ".birth-provisioning-v1.txn."
TRANSACTION_PREFIX_V2 = ".birth-provisioning-v2.txn."
HEADER_PENDING_PREFIX_V1 = ".transaction-v1.pending."
HEADER_PENDING_PREFIX_V2 = ".transaction-v2.pending."
CHECKPOINT_PENDING_PREFIX_V1 = ".checkpoint-pending-"


def transaction_root_name_v1(transaction_id: str) -> str:
    """The only admitted name of one transaction directory."""
    if not _is_hex(transaction_id, 32):
        raise _conflict()
    return TRANSACTION_PREFIX_V1 + transaction_id


def transaction_root_name_v2(transaction_id: str) -> str:
    """The only admitted name of one transition transaction directory."""
    if not _is_hex(transaction_id, 32):
        raise _conflict()
    return TRANSACTION_PREFIX_V2 + transaction_id


_JOURNAL_FORMAT_V1 = _TransactionJournalFormat(
    TRANSACTION_PREFIX_V1,
    TRANSACTION_HEADER_BASENAME_V1,
    HEADER_PENDING_PREFIX_V1,
    decode_transaction_header_v1,
    None,
    _JOURNAL_FORMAT_SEAL,
)
_JOURNAL_FORMAT_V2 = _TransactionJournalFormat(
    TRANSACTION_PREFIX_V2,
    TRANSACTION_HEADER_BASENAME_V2,
    HEADER_PENDING_PREFIX_V2,
    decode_transaction_header_v2,
    MATERIAL_PLAN_BASENAME_V2,
    _JOURNAL_FORMAT_SEAL,
)


def new_transaction_id_v1() -> str:
    """A fresh 128-bit nonce, from the operating system alone."""
    return secrets.token_hex(16)


PAYLOAD_PENDING_PREFIX_V1 = ".payload-pending-"


def _payload_pending_name_v1(sequence: int, transaction_id: str) -> str:
    if not _is_hex(transaction_id, 32):
        raise _conflict()
    return (
        PAYLOAD_PENDING_PREFIX_V1 + _sequence_component_v1(sequence)
        + "-" + transaction_id
    )


def _is_payload_pending_name_v1(name: str, transaction_id: str) -> bool:
    suffix = "-" + transaction_id
    if not name.startswith(PAYLOAD_PENDING_PREFIX_V1) or not name.endswith(suffix):
        return False
    body = name[len(PAYLOAD_PENDING_PREFIX_V1): -len(suffix)]
    return len(body) == 20 and body.isdigit() and body.isascii()


def _checkpoint_pending_name_v1(sequence: int, transaction_id: str) -> str:
    if not _is_hex(transaction_id, 32):
        raise _conflict()
    return (
        CHECKPOINT_PENDING_PREFIX_V1 + _sequence_component_v1(sequence)
        + "-" + transaction_id
    )


def _is_checkpoint_name_v1(name: str) -> int | None:
    """Return the sequence of an authoritative checkpoint name, or nothing."""
    if not name.endswith(".json"):
        return None
    body = name[: -len(".json")]
    if len(body) != 20 or not body.isdigit() or not body.isascii():
        return None
    sequence = int(body)
    return sequence if sequence <= MAXIMUM_CHECKPOINT_SEQUENCE_V1 else None


@dataclass(frozen=True, slots=True)
class TransactionStateV1:
    """What one transaction directory holds right now."""

    header: TransactionHeaderV1 | TransactionHeaderV2 | None
    chain: tuple[CheckpointV1, ...]
    header_pending: bool
    pending_checkpoint_sequence: int | None

    @property
    def last(self) -> CheckpointV1 | None:
        return self.chain[-1] if self.chain else None


class _TransactionJournalV1:
    """Append-only durable memory of one provisioning transaction.

    Every authoritative file is born under a pending name, is written whole,
    is re-read from the same session and only then is renamed without
    replacement, so a name that exists is always a complete file (section 4.3).
    The journal records; it decides nothing about authority.
    """

    __slots__ = (
        "_session", "_transaction_id", "_root", "_checkpoints", "_format",
    )

    def __init__(
        self, session, transaction_id: str,
        *, _format: _TransactionJournalFormat = _JOURNAL_FORMAT_V1,
    ) -> None:
        if (
            not _is_hex(transaction_id, 32)
            or not isinstance(_format, _TransactionJournalFormat)
            or _format._seal is not _JOURNAL_FORMAT_SEAL
        ):
            raise _conflict()
        self._session = session
        self._transaction_id = transaction_id
        self._format = _format
        self._root = (_format.root_prefix + transaction_id,)
        self._checkpoints = self._root + (CHECKPOINTS_BASENAME_V1,)

    @classmethod
    def transition_v2(cls, session, transaction_id: str):
        """Create the same journal discipline with the closed V2 header."""
        return cls(session, transaction_id, _format=_JOURNAL_FORMAT_V2)

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    @property
    def root_components(self) -> tuple[str, ...]:
        return self._root

    @property
    def checkpoints_components(self) -> tuple[str, ...]:
        return self._checkpoints

    @property
    def header_basename(self) -> str:
        return self._format.header_basename

    # -- writing ---------------------------------------------------------

    def create_root(self) -> None:
        """Create the transaction directory alone.

        The checkpoint container comes after the header on purpose: every stop
        between two steps then leaves a shape the recovery matrix of section
        8.2 names — an empty root, a root with the header, a root with the
        header and an empty container.
        """
        with _translated():
            self._session.create_directory_exclusive(
                self._root, role=_integrity_role(),
            )

    def ensure_checkpoints(self) -> None:
        """Create the checkpoint container when the stop happened before it."""
        with _translated():
            if CHECKPOINTS_BASENAME_V1 in self._session.inventory(self._root):
                return
            self._session.create_directory_exclusive(
                self._checkpoints, role=_integrity_role(),
            )

    def write_header(self, header: object) -> None:
        if header.transaction_id != self._transaction_id:
            raise _conflict()
        self._publish(
            self._root,
            self._format.header_pending_prefix + self._transaction_id,
            self._format.header_basename,
            header.encode(),
        )

    def append(self, checkpoint: CheckpointV1) -> None:
        """Make one further step durable, without touching the previous ones."""
        if checkpoint.transaction_id != self._transaction_id:
            raise _conflict()
        self._publish(
            self._checkpoints,
            _checkpoint_pending_name_v1(
                checkpoint.checkpoint_sequence, self._transaction_id,
            ),
            checkpoint.name(),
            checkpoint.encode(),
        )

    def ensure_material_plan_v2(
        self, factory: Callable[[], MaterialPlanV2],
    ) -> MaterialPlanV2:
        """Recover or create the one confidential plan committed by V2 staging."""
        if self._format.material_plan_basename != MATERIAL_PLAN_BASENAME_V2:
            raise _conflict()
        state = self.read_state()
        header = state.header
        if not isinstance(header, TransactionHeaderV2):
            raise _conflict()
        with _translated():
            names = set(self._session.inventory(self._root))
        final = MATERIAL_PLAN_BASENAME_V2
        pending = MATERIAL_PLAN_PENDING_PREFIX_V2 + self._transaction_id
        if final in names:
            if pending in names:
                raise _reject("birth_provisioning_recovery_ambiguous")
            return self._read_material_plan_v2(header)
        if AUTHORITY_SET_BASENAME_V1 in names:
            raise _reject("birth_provisioning_recovery_ambiguous")
        if pending in names:
            try:
                plan = decode_material_plan_v2(self._read_payload(
                    self._root + (pending,),
                    role=self._confidential_role(),
                ))
            except BirthProvisioningError:
                self._discard_pending_by_name(
                    self._root, pending, role=self._confidential_role(),
                    maximum=MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1,
                )
            else:
                # A complete plan bound to another header is conflicting
                # evidence, not an interrupted write that may be discarded.
                self._require_plan_header_v2(plan, header)
                with _translated():
                    self._session.rename_no_replace(
                        self._root + (pending,), self._root + (final,),
                        directory=False,
                    )
                return self._read_material_plan_v2(header)
        plan = factory()
        if not isinstance(plan, MaterialPlanV2):
            raise _conflict()
        self._require_plan_header_v2(plan, header)
        self._publish(
            self._root, pending, final, plan.encode(),
            role=self._confidential_role(),
        )
        return self._read_material_plan_v2(header)

    def _read_material_plan_v2(
        self, header: TransactionHeaderV2,
    ) -> MaterialPlanV2:
        plan = decode_material_plan_v2(self._read_payload(
            self._root + (MATERIAL_PLAN_BASENAME_V2,),
            role=self._confidential_role(),
        ))
        self._require_plan_header_v2(plan, header)
        return plan

    def _require_plan_header_v2(
        self, plan: MaterialPlanV2, header: TransactionHeaderV2,
    ) -> None:
        if (
            plan.transaction_id != self._transaction_id
            or plan.transaction_header_sha256
            != hashlib.sha256(header.encode()).hexdigest()
        ):
            raise _conflict()
        from executor_birth_secure_fs import (
            _BirthObjectRole, _ObjectKind, _matching_rows,
        )

        kinds = {
            PayloadObjectTypeV1.directory: _ObjectKind.directory,
            PayloadObjectTypeV1.file: _ObjectKind.regular_file,
        }
        roles = {
            PayloadConfidentialityV1.confidential: (
                _BirthObjectRole.birth_confidential
            ),
            PayloadConfidentialityV1.integrity_only: (
                _BirthObjectRole.birth_integrity_only
            ),
        }
        for entry in plan.entries:
            components = self._root + tuple(entry.relative_path.split("/"))
            observed = {
                (kind, role) for _pattern, kind, role in _matching_rows(components)
            }
            expected = {
                (kinds[entry.object_type], roles[entry.confidentiality])
            }
            if observed != expected:
                raise _conflict()

    @staticmethod
    def _confidential_role():
        from executor_birth_secure_fs import _BirthObjectRole

        return _BirthObjectRole.birth_confidential

    def publish_payload(
        self,
        parent: tuple[str, ...],
        final: str,
        payload: bytes,
        *,
        role,
        object_sequence: int,
    ):
        """Write one payload file under the same pending discipline.

        No authoritative name is born final, here either: the object sequence
        keeps at most one pending in the whole transaction, so recovery knows
        which step was interrupted (section 4.3).
        """
        pending = _payload_pending_name_v1(object_sequence, self._transaction_id)
        return self._publish(parent, pending, final, payload, role=role)

    def _publish(
        self, parent: tuple[str, ...], pending: str, final: str, payload: bytes,
        *, role=None,
    ):
        role = _integrity_role() if role is None else role
        with _translated():
            identity = self._session.create_file_exclusive(
                parent + (pending,), payload, role=role,
            )
            try:
                observed = self._session.read_file(
                    parent + (pending,),
                    maximum=MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1,
                    role=role,
                )
                if observed != payload:
                    raise _reject("birth_provisioning_io_unavailable")
                self._session.rename_no_replace(
                    parent + (pending,), parent + (final,), directory=False,
                )
            except BaseException:
                # At most one pending may exist under the exclusive lock, so a
                # promotion that did not happen takes its own object away
                # again.  The primary failure travels: the removal is the
                # cleanup, never the news.
                self._discard_pending(
                    parent + (pending,), identity, payload, role=role,
                )
                raise
            return identity

    def _discard_pending(
        self, components: tuple[str, ...], identity, payload: bytes, *, role,
    ) -> None:
        from executor_birth_secure_fs import (
            _DisposalClass, _DisposalExpectation, _ObjectKind,
        )

        expectation = _DisposalExpectation(
            components=components,
            identity=identity,
            kind=_ObjectKind.regular_file,
            role=role,
            disposal_class=_DisposalClass.complete_file,
            links=1,
            expected_size=len(payload),
            maximum_partial_size=None,
            content_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
            inventory=None,
        )
        self._session.dispose_transaction_object(expectation)


    # -- recovery --------------------------------------------------------

    def recover_header(
        self, header: object, state: "TransactionStateV1",
    ) -> "TransactionStateV1":
        """Bring the header into existence from whatever the stop left behind.

        A complete pending is promoted; an empty or partial one is removed on
        the handle it was opened with and written again.  Nothing here reads an
        instruction from the pending: the name and the content of the next step
        come from the closed catalogue and from this transaction alone.
        """
        if state.header is not None:
            return state
        # Before the header exists nothing else may: the matrix admits an empty
        # transaction root, or one that holds the single header pending, and
        # calls any other child ambiguous.
        with _translated():
            names = set(self._session.inventory(self._root))
        pending = self._format.header_pending_prefix + self._transaction_id
        if names - ({pending} if state.header_pending else set()):
            raise _reject("birth_provisioning_recovery_ambiguous")
        if state.header_pending:
            if self._promote_pending_header():
                return self.read_state()
            self._discard_pending_by_name(
                self._root, pending, role=_integrity_role(),
                maximum=MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1,
            )
        self.write_header(header)
        return self.read_state()

    def _promote_pending_header(self) -> bool:
        """Promote the header pending when it is already whole and coherent."""
        pending = self._format.header_pending_prefix + self._transaction_id
        try:
            observed = self._format.decode_header(
                self._read(self._root + (pending,))
            )
        except BirthProvisioningError:
            return False
        if observed.transaction_id != self._transaction_id:
            return False
        with _translated():
            self._session.rename_no_replace(
                self._root + (pending,),
                self._root + (self._format.header_basename,),
                directory=False,
            )
        return True

    def recover_checkpoint_pending(
        self, state: "TransactionStateV1",
    ) -> "TransactionStateV1":
        """Promote or remove the single pending of the immediately next step."""
        sequence = state.pending_checkpoint_sequence
        if sequence is None:
            return state
        name = _checkpoint_pending_name_v1(sequence, self._transaction_id)
        previous = state.chain[-1] if state.chain else None
        if not self._promote_pending_checkpoint(name, sequence, previous):
            self._discard_pending_by_name(
                self._checkpoints, name, role=_integrity_role(),
                maximum=MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1,
            )
        return self.read_state()

    def _promote_pending_checkpoint(
        self, name: str, sequence: int, previous: CheckpointV1 | None,
    ) -> bool:
        try:
            observed = decode_checkpoint_v1(
                self._read(self._checkpoints + (name,))
            )
        except BirthProvisioningError:
            return False
        if (
            observed.transaction_id != self._transaction_id
            or observed.checkpoint_sequence != sequence
        ):
            return False
        if previous is None:
            if observed.previous_checkpoint_sha256 is not None:
                return False
        elif (
            observed.previous_checkpoint_sha256 != previous.digest()
            or state_rank_v1(observed.state) < state_rank_v1(previous.state)
        ):
            return False
        with _translated():
            self._session.rename_no_replace(
                self._checkpoints + (name,),
                self._checkpoints + (observed.name(),),
                directory=False,
            )
        return True

    def _discard_pending_by_name(
        self, parent: tuple[str, ...], name: str, *, role, maximum: int,
    ) -> None:
        """Remove one partial pending after observing exactly what it is."""
        from executor_birth_secure_fs import (
            _DisposalClass, _DisposalExpectation, _ObjectKind,
        )

        with _translated():
            entries = self._session._inventory_state(parent)
        entry = next((item for item in entries if item.name == name), None)
        if entry is None or entry.kind is not _ObjectKind.regular_file:
            raise _reject("birth_provisioning_recovery_ambiguous")
        with _translated():
            self._session.dispose_transaction_object(_DisposalExpectation(
                components=parent + (name,),
                identity=entry.identity,
                kind=_ObjectKind.regular_file,
                role=role,
                disposal_class=_DisposalClass.partial_pending_file,
                links=entry.links,
                expected_size=None,
                maximum_partial_size=maximum,
                content_sha256=None,
                inventory=None,
            ))

    # -- reading ---------------------------------------------------------

    def read_state(self) -> TransactionStateV1:
        """Classify the whole transaction directory before trusting any of it."""
        with _translated():
            names = set(self._session.inventory(self._root))
        header_pending = self._format.header_pending_prefix + self._transaction_id
        chain, pending = ((), None)
        if CHECKPOINTS_BASENAME_V1 in names:
            chain, pending = self._read_chain()
        admitted = {
            self._format.header_basename, CHECKPOINTS_BASENAME_V1,
            header_pending,
        }
        if self._format.material_plan_basename is not None:
            admitted.update({
                self._format.material_plan_basename,
                MATERIAL_PLAN_PENDING_PREFIX_V2 + self._transaction_id,
            })
            if self._format.material_plan_basename in names:
                admitted.add(AUTHORITY_SET_BASENAME_V1)
        # A payload is admitted only where the most recent checkpoint declares
        # it: the journal is the authority on what may exist, and anything
        # else asks for a human rather than a guess (sections 4.3 and 7.6).
        if chain:
            admitted.update(
                record.relative_path.split("/", 1)[0]
                for record in chain[-1].payload_inventory
            )
        payload_pendings = {
            name for name in names - admitted
            if _is_payload_pending_name_v1(name, self._transaction_id)
        }
        if len(payload_pendings) > 1 or (names - admitted) - payload_pendings:
            raise _reject("birth_provisioning_recovery_ambiguous")
        header = None
        if self._format.header_basename in names:
            header = self.read_header()
            if header.transaction_id != self._transaction_id:
                raise _conflict()
        return TransactionStateV1(
            header=header,
            chain=chain,
            header_pending=header_pending in names,
            pending_checkpoint_sequence=pending,
        )

    def read_header(self) -> TransactionHeaderV1 | TransactionHeaderV2:
        return self._format.decode_header(
            self._read(self._root + (self._format.header_basename,))
        )

    def _read_chain(self) -> tuple[tuple[CheckpointV1, ...], int | None]:
        with _translated():
            names = self._session.inventory(self._checkpoints)
        sequences: dict[int, str] = {}
        pendings: list[int] = []
        prefix = CHECKPOINT_PENDING_PREFIX_V1
        suffix = "-" + self._transaction_id
        for name in names:
            sequence = _is_checkpoint_name_v1(name)
            if sequence is not None:
                if sequence in sequences:
                    raise _reject("birth_provisioning_recovery_ambiguous")
                sequences[sequence] = name
                continue
            if name.startswith(prefix) and name.endswith(suffix):
                body = name[len(prefix): -len(suffix)]
                pending = _is_checkpoint_name_v1(body + ".json")
                if pending is not None:
                    pendings.append(pending)
                    continue
            raise _reject("birth_provisioning_recovery_ambiguous")
        if sequences and sorted(sequences) != list(range(len(sequences))):
            raise _reject("birth_provisioning_recovery_ambiguous")
        if len(pendings) > 1 or (pendings and pendings[0] != len(sequences)):
            raise _reject("birth_provisioning_recovery_ambiguous")
        chain: list[CheckpointV1] = []
        previous: CheckpointV1 | None = None
        for sequence in range(len(sequences)):
            checkpoint = decode_checkpoint_v1(
                self._read(self._checkpoints + (sequences[sequence],))
            )
            if (
                checkpoint.transaction_id != self._transaction_id
                or checkpoint.checkpoint_sequence != sequence
            ):
                raise _conflict()
            if previous is None:
                if checkpoint.previous_checkpoint_sha256 is not None:
                    raise _conflict()
            elif (
                checkpoint.previous_checkpoint_sha256 != previous.digest()
                or state_rank_v1(checkpoint.state) < state_rank_v1(previous.state)
            ):
                raise _conflict()
            chain.append(checkpoint)
            previous = checkpoint
        return tuple(chain), (pendings[0] if pendings else None)

    def _read(self, components: tuple[str, ...]) -> bytes:
        return self._read_payload(components, role=_integrity_role())

    def _read_payload(self, components: tuple[str, ...], *, role) -> bytes:
        with _translated():
            return self._session.read_file(
                components,
                maximum=MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1,
                role=role,
            )


def _integrity_role():
    from executor_birth_secure_fs import _BirthObjectRole

    return _BirthObjectRole.birth_integrity_only


def _material_plan_role_v2(confidentiality: PayloadConfidentialityV1):
    from executor_birth_secure_fs import _BirthObjectRole

    if confidentiality is PayloadConfidentialityV1.confidential:
        return _BirthObjectRole.birth_confidential
    if confidentiality is PayloadConfidentialityV1.integrity_only:
        return _BirthObjectRole.birth_integrity_only
    raise _conflict()


def _materialize_material_plan_v2(
    session, journal: _TransactionJournalV1, plan: MaterialPlanV2,
) -> tuple[PayloadRecordV1, ...]:
    """Expand one committed plan exactly, reusing every matching object."""
    header = journal.read_state().header
    if not isinstance(header, TransactionHeaderV2):
        raise _conflict()
    journal._require_plan_header_v2(plan, header)
    committed = journal._read_material_plan_v2(header)
    if plan != committed:
        raise _conflict()
    records: list[PayloadRecordV1] = []
    file_sequence = 1
    for entry in plan.entries:
        components = journal.root_components + tuple(
            entry.relative_path.split("/")
        )
        role = _material_plan_role_v2(entry.confidentiality)
        if entry.object_type is PayloadObjectTypeV1.directory:
            _ensure_material_plan_directory_v2(session, components, role)
            size = None
            digest = None
        else:
            payload = entry.payload
            if payload is None:
                raise _conflict()
            _ensure_material_plan_file_v2(
                session, journal, components, payload, role,
                object_sequence=file_sequence,
            )
            file_sequence += 1
            size = len(payload)
            digest = hashlib.sha256(payload).hexdigest()
        records.append(PayloadRecordV1(
            relative_path=entry.relative_path,
            object_type=entry.object_type,
            confidentiality=entry.confidentiality,
            size=size,
            sha256=digest,
            platform_identity=_platform_identity_v1(
                _identity_v1(session, components)
            ),
        ))
    _require_material_plan_inventory_v2(session, journal, plan)
    return _ordered_payload_records(records)


def _ensure_material_plan_directory_v2(session, components, role) -> None:
    name = components[-1]
    with _translated():
        present = {entry.name for entry in session._inventory_state(components[:-1])}
        if name not in present:
            session.create_directory_exclusive(components, role=role)


def _ensure_material_plan_file_v2(
    session, journal: _TransactionJournalV1, components: tuple[str, ...],
    payload: bytes, role, *, object_sequence: int,
) -> None:
    parent = components[:-1]
    final = components[-1]
    pending = _payload_pending_name_v1(
        object_sequence, journal.transaction_id,
    )
    with _translated():
        names = set(session.inventory(parent))
    if final in names and pending in names:
        raise _reject("birth_provisioning_recovery_ambiguous")
    if final not in names and pending in names:
        try:
            with _translated():
                observed = session.read_file(
                    parent + (pending,), maximum=len(payload), role=role,
                )
        except BirthProvisioningError:
            # A file that does not fit the exact bound is not a torn prefix.
            raise _conflict() from None
        if observed == payload:
            with _translated():
                session.rename_no_replace(
                    parent + (pending,), components, directory=False,
                )
        elif len(observed) < len(payload) and payload.startswith(observed):
            journal._discard_pending_by_name(
                parent, pending, role=role, maximum=len(payload),
            )
        else:
            # Preserve complete conflicting evidence.
            raise _conflict()
    with _translated():
        names = set(session.inventory(parent))
    if final not in names:
        journal.publish_payload(
            parent, final, payload, role=role,
            object_sequence=object_sequence,
        )
    with _translated():
        observed = session.read_file(
            components, maximum=len(payload), role=role,
        )
    if observed != payload:
        raise _reject("birth_provisioning_recovery_ambiguous")


def _require_material_plan_inventory_v2(
    session, journal: _TransactionJournalV1, plan: MaterialPlanV2,
) -> None:
    children: dict[str, set[str]] = {}
    directories = {
        entry.relative_path
        for entry in plan.entries
        if entry.object_type is PayloadObjectTypeV1.directory
    }
    for entry in plan.entries:
        if "/" not in entry.relative_path:
            continue
        parent, name = entry.relative_path.rsplit("/", 1)
        children.setdefault(parent, set()).add(name)
    for relative in directories:
        components = journal.root_components + tuple(relative.split("/"))
        with _translated():
            observed = set(session.inventory(components))
        if observed != children.get(relative, set()):
            raise _reject("birth_provisioning_recovery_ambiguous")


def _build_material_plan_v2(
    session, journal: _TransactionJournalV1, layout,
    previous_set: object, distribution: object,
) -> MaterialPlanV2:
    """Freeze a complete new set from the installed author and target release."""
    from executor_birth_distribution_manifest import is_verified_distribution
    from executor_birth_keystore import raw_public_key
    from executor_birth_prepared_set import is_prepared_set_v1

    header = journal.read_state().header
    if (
        not isinstance(header, TransactionHeaderV2)
        or not is_prepared_set_v1(previous_set)
        or not is_verified_distribution(distribution)
        or header.previous_set_id != previous_set.set_id
        or header.closed_build_id != distribution.identity.closed_build_id
        or header.distribution_payload_hash
        != "sha256:" + hashlib.sha256(distribution.encoded).hexdigest()
        or header.distribution_signature_hash
        != "sha256:" + hashlib.sha256(distribution.signature).hexdigest()
        or header.source_inventory_hash
        != provisioning_source_inventory_hash_v2(distribution)
    ):
        raise _conflict()
    author = verify_author_store_v1(
        session, (AUTHOR_STORE_BASENAME_V1,), None,
    )
    author_publics = {
        key_id: raw_public_key(key)
        for key_id, key in author.verifier_keys.items()
    }
    if (
        author.active_key_id != previous_set.author_active_key_id
        or tuple(sorted(author_publics))
        != previous_set.author_verifier_key_ids
    ):
        raise _reject("birth_author_keystore_existing_invalid")
    inputs = acquire_operator_inputs_v1(layout.operator_input)
    catalog = producer_catalog_v1()
    admission_key_id, admission_private, admission_publics = _generate_keypair_v1()
    producer_material: dict[str, tuple[str, bytes, dict[str, bytes]]] = {}
    generated = list(admission_publics.values())
    for producer_id, operation in catalog:
        name = producer_store_name_v1(producer_id, operation)
        if name in producer_material:
            raise _conflict()
        material = _generate_keypair_v1()
        producer_material[name] = material
        generated.extend(material[2].values())
    _require_separated_authority_keys_v1(
        author_publics=author_publics, generated=generated, inputs=inputs,
    )
    registry = _planned_authority_registry_v2(
        inputs, admission_key_id, admission_publics, producer_material,
    )
    prepared = _prepare_verified_admission_context_v2(
        distribution, registry,
    )
    sandbox_document = measure_sandbox_backend_v1()
    digests = empty_digests_v1()
    digests.update({
        "approval_input_sha256": inputs.approval_sha256,
        "semantic_input_sha256": inputs.semantic_sha256,
        "producer_catalog_sha256": producer_catalog_sha256_v1(catalog),
        "context_source_inventory_sha256": prepared.source_inventory_sha256,
        "author_store_public_inventory_sha256": (
            author_store_public_inventory_sha256_v1(author_publics)
        ),
        "context_material_sha256": prepared.material_sha256,
    })
    set_document, _set_id = build_set_document_v1(
        transaction_id=journal.transaction_id,
        provisioner_build_id=header.provisioner_build_id,
        author={
            "active_key_id": author.active_key_id,
            "verifier_key_ids": sorted(author_publics),
        },
        registry=registry,
        catalog=catalog,
        digests=digests,
        prepared=prepared,
        approval_document=inputs.approval_document,
        semantic_document=inputs.semantic_document,
        sandbox_document=sandbox_document,
    )
    digests["set_json_sha256"] = hashlib.sha256(set_document).hexdigest()
    entries: list[MaterialPlanEntryV2] = []

    def directory(relative: str, confidentiality) -> None:
        entries.append(MaterialPlanEntryV2(
            relative, PayloadObjectTypeV1.directory, confidentiality, None,
        ))

    def file(relative: str, confidentiality, payload: bytes) -> None:
        entries.append(MaterialPlanEntryV2(
            relative, PayloadObjectTypeV1.file, confidentiality, payload,
        ))

    integrity = PayloadConfidentialityV1.integrity_only
    confidential = PayloadConfidentialityV1.confidential
    directory(AUTHORITY_SET_BASENAME_V1, integrity)
    directory(f"{AUTHORITY_SET_BASENAME_V1}/producers", integrity)
    directory(f"{AUTHORITY_SET_BASENAME_V1}/approval", integrity)
    directory(f"{AUTHORITY_SET_BASENAME_V1}/semantic", integrity)
    directory(f"{AUTHORITY_SET_BASENAME_V1}/semantic/public", integrity)
    directory(f"{AUTHORITY_SET_BASENAME_V1}/semantic/evidence", integrity)
    directory(
        f"{AUTHORITY_SET_BASENAME_V1}/{SANDBOX_CONTAINER_BASENAME_V1}",
        integrity,
    )
    directory(
        f"{AUTHORITY_SET_BASENAME_V1}/{CONTEXT_CONTAINER_BASENAME_V1}",
        integrity,
    )
    _add_planned_keystore_v2(
        entries, f"{AUTHORITY_SET_BASENAME_V1}/admission",
        admission_key_id, admission_private, admission_publics,
    )
    for name, (key_id, private_raw, publics) in producer_material.items():
        _add_planned_keystore_v2(
            entries, f"{AUTHORITY_SET_BASENAME_V1}/producers/{name}",
            key_id, private_raw, publics,
        )
    file(
        f"{AUTHORITY_SET_BASENAME_V1}/approval/authority.json", integrity,
        inputs.approval_document,
    )
    file(
        f"{AUTHORITY_SET_BASENAME_V1}/semantic/authority.json", integrity,
        inputs.semantic_document,
    )
    for name, public in inputs.semantic_publics.items():
        file(
            f"{AUTHORITY_SET_BASENAME_V1}/semantic/public/{name}",
            integrity, public,
        )
    file(
        f"{AUTHORITY_SET_BASENAME_V1}/{SANDBOX_CONTAINER_BASENAME_V1}/"
        f"{SANDBOX_REGISTRY_BASENAME_V1}",
        integrity, sandbox_document,
    )
    file(
        f"{AUTHORITY_SET_BASENAME_V1}/{CONTEXT_CONTAINER_BASENAME_V1}/"
        f"{CONTEXT_MATERIAL_BASENAME_V1}",
        integrity, prepared.document,
    )
    file(
        f"{AUTHORITY_SET_BASENAME_V1}/{SET_DOCUMENT_BASENAME_V1}",
        integrity, set_document,
    )
    return MaterialPlanV2(
        transaction_id=journal.transaction_id,
        transaction_header_sha256=hashlib.sha256(header.encode()).hexdigest(),
        entries=tuple(sorted(entries, key=lambda item: item.sort_key)),
    )


def _add_planned_keystore_v2(
    entries: list[MaterialPlanEntryV2], base: str, active_key_id: str,
    active_private: bytes, publics: Mapping[str, bytes],
) -> None:
    confidential = PayloadConfidentialityV1.confidential
    integrity = PayloadConfidentialityV1.integrity_only
    entries.extend((
        MaterialPlanEntryV2(
            base, PayloadObjectTypeV1.directory, confidential, None,
        ),
        MaterialPlanEntryV2(
            f"{base}/private", PayloadObjectTypeV1.directory,
            confidential, None,
        ),
        MaterialPlanEntryV2(
            f"{base}/public", PayloadObjectTypeV1.directory,
            integrity, None,
        ),
        MaterialPlanEntryV2(
            f"{base}/birth-keystore.lock", PayloadObjectTypeV1.file,
            confidential, b"0",
        ),
        MaterialPlanEntryV2(
            f"{base}/keystore.json", PayloadObjectTypeV1.file,
            confidential, keystore_config_v1(active_key_id, publics),
        ),
        MaterialPlanEntryV2(
            f"{base}/private/{active_key_id}.key", PayloadObjectTypeV1.file,
            confidential, active_private,
        ),
    ))
    entries.extend(
        MaterialPlanEntryV2(
            f"{base}/public/{key_id}.pub", PayloadObjectTypeV1.file,
            integrity, publics[key_id],
        )
        for key_id in sorted(publics)
    )


def _planned_authority_registry_v2(
    inputs: OperatorInputsV1, admission_key_id: str,
    admission_publics: Mapping[str, bytes],
    producer_material: Mapping[str, tuple[str, bytes, Mapping[str, bytes]]],
) -> dict[str, object]:
    from executor_birth_approval_authority import _decode_approval_authority
    from executor_birth_keystore import raw_public_key

    def store(active_key_id: str, publics: Mapping[str, bytes]):
        return {
            "active_key_id": active_key_id,
            "verifier_key_ids": sorted(publics),
            "public_keys": {
                key_id: publics[key_id].hex() for key_id in sorted(publics)
            },
        }

    approval = _decode_approval_authority(inputs.approval_document)
    semantic = decode_canonical_document_v1(inputs.semantic_document)
    return {
        "admission": store(admission_key_id, admission_publics),
        "producers": {
            name: store(key_id, publics)
            for name, (key_id, _private, publics) in producer_material.items()
        },
        "approval": {
            "revision": approval.revision,
            "keys": {
                key_id: raw_public_key(key).hex()
                for key_id, key in sorted(approval.keys.items())
            },
            "actors": {
                actor: {
                    "key_ids": sorted(entry["key_ids"]),
                    "scopes": sorted(entry["scopes"]),
                }
                for actor, entry in sorted(approval.actors.items())
            },
        },
        "semantic": {
            key_id: spec["status"]
            for key_id, spec in sorted(semantic["verifiers"].items())
        },
    }


def _prepare_verified_admission_context_v2(
    distribution: object, authority_registry: Mapping[str, object],
) -> PreparedContextMaterialV1:
    from executor_birth_prepared_root import (
        PreparedRootError, _open_distribution_sources_for_verified_v1,
    )

    try:
        sources = _open_distribution_sources_for_verified_v1(distribution)
    except PreparedRootError as exc:
        raise BirthProvisioningError(exc.code, exc) from None
    try:
        return prepare_context_material_v1(sources, authority_registry)
    except ContextMaterialError as exc:
        raise BirthProvisioningError(exc.code, exc) from None
    finally:
        sources.close()


def _prepare_staged_authority_set_v2(
    session, layout, expected_header: TransactionHeaderV2,
    previous_set: object, distribution: object,
) -> CheckpointV1:
    """Converge one V2 transaction on a verified staged authority set."""
    journal = _TransactionJournalV1.transition_v2(
        session, expected_header.transaction_id,
    )
    state = journal.recover_header(expected_header, journal.read_state())
    if state.header != expected_header:
        raise _conflict()
    journal.ensure_checkpoints()
    state = journal.recover_checkpoint_pending(journal.read_state())
    zero = CheckpointV1(
        expected_header.transaction_id, 0, None, ProvisioningStateV1.created,
        (), empty_digests_v1(), None,
    )
    last = state.last
    if last is None:
        journal.append(zero)
        last = zero
    elif last != zero and last.state is not ProvisioningStateV1.verified:
        raise _reject("birth_provisioning_recovery_ambiguous")
    plan = journal.ensure_material_plan_v2(lambda: _build_material_plan_v2(
        session, journal, layout, previous_set, distribution,
    ))
    records = _materialize_material_plan_v2(session, journal, plan)
    digests, set_id = _staged_material_digests_v2(
        session, journal, previous_set,
    )
    candidate = CheckpointV1(
        expected_header.transaction_id, 1, zero.digest(),
        ProvisioningStateV1.verified, records, digests, set_id,
    )
    if last.state is ProvisioningStateV1.verified:
        if last != candidate or len(state.chain) != 2:
            raise _reject("birth_provisioning_recovery_ambiguous")
        return last
    journal.append(candidate)
    committed = journal.read_state()
    if committed.last != candidate or len(committed.chain) != 2:
        raise _reject("birth_provisioning_io_unavailable")
    return candidate


def _staged_material_digests_v2(
    session, journal: _TransactionJournalV1, previous_set: object,
) -> tuple[dict[str, str | None], str]:
    from executor_birth_keystore import raw_public_key
    from executor_birth_prepared_set import SET_FIELDS_V1, is_prepared_set_v1

    if not is_prepared_set_v1(previous_set):
        raise _conflict()
    base = journal.root_components + (AUTHORITY_SET_BASENAME_V1,)
    set_payload = _read_set_document_v1(
        session, base + (SET_DOCUMENT_BASENAME_V1,),
    )
    set_document = decode_canonical_document_v1(set_payload)
    context_payload = _read_set_document_v1(
        session,
        base + (CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1),
    )
    author = verify_author_store_v1(
        session, (AUTHOR_STORE_BASENAME_V1,), None,
    )
    author_publics = {
        key_id: raw_public_key(key)
        for key_id, key in author.verifier_keys.items()
    }
    set_id = set_document.get("set_id")
    unsigned = dict(set_document)
    unsigned.pop("set_id", None)
    if (
        set(set_document) != SET_FIELDS_V1
        or set_document["schema_version"] != 1
        or set_document["state"] != "complete"
        or not _is_hex(set_id, 64)
        or set_id != hashlib.sha256(
            SET_ID_DIGEST_DOMAIN_V1
            + encode_canonical_document_v1(unsigned)
        ).hexdigest()
        or set_document["provisioning_transaction_id"]
        != journal.transaction_id
        or set_document["author_active_key_id"]
        != previous_set.author_active_key_id
        or tuple(set_document["author_verifier_key_ids"])
        != previous_set.author_verifier_key_ids
        or set_document["context_material_sha256"]
        != hashlib.sha256(context_payload).hexdigest()
    ):
        raise _reject("birth_authority_set_conflict")
    producer_keys = set_document["producer_keys"]
    if (
        not isinstance(producer_keys, dict)
        or any(
            not isinstance(entry, dict)
            or set(entry) != {
                "store_name", "active_key_id", "verifier_key_ids",
            }
            or not isinstance(entry["store_name"], str)
            or not isinstance(entry["active_key_id"], str)
            or not isinstance(entry["verifier_key_ids"], list)
            for entry in producer_keys.values()
        )
    ):
        raise _reject("birth_authority_set_conflict")
    staged = StagedAuthoritySetV1(
        payload_inventory=(),
        admission_key_id=set_document["admission_active_key_id"],
        producer_key_ids={
            entry["store_name"]: entry["active_key_id"]
            for entry in producer_keys.values()
        },
        next_object_sequence=0,
    )
    if len(staged.producer_key_ids) != len(producer_keys):
        raise _reject("birth_authority_set_conflict")
    verify_authority_set_v1(session, base, staged)
    digests = empty_digests_v1()
    digests.update({
        "approval_input_sha256": set_document["approval_input_sha256"],
        "semantic_input_sha256": set_document["semantic_input_sha256"],
        "producer_catalog_sha256": set_document["producer_catalog_sha256"],
        "context_source_inventory_sha256": (
            set_document["context_source_inventory_sha256"]
        ),
        "author_store_public_inventory_sha256": (
            author_store_public_inventory_sha256_v1(author_publics)
        ),
        "set_json_sha256": hashlib.sha256(set_payload).hexdigest(),
        "context_material_sha256": set_document["context_material_sha256"],
    })
    return digests, set_id


@contextmanager
def _translated():
    """Present a filesystem refusal under the single public provisioning type.

    The stable code is already the one section 11 declares, so it travels
    unchanged: only the type is unified, and the original stays private.
    """
    from executor_birth_secure_fs import BirthSecureFSError

    try:
        yield
    except BirthSecureFSError as exc:
        raise BirthProvisioningError(exc.code, exc) from None


AUTHOR_PRIVATE_BASENAME_V1 = "author_priv.bin"
PUBLIC_SUFFIX_V1 = "_pub.bin"
AUTHOR_PUBLIC_BASENAME_V1 = "author" + PUBLIC_SUFFIX_V1
RAW_KEY_BYTES_V1 = 32
AUTHOR_SOURCE_DIGEST_DOMAIN_V1 = (
    b"metnos.executor-birth.author-source-public-inventory/v1\0"
)


@dataclass(frozen=True, slots=True)
class AuthorSourceV1:
    """The previous author identity, exactly as the fixed names declare it.

    Only the default private key travels; every other private key of the old
    registry is neither read nor copied (section 4.2).
    """

    active_key_id: str
    active_private: bytes
    publics: Mapping[str, bytes]
    inventory_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "publics", MappingProxyType(dict(self.publics)))


def acquire_author_source_v1(source_session) -> AuthorSourceV1:
    """Read the previous author identity from the fixed names alone.

    The enumeration is the authority on what exists: ``sign.list_trusted_publics``
    is not called, because it drops an invalid file in silence, and a malformed
    public key here is a refusal rather than a key fewer.
    """
    from executor_birth_keystore import birth_key_id

    with _translated():
        entries = {item.name: item for item in source_session._inventory_state(())}
    regular = {
        name: entry
        for name, entry in entries.items()
        if entry.kind.value == "regular_file"
    }
    for required in (AUTHOR_PRIVATE_BASENAME_V1, AUTHOR_PUBLIC_BASENAME_V1):
        if required not in regular:
            raise _reject("birth_author_identity_incomplete")
    publics: dict[str, bytes] = {}
    records: list[dict[str, str]] = []
    for name in sorted(
        (item for item in entries if item.endswith(PUBLIC_SUFFIX_V1)),
        key=lambda item: item.encode("utf-8"),
    ):
        entry = entries[name]
        if (
            entry.kind.value != "regular_file"
            or entry.links != 1
            or entry.size != RAW_KEY_BYTES_V1
        ):
            raise _reject("birth_author_source_invalid")
        raw = _read_raw_key(source_session, name, private=False)
        publics[birth_key_id(raw)] = raw
        records.append(
            {"name": name, "sha256": hashlib.sha256(raw).hexdigest()}
        )
    private_entry = regular[AUTHOR_PRIVATE_BASENAME_V1]
    if private_entry.links != 1 or private_entry.size != RAW_KEY_BYTES_V1:
        raise _reject("birth_author_identity_incomplete")
    private_raw = _read_raw_key(
        source_session, AUTHOR_PRIVATE_BASENAME_V1, private=True,
    )
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    try:
        derived = Ed25519PrivateKey.from_private_bytes(private_raw).public_key(
        ).public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    except ValueError as exc:
        raise _reject("birth_author_identity_mismatch", exc) from None
    active_key_id = birth_key_id(derived)
    declared = publics.get(active_key_id)
    if declared is None or not hmac.compare_digest(declared, derived):
        raise _reject("birth_author_identity_mismatch")
    return AuthorSourceV1(
        active_key_id=active_key_id,
        active_private=private_raw,
        publics=publics,
        inventory_sha256=hashlib.sha256(
            AUTHOR_SOURCE_DIGEST_DOMAIN_V1
            + encode_canonical_document_v1(records)
        ).hexdigest(),
    )


def _read_raw_key(source_session, name: str, *, private: bool) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey,
    )

    with _translated():
        raw = source_session.read_file(
            (name,), maximum=RAW_KEY_BYTES_V1, exact_private=private,
        )
    if len(raw) != RAW_KEY_BYTES_V1:
        raise _reject(
            "birth_author_identity_incomplete" if private
            else "birth_author_source_invalid"
        )
    try:
        if private:
            Ed25519PrivateKey.from_private_bytes(raw)
        else:
            Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise _reject(
            "birth_author_identity_mismatch" if private
            else "birth_author_source_invalid", exc,
        ) from None
    return raw


AUTHOR_STORE_BASENAME_V1 = "author-root-v1"
AUTHOR_STORE_DIGEST_DOMAIN_V1 = (
    b"metnos.executor-birth.author-store-public-inventory/v1\0"
)


@dataclass(frozen=True, slots=True)
class StagedAuthorStoreV1:
    """What the staged author store contributes to the journal."""

    payload_inventory: tuple[PayloadRecordV1, ...]
    public_inventory_sha256: str
    next_object_sequence: int


def _platform_identity_v1(identity) -> PlatformIdentityV1:
    """Translate one filesystem identity into the journal's typed form."""
    if os.name == "nt":
        return PlatformIdentityV1(
            "windows", volume_serial=identity.volume, file_id=identity.object_id,
        )
    return PlatformIdentityV1(
        "posix", device=int(identity.volume, 16), inode=int(identity.object_id, 16),
    )


def author_store_public_inventory_sha256_v1(publics: Mapping[str, bytes]) -> str:
    """Digest of the public ring alone, independent of where it is stored."""
    return hashlib.sha256(
        AUTHOR_STORE_DIGEST_DOMAIN_V1 + encode_canonical_document_v1([
            {"key_id": key_id, "sha256": hashlib.sha256(publics[key_id]).hexdigest()}
            for key_id in sorted(publics)
        ])
    ).hexdigest()


def keystore_config_v1(active_key_id: str, publics: Mapping[str, bytes]) -> bytes:
    """The store configuration the productive loader already validates."""
    from executor_birth_keystore import SCHEMA_VERSION

    if active_key_id not in publics:
        raise _reject("birth_author_identity_mismatch")
    return encode_canonical_document_v1({
        "active_key_id": active_key_id,
        "config_revision": 1,
        "keys": [
            {
                "key_id": key_id,
                "public_file": f"public/{key_id}.pub",
                "status": "active" if key_id == active_key_id else "verifier",
            }
            for key_id in sorted(publics)
        ],
        "private_file": f"private/{active_key_id}.key",
        "schema_version": SCHEMA_VERSION,
    })


def _stage_author_store_v1(
    session,
    journal: "_TransactionJournalV1",
    source: AuthorSourceV1,
    *,
    first_object_sequence: int = 0,
) -> StagedAuthorStoreV1:
    """Build the author store inside the transaction, never in a temporary place.

    The private key is written under the confidential profile of the layout, so
    it never exists outside an object the transaction owns (section 6.1 applies
    the same rule to every generated key).
    """
    records, sequence = _stage_keystore_v1(
        session, journal, journal.root_components + (AUTHOR_STORE_BASENAME_V1,),
        active_key_id=source.active_key_id,
        active_private=source.active_private,
        publics=source.publics,
        first_object_sequence=first_object_sequence,
    )
    return StagedAuthorStoreV1(
        payload_inventory=tuple(records),
        public_inventory_sha256=author_store_public_inventory_sha256_v1(
            source.publics
        ),
        next_object_sequence=sequence,
    )


def _stage_keystore_v1(
    session,
    journal: "_TransactionJournalV1",
    base: tuple[str, ...],
    *,
    active_key_id: str,
    active_private: bytes,
    publics: Mapping[str, bytes],
    first_object_sequence: int,
) -> tuple[list[PayloadRecordV1], int]:
    """Build one V1 key store inside the transaction.

    Every store of the layout has the same shape, so it has one builder: the
    author root, Admission and each Producer differ in their keys, never in
    their form.
    """
    from executor_birth_keystore import CONFIG_BASENAME, LOCK_BASENAME
    from executor_birth_secure_fs import _BirthObjectRole, _LOCK_BYTE

    confidential = _BirthObjectRole.birth_confidential
    integrity = _BirthObjectRole.birth_integrity_only
    records = _create_directories_v1(session, journal, (
        (base, confidential),
        (base + ("private",), confidential),
        (base + ("public",), integrity),
    ))
    files: list[tuple[tuple[str, ...], str, bytes, object]] = [
        (base + ("public",), f"{key_id}.pub", publics[key_id], integrity)
        for key_id in sorted(publics)
    ]
    files.append(
        (base + ("private",), f"{active_key_id}.key", active_private, confidential)
    )
    files.append((
        base, CONFIG_BASENAME,
        keystore_config_v1(active_key_id, publics), confidential,
    ))
    # The store lock is a payload like any other: the loader takes a shared
    # lock on it before reading, so it must already carry the marker byte the
    # capability writes when it creates one itself.
    files.append((base, LOCK_BASENAME, _LOCK_BYTE, confidential))
    written, sequence = _publish_files_v1(
        session, journal, files, first_object_sequence,
    )
    return records + written, sequence


def _create_directories_v1(
    session, journal: "_TransactionJournalV1", requested,
) -> list[PayloadRecordV1]:
    records: list[PayloadRecordV1] = []
    with _translated():
        for components, role in requested:
            session.create_directory_exclusive(components, role=role)
            records.append(PayloadRecordV1(
                _transaction_relative_v1(journal, components),
                PayloadObjectTypeV1.directory,
                _confidentiality_v1(role), None, None,
                _platform_identity_v1(_identity_v1(session, components)),
            ))
    return records


def _publish_files_v1(
    session, journal: "_TransactionJournalV1", files, sequence: int,
) -> tuple[list[PayloadRecordV1], int]:
    records: list[PayloadRecordV1] = []
    for parent, name, payload, role in files:
        identity = journal.publish_payload(
            parent, name, payload, role=role, object_sequence=sequence,
        )
        sequence += 1
        records.append(PayloadRecordV1(
            _transaction_relative_v1(journal, parent + (name,)),
            PayloadObjectTypeV1.file,
            _confidentiality_v1(role), len(payload),
            hashlib.sha256(payload).hexdigest(),
            _platform_identity_v1(identity),
        ))
    return records, sequence


def _transaction_relative_v1(
    journal: "_TransactionJournalV1", components: tuple[str, ...],
) -> str:
    return "/".join(components[len(journal.root_components):])


def _identity_v1(session, components: tuple[str, ...]):
    """Identity of one object, observed from its authenticated parent.

    The directory capability deliberately reveals neither path nor handle, so
    the journal takes the identity from the enumeration of the parent, which is
    the same source the recovery uses when it reopens the object later.
    """
    name = components[-1]
    for entry in session._inventory_state(components[:-1]):
        if entry.name == name:
            return entry.identity
    raise _reject("birth_provisioning_io_unavailable")


def _confidentiality_v1(role) -> PayloadConfidentialityV1:
    from executor_birth_secure_fs import _BirthObjectRole

    return (
        PayloadConfidentialityV1.confidential
        if role is _BirthObjectRole.birth_confidential
        else PayloadConfidentialityV1.integrity_only
    )


def verify_author_store_v1(session, components: tuple[str, ...], source):
    """Re-read one store with the productive loader and compare the identity."""
    from executor_birth_keystore import (
        BirthKeyStoreError, _load_birth_keystore_in_session, raw_public_key,
    )

    try:
        loaded = _load_birth_keystore_in_session(tuple(components), session)
    except BirthKeyStoreError as exc:
        raise _reject("birth_author_keystore_existing_invalid", exc) from None
    except Exception as exc:
        code = getattr(exc, "code", None)
        raise _reject(
            code if isinstance(code, str) else "birth_author_keystore_existing_invalid",
            exc,
        ) from None
    observed = {
        key_id: raw_public_key(key) for key_id, key in loaded.verifier_keys.items()
    }
    if source is not None and (
        loaded.active_key_id != source.active_key_id
        or observed != dict(source.publics)
    ):
        raise _reject("birth_author_keystore_existing_invalid")
    return loaded


AUTHOR_SOURCE_BASENAME_V1 = "keys"


class AuthorProvisioningOutcomeV1(str, Enum):
    """What one provisioning run actually did, never what it hoped to do."""

    installed = "installed"
    already_installed = "already_installed"
    author_not_yet_created = "author_not_yet_created"


@dataclass(frozen=True, slots=True)
class AuthorProvisioningResultV1:
    outcome: AuthorProvisioningOutcomeV1
    active_key_id: str | None
    public_inventory_sha256: str | None
    transaction_id: str | None


@dataclass(frozen=True, slots=True)
class _FreshPublicationPassV1:
    """Durable handoff from staging to a new handle graph.

    Windows refuses the atomic rename of a directory while files below that
    directory are open.  Staging necessarily authenticates those descendants,
    so publication must start in another session after the ``verified``
    checkpoint is durable.  This private value crosses only the installer
    entry; it is neither a public outcome nor a journal state.
    """

    transaction_id: str


def _resolve_author_source_v1():
    """Open the previous author source from the one fixed name of section 4.2.

    The location belongs to the installer, exactly like the Birth root: the
    provisioner never receives it from a caller, an environment read at runtime
    or a document.  Absence is an ordinary state; a source that exists but
    cannot be opened safely is a refusal.
    """
    import config as runtime_config
    from executor_birth_secure_fs import BirthSecureFSError, _open_legacy_root_session

    path = Path(runtime_config.PATH_USER_CONFIG) / AUTHOR_SOURCE_BASENAME_V1
    try:
        return _open_legacy_root_session(path, exact_private=True)
    except BirthSecureFSError:
        if os.path.lexists(path):
            raise BirthProvisioningError(
                "birth_provisioning_acl_unsafe"
            ) from None
        return None


def _provision_prepared_authorities_v1(
    layout, *, provisioner_build_id: str,
) -> AuthorProvisioningResultV1 | _FreshPublicationPassV1:
    """Inspect first, migrate only when there is nothing installed yet.

    The census happens by handle under the exclusive lock and before any
    external input is opened (section 8.1).  A run that finds a valid author
    root does not open the previous source at all, which is what makes the
    second execution an inspection and not a second migration.
    """
    session = layout.birth_session
    with _translated():
        lock = session.global_lock(exclusive=True, create=True)
    with lock:
        with _translated():
            names = set(session.inventory(()))
        transactions = sorted(
            name for name in names if name.startswith(TRANSACTION_PREFIX_V1)
        )
        if len(transactions) > 1:
            raise _reject("birth_provisioning_recovery_ambiguous")
        installed = AUTHOR_STORE_BASENAME_V1 in names
        marker = PREPARED_MARKER_BASENAME_V1 in names
        if transactions:
            return _resume_author_root_v1(
                session, layout, transactions[0][len(TRANSACTION_PREFIX_V1):],
                provisioner_build_id=provisioner_build_id,
                installed=installed,
            )
        if marker:
            # Inspection only: with the three finals in place and no
            # transaction, no previous source and no operator input is opened.
            return _inspect_installed_v1(session, installed)
        if installed:
            raise _reject("birth_provisioning_recovery_ambiguous")
        return _provision_from_nothing_v1(
            session, layout, provisioner_build_id=provisioner_build_id,
        )


def _inspect_installed_v1(session, installed: bool) -> AuthorProvisioningResultV1:
    """Verify what is installed without opening anything external."""
    from executor_birth_keystore import raw_public_key

    if not installed:
        raise _reject("birth_authority_set_conflict")
    payload = _read_set_document_v1(session, (PREPARED_MARKER_BASENAME_V1,))
    marker = decode_canonical_document_v1(payload)
    if set(marker) != {
        "schema_version", "state", "set_id", "authority_set", "author_store",
        "author_store_public_inventory_sha256", "set_json_sha256",
        "context_material_sha256", "provisioner_build_id", "transaction_id",
    } or marker["state"] != PREPARED_MARKER_STATE_V1:
        raise _reject("birth_authority_set_conflict")
    author = verify_author_store_v1(session, (AUTHOR_STORE_BASENAME_V1,), None)
    observed = author_store_public_inventory_sha256_v1({
        key_id: raw_public_key(key)
        for key_id, key in author.verifier_keys.items()
    })
    if observed != marker["author_store_public_inventory_sha256"]:
        raise _reject("birth_author_keystore_existing_invalid")
    location = (AUTHORITY_SETS_BASENAME_V1, marker["set_id"])
    document = _read_set_document_v1(
        session, location + (SET_DOCUMENT_BASENAME_V1,),
    )
    if hashlib.sha256(document).hexdigest() != marker["set_json_sha256"]:
        raise _reject("birth_authority_set_conflict")
    material = _read_set_document_v1(
        session,
        location + (CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1),
    )
    if hashlib.sha256(material).hexdigest() != marker["context_material_sha256"]:
        raise _reject("birth_context_material_changed")
    return AuthorProvisioningResultV1(
        AuthorProvisioningOutcomeV1.already_installed,
        author.active_key_id,
        marker["author_store_public_inventory_sha256"],
        None,
    )


def _provision_from_nothing_v1(
    session, layout, *, provisioner_build_id: str,
) -> AuthorProvisioningResultV1 | _FreshPublicationPassV1:
    """Create the transaction and advance it as far as this increment goes."""
    source_session = _resolve_author_source_v1()
    if source_session is None:
        return AuthorProvisioningResultV1(
            AuthorProvisioningOutcomeV1.author_not_yet_created, None, None, None,
        )
    try:
        source = acquire_author_source_v1(source_session)
    finally:
        source_session.close()
    transaction_id = new_transaction_id_v1()
    journal = _TransactionJournalV1(session, transaction_id)
    journal.create_root()
    journal.write_header(
        TransactionHeaderV1(transaction_id, provisioner_build_id)
    )
    journal.ensure_checkpoints()
    zero = CheckpointV1(
        transaction_id, 0, None, ProvisioningStateV1.created, (),
        empty_digests_v1(), None,
    )
    journal.append(zero)
    last = _record_author_source_v1(journal, zero, source)
    last = _stage_and_record_v1(session, journal, last, source)
    last = _advance_to_authorities_v1(session, journal, layout, last)
    # Do not carry the staging handle graph into publication.  In particular,
    # Windows FileRenameInformation refuses a non-empty directory while a
    # descendant is open.  The verified checkpoint is the recovery boundary:
    # the entry closes this session and resumes the same transaction once.
    return _FreshPublicationPassV1(transaction_id)


def _record_author_source_v1(
    journal: "_TransactionJournalV1", previous: CheckpointV1, source,
) -> CheckpointV1:
    """Make the digest of what was acquired durable before building anything."""
    digests = dict(previous.digests)
    digests["author_source_public_inventory_sha256"] = source.inventory_sha256
    acquired = CheckpointV1(
        previous.transaction_id, previous.checkpoint_sequence + 1,
        previous.digest(), ProvisioningStateV1.created,
        previous.payload_inventory, digests, previous.set_id,
    )
    journal.append(acquired)
    return acquired


def _stage_and_record_v1(
    session, journal: "_TransactionJournalV1", previous: CheckpointV1, source,
) -> CheckpointV1:
    """Build the store inside the transaction and record what it contains."""
    staged = _stage_author_store_v1(
        session, journal, source,
        first_object_sequence=_next_object_sequence_v1(previous),
    )
    digests = dict(previous.digests)
    digests["author_store_public_inventory_sha256"] = (
        staged.public_inventory_sha256
    )
    checkpoint = CheckpointV1(
        previous.transaction_id, previous.checkpoint_sequence + 1,
        previous.digest(), ProvisioningStateV1.author_staged,
        staged.payload_inventory, digests, previous.set_id,
    )
    journal.append(checkpoint)
    verify_author_store_v1(
        session, journal.root_components + (AUTHOR_STORE_BASENAME_V1,), source,
    )
    return checkpoint


def _advance_to_authorities_v1(
    session, journal: "_TransactionJournalV1", layout, last: CheckpointV1,
) -> CheckpointV1:
    """Carry one transaction from a staged author to a staged authority set."""
    if state_rank_v1(last.state) < state_rank_v1(ProvisioningStateV1.inputs_staged):
        last = _record_operator_inputs_v1(journal, last, layout)
    if state_rank_v1(last.state) < state_rank_v1(
        ProvisioningStateV1.authorities_staged
    ):
        last = _stage_authorities_and_record_v1(session, journal, layout, last)
    if state_rank_v1(last.state) < state_rank_v1(
        ProvisioningStateV1.context_staged
    ):
        last = _stage_context_and_record_v1(session, journal, last)
    if state_rank_v1(last.state) < state_rank_v1(ProvisioningStateV1.verified):
        last = _write_set_and_record_v1(session, journal, last)
    return last


def _complete_provisioning_v1(
    session, journal: "_TransactionJournalV1", last: CheckpointV1,
    *, provisioner_build_id: str,
) -> CheckpointV1:
    """Publish the finals, verify them once more and drop the transaction."""
    last = _advance_to_installed_v1(
        session, journal, last, provisioner_build_id=provisioner_build_id,
    )
    _remove_transaction_v1(session, journal)
    return last


def _record_operator_inputs_v1(
    journal: "_TransactionJournalV1", previous: CheckpointV1, layout,
) -> CheckpointV1:
    """Acquire the public registries and make their digests durable."""
    inputs = acquire_operator_inputs_v1(layout.operator_input)
    catalog_digest = producer_catalog_sha256_v1(producer_catalog_v1())
    digests = dict(previous.digests)
    recorded = (
        ("approval_input_sha256", inputs.approval_sha256),
        ("semantic_input_sha256", inputs.semantic_sha256),
        ("producer_catalog_sha256", catalog_digest),
    )
    for field, value in recorded:
        if digests[field] not in (None, value):
            # An input that comes back different is a conflict, never a new
            # start: the transaction already promised these bytes.
            raise _conflict()
        digests[field] = value
    checkpoint = CheckpointV1(
        previous.transaction_id, previous.checkpoint_sequence + 1,
        previous.digest(), ProvisioningStateV1.inputs_staged,
        previous.payload_inventory, digests, previous.set_id,
    )
    journal.append(checkpoint)
    return checkpoint


def _stage_authorities_and_record_v1(
    session, journal: "_TransactionJournalV1", layout, previous: CheckpointV1,
) -> CheckpointV1:
    """Generate the authority set once and record everything it produced."""
    with _translated():
        present = set(session.inventory(journal.root_components))
    if AUTHORITY_SET_BASENAME_V1 in present:
        # A set half generated by an interrupted run is never adopted: the keys
        # inside it were never inventoried, and nothing final was published.
        raise _reject("birth_provisioning_recovery_ambiguous")
    inputs = acquire_operator_inputs_v1(layout.operator_input)
    if inputs.approval_sha256 != previous.digests["approval_input_sha256"]:
        raise _conflict()
    if inputs.semantic_sha256 != previous.digests["semantic_input_sha256"]:
        raise _conflict()
    catalog = producer_catalog_v1()
    if producer_catalog_sha256_v1(catalog) != previous.digests[
        "producer_catalog_sha256"
    ]:
        raise _conflict()
    author_publics = _staged_author_publics_v1(session, journal)
    staged = _stage_authority_set_v1(
        session, journal, inputs, catalog,
        author_publics=author_publics,
        first_object_sequence=_next_object_sequence_v1(previous),
    )
    verify_authority_set_v1(
        session,
        journal.root_components + (AUTHORITY_SET_BASENAME_V1,),
        staged,
    )
    checkpoint = CheckpointV1(
        previous.transaction_id, previous.checkpoint_sequence + 1,
        previous.digest(), ProvisioningStateV1.authorities_staged,
        previous.payload_inventory + staged.payload_inventory,
        dict(previous.digests), previous.set_id,
    )
    journal.append(checkpoint)
    return checkpoint


def _staged_author_publics_v1(session, journal: "_TransactionJournalV1"):
    """The public ring of the staged author root, read back from the store."""
    from executor_birth_keystore import raw_public_key

    loaded = verify_author_store_v1(
        session, journal.root_components + (AUTHOR_STORE_BASENAME_V1,), None,
    )
    return {
        key_id: raw_public_key(key)
        for key_id, key in loaded.verifier_keys.items()
    }


def _next_object_sequence_v1(checkpoint: CheckpointV1) -> int:
    """Continue the payload sequence where the recorded inventory left it."""
    return sum(
        1 for record in checkpoint.payload_inventory
        if record.object_type is PayloadObjectTypeV1.file
    )


def _resume_author_root_v1(
    session, layout, transaction_id: str, *, provisioner_build_id: str,
    installed: bool,
) -> AuthorProvisioningResultV1 | _FreshPublicationPassV1:
    """Continue the one recognised transaction from the bytes it already holds.

    From ``author_staged`` on, completion never consults the previous source
    again: everything the installation needs is inside the transaction, so a
    restart converges even when the old key directory has disappeared.
    """
    journal = _TransactionJournalV1(session, transaction_id)
    state = journal.recover_header(
        TransactionHeaderV1(transaction_id, provisioner_build_id),
        journal.read_state(),
    )
    header = state.header
    if header is None or header.provisioner_build_id != provisioner_build_id:
        raise _conflict()
    journal.ensure_checkpoints()
    state = journal.recover_checkpoint_pending(journal.read_state())
    last = state.last
    if last is None:
        # The stop happened before the first durable step, so the transaction
        # starts from its own zero rather than from a new nonce.
        zero = CheckpointV1(
            transaction_id, 0, None, ProvisioningStateV1.created, (),
            empty_digests_v1(), None,
        )
        journal.append(zero)
        last = zero
    if last.state is ProvisioningStateV1.created:
        last = _resume_before_staging_v1(
            session, journal, last, installed=installed,
        )
    if installed and state_rank_v1(last.state) < state_rank_v1(
        ProvisioningStateV1.verified
    ):
        # A final author root beside a transaction that has not been verified
        # yet is a state no conforming stop can produce.
        raise _reject("birth_provisioning_recovery_ambiguous")
    if state_rank_v1(last.state) < state_rank_v1(ProvisioningStateV1.verified):
        last = _advance_to_authorities_v1(session, journal, layout, last)
        # The same rule as a new transaction: after this durable checkpoint,
        # publication owns a fresh session and no staged descendant handle.
        return _FreshPublicationPassV1(transaction_id)
    last = _complete_provisioning_v1(
        session, journal, last, provisioner_build_id=provisioner_build_id,
    )
    # The identity is read back from the installed store, not remembered from
    # a run that may not be this one.
    loaded = verify_author_store_v1(session, (AUTHOR_STORE_BASENAME_V1,), None)
    return AuthorProvisioningResultV1(
        AuthorProvisioningOutcomeV1.installed,
        loaded.active_key_id,
        last.digests["author_store_public_inventory_sha256"],
        transaction_id,
    )


def _resume_before_staging_v1(
    session, journal: "_TransactionJournalV1", last: CheckpointV1, *,
    installed: bool,
) -> CheckpointV1:
    """Continue a transaction that has not staged any byte yet.

    Only the inputs that were not acquired are consulted again, and an input
    that comes back different is a conflict rather than a new start.  A store
    already half-built inside the transaction is not adopted here: refusing is
    safe, because nothing final has been published yet.
    """
    if installed or AUTHOR_STORE_BASENAME_V1 in set(
        session.inventory(journal.root_components)
    ):
        raise _reject("birth_provisioning_recovery_ambiguous")
    source_session = _resolve_author_source_v1()
    if source_session is None:
        raise _reject("birth_author_identity_incomplete")
    try:
        source = acquire_author_source_v1(source_session)
    finally:
        source_session.close()
    recorded = last.digests["author_source_public_inventory_sha256"]
    if recorded is not None and recorded != source.inventory_sha256:
        raise _conflict()
    acquired = (
        last if recorded is not None
        else _record_author_source_v1(journal, last, source)
    )
    return _stage_and_record_v1(session, journal, acquired, source)


OPERATOR_APPROVAL_BASENAME_V1 = "approval-authority.json"
OPERATOR_SEMANTIC_BASENAME_V1 = "semantic-authority.json"
OPERATOR_SEMANTIC_PUBLIC_BASENAME_V1 = "semantic-public"
SEMANTIC_EVIDENCE_BASENAME_V1 = "evidence"
OPERATOR_SEMANTIC_KEY_PREFIX_V1 = "public/"
MAXIMUM_OPERATOR_DOCUMENT_BYTES_V1 = 64 * 1024
APPROVAL_INPUT_DIGEST_DOMAIN_V1 = b"metnos.executor-birth.approval-input/v1\0"
SEMANTIC_INPUT_DIGEST_DOMAIN_V1 = b"metnos.executor-birth.semantic-input/v1\0"
PRODUCER_CATALOG_DIGEST_DOMAIN_V1 = (
    b"metnos.executor-birth.producer-catalog/v1\0"
)
PRODUCER_PATH_DIGEST_DOMAIN_V1 = (
    b"metnos.executor-birth.producer-capability-path/v1\0"
)


@dataclass(frozen=True, slots=True)
class OperatorInputsV1:
    """The two public registries the administrator installed, as bytes.

    No private key of the approver or of the semantic reviewer belongs here,
    in the authority set or in the Birth process (section 4.1).
    """

    approval_document: bytes
    approval_sha256: str
    semantic_document: bytes
    semantic_publics: Mapping[str, bytes]
    semantic_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "semantic_publics", MappingProxyType(dict(self.semantic_publics))
        )


def acquire_operator_inputs_v1(operator_input) -> OperatorInputsV1:
    """Read and validate the operator registries before anything is created.

    Absence and invalidity are different outcomes on purpose: an installation
    that never received the registries is incomplete, while one that received a
    malformed registry must not be completed by inventing a key
    (sections 6.3 and 6.4).
    """
    with _translated():
        names = set(operator_input.inventory())
    if OPERATOR_APPROVAL_BASENAME_V1 not in names:
        raise _reject("birth_approval_authority_input_missing")
    if OPERATOR_SEMANTIC_BASENAME_V1 not in names:
        raise _reject("birth_semantic_authority_input_missing")
    if names - {
        OPERATOR_APPROVAL_BASENAME_V1, OPERATOR_SEMANTIC_BASENAME_V1,
        OPERATOR_SEMANTIC_PUBLIC_BASENAME_V1,
    }:
        raise _reject("birth_provisioning_recovery_ambiguous")
    if OPERATOR_SEMANTIC_PUBLIC_BASENAME_V1 not in names:
        raise _reject("birth_semantic_authority_input_missing")
    approval = _read_operator_document(
        operator_input, OPERATOR_APPROVAL_BASENAME_V1,
    )
    _validate_approval_document_v1(approval)
    semantic = _read_operator_document(
        operator_input, OPERATOR_SEMANTIC_BASENAME_V1,
    )
    publics = _acquire_semantic_publics_v1(operator_input, semantic)
    return OperatorInputsV1(
        approval_document=approval,
        approval_sha256=hashlib.sha256(
            APPROVAL_INPUT_DIGEST_DOMAIN_V1 + approval
        ).hexdigest(),
        semantic_document=semantic,
        semantic_publics=publics,
        semantic_sha256=hashlib.sha256(
            SEMANTIC_INPUT_DIGEST_DOMAIN_V1 + semantic
            + encode_canonical_document_v1([
                {"name": name, "sha256": hashlib.sha256(publics[name]).hexdigest()}
                for name in sorted(publics)
            ])
        ).hexdigest(),
    )


def _read_operator_document(operator_input, name: str) -> bytes:
    with _translated():
        return operator_input.read_file(
            name, maximum=MAXIMUM_OPERATOR_DOCUMENT_BYTES_V1,
        )


def _validate_approval_document_v1(raw: bytes) -> None:
    """Decode the registry with the productive loader, never with a copy."""
    from executor_birth_approval import BirthApprovalError
    from executor_birth_approval_authority import _decode_approval_authority

    try:
        _decode_approval_authority(raw)
    except BirthApprovalError as exc:
        raise _reject("birth_approval_authority_invalid", exc) from None


def _acquire_semantic_publics_v1(operator_input, raw: bytes) -> dict[str, bytes]:
    """Validate the semantic authority and read every key it references."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from executor_birth_semantic_review import IndependentEvidenceKind

    try:
        document = decode_canonical_document_v1(raw)
    except BirthProvisioningError as exc:
        raise _reject("birth_semantic_authority_invalid", exc) from None
    if set(document) != {"evidence_dir", "verifiers", "versions", "owners"}:
        raise _reject("birth_semantic_authority_invalid")
    if document["evidence_dir"] != SEMANTIC_EVIDENCE_BASENAME_V1:
        raise _reject("birth_semantic_authority_invalid")
    kinds = {kind.value for kind in IndependentEvidenceKind}
    for field in ("versions", "owners"):
        entry = document[field]
        if not isinstance(entry, dict) or set(entry) != kinds:
            raise _reject("birth_semantic_authority_invalid")
        for item in entry.values():
            if not isinstance(item, list) or not item or any(
                not isinstance(value, str) or not value for value in item
            ):
                raise _reject("birth_semantic_authority_invalid")
    verifiers = document["verifiers"]
    if not isinstance(verifiers, dict) or not verifiers:
        raise _reject("birth_semantic_authority_invalid")
    with _translated():
        container = operator_input.open_directory(
            OPERATOR_SEMANTIC_PUBLIC_BASENAME_V1
        )
        present = set(container.inventory())
    publics: dict[str, bytes] = {}
    for key_id, spec in verifiers.items():
        if (
            not isinstance(key_id, str) or not key_id
            or not isinstance(spec, dict) or set(spec) != {"status", "path"}
            or spec["status"] not in {"active", "revoked"}
            or not isinstance(spec["path"], str)
        ):
            raise _reject("birth_semantic_authority_invalid")
        # The document names the key where the installed set will hold it, so
        # the one admitted form is the final relative one.
        prefix = OPERATOR_SEMANTIC_KEY_PREFIX_V1
        if not spec["path"].startswith(prefix):
            raise _reject("birth_semantic_authority_invalid")
        name = spec["path"][len(prefix):]
        if not name.endswith(".pub") or "/" in name or len(name) <= len(".pub"):
            raise _reject("birth_semantic_authority_invalid")
        if spec["status"] == "revoked":
            continue
        if name not in present:
            raise _reject("birth_semantic_authority_invalid")
        with _translated():
            raw_key = container.read_file(name, maximum=RAW_KEY_BYTES_V1)
        if len(raw_key) != RAW_KEY_BYTES_V1:
            raise _reject("birth_semantic_authority_invalid")
        try:
            Ed25519PublicKey.from_public_bytes(raw_key)
        except ValueError as exc:
            raise _reject("birth_semantic_authority_invalid", exc) from None
        if name in publics and publics[name] != raw_key:
            raise _reject("birth_semantic_authority_invalid")
        publics[name] = raw_key
    if not publics or present != set(publics):
        # A key nobody references is as much a defect as a missing one: the
        # location is an import, not a store of spare material.
        raise _reject("birth_semantic_authority_invalid")
    return publics


def producer_catalog_v1() -> tuple[tuple[str, str], ...]:
    """The closed capability catalogue, taken once from its sealed symbol."""
    from executor_birth_intent import _producer_capabilities_for_bootstrap

    catalog = tuple(
        (capability.producer_id, capability.operation)
        for capability in _producer_capabilities_for_bootstrap()
    )
    if len(set(catalog)) != len(catalog) or not catalog:
        raise _reject("birth_provisioning_transaction_conflict")
    return catalog


def producer_catalog_sha256_v1(catalog: Sequence[tuple[str, str]]) -> str:
    return hashlib.sha256(
        PRODUCER_CATALOG_DIGEST_DOMAIN_V1 + encode_canonical_document_v1([
            {"producer_id": producer, "operation": operation}
            for producer, operation in catalog
        ])
    ).hexdigest()


from executor_birth_producer_table_v1 import (  # noqa: E402
    producer_store_name_v1,
)


AUTHORITY_SET_BASENAME_V1 = "authority-set"


@dataclass(frozen=True, slots=True)
class StagedAuthoritySetV1:
    """What the staged authority set contributes to the journal."""

    payload_inventory: tuple[PayloadRecordV1, ...]
    admission_key_id: str
    producer_key_ids: Mapping[str, str]
    next_object_sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "producer_key_ids", MappingProxyType(dict(self.producer_key_ids))
        )


def _generate_keypair_v1() -> tuple[str, bytes, dict[str, bytes]]:
    """One fresh Ed25519 identity, generated inside the transaction alone."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from executor_birth_keystore import birth_key_id

    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = birth_key_id(public_raw)
    return key_id, private_raw, {key_id: public_raw}


def _stage_authority_set_v1(
    session,
    journal: "_TransactionJournalV1",
    inputs: OperatorInputsV1,
    catalog: Sequence[tuple[str, str]],
    *,
    author_publics: Mapping[str, bytes],
    first_object_sequence: int,
) -> StagedAuthoritySetV1:
    """Generate Admission and every Producer, and copy the public registries.

    Generation happens once and inside the transaction: a restart that finds a
    valid transaction reuses the same pair rather than making another one
    (section 6.1).  The registries are copied byte for byte; nothing here
    creates an approver or a reviewer key.
    """
    from executor_birth_secure_fs import _BirthObjectRole

    confidential = _BirthObjectRole.birth_confidential
    integrity = _BirthObjectRole.birth_integrity_only
    base = journal.root_components + (AUTHORITY_SET_BASENAME_V1,)
    records = _create_directories_v1(session, journal, (
        (base, integrity),
        (base + ("producers",), integrity),
        (base + ("approval",), integrity),
        (base + ("semantic",), integrity),
        (base + ("semantic", "public"), integrity),
        (base + ("semantic", SEMANTIC_EVIDENCE_BASENAME_V1), integrity),
        (base + (SANDBOX_CONTAINER_BASENAME_V1,), integrity),
    ))
    sequence = first_object_sequence

    admission_key_id, admission_private, admission_publics = _generate_keypair_v1()
    written, sequence = _stage_keystore_v1(
        session, journal, base + ("admission",),
        active_key_id=admission_key_id,
        active_private=admission_private,
        publics=admission_publics,
        first_object_sequence=sequence,
    )
    records.extend(written)

    producer_key_ids: dict[str, str] = {}
    # A list, not a map: two roles that shared the same bytes would collapse
    # into one entry of a map and the separation check would never see them.
    generated: list[bytes] = list(admission_publics.values())
    for producer_id, operation in catalog:
        name = producer_store_name_v1(producer_id, operation)
        if name in producer_key_ids:
            raise _conflict()
        key_id, private_raw, publics = _generate_keypair_v1()
        written, sequence = _stage_keystore_v1(
            session, journal, base + ("producers", name),
            active_key_id=key_id,
            active_private=private_raw,
            publics=publics,
            first_object_sequence=sequence,
        )
        records.extend(written)
        producer_key_ids[name] = key_id
        generated.extend(publics.values())

    files = [
        (base + ("approval",), "authority.json", inputs.approval_document, integrity),
        (base + ("semantic",), "authority.json", inputs.semantic_document, integrity),
        # A measurement of this machine, not an operator opinion: the two
        # programs that run a phase are named and digested here, once, while
        # the installer holds its single door.
        (base + (SANDBOX_CONTAINER_BASENAME_V1,), SANDBOX_REGISTRY_BASENAME_V1,
         measure_sandbox_backend_v1(), integrity),
    ]
    files.extend(
        (base + ("semantic", "public"), name, inputs.semantic_publics[name], integrity)
        for name in sorted(inputs.semantic_publics)
    )
    written, sequence = _publish_files_v1(session, journal, files, sequence)
    records.extend(written)

    _require_separated_authority_keys_v1(
        author_publics=author_publics, generated=generated, inputs=inputs,
    )
    return StagedAuthoritySetV1(
        payload_inventory=tuple(records),
        admission_key_id=admission_key_id,
        producer_key_ids=producer_key_ids,
        next_object_sequence=sequence,
    )


def _require_separated_authority_keys_v1(
    *,
    author_publics: Mapping[str, bytes],
    generated: Sequence[bytes],
    inputs: OperatorInputsV1,
) -> None:
    """No two cryptographic roles may share the same public bytes.

    The comparison is on the raw 32 bytes, not on a name or an identifier, so
    a key reused under a different label is still caught (section 5.2).
    """
    from executor_birth_approval_authority import _decode_approval_authority
    from executor_birth_keystore import raw_public_key

    seen: set[bytes] = set()
    approval = _decode_approval_authority(inputs.approval_document)
    groups = (
        tuple(author_publics.values()),
        tuple(generated),
        tuple(raw_public_key(key) for key in approval.keys.values()),
        tuple(inputs.semantic_publics.values()),
    )
    for group in groups:
        for raw in group:
            if raw in seen:
                raise _reject("birth_authority_key_reused")
            seen.add(raw)


def verify_authority_set_v1(
    session, base: tuple[str, ...], staged: StagedAuthoritySetV1,
) -> None:
    """Re-read every staged authority with the loader that will use it.

    A store that only looks right is not enough: the productive loader must
    prove the pair, the closed inventory, the single active key and the
    ordered ring, and the two registries must load through their own
    decoders (section 8.1, step 10).
    """
    from executor_birth_approval import BirthApprovalError
    from executor_birth_approval_authority import _load_approval_authority_in_session
    from executor_birth_keystore import BirthKeyStoreError, _load_birth_keystore_in_session
    from executor_birth_semantic_authority import (
        _load_semantic_authority_in_session,
    )
    from executor_birth_semantic_review import SemanticReviewError

    expected = {"admission": staged.admission_key_id}
    expected.update(
        (f"producers/{name}", key_id)
        for name, key_id in staged.producer_key_ids.items()
    )
    for relative, key_id in expected.items():
        components = base + tuple(relative.split("/"))
        try:
            loaded = _load_birth_keystore_in_session(components, session)
        except BirthKeyStoreError as exc:
            raise _reject("birth_author_keystore_existing_invalid", exc) from None
        if loaded.active_key_id != key_id or set(loaded.verifier_keys) != {key_id}:
            raise _reject("birth_author_keystore_existing_invalid")
    try:
        _load_approval_authority_in_session(
            base + ("approval", "authority.json"), session,
        )
    except BirthApprovalError as exc:
        raise _reject("birth_approval_authority_invalid", exc) from None
    try:
        _load_semantic_authority_in_session(
            base + ("semantic", "authority.json"),
            base + ("semantic", "public"),
            base + ("semantic", SEMANTIC_EVIDENCE_BASENAME_V1),
            session,
        )
    except SemanticReviewError as exc:
        raise _reject("birth_semantic_authority_invalid", exc) from None


from executor_birth_context_v1 import (  # noqa: E402
    CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1,
    ContextMaterialError, PreparedContextMaterialV1,
    prepare_context_material_v1,
)
from executor_birth_sandbox_registry_v1 import (
    SANDBOX_CONTAINER_BASENAME_V1, SANDBOX_REGISTRY_BASENAME_V1,
    measure_sandbox_backend_v1,
)


def _resolve_context_sources_v1():
    """Open the installed runtime directory as a read-only source."""
    import config as runtime_config
    from executor_birth_secure_fs import BirthSecureFSError, _open_legacy_root_session

    path = Path(runtime_config.PATH_RUNTIME)
    try:
        return _open_legacy_root_session(path, exact_private=False)
    except BirthSecureFSError as exc:
        # An absent distribution is an incomplete catalogue; one that exists
        # but cannot be opened safely keeps the refusal of the capability,
        # because a source anyone may rewrite is not a source.
        if os.path.lexists(path):
            raise BirthProvisioningError(exc.code, exc) from None
        raise _reject("birth_context_catalog_incomplete", exc) from None


def _prepare_installed_admission_context_v1(
    authority_registry: Mapping[str, object],
) -> PreparedContextMaterialV1:
    """Open the distribution and hand it to the shared factory.

    The catalogue and the freezing live in the runtime module, because the
    runtime has to rebuild the very same material under its own barrier and two
    implementations of one digest would diverge unnoticed.  What belongs here
    is only the authority to open the source.
    """
    sources = _resolve_context_sources_v1()
    try:
        return prepare_context_material_v1(sources, authority_registry)
    except ContextMaterialError as exc:
        raise BirthProvisioningError(exc.code, exc) from None
    finally:
        sources.close()


SET_DOCUMENT_BASENAME_V1 = "set.json"
SET_ID_DIGEST_DOMAIN_V1 = b"metnos.executor-birth.authority-set/v1\0"


from executor_birth_prepared_set import (  # noqa: E402
    authority_registry_v1 as _authority_registry_v1,
    read_document_v1 as _read_set_document_v1,
)


def build_set_document_v1(
    *,
    transaction_id: str,
    provisioner_build_id: str,
    author: Mapping[str, object],
    registry: Mapping[str, object],
    catalog: Sequence[tuple[str, str]],
    digests: Mapping[str, str | None],
    prepared: PreparedContextMaterialV1,
    approval_document: bytes,
    semantic_document: bytes,
    sandbox_document: bytes,
) -> tuple[bytes, str]:
    """Build ``set.json`` and derive the immutable identity of the set."""
    producers = {}
    for producer_id, operation in catalog:
        name = producer_store_name_v1(producer_id, operation)
        entry = registry["producers"].get(name)
        if entry is None:
            raise _reject("birth_authority_set_conflict")
        producers[f"{producer_id}:{operation}"] = {
            "store_name": name,
            "active_key_id": entry["active_key_id"],
            "verifier_key_ids": list(entry["verifier_key_ids"]),
        }
    document: dict[str, object] = {
        "schema_version": 1,
        "state": "complete",
        "provisioning_transaction_id": transaction_id,
        "provisioner_build_id": provisioner_build_id,
        "author_active_key_id": author["active_key_id"],
        "author_verifier_key_ids": list(author["verifier_key_ids"]),
        "admission_active_key_id": registry["admission"]["active_key_id"],
        "admission_verifier_key_ids": list(
            registry["admission"]["verifier_key_ids"]
        ),
        "producer_keys": producers,
        "approval_authority_sha256": hashlib.sha256(
            approval_document
        ).hexdigest(),
        "semantic_authority_sha256": hashlib.sha256(
            semantic_document
        ).hexdigest(),
        "sandbox_registry_sha256": hashlib.sha256(
            sandbox_document
        ).hexdigest(),
        "semantic_public_key_ids": sorted(registry["semantic"]),
        "approval_input_sha256": digests["approval_input_sha256"],
        "semantic_input_sha256": digests["semantic_input_sha256"],
        "producer_catalog_sha256": digests["producer_catalog_sha256"],
        "context_source_inventory_sha256": prepared.source_inventory_sha256,
        "prepared_admission_context_id": prepared.prepared_admission_context_id,
        "prepared_context_epoch": prepared.prepared_context_epoch,
        "context_material_sha256": prepared.material_sha256,
    }
    if any(value is None for value in document.values()):
        raise _reject("birth_authority_set_conflict")
    set_id = hashlib.sha256(
        SET_ID_DIGEST_DOMAIN_V1 + encode_canonical_document_v1(document)
    ).hexdigest()
    document["set_id"] = set_id
    return encode_canonical_document_v1(document), set_id


def _stage_context_and_record_v1(
    session, journal: "_TransactionJournalV1", previous: CheckpointV1,
) -> CheckpointV1:
    """Freeze the context material and make it durable before ``set.json``.

    The identity of the set depends on this document, so nothing about the set
    may be written until these bytes are on disk and read back (section 10.4).
    """
    from executor_birth_secure_fs import _BirthObjectRole

    integrity = _BirthObjectRole.birth_integrity_only
    base = journal.root_components + (AUTHORITY_SET_BASENAME_V1,)
    with _translated():
        if CONTEXT_CONTAINER_BASENAME_V1 in set(session.inventory(base)):
            raise _reject("birth_provisioning_recovery_ambiguous")
    registry = _authority_registry_v1(session, base)
    prepared = _prepare_installed_admission_context_v1(registry)
    records = _create_directories_v1(session, journal, (
        (base + (CONTEXT_CONTAINER_BASENAME_V1,), integrity),
    ))
    written, _ = _publish_files_v1(
        session, journal,
        [(
            base + (CONTEXT_CONTAINER_BASENAME_V1,),
            CONTEXT_MATERIAL_BASENAME_V1, prepared.document, integrity,
        )],
        _next_object_sequence_v1(previous),
    )
    observed = _read_set_document_v1(
        session,
        base + (CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1),
    )
    if observed != prepared.document:
        raise _reject("birth_context_material_changed")
    digests = dict(previous.digests)
    digests["context_source_inventory_sha256"] = prepared.source_inventory_sha256
    digests["context_material_sha256"] = prepared.material_sha256
    checkpoint = CheckpointV1(
        previous.transaction_id, previous.checkpoint_sequence + 1,
        previous.digest(), ProvisioningStateV1.context_staged,
        previous.payload_inventory + tuple(records) + tuple(written),
        digests, previous.set_id,
    )
    journal.append(checkpoint)
    return checkpoint


def _write_set_and_record_v1(
    session, journal: "_TransactionJournalV1", previous: CheckpointV1,
) -> CheckpointV1:
    """Write ``set.json``, derive ``set_id`` and close the payload inventory."""
    from executor_birth_keystore import _load_birth_keystore_in_session
    from executor_birth_secure_fs import _BirthObjectRole

    integrity = _BirthObjectRole.birth_integrity_only
    base = journal.root_components + (AUTHORITY_SET_BASENAME_V1,)
    with _translated():
        if SET_DOCUMENT_BASENAME_V1 in set(session.inventory(base)):
            raise _reject("birth_authority_set_conflict")
    material = _read_set_document_v1(
        session,
        base + (CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1),
    )
    if hashlib.sha256(material).hexdigest() != previous.digests[
        "context_material_sha256"
    ]:
        raise _reject("birth_context_material_changed")
    document = decode_canonical_document_v1(material)
    prepared = PreparedContextMaterialV1(
        document=material,
        prepared_admission_context_id=document["prepared_admission_context_id"],
        prepared_context_epoch=document["prepared_context_epoch"],
        source_inventory_sha256=previous.digests[
            "context_source_inventory_sha256"
        ],
        material_sha256=previous.digests["context_material_sha256"],
    )
    author = _load_birth_keystore_in_session(
        journal.root_components + (AUTHOR_STORE_BASENAME_V1,), session,
    )
    payload, set_id = build_set_document_v1(
        transaction_id=journal.transaction_id,
        provisioner_build_id=journal.read_header().provisioner_build_id,
        author={
            "active_key_id": author.active_key_id,
            "verifier_key_ids": sorted(author.verifier_keys),
        },
        registry=_authority_registry_v1(session, base),
        catalog=producer_catalog_v1(),
        digests=previous.digests,
        prepared=prepared,
        approval_document=_read_set_document_v1(
            session, base + ("approval", "authority.json"),
        ),
        semantic_document=_read_set_document_v1(
            session, base + ("semantic", "authority.json"),
        ),
        sandbox_document=_read_set_document_v1(
            session,
            base + (SANDBOX_CONTAINER_BASENAME_V1, SANDBOX_REGISTRY_BASENAME_V1),
        ),
    )
    written, _ = _publish_files_v1(
        session, journal,
        [(base, SET_DOCUMENT_BASENAME_V1, payload, integrity)],
        _next_object_sequence_v1(previous),
    )
    observed = _read_set_document_v1(session, base + (SET_DOCUMENT_BASENAME_V1,))
    if observed != payload:
        raise _reject("birth_authority_set_conflict")
    digests = dict(previous.digests)
    digests["set_json_sha256"] = hashlib.sha256(payload).hexdigest()
    checkpoint = CheckpointV1(
        previous.transaction_id, previous.checkpoint_sequence + 1,
        previous.digest(), ProvisioningStateV1.verified,
        previous.payload_inventory + tuple(written), digests, set_id,
    )
    journal.append(checkpoint)
    return checkpoint


AUTHORITY_SETS_BASENAME_V1 = "authority-sets"
PREPARED_MARKER_BASENAME_V1 = "prepared-v1.json"
PREPARED_MARKER_STATE_V1 = "prepared_not_active"


def build_prepared_marker_v1(
    *, set_id: str, transaction_id: str, provisioner_build_id: str,
    digests: Mapping[str, str | None],
) -> bytes:
    """The marker of a prepared, inactive installation.

    It does not enable Birth, does not attest a Phase 3 state, does not
    authenticate the distribution and does not replace the future F4
    certificate (section 4.3).
    """
    if not _is_hex(set_id, 64):
        raise _reject("birth_authority_set_conflict")
    document = {
        "schema_version": 1,
        "state": PREPARED_MARKER_STATE_V1,
        "set_id": set_id,
        "authority_set": f"{AUTHORITY_SETS_BASENAME_V1}/{set_id}",
        "author_store": AUTHOR_STORE_BASENAME_V1,
        "author_store_public_inventory_sha256": digests[
            "author_store_public_inventory_sha256"
        ],
        "set_json_sha256": digests["set_json_sha256"],
        "context_material_sha256": digests["context_material_sha256"],
        "provisioner_build_id": provisioner_build_id,
        "transaction_id": transaction_id,
    }
    if any(value is None for value in document.values()):
        raise _reject("birth_authority_set_conflict")
    return encode_canonical_document_v1(document)


def _install_author_store_v1(session, journal: "_TransactionJournalV1") -> None:
    """Publish the staged author root under its final name, no replacement."""
    with _translated():
        session.rename_no_replace(
            journal.root_components + (AUTHOR_STORE_BASENAME_V1,),
            (AUTHOR_STORE_BASENAME_V1,),
            directory=True,
        )


def _install_authority_set_v1(
    session, journal: "_TransactionJournalV1", set_id: str,
) -> None:
    """Publish the staged set under the immutable name derived from its id."""
    from executor_birth_secure_fs import _BirthObjectRole

    with _translated():
        if AUTHORITY_SETS_BASENAME_V1 not in set(session.inventory(())):
            session.create_directory_exclusive(
                (AUTHORITY_SETS_BASENAME_V1,),
                role=_BirthObjectRole.birth_integrity_only,
            )
        session.rename_no_replace(
            journal.root_components + (AUTHORITY_SET_BASENAME_V1,),
            (AUTHORITY_SETS_BASENAME_V1, set_id),
            directory=True,
        )


def _install_marker_v1(
    session, journal: "_TransactionJournalV1", payload: bytes,
) -> None:
    """Write the marker inside the transaction, then publish it."""
    from executor_birth_secure_fs import _BirthObjectRole

    journal.publish_payload(
        journal.root_components, PREPARED_MARKER_BASENAME_V1, payload,
        role=_BirthObjectRole.birth_integrity_only,
        object_sequence=MAXIMUM_CHECKPOINT_SEQUENCE_V1,
    )
    with _translated():
        session.rename_no_replace(
            journal.root_components + (PREPARED_MARKER_BASENAME_V1,),
            (PREPARED_MARKER_BASENAME_V1,),
            directory=False,
        )


def _verify_installed_finals_v1(session, last: CheckpointV1) -> None:
    """Re-read the three finals with the productive loaders and compare.

    A rename does not change the identity of an object, so what the journal
    recorded about the staged tree must still describe the installed one; and
    a coherent marker never makes an incoherent set authoritative.
    """
    from executor_birth_keystore import raw_public_key

    author = verify_author_store_v1(session, (AUTHOR_STORE_BASENAME_V1,), None)
    observed = author_store_public_inventory_sha256_v1({
        key_id: raw_public_key(key)
        for key_id, key in author.verifier_keys.items()
    })
    if observed != last.digests["author_store_public_inventory_sha256"]:
        raise _reject("birth_author_keystore_existing_invalid")
    location = (AUTHORITY_SETS_BASENAME_V1, last.set_id)
    payload = _read_set_document_v1(
        session, location + (SET_DOCUMENT_BASENAME_V1,),
    )
    if hashlib.sha256(payload).hexdigest() != last.digests["set_json_sha256"]:
        raise _reject("birth_authority_set_conflict")
    document = decode_canonical_document_v1(payload)
    if (
        document["set_id"] != last.set_id
        or document["author_active_key_id"] != author.active_key_id
        or document["author_verifier_key_ids"] != sorted(author.verifier_keys)
    ):
        raise _reject("birth_authority_set_conflict")
    material = _read_set_document_v1(
        session,
        location + (CONTEXT_CONTAINER_BASENAME_V1, CONTEXT_MATERIAL_BASENAME_V1),
    )
    if hashlib.sha256(material).hexdigest() != last.digests[
        "context_material_sha256"
    ]:
        raise _reject("birth_context_material_changed")


def _verify_installed_marker_v1(session, last: CheckpointV1) -> dict[str, object]:
    payload = _read_set_document_v1(session, (PREPARED_MARKER_BASENAME_V1,))
    document = decode_canonical_document_v1(payload)
    expected = build_prepared_marker_v1(
        set_id=last.set_id,
        transaction_id=last.transaction_id,
        provisioner_build_id=document.get("provisioner_build_id", ""),
        digests=last.digests,
    )
    if payload != expected:
        raise _reject("birth_authority_set_conflict")
    return document


def _advance_to_installed_v1(
    session, journal: "_TransactionJournalV1", last: CheckpointV1,
    *, provisioner_build_id: str,
) -> CheckpointV1:
    """Publish the three finals in the only admitted order.

    There is no replacement of a final destination.  The author root may be
    published before the set because it keeps the previous identity and stays
    inert, and the journal survives until the marker, so a stop between two
    renames is always classifiable (section 8.1).
    """
    if state_rank_v1(last.state) < state_rank_v1(
        ProvisioningStateV1.author_installed
    ):
        with _translated():
            staged = AUTHOR_STORE_BASENAME_V1 in set(
                session.inventory(journal.root_components)
            )
        if staged:
            _install_author_store_v1(session, journal)
        last = _record_state_v1(
            journal, last, ProvisioningStateV1.author_installed,
        )
    if state_rank_v1(last.state) < state_rank_v1(
        ProvisioningStateV1.set_installed
    ):
        with _translated():
            staged = AUTHORITY_SET_BASENAME_V1 in set(
                session.inventory(journal.root_components)
            )
        if staged:
            _install_authority_set_v1(session, journal, last.set_id)
        last = _record_state_v1(journal, last, ProvisioningStateV1.set_installed)
    _verify_installed_finals_v1(session, last)
    if state_rank_v1(last.state) < state_rank_v1(
        ProvisioningStateV1.marker_installed
    ):
        with _translated():
            present = PREPARED_MARKER_BASENAME_V1 in set(session.inventory(()))
        if not present:
            _install_marker_v1(session, journal, build_prepared_marker_v1(
                set_id=last.set_id,
                transaction_id=last.transaction_id,
                provisioner_build_id=provisioner_build_id,
                digests=last.digests,
            ))
        last = _record_state_v1(
            journal, last, ProvisioningStateV1.marker_installed,
        )
    _verify_installed_marker_v1(session, last)
    return last


def _record_state_v1(
    journal: "_TransactionJournalV1", previous: CheckpointV1,
    state: ProvisioningStateV1,
) -> CheckpointV1:
    checkpoint = CheckpointV1(
        previous.transaction_id, previous.checkpoint_sequence + 1,
        previous.digest(), state, previous.payload_inventory,
        dict(previous.digests), previous.set_id,
    )
    journal.append(checkpoint)
    return checkpoint


def _remove_transaction_v1(session, journal: "_TransactionJournalV1") -> None:
    """Remove only the transaction that agrees with what was installed.

    Every object is opened and compared before it is disposed of, bottom up,
    and no glob or recursive walk is involved (section 7.6).
    """
    from executor_birth_secure_fs import (
        _DisposalClass, _DisposalExpectation, _ObjectKind,
    )

    checkpoints = journal.checkpoints_components
    with _translated():
        for entry in session._inventory_state(checkpoints):
            payload = session.read_file(
                checkpoints + (entry.name,),
                maximum=MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1,
                role=_integrity_role(),
            )
            session.dispose_transaction_object(_DisposalExpectation(
                components=checkpoints + (entry.name,),
                identity=entry.identity,
                kind=_ObjectKind.regular_file,
                role=_integrity_role(),
                disposal_class=_DisposalClass.complete_file,
                links=entry.links,
                expected_size=len(payload),
                maximum_partial_size=None,
                content_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
                inventory=None,
            ))
        for components in (checkpoints, journal.root_components):
            for entry in session._inventory_state(components[:-1]):
                if entry.name != components[-1]:
                    continue
                if entry.name == journal.header_basename:
                    continue
                session.dispose_transaction_object(_DisposalExpectation(
                    components=components,
                    identity=entry.identity,
                    kind=_ObjectKind.directory,
                    role=_integrity_role(),
                    disposal_class=_DisposalClass.empty_directory,
                    links=entry.links,
                    expected_size=None,
                    maximum_partial_size=None,
                    content_sha256=None,
                    inventory=(),
                ))
            if components is checkpoints:
                _dispose_header_v1(session, journal)


def _dispose_header_v1(session, journal: "_TransactionJournalV1") -> None:
    from executor_birth_secure_fs import (
        _DisposalClass, _DisposalExpectation, _ObjectKind,
    )

    components = journal.root_components + (journal.header_basename,)
    for entry in session._inventory_state(journal.root_components):
        if entry.name != journal.header_basename:
            continue
        payload = session.read_file(
            components,
            maximum=MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1,
            role=_integrity_role(),
        )
        session.dispose_transaction_object(_DisposalExpectation(
            components=components,
            identity=entry.identity,
            kind=_ObjectKind.regular_file,
            role=_integrity_role(),
            disposal_class=_DisposalClass.complete_file,
            links=entry.links,
            expected_size=len(payload),
            maximum_partial_size=None,
            content_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
            inventory=None,
        ))


PROVISIONER_BUILD_DIGEST_DOMAIN_V1 = (
    b"metnos.executor-birth.provisioner-build/v1\0"
)
PROVISIONER_BUILD_DIGEST_DOMAIN_V2 = (
    b"metnos.executor-birth.provisioner-build/v2\0"
)


def _provisioner_build_id_v1() -> str:
    """Identify this build from the code that is actually loaded.

    The identifier changes together with the modules, which is exactly what
    section 10 requires: a transaction of another build is a conflict and is
    never continued by a different provisioner.
    """
    import inspect

    import executor_birth_commit_publisher

    body = bytearray(PROVISIONER_BUILD_DIGEST_DOMAIN_V1)
    for module in (inspect.getmodule(_provisioner_build_id_v1),
                   executor_birth_commit_publisher):
        source = inspect.getsource(module).encode("utf-8")
        body += len(source).to_bytes(8, "big") + source
    return "birth-provisioner-v1-" + hashlib.sha256(body).hexdigest()


def _provisioner_build_id_v2() -> str:
    """Identify the exact transition provisioner loaded for this process."""
    import inspect

    import executor_birth_commit_publisher

    body = bytearray(PROVISIONER_BUILD_DIGEST_DOMAIN_V2)
    for module in (
        inspect.getmodule(_provisioner_build_id_v2),
        executor_birth_commit_publisher,
    ):
        source = inspect.getsource(module).encode("utf-8")
        body += len(source).to_bytes(8, "big") + source
    return "birth-provisioner-v2-" + hashlib.sha256(body).hexdigest()


def _open_installer_layout_v1():
    """Take the one layout the installer knows how to build."""
    from install.birth_authority_provisioning import (
        open_birth_provisioning_layout_v1,
    )

    with _translated():
        return open_birth_provisioning_layout_v1()


def _prepare_transition_authority_set_v2(
    claim: object, distribution: object, previous_set: object,
) -> PreparedAuthoritySetV2:
    """Prepare or resume the sole V2 set transaction at the fixed Birth root."""
    from executor_birth_distribution_manifest import is_verified_distribution
    from executor_birth_ownership_coordinator import SuccessorClaimV1
    from executor_birth_prepared_set import is_prepared_set_v1

    if (
        not isinstance(claim, SuccessorClaimV1)
        or not is_verified_distribution(distribution)
        or not is_prepared_set_v1(previous_set)
    ):
        raise _conflict()
    layout = _open_installer_layout_v1()
    result = None
    published = False
    try:
        session = layout.birth_session
        with _translated():
            lock = session.global_lock(exclusive=True, create=True)
        with lock:
            with _translated():
                names = set(session.inventory(()))
            legacy = {
                name for name in names if name.startswith(TRANSACTION_PREFIX_V1)
            }
            transitions = sorted(
                name for name in names if name.startswith(TRANSACTION_PREFIX_V2)
            )
            if legacy or len(transitions) > 1:
                raise _reject("birth_provisioning_recovery_ambiguous")
            if transitions:
                transaction_id = transitions[0][len(TRANSACTION_PREFIX_V2):]
                if not _is_hex(transaction_id, 32):
                    raise _reject("birth_provisioning_recovery_ambiguous")
            else:
                transaction_id = new_transaction_id_v1()
            header = _build_transaction_header_v2(
                transaction_id=transaction_id,
                provisioner_build_id=_provisioner_build_id_v2(),
                claim=claim,
                distribution=distribution,
                previous_set=previous_set,
            )
            journal = _TransactionJournalV1.transition_v2(
                session, transaction_id,
            )
            if not transitions:
                journal.create_root()
            if transitions:
                result = _resume_published_authority_set_v2(
                    session, journal, header,
                )
                published = result is not None
            if result is None:
                checkpoint = _prepare_staged_authority_set_v2(
                    session, layout, header, previous_set, distribution,
                )
                plan = journal._read_material_plan_v2(header)
                set_payload = _read_set_document_v1(
                    session,
                    journal.root_components
                    + (AUTHORITY_SET_BASENAME_V1, SET_DOCUMENT_BASENAME_V1),
                )
                set_document = decode_canonical_document_v1(set_payload)
                result = _prepared_authority_set_result_v2(
                    header, checkpoint, plan, set_document,
                )
    finally:
        layout.birth_session.close()
    assert result is not None
    if published:
        _verify_published_authority_set_v2(result)
    return result


def _resume_published_authority_set_v2(
    session, journal: _TransactionJournalV1, expected_header: TransactionHeaderV2,
) -> PreparedAuthoritySetV2 | None:
    """Recover only the exact final set moved by a verified V2 transaction."""
    state = journal.read_state()
    if state.header is None and state.last is None:
        return None
    if state.header != expected_header:
        raise _conflict()
    with _translated():
        transaction_names = set(session.inventory(journal.root_components))
    staged = AUTHORITY_SET_BASENAME_V1 in transaction_names
    if state.last is None or state.last.state is not ProvisioningStateV1.verified:
        return None
    if len(state.chain) != 2:
        raise _reject("birth_provisioning_recovery_ambiguous")
    checkpoint = state.last
    assert checkpoint.set_id is not None
    with _translated():
        published_names = set(session.inventory(
            (AUTHORITY_SETS_BASENAME_V1,),
        ))
    final = checkpoint.set_id in published_names
    if staged == final:
        raise _reject("birth_provisioning_recovery_ambiguous")
    if staged:
        return None
    plan = journal._read_material_plan_v2(expected_header)
    set_payload = _read_set_document_v1(
        session,
        (AUTHORITY_SETS_BASENAME_V1, checkpoint.set_id,
         SET_DOCUMENT_BASENAME_V1),
    )
    result = _prepared_authority_set_result_v2(
        expected_header, checkpoint, plan,
        decode_canonical_document_v1(set_payload),
    )
    _validate_prepared_authority_set_v2(session, result)
    return result


def _prepared_authority_set_result_v2(
    header: TransactionHeaderV2, checkpoint: CheckpointV1,
    plan: MaterialPlanV2, set_document: Mapping[str, object],
) -> PreparedAuthoritySetV2:
    values = dict(
        transaction_id=header.transaction_id,
        provisioner_build_id=header.provisioner_build_id,
        request_id=header.request_id,
        closed_build_id=header.closed_build_id,
        distribution_payload_hash=header.distribution_payload_hash,
        distribution_signature_hash=header.distribution_signature_hash,
        previous_set_id=header.previous_set_id,
        target_set_id=checkpoint.set_id,
        target_admission_context_id=(
            set_document["prepared_admission_context_id"]
        ),
        target_context_epoch=set_document["prepared_context_epoch"],
        target_context_material_sha256=(
            checkpoint.digests["context_material_sha256"]
        ),
        target_set_json_sha256=checkpoint.digests["set_json_sha256"],
        source_inventory_hash=header.source_inventory_hash,
        material_plan_sha256=plan.digest(),
        verified_checkpoint_sha256=checkpoint.digest(),
    )
    binding = _prepared_authority_set_binding_v2(values)
    return PreparedAuthoritySetV2(
        **values, _artifact_binding=binding,
        _seal=_PREPARED_AUTHORITY_SET_SEAL_V2,
    )


def _publish_prepared_authority_set_v2(
    prepared: object,
) -> PreparedAuthoritySetV2:
    """Publish one authorized staged set without changing the context selector."""
    if not is_prepared_authority_set_v2(prepared):
        raise _conflict()
    layout = _open_installer_layout_v1()
    try:
        session = layout.birth_session
        with _translated():
            lock = session.global_lock(exclusive=True, create=True)
        with lock:
            journal = _validate_prepared_authority_set_v2(session, prepared)
            with _translated():
                root_names = set(session.inventory(()))
                transaction_names = set(session.inventory(
                    journal.root_components,
                ))
                published = set(session.inventory(
                    (AUTHORITY_SETS_BASENAME_V1,),
                ))
            staged = AUTHORITY_SET_BASENAME_V1 in transaction_names
            final = prepared.target_set_id in published
            if PREPARED_MARKER_BASENAME_V1 not in root_names:
                raise _reject("birth_provisioning_recovery_ambiguous")
            if staged and final:
                raise _reject("birth_provisioning_recovery_ambiguous")
            if staged:
                with _translated():
                    session.rename_no_replace(
                        journal.root_components + (AUTHORITY_SET_BASENAME_V1,),
                        (AUTHORITY_SETS_BASENAME_V1, prepared.target_set_id),
                        directory=True,
                    )
            elif not final:
                raise _reject("birth_provisioning_recovery_ambiguous")
    finally:
        layout.birth_session.close()
    _verify_published_authority_set_v2(prepared)
    return prepared


def _validate_prepared_authority_set_v2(
    session, prepared: PreparedAuthoritySetV2,
) -> _TransactionJournalV1:
    journal = _TransactionJournalV1.transition_v2(
        session, prepared.transaction_id,
    )
    state = journal.read_state()
    header = state.header
    if (
        not isinstance(header, TransactionHeaderV2)
        or len(state.chain) != 2
        or state.last is None
        or state.last.state is not ProvisioningStateV1.verified
        or state.last.digest() != prepared.verified_checkpoint_sha256
        or state.last.set_id != prepared.target_set_id
        or state.last.digests["context_material_sha256"]
        != prepared.target_context_material_sha256
        or state.last.digests["set_json_sha256"]
        != prepared.target_set_json_sha256
        or header.transaction_id != prepared.transaction_id
        or header.provisioner_build_id != prepared.provisioner_build_id
        or header.request_id != prepared.request_id
        or header.closed_build_id != prepared.closed_build_id
        or header.distribution_payload_hash
        != prepared.distribution_payload_hash
        or header.distribution_signature_hash
        != prepared.distribution_signature_hash
        or header.previous_set_id != prepared.previous_set_id
        or header.source_inventory_hash != prepared.source_inventory_hash
    ):
        raise _conflict()
    plan = journal._read_material_plan_v2(header)
    if plan.digest() != prepared.material_plan_sha256:
        raise _conflict()
    return journal


def _verify_published_authority_set_v2(
    prepared: PreparedAuthoritySetV2,
) -> None:
    from executor_birth_prepared_set import (
        PreparedSetError, load_authority_set_v1,
    )

    layout = _open_installer_layout_v1()
    try:
        session = layout.birth_session
        with _translated():
            lock = session.global_lock(exclusive=False, create=False)
        with lock:
            try:
                observed = load_authority_set_v1(
                    session, prepared.target_set_id,
                    expected_set_json_sha256=prepared.target_set_json_sha256,
                    expected_transaction_id=prepared.transaction_id,
                    expected_context_material_sha256=(
                        prepared.target_context_material_sha256
                    ),
                )
            except PreparedSetError as exc:
                raise BirthProvisioningError(exc.code, exc) from None
            if (
                observed.set_id != prepared.target_set_id
                or observed.prepared_admission_context_id
                != prepared.target_admission_context_id
                or observed.prepared_context_epoch
                != prepared.target_context_epoch
                or observed.provisioner_build_id
                != prepared.provisioner_build_id
            ):
                raise _reject("birth_authority_set_conflict")
    finally:
        layout.birth_session.close()


_TRANSITION_RECEIPT_PREPARATION_SEAL_V2 = object()


@dataclass(frozen=True, slots=True)
class _TransitionReceiptPreparationV2:
    distribution: object
    descriptor: object
    previous_context: object
    prepared_authority_set: PreparedAuthoritySetV2
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _TRANSITION_RECEIPT_PREPARATION_SEAL_V2
            or not is_prepared_authority_set_v2(self.prepared_authority_set)
        ):
            raise _conflict()


def _prepare_transition_receipt_material_locked_v2(
    session: object, distribution: object,
) -> _TransitionReceiptPreparationV2:
    """Complete reversible preparation before entering maintenance."""
    from executor_birth_distribution_manifest import (
        capture_current_deployment_descriptor_v1,
    )
    from executor_birth_ownership_coordinator import (
        _require_deployment_lock_session_v1, _transition_edge_locked_v2,
    )
    from executor_birth_prepared_root import (
        _load_historical_transition_anchor_v1,
        load_required_context_runtime_v1,
    )

    _require_deployment_lock_session_v1(session)
    verified, descriptor = capture_current_deployment_descriptor_v1(
        distribution,
    )
    claim, predecessor = _transition_edge_locked_v2(session, verified)
    if predecessor is None:
        if claim.release_sequence != 1:
            raise _conflict()
        previous_set = _load_historical_transition_anchor_v1()
        previous_context = previous_set
    else:
        required = load_required_context_runtime_v1()
        if (
            required.required_head_id != claim.previous_head_id
            or required.required_head_id != predecessor.head_id
        ):
            raise _conflict()
        previous_context = required.selection
        previous_set = required.authorities.prepared
    prepared = _prepare_transition_authority_set_v2(
        claim, verified, previous_set,
    )
    return _TransitionReceiptPreparationV2(
        verified, descriptor, previous_context, prepared,
        _TRANSITION_RECEIPT_PREPARATION_SEAL_V2,
    )


def _complete_transition_receipts_locked_v2(
    session: object, preparation: object, frozen: object,
) -> object:
    """Reach receipt completeness under caller-held deployment and maintenance."""
    from datetime import datetime, timezone

    from executor_birth_distribution_manifest import (
        capture_current_deployment_descriptor_v1,
    )
    from executor_birth_ownership_coordinator import (
        _append_prepared_transition_locked_v2,
        _append_receipts_complete_locked_v2,
        _build_staged_current_receipts_v2,
        _prepared_transition_publication_v2,
        _publish_context_transition_locked_v2,
        _require_deployment_lock_session_v1,
    )
    from executor_birth_prepared_root import (
        _load_staged_reattestation_context_v1,
    )
    from executor_birth_ownership_preflight import (
        canonical_maintenance_proof,
    )

    _require_deployment_lock_session_v1(session)
    if (
        type(preparation) is not _TransitionReceiptPreparationV2
        or preparation._seal is not _TRANSITION_RECEIPT_PREPARATION_SEAL_V2
        or type(frozen) is not tuple or len(frozen) != 3
    ):
        raise _conflict()
    distribution = preparation.distribution
    descriptor = preparation.descriptor
    verified, repeated_descriptor = capture_current_deployment_descriptor_v1(
        distribution,
    )
    if verified != distribution or repeated_descriptor != descriptor:
        raise _conflict()
    maintenance, current_inventory, evidence = frozen
    if not callable(maintenance):
        raise _conflict()
    previous_context = preparation.previous_context
    prepared = preparation.prepared_authority_set
    record, transition = _append_prepared_transition_locked_v2(
        session,
        distribution=verified,
        previous_context=previous_context,
        prepared_authority_set=prepared,
        current_inventory=current_inventory,
        deployment_descriptor=descriptor,
    )
    _publish_prepared_authority_set_v2(prepared)
    publication = _prepared_transition_publication_v2(
        record, transition,
        prepared_authority_set=prepared,
        distribution=verified,
        deployment_descriptor=descriptor,
        current_inventory=current_inventory,
    )
    staged_context = _load_staged_reattestation_context_v1(
        transition, verified, current_inventory,
    )
    proof = _build_staged_current_receipts_v2(
        staged_context,
        now=lambda: datetime.now(timezone.utc),
        prove_quiescent=maintenance,
        expected_inventory=current_inventory,
    )
    observed = maintenance.observe()
    final_evidence = canonical_maintenance_proof(
        source=observed["source"], units=observed["units"],
    )
    complete = _append_receipts_complete_locked_v2(
        session, publication,
        proof=proof,
        maintenance_before=evidence,
        maintenance_after=final_evidence,
    )
    _publish_context_transition_locked_v2(
        session, publication, complete,
    )
    return complete


def _require_transition_directory_v2(
    path: Path, *, owner: tuple[int, int],
) -> Path:
    """Bind a transition root to one non-writable directory identity."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise _reject("birth_transition_root_invalid")
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _reject("birth_transition_root_invalid", exc) from None
    if (
        resolved != path
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (info.st_uid, info.st_gid) != owner
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise _reject("birth_transition_root_invalid")
    return path


def _path_is_within_v2(path: str, root: Path) -> bool:
    if not path.startswith("/"):
        return False
    candidate = Path(path.removesuffix(" (deleted)"))
    return candidate == root or root in candidate.parents


def _process_tree_references_root_v2(
    root: Path, *, proc_root: Path = Path("/proc"),
) -> bool:
    """Observe executable, working, mapped and open paths under one root."""
    if (
        not isinstance(root, Path) or not root.is_absolute()
        or not isinstance(proc_root, Path) or not proc_root.is_absolute()
    ):
        raise _reject("birth_transition_process_observation_invalid")
    try:
        resolved_root = root.resolve(strict=True)
        processes = tuple(sorted(
            (item for item in proc_root.iterdir() if item.name.isdecimal()),
            key=lambda item: int(item.name),
        ))
    except OSError as exc:
        raise _reject(
            "birth_transition_process_observation_invalid", exc,
        ) from None
    for process in processes:
        try:
            for name in ("cwd", "exe"):
                try:
                    target = os.readlink(process / name)
                except FileNotFoundError:
                    continue
                if _path_is_within_v2(target, resolved_root):
                    return True
            descriptors = process / "fd"
            try:
                entries = tuple(descriptors.iterdir())
            except FileNotFoundError:
                entries = ()
            for entry in entries:
                try:
                    target = os.readlink(entry)
                except FileNotFoundError:
                    continue
                if _path_is_within_v2(target, resolved_root):
                    return True
            try:
                mappings = (process / "maps").read_text(
                    encoding="utf-8", errors="strict",
                )
            except FileNotFoundError:
                mappings = ""
            for line in mappings.splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) == 6 and _path_is_within_v2(
                    fields[5], resolved_root,
                ):
                    return True
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, UnicodeError) as exc:
            raise _reject(
                "birth_transition_process_observation_invalid", exc,
            ) from None
    return False


def _capture_bound_transition_catalog_v2(
    distribution: object, prepared: object,
):
    """Reread the signed catalog and bind it to the prepared candidate."""
    from executor_birth_service_catalog import (
        capture_current_service_catalog_v1,
    )

    loaded = capture_current_service_catalog_v1(distribution)
    materials = getattr(prepared, "materials", None)
    expected = getattr(materials, "catalog", None)
    expected_fragments = getattr(materials, "unit_fragments", None)
    if (
        expected is None
        or loaded.catalog.catalog_id != expected.catalog_id
        or loaded.catalog.service_coverage_hash
        != expected.service_coverage_hash
        or loaded.catalog.encoded != expected.encoded
        or loaded.unit_fragments != expected_fragments
    ):
        raise _reject("birth_transition_catalog_changed")
    return loaded


def _observe_bound_enforcement_v2(prepared: object) -> str:
    """Bind the closed-bit observation to the signed candidate file bytes."""
    from executor_birth_distribution_manifest import file_content_hash
    from executor_birth_enforcement_evidence import (
        observe_enforcement_v1, require_enforced_v1,
    )

    materials = getattr(prepared, "materials", None)
    distribution = getattr(materials, "distribution", None)
    facts = getattr(distribution, "facts", None)
    files = getattr(distribution, "files", ())
    relative = "runtime/executor_birth_legacy_gate.py"
    matches = tuple(item for item in files if item.path == relative)
    if facts is None or len(matches) != 1:
        raise _reject("birth_transition_enforcement_invalid")
    gate = Path(facts.installation_root) / relative
    evidence = observe_enforcement_v1(gate)
    try:
        payload = gate.read_bytes()
    except OSError as exc:
        raise _reject("birth_transition_enforcement_invalid", exc) from None
    expected = matches[0]
    if (
        len(payload) != evidence.module_bytes
        or "sha256:" + hashlib.sha256(payload).hexdigest()
        != evidence.module_digest
        or len(payload) != expected.size
        or file_content_hash(relative, payload) != expected.content_hash
    ):
        raise _reject("birth_transition_enforcement_invalid")
    return require_enforced_v1(evidence)


def _transition_roots_v2(prepared: object) -> Mapping[str, Path]:
    """Derive every mutable root only from the authenticated candidate."""
    materials = getattr(prepared, "materials", None)
    descriptor = getattr(materials, "descriptor", None)
    predecessor = getattr(materials, "predecessor", None)
    if descriptor is None or predecessor is None:
        raise _reject("birth_transition_root_invalid")
    roots = {
        "system": _require_transition_directory_v2(
            Path(descriptor.system_unit_root), owner=(0, 0),
        ),
        "user": _require_transition_directory_v2(
            Path(descriptor.service_home) / ".config/systemd/user",
            owner=(descriptor.service_uid, descriptor.service_gid),
        ),
        "repository": _require_transition_directory_v2(
            Path(predecessor.installation_root), owner=(0, 0),
        ),
    }
    return MappingProxyType(roots)


def _retire_bound_catalog_v2(
    distribution: object, prepared: object, maintenance: object,
) -> str:
    """Prove quiescence, apply the signed plan and return its stable digest."""
    from executor_birth_legacy_neutralizer import _neutralize_core_v1
    from executor_birth_legacy_retirement import (
        plan_catalog_retirement_v1, plan_digest_v1,
        require_no_legacy_in_flight_v1,
    )
    loaded = _capture_bound_transition_catalog_v2(distribution, prepared)
    plan = plan_catalog_retirement_v1(loaded.catalog)
    roots = _transition_roots_v2(prepared)
    observed = maintenance.observe()
    unit_states = {
        (item["scope"], item["unit"]): item["active_state"]
        for item in observed["units"]
    }
    repository_steps = tuple(
        step for step in plan.steps if step.scope == "repository"
    )
    if repository_steps and _process_tree_references_root_v2(
        roots["repository"],
    ):
        raise _reject("birth_transition_repository_in_use")
    states = {
        (step.scope, step.locator): "inactive"
        for step in plan.steps if step.scope == "repository"
    }
    states.update({
        (step.scope, step.locator): unit_states[(step.scope, step.locator)]
        for step in plan.steps
        if step.scope != "repository"
        and (step.scope, step.locator) in unit_states
    })
    require_no_legacy_in_flight_v1(plan.steps, states)

    preserve_action = "preserve_replaced_system_unit"
    ordinary = tuple(
        step for step in plan.steps if step.action != preserve_action
    )
    preserved = tuple(
        step for step in plan.steps if step.action == preserve_action
    )
    for scope in ("repository", "user", "system"):
        scoped = tuple(step for step in ordinary if step.scope == scope)
        if scoped:
            _neutralize_core_v1(roots[scope], scoped, {})
    fragments = dict(loaded.unit_fragments)
    replacements = {
        (step.scope, step.locator): fragments.get(step.locator, b"")
        for step in preserved
    }
    if preserved:
        _neutralize_core_v1(roots["system"], preserved, replacements)
    return plan_digest_v1(plan.steps)


def _install_bound_topology_v2(
    distribution: object, prepared: object,
):
    """Install, reload and measure the exact signed dominant topology."""
    from executor_birth_admin_preflight import (
        _capture_cutover_effective_systemd_v2,
    )
    from executor_birth_dominant_topology import (
        _install_core_v1, _install_enablement_links_core_v1,
    )

    loaded = _capture_bound_transition_catalog_v2(distribution, prepared)
    roots = _transition_roots_v2(prepared)
    fragments = dict(loaded.unit_fragments)
    if len(fragments) != len(loaded.unit_fragments):
        raise _reject("birth_transition_topology_invalid")
    materials = prepared.materials
    expected_units = tuple(
        item.unit_name for item in materials.candidate_units.entries
    )
    if tuple(sorted(fragments)) != tuple(sorted(expected_units)):
        raise _reject("birth_transition_topology_invalid")
    _install_core_v1(roots["system"], fragments)
    links = tuple(sorted(
        (
            link for entry in materials.candidate_units.entries
            for link in entry.enablement_links
        ),
        key=lambda link: link.path.encode("utf-8"),
    ))
    if links:
        _install_enablement_links_core_v1(
            roots["system"], links, owner=(0, 0),
        )
    environment = {
        "LANG": "C", "LC_ALL": "C",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    }
    try:
        result = subprocess.run(
            [materials.descriptor.systemctl_executable, "daemon-reload"],
            capture_output=True, check=False, close_fds=True,
            env=environment, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _reject("birth_transition_manager_reload_failed", exc) from None
    if result.returncode != 0:
        raise _reject("birth_transition_manager_reload_failed")
    _capture_bound_transition_catalog_v2(distribution, prepared)
    return _capture_cutover_effective_systemd_v2(prepared)


def complete_transition_cutover_v2(
    distribution: object, source_id: object, *, service_state_root: object,
):
    """Complete one reserved V2 crossing while retaining all three locks."""
    from contract_cutover_guard import (
        _begin_topology_transition_v1,
        _contract_cutover_guard_for_service_user_v1,
        _maintenance_evidence_under_transition_v1,
    )
    from executor_birth_admin_preflight import (
        _attest_operational_preflight_v1,
        _build_startup_prerequisite_for_cutover_v2,
        _prepare_cutover_candidate_v2,
    )
    from executor_birth_distribution_manifest import (
        capture_current_deployment_descriptor_v1,
        verify_current_installation_distribution_v1,
    )
    from executor_birth_bootstrap import verify_initial_installer_store_v1
    from executor_birth_dominant_startup import complete_dominant_startup_v1
    from executor_birth_ownership_authorities import (
        load_root_ownership_authorities_v1,
    )
    from executor_birth_ownership_coordinator import (
        _certificate_ready_material_v2, _completed_transition_locked_v2,
        _cross_certificate_boundary_locked_v2,
        _cross_head_boundary_locked_v2,
        _cross_preflight_boundary_locked_v2, _deployment_lock_v1,
        _observe_dominant_identity_locked_v2, _result,
        _reserve_transition_edge_locked_v2,
        _transition_inventory_under_maintenance_v2,
    )
    from executor_birth_legacy_gate import closed_build_enforcement
    from executor_birth_startup_gate import _exclusive_startup_gate_v1
    from install.executor_birth_source_receiver import (
        _load_received_source_with_product_session_v1,
    )
    from install.executor_birth_startup_prerequisite import (
        _publish_startup_prerequisite_locked_v2,
    )

    if closed_build_enforcement() is not True:
        raise _reject("birth_ownership_closed_enforcement_required")
    try:
        selected_state_root = Path(os.fspath(service_state_root))
    except TypeError as exc:
        raise _reject("birth_transition_service_identity_changed", exc) from None
    if not selected_state_root.is_absolute() or selected_state_root == Path("/"):
        raise _reject("birth_transition_service_identity_changed")
    selected_state_root = Path(os.path.abspath(selected_state_root))
    with _deployment_lock_v1() as deployment_session:
        verified = verify_current_installation_distribution_v1(
            distribution.encoded, distribution.signature,
        )
        if verified != distribution:
            raise _reject("birth_transition_distribution_changed")
        verified, signed_descriptor = (
            capture_current_deployment_descriptor_v1(verified)
        )
        from config import PATH_USER_STATE

        signed_state_root = Path(os.path.abspath(
            Path(signed_descriptor.service_home)
            / ".local" / "state" / "metnos"
        ))
        configured_state_root = Path(os.path.abspath(PATH_USER_STATE))
        if not (
            selected_state_root == signed_state_root == configured_state_root
        ):
            raise _reject("birth_transition_service_identity_changed")
        received = _load_received_source_with_product_session_v1(
            source_id, deployment_session,
        )
        _reserve_transition_edge_locked_v2(
            deployment_session, distribution=verified,
            source_id=received.source_id,
        )
        completed = _completed_transition_locked_v2(
            deployment_session, verified,
        )
        if completed is not None:
            _attest_operational_preflight_v1()
            repeated = _completed_transition_locked_v2(
                deployment_session, verified,
            )
            if repeated != completed:
                raise _reject("birth_transition_final_state_changed")
            return _result(completed)

        preparation = _prepare_transition_receipt_material_locked_v2(
            deployment_session, verified,
        )
        descriptor = preparation.descriptor
        if descriptor != signed_descriptor:
            raise _reject("birth_transition_service_identity_changed")
        with _exclusive_startup_gate_v1() as startup_session:
            with _contract_cutover_guard_for_service_user_v1(
                descriptor.service_user,
            ) as (maintenance, evidence):
                if verified.release_sequence == 1:
                    verify_initial_installer_store_v1(
                        prove_quiescent=maintenance,
                        authoring_owner=(
                            descriptor.service_uid,
                            descriptor.service_gid,
                        ),
                    )
                with _transition_inventory_under_maintenance_v2(
                    maintenance, evidence,
                ) as frozen:
                    complete = _complete_transition_receipts_locked_v2(
                        deployment_session, preparation, frozen,
                    )
                    prepared = _prepare_cutover_candidate_v2(
                        complete, verified,
                    )
                    _begin_topology_transition_v1(
                        maintenance, complete.maintenance_proof,
                    )
                    sessions = (
                        deployment_session, startup_session, maintenance,
                    )
                    effective_observations: list[object] = []
                    final_records: list[object] = []

                    def observe_identity():
                        return _observe_dominant_identity_locked_v2(
                            deployment_session, complete,
                        )

                    def observe_catalog() -> str:
                        return _capture_bound_transition_catalog_v2(
                            verified, prepared,
                        ).catalog.catalog_id

                    def observe_enforcement() -> str:
                        return _observe_bound_enforcement_v2(prepared)

                    def plan_retirement() -> str:
                        return _retire_bound_catalog_v2(
                            verified, prepared, maintenance,
                        )

                    def observe_topology() -> str:
                        observed = _install_bound_topology_v2(
                            verified, prepared,
                        )
                        effective_observations.append(observed)
                        return observed.snapshot.effective_units_hash

                    def observe_maintenance() -> bytes:
                        return _maintenance_evidence_under_transition_v1(
                            maintenance,
                        )

                    def cross(receipt) -> None:
                        if len(effective_observations) < 2:
                            raise _reject(
                                "birth_transition_topology_unconfirmed",
                            )
                        prerequisite = (
                            _build_startup_prerequisite_for_cutover_v2(
                                prepared, effective_observations[-1],
                            )
                        )
                        sealed = _publish_startup_prerequisite_locked_v2(
                            prerequisite, complete, sessions,
                        )
                        authorities = load_root_ownership_authorities_v1()
                        material = _certificate_ready_material_v2(
                            complete,
                            authorities=authorities,
                            prerequisite=sealed,
                            observe_maintenance=observe_maintenance,
                            crossing_receipt=receipt,
                        )
                        published = _cross_certificate_boundary_locked_v2(
                            deployment_session, material,
                            authorities=authorities,
                        )
                        head = _cross_head_boundary_locked_v2(
                            sessions, published, verified,
                            authorities=authorities,
                        )
                        final_records.append(
                            _cross_preflight_boundary_locked_v2(
                                sessions, head,
                            )
                        )

                    complete_dominant_startup_v1(
                        sessions=sessions,
                        observe_identity=observe_identity,
                        observe_topology=observe_topology,
                        observe_catalog=observe_catalog,
                        plan_retirement=plan_retirement,
                        observe_enforcement=observe_enforcement,
                        cross=cross,
                    )
                    if len(final_records) != 1:
                        raise _reject("birth_transition_final_state_missing")
                    return _result(final_records[0])


def prepare_transition_receipts_v2(
    distribution: object,
):
    """Reach V2 receipt completeness without exposing a partial product door."""
    from executor_birth_ownership_coordinator import (
        _deployment_lock_v1, _result, _transition_maintenance_inventory_v2,
    )

    with _deployment_lock_v1() as session:
        preparation = _prepare_transition_receipt_material_locked_v2(
            session, distribution,
        )
        with _transition_maintenance_inventory_v2() as frozen:
            complete = _complete_transition_receipts_locked_v2(
                session, preparation, frozen,
            )
        return _result(complete)


def _run_provisioning_entry_v1(
    *, preflight_operator_inputs: bool,
) -> AuthorProvisioningResultV1:
    """Run at most one staging pass and one fresh publication pass.

    Reopening is bounded, not a retry policy.  The first pass can only request
    it after recording ``verified``; a second request contradicts the durable
    state machine and is therefore refused.
    """
    provisioner_build_id = _provisioner_build_id_v1()
    boundary: _FreshPublicationPassV1 | None = None
    for pass_index in range(2):
        layout = _open_installer_layout_v1()
        try:
            if preflight_operator_inputs and pass_index == 0:
                # Read-only preflight first: the administrator installs the
                # two public registries before Phase 3, so their absence or
                # invalidity is named before any transaction exists.
                acquire_operator_inputs_v1(layout.operator_input)
            result = _provision_prepared_authorities_v1(
                layout, provisioner_build_id=provisioner_build_id,
            )
        finally:
            layout.birth_session.close()
        if isinstance(result, _FreshPublicationPassV1):
            if pass_index != 0:
                raise _reject("birth_provisioning_recovery_ambiguous")
            boundary = result
            continue
        if (
            boundary is not None
            and result.transaction_id not in {None, boundary.transaction_id}
        ):
            raise _conflict()
        return result
    raise _reject("birth_provisioning_recovery_ambiguous")


def prepare_or_defer_until_legacy_author_exists() -> AuthorProvisioningResultV1:
    """Inspect first; defer only from a completely empty installation.

    The entry always inspects and resumes an existing transaction or an
    installed set.  It creates no object when there is nothing at all and no
    previous author identity yet, because on a fresh installation the author
    key is created later in the same phase (section 10.6).
    """
    return _run_provisioning_entry_v1(preflight_operator_inputs=True)


def ensure_executor_birth_authorities_prepared() -> AuthorProvisioningResultV1:
    """Run the idempotent provisioner once the author identity exists.

    The adapter receives no path, no key and no mode: it resolves the fixed
    layout of the installer and calls the same provisioner as the first entry.
    Deferring is no longer an admitted outcome here.
    """
    result = _run_provisioning_entry_v1(preflight_operator_inputs=False)
    if result.outcome is AuthorProvisioningOutcomeV1.author_not_yet_created:
        raise _reject("birth_author_identity_incomplete")
    return result
