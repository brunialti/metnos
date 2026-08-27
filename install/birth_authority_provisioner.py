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
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence
import sys

_RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
if str(_RUNTIME) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(_RUNTIME))

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
HEADER_PENDING_PREFIX_V1 = ".transaction-v1.pending."
CHECKPOINT_PENDING_PREFIX_V1 = ".checkpoint-pending-"


def transaction_root_name_v1(transaction_id: str) -> str:
    """The only admitted name of one transaction directory."""
    if not _is_hex(transaction_id, 32):
        raise _conflict()
    return TRANSACTION_PREFIX_V1 + transaction_id


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

    header: TransactionHeaderV1 | None
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

    __slots__ = ("_session", "_transaction_id", "_root", "_checkpoints")

    def __init__(self, session, transaction_id: str) -> None:
        self._session = session
        self._transaction_id = transaction_id
        self._root = (transaction_root_name_v1(transaction_id),)
        self._checkpoints = self._root + (CHECKPOINTS_BASENAME_V1,)

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    @property
    def root_components(self) -> tuple[str, ...]:
        return self._root

    @property
    def checkpoints_components(self) -> tuple[str, ...]:
        return self._checkpoints

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

    def write_header(self, header: TransactionHeaderV1) -> None:
        if header.transaction_id != self._transaction_id:
            raise _conflict()
        self._publish(
            self._root,
            HEADER_PENDING_PREFIX_V1 + self._transaction_id,
            TRANSACTION_HEADER_BASENAME_V1,
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
        self, header: TransactionHeaderV1, state: "TransactionStateV1",
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
        pending = HEADER_PENDING_PREFIX_V1 + self._transaction_id
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
        pending = HEADER_PENDING_PREFIX_V1 + self._transaction_id
        try:
            observed = decode_transaction_header_v1(
                self._read(self._root + (pending,))
            )
        except BirthProvisioningError:
            return False
        if observed.transaction_id != self._transaction_id:
            return False
        with _translated():
            self._session.rename_no_replace(
                self._root + (pending,),
                self._root + (TRANSACTION_HEADER_BASENAME_V1,),
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
        header_pending = HEADER_PENDING_PREFIX_V1 + self._transaction_id
        chain, pending = ((), None)
        if CHECKPOINTS_BASENAME_V1 in names:
            chain, pending = self._read_chain()
        admitted = {
            TRANSACTION_HEADER_BASENAME_V1, CHECKPOINTS_BASENAME_V1,
            header_pending,
        }
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
        if TRANSACTION_HEADER_BASENAME_V1 in names:
            header = self.read_header()
            if header.transaction_id != self._transaction_id:
                raise _conflict()
        return TransactionStateV1(
            header=header,
            chain=chain,
            header_pending=header_pending in names,
            pending_checkpoint_sequence=pending,
        )

    def read_header(self) -> TransactionHeaderV1:
        return decode_transaction_header_v1(
            self._read(self._root + (TRANSACTION_HEADER_BASENAME_V1,))
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
        with _translated():
            return self._session.read_file(
                components,
                maximum=MAXIMUM_JOURNAL_DOCUMENT_BYTES_V1,
                role=_integrity_role(),
            )


def _integrity_role():
    from executor_birth_secure_fs import _BirthObjectRole

    return _BirthObjectRole.birth_integrity_only


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


def author_store_config_v1(source: AuthorSourceV1) -> bytes:
    """The store configuration the productive loader already validates."""
    from executor_birth_keystore import SCHEMA_VERSION

    key_ids = sorted(source.publics)
    if source.active_key_id not in source.publics:
        raise _reject("birth_author_identity_mismatch")
    return encode_canonical_document_v1({
        "active_key_id": source.active_key_id,
        "config_revision": 1,
        "keys": [
            {
                "key_id": key_id,
                "public_file": f"public/{key_id}.pub",
                "status": "active" if key_id == source.active_key_id else "verifier",
            }
            for key_id in key_ids
        ],
        "private_file": f"private/{source.active_key_id}.key",
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
    from executor_birth_keystore import CONFIG_BASENAME, LOCK_BASENAME
    from executor_birth_secure_fs import _BirthObjectRole

    confidential = _BirthObjectRole.birth_confidential
    integrity = _BirthObjectRole.birth_integrity_only
    base = journal.root_components + (AUTHOR_STORE_BASENAME_V1,)
    records: list[PayloadRecordV1] = []
    sequence = first_object_sequence

    def relative(components: tuple[str, ...]) -> str:
        return "/".join(components[len(journal.root_components):])

    with _translated():
        for components, role in (
            (base, confidential),
            (base + ("private",), confidential),
            (base + ("public",), integrity),
        ):
            session.create_directory_exclusive(components, role=role)
            records.append(PayloadRecordV1(
                relative(components), PayloadObjectTypeV1.directory,
                _confidentiality_v1(role), None, None,
                _platform_identity_v1(_identity_v1(session, components)),
            ))

    files: list[tuple[tuple[str, ...], str, bytes, object]] = [
        (base + ("public",), f"{key_id}.pub", source.publics[key_id], integrity)
        for key_id in sorted(source.publics)
    ]
    files.append((
        base + ("private",), f"{source.active_key_id}.key",
        source.active_private, confidential,
    ))
    files.append((base, CONFIG_BASENAME, author_store_config_v1(source), confidential))
    # The store lock is a payload like any other: the loader takes a shared
    # lock on it before reading, so it must already carry the marker byte the
    # capability writes when it creates one itself.
    from executor_birth_secure_fs import _LOCK_BYTE

    files.append((base, LOCK_BASENAME, _LOCK_BYTE, confidential))
    for parent, name, payload, role in files:
        identity = journal.publish_payload(
            parent, name, payload, role=role, object_sequence=sequence,
        )
        sequence += 1
        records.append(PayloadRecordV1(
            relative(parent + (name,)), PayloadObjectTypeV1.file,
            _confidentiality_v1(role), len(payload),
            hashlib.sha256(payload).hexdigest(),
            _platform_identity_v1(identity),
        ))
    return StagedAuthorStoreV1(
        payload_inventory=tuple(records),
        public_inventory_sha256=author_store_public_inventory_sha256_v1(
            source.publics
        ),
        next_object_sequence=sequence,
    )


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


def _install_author_store_v1(session, journal: "_TransactionJournalV1") -> None:
    """Publish the staged store under its final name, without replacement."""
    with _translated():
        session.rename_no_replace(
            journal.root_components + (AUTHOR_STORE_BASENAME_V1,),
            (AUTHOR_STORE_BASENAME_V1,),
            directory=True,
        )


AUTHOR_SOURCE_BASENAME_V1 = "keys"


class AuthorProvisioningOutcomeV1(str, Enum):
    """What one provisioning run actually did, never what it hoped to do."""

    installed = "installed"
    already_installed = "already_installed"
    resumed = "resumed"
    author_not_yet_created = "author_not_yet_created"


@dataclass(frozen=True, slots=True)
class AuthorProvisioningResultV1:
    outcome: AuthorProvisioningOutcomeV1
    active_key_id: str | None
    public_inventory_sha256: str | None
    transaction_id: str | None


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


def provision_author_root_v1(
    layout, *, provisioner_build_id: str,
) -> AuthorProvisioningResultV1:
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
        if installed and not transactions:
            loaded = verify_author_store_v1(
                session, (AUTHOR_STORE_BASENAME_V1,), None,
            )
            return AuthorProvisioningResultV1(
                AuthorProvisioningOutcomeV1.already_installed,
                loaded.active_key_id,
                author_store_public_inventory_sha256_v1({
                    key_id: _raw_public(key)
                    for key_id, key in loaded.verifier_keys.items()
                }),
                None,
            )
        if transactions:
            return _resume_author_root_v1(
                session, transactions[0][len(TRANSACTION_PREFIX_V1):],
                provisioner_build_id=provisioner_build_id,
                installed=installed,
            )
        if installed:
            raise _reject("birth_provisioning_recovery_ambiguous")
        return _first_author_migration_v1(
            session, provisioner_build_id=provisioner_build_id,
        )


def _raw_public(key) -> bytes:
    from executor_birth_keystore import raw_public_key

    return raw_public_key(key)


def _first_author_migration_v1(
    session, *, provisioner_build_id: str,
) -> AuthorProvisioningResultV1:
    """Acquire the previous identity and install it inside one transaction."""
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
    acquired = _record_author_source_v1(journal, zero, source)
    staged_checkpoint = _stage_and_record_v1(session, journal, acquired, source)
    _install_and_record_v1(session, journal, staged_checkpoint)
    return AuthorProvisioningResultV1(
        AuthorProvisioningOutcomeV1.installed,
        source.active_key_id,
        staged_checkpoint.digests["author_store_public_inventory_sha256"],
        transaction_id,
    )


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
    staged = _stage_author_store_v1(session, journal, source)
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


def _install_and_record_v1(
    session, journal: "_TransactionJournalV1", staged: CheckpointV1,
) -> None:
    """Publish the author root and make the fact durable before cleaning up."""
    _install_author_store_v1(session, journal)
    verify_installed_author_root_v1(session, staged)
    journal.append(CheckpointV1(
        staged.transaction_id, staged.checkpoint_sequence + 1,
        staged.digest(), ProvisioningStateV1.author_installed,
        staged.payload_inventory, dict(staged.digests), staged.set_id,
    ))


def verify_installed_author_root_v1(session, staged: CheckpointV1):
    """Re-read the final store and compare it with what the journal recorded.

    The rename does not change the identity of an object, so the recorded
    device and inode must still describe the installed tree: a store that
    matches by name but not by identity is a different object.
    """
    from executor_birth_keystore import raw_public_key

    loaded = verify_author_store_v1(session, (AUTHOR_STORE_BASENAME_V1,), None)
    observed = author_store_public_inventory_sha256_v1({
        key_id: raw_public_key(key)
        for key_id, key in loaded.verifier_keys.items()
    })
    if observed != staged.digests["author_store_public_inventory_sha256"]:
        raise _reject("birth_author_keystore_existing_invalid")
    for record in staged.payload_inventory:
        components = tuple(record.relative_path.split("/"))
        identity = _platform_identity_v1(_identity_v1(session, components))
        if identity != record.platform_identity:
            raise _reject("birth_provisioning_recovery_ambiguous")
    return loaded


def _resume_author_root_v1(
    session, transaction_id: str, *, provisioner_build_id: str, installed: bool,
) -> AuthorProvisioningResultV1:
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
        return _resume_before_staging_v1(
            session, journal, last, installed=installed,
            transaction_id=transaction_id,
        )
    if last.state is ProvisioningStateV1.author_staged:
        if installed:
            raise _reject("birth_provisioning_recovery_ambiguous")
        _install_and_record_v1(session, journal, last)
        return AuthorProvisioningResultV1(
            AuthorProvisioningOutcomeV1.resumed,
            None,
            last.digests["author_store_public_inventory_sha256"],
            transaction_id,
        )
    if last.state is ProvisioningStateV1.author_installed and installed:
        loaded = verify_installed_author_root_v1(session, last)
        return AuthorProvisioningResultV1(
            AuthorProvisioningOutcomeV1.resumed,
            loaded.active_key_id,
            last.digests["author_store_public_inventory_sha256"],
            transaction_id,
        )
    raise _reject("birth_provisioning_recovery_ambiguous")


def _resume_before_staging_v1(
    session, journal: "_TransactionJournalV1", last: CheckpointV1, *,
    installed: bool, transaction_id: str,
) -> AuthorProvisioningResultV1:
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
    staged = _stage_and_record_v1(session, journal, acquired, source)
    _install_and_record_v1(session, journal, staged)
    return AuthorProvisioningResultV1(
        AuthorProvisioningOutcomeV1.resumed,
        source.active_key_id,
        staged.digests["author_store_public_inventory_sha256"],
        transaction_id,
    )
