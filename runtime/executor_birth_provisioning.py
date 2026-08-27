"""Canonical journal of the Birth provisioning transaction (increment 2B).

Sections 4.3 and 8 of the group 2 analysis fix these bytes.  The header and
every checkpoint are immutable documents with a closed schema, a canonical
encoding and a digest bound to a domain, so a resumed run can tell an expected
state from a tampered one without interpreting anything written by a candidate.

The journal provides consistency and recovery, not authentication: before
groups 5 and 6 a process that controls the transitional root can fabricate a
coherent journal, so none of these documents is an authorisation decision.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Sequence

TRANSACTION_PROTOCOL_V1 = "birth-authority-provisioning-v1"
CHECKPOINT_DIGEST_DOMAIN_V1 = b"metnos.executor-birth.provisioning-checkpoint/v1\0"
TRANSACTION_HEADER_BASENAME_V1 = "transaction-v1.json"
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


def checkpoint_name_v1(sequence: int) -> str:
    """The only authoritative name of one checkpoint."""
    if (
        not _is_exact_int(sequence)
        or not 0 <= sequence <= MAXIMUM_CHECKPOINT_SEQUENCE_V1
    ):
        raise _conflict()
    return f"{sequence:020d}.json"


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
