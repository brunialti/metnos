"""Pure closed state machine for initial host-provisioning records."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from executor_birth_account_identity import PosixAccountSnapshotV1
from executor_birth_canonical import (
    CanonicalDocumentError,
    decode_canonical_ascii_v1,
    encode_canonical_ascii_v1,
)
from executor_birth_crypto_framing import (
    framed_sha256_v1,
    is_framed_sha256_v1,
)
from executor_birth_host_layout import HostLayoutObservationV1
from executor_birth_host_provisioning_evidence import (
    HostProvisioningEvidenceError,
    host_account_snapshot_sha256_v1,
    host_layout_observation_sha256_v1,
    host_layout_spec_sha256_v1,
    host_provisioning_policy_sha256_v1,
    host_provisioning_request_id_v1,
)


HOST_PROVISIONING_PROTOCOL_V1 = (
    "metnos.executor-birth.host-provisioning-journal/v1"
)
MAX_HOST_PROVISIONING_RECORD_BYTES_V1 = 64 * 1024
_RECORD_DOMAIN_V1 = b"metnos.executor-birth.host-provisioning-record/v1\0"
_RECORD_SEAL_V1 = object()


class HostProvisioningStateV1(str, Enum):
    PLANNED = "PLANNED"
    ACCOUNT_READY = "ACCOUNT_READY"
    LAYOUT_READY = "LAYOUT_READY"
    HOST_VERIFIED = "HOST_VERIFIED"


class HostProvisioningIntentV1(str, Enum):
    ENSURE_ACCOUNT = "ENSURE_ACCOUNT"
    ENSURE_LAYOUT = "ENSURE_LAYOUT"
    VERIFY_HOST = "VERIFY_HOST"


_STATES_V1 = tuple(HostProvisioningStateV1)
_INTENTS_V1 = (
    HostProvisioningIntentV1.ENSURE_ACCOUNT,
    HostProvisioningIntentV1.ENSURE_LAYOUT,
    HostProvisioningIntentV1.VERIFY_HOST,
    None,
)


class HostProvisioningJournalError(RuntimeError):
    """A value is outside the closed host-provisioning journal grammar."""

    def __init__(self, detail: str = "record") -> None:
        self.code = "host_provisioning_journal_invalid"
        self.detail = detail
        super().__init__(self.code)


def _invalid(detail: str) -> HostProvisioningJournalError:
    return HostProvisioningJournalError(detail)


@dataclass(frozen=True, slots=True)
class HostProvisioningRecordV1:
    sequence: int
    state: HostProvisioningStateV1
    intent: HostProvisioningIntentV1 | None
    previous_record_sha256: str | None
    request_id: str
    policy_sha256: str
    account_sha256: str | None
    layout_spec_sha256: str | None
    layout_observation_sha256: str | None
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_record_v1(self)

    @property
    def record_sha256(self) -> str:
        encoded = encode_canonical_ascii_v1(_record_material_v1(self))
        return framed_sha256_v1(_RECORD_DOMAIN_V1, encoded)


def _record_material_v1(record: HostProvisioningRecordV1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": HOST_PROVISIONING_PROTOCOL_V1,
        "sequence": record.sequence,
        "state": record.state.value,
        "intent": record.intent.value if record.intent is not None else None,
        "previous_record_sha256": record.previous_record_sha256,
        "request_id": record.request_id,
        "policy_sha256": record.policy_sha256,
        "account_sha256": record.account_sha256,
        "layout_spec_sha256": record.layout_spec_sha256,
        "layout_observation_sha256": record.layout_observation_sha256,
    }


def _validate_record_v1(record: HostProvisioningRecordV1) -> None:
    sequence = record.sequence
    if (
        record._seal is not _RECORD_SEAL_V1
        or type(sequence) is not int
        or not 0 <= sequence < len(_STATES_V1)
        or record.state is not _STATES_V1[sequence]
        or record.intent is not _INTENTS_V1[sequence]
        or record.request_id != host_provisioning_request_id_v1()
        or record.policy_sha256 != host_provisioning_policy_sha256_v1()
    ):
        raise _invalid("record_grammar")
    first = sequence == 0
    if first != (record.previous_record_sha256 is None):
        raise _invalid("previous_record")
    if not first and not is_framed_sha256_v1(record.previous_record_sha256):
        raise _invalid("previous_record")
    digests = (
        record.account_sha256,
        record.layout_spec_sha256,
        record.layout_observation_sha256,
    )
    expected = (sequence >= 1, sequence >= 1, sequence >= 2)
    if any(present != is_framed_sha256_v1(value)
           for present, value in zip(expected, digests)):
        raise _invalid("state_payload")


def _record_value_v1(record: HostProvisioningRecordV1) -> dict[str, object]:
    value = _record_material_v1(record)
    value["record_sha256"] = record.record_sha256
    return value


_RECORD_KEYS_V1 = frozenset({
    "schema_version", "protocol", "sequence", "state", "intent",
    "previous_record_sha256", "request_id", "policy_sha256",
    "account_sha256", "layout_spec_sha256", "layout_observation_sha256",
    "record_sha256",
})


def encode_host_provisioning_record_v1(record: HostProvisioningRecordV1) -> bytes:
    """Encode one sealed record in its sole admitted representation."""
    if (type(record) is not HostProvisioningRecordV1
            or record._seal is not _RECORD_SEAL_V1):
        raise _invalid("record_type")
    encoded = encode_canonical_ascii_v1(_record_value_v1(record))
    if len(encoded) > MAX_HOST_PROVISIONING_RECORD_BYTES_V1:
        raise _invalid("record_size")
    return encoded


def _decode_value_v1(value: object) -> HostProvisioningRecordV1:
    if (
        type(value) is not dict
        or set(value) != _RECORD_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("protocol") != HOST_PROVISIONING_PROTOCOL_V1
    ):
        raise _invalid("record_schema")
    try:
        state = HostProvisioningStateV1(value["state"])
        raw_intent = value["intent"]
        intent = None if raw_intent is None else HostProvisioningIntentV1(raw_intent)
        record = HostProvisioningRecordV1(
            value["sequence"], state, intent, value["previous_record_sha256"],
            value["request_id"], value["policy_sha256"],
            value["account_sha256"], value["layout_spec_sha256"],
            value["layout_observation_sha256"], _RECORD_SEAL_V1,
        )
    except HostProvisioningJournalError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid("record_decode") from exc
    if value["record_sha256"] != record.record_sha256:
        raise _invalid("record_hash")
    return record


def decode_host_provisioning_record_v1(raw: bytes) -> HostProvisioningRecordV1:
    """Decode one bounded, canonical, duplicate-free, self-hashed record."""
    try:
        value = decode_canonical_ascii_v1(
            raw, maximum=MAX_HOST_PROVISIONING_RECORD_BYTES_V1,
        )
    except CanonicalDocumentError as exc:
        raise _invalid("record_document") from exc
    return _decode_value_v1(value)


def _require_link_v1(
    previous: HostProvisioningRecordV1,
    current: HostProvisioningRecordV1,
) -> None:
    immutable_digests_changed = previous.sequence >= 1 and (
        current.account_sha256 != previous.account_sha256
        or current.layout_spec_sha256 != previous.layout_spec_sha256
    )
    observation_changed = previous.sequence >= 2 and (
        current.layout_observation_sha256
        != previous.layout_observation_sha256
    )
    if (
        current.sequence != previous.sequence + 1
        or current.previous_record_sha256 != previous.record_sha256
        or current.request_id != previous.request_id
        or current.policy_sha256 != previous.policy_sha256
        or immutable_digests_changed
        or observation_changed
    ):
        raise _invalid("record_chain")


def decode_host_provisioning_chain_v1(
    encoded_records: tuple[bytes, ...],
) -> tuple[HostProvisioningRecordV1, ...]:
    """Decode one non-empty prefix of the exact four-state chain."""
    if type(encoded_records) is not tuple or not 1 <= len(encoded_records) <= 4:
        raise _invalid("chain_size")
    records = tuple(decode_host_provisioning_record_v1(raw)
                    for raw in encoded_records)
    if records[0].sequence != 0:
        raise _invalid("chain_start")
    for previous, current in zip(records, records[1:]):
        _require_link_v1(previous, current)
    return records


def _require_predecessor_v1(
    record: HostProvisioningRecordV1,
    state: HostProvisioningStateV1,
) -> None:
    if (
        type(record) is not HostProvisioningRecordV1
        or record._seal is not _RECORD_SEAL_V1
        or record.state is not state
    ):
        raise _invalid("transition_predecessor")


def _advance_v1(
    previous: HostProvisioningRecordV1,
    state: HostProvisioningStateV1,
    *,
    account_sha256: str,
    layout_spec_sha256: str,
    layout_observation_sha256: str | None,
) -> HostProvisioningRecordV1:
    record = HostProvisioningRecordV1(
        previous.sequence + 1, state, _INTENTS_V1[previous.sequence + 1],
        previous.record_sha256, previous.request_id, previous.policy_sha256,
        account_sha256, layout_spec_sha256, layout_observation_sha256,
        _RECORD_SEAL_V1,
    )
    _require_link_v1(previous, record)
    return record


def _account_digests_v1(account: PosixAccountSnapshotV1) -> tuple[str, str]:
    try:
        return (
            host_account_snapshot_sha256_v1(account),
            host_layout_spec_sha256_v1(account),
        )
    except HostProvisioningEvidenceError as exc:
        raise _invalid("typed_account_evidence") from exc


def _observation_digest_v1(
    account: PosixAccountSnapshotV1,
    observation: HostLayoutObservationV1,
) -> str:
    try:
        return host_layout_observation_sha256_v1(account, observation)
    except HostProvisioningEvidenceError as exc:
        raise _invalid("typed_layout_evidence") from exc


def plan_host_provisioning_v1() -> HostProvisioningRecordV1:
    """Create the sole durable intent allowed before account effects."""
    return HostProvisioningRecordV1(
        0, HostProvisioningStateV1.PLANNED,
        HostProvisioningIntentV1.ENSURE_ACCOUNT, None,
        host_provisioning_request_id_v1(),
        host_provisioning_policy_sha256_v1(), None, None, None,
        _RECORD_SEAL_V1,
    )


def record_account_ready_v1(
    planned: HostProvisioningRecordV1,
    account: PosixAccountSnapshotV1,
) -> HostProvisioningRecordV1:
    """Bind the canonical account and declare layout intent."""
    _require_predecessor_v1(planned, HostProvisioningStateV1.PLANNED)
    account_sha, layout_sha = _account_digests_v1(account)
    return _advance_v1(
        planned, HostProvisioningStateV1.ACCOUNT_READY,
        account_sha256=account_sha, layout_spec_sha256=layout_sha,
        layout_observation_sha256=None,
    )


def record_layout_ready_v1(
    account_ready: HostProvisioningRecordV1,
    account: PosixAccountSnapshotV1,
    observation: HostLayoutObservationV1,
) -> HostProvisioningRecordV1:
    """Bind an exact converged layout and declare verification intent."""
    _require_predecessor_v1(account_ready, HostProvisioningStateV1.ACCOUNT_READY)
    account_sha, layout_sha = _account_digests_v1(account)
    if (account_sha, layout_sha) != (
        account_ready.account_sha256, account_ready.layout_spec_sha256,
    ):
        raise _invalid("account_changed")
    return _advance_v1(
        account_ready, HostProvisioningStateV1.LAYOUT_READY,
        account_sha256=account_sha, layout_spec_sha256=layout_sha,
        layout_observation_sha256=_observation_digest_v1(account, observation),
    )


def record_host_verified_v1(
    layout_ready: HostProvisioningRecordV1,
    account: PosixAccountSnapshotV1,
    observation: HostLayoutObservationV1,
) -> HostProvisioningRecordV1:
    """Close only after fresh evidence matches every carried digest."""
    _require_predecessor_v1(layout_ready, HostProvisioningStateV1.LAYOUT_READY)
    account_sha, layout_sha = _account_digests_v1(account)
    observation_sha = _observation_digest_v1(account, observation)
    if (account_sha, layout_sha, observation_sha) != (
        layout_ready.account_sha256, layout_ready.layout_spec_sha256,
        layout_ready.layout_observation_sha256,
    ):
        raise _invalid("host_changed")
    return _advance_v1(
        layout_ready, HostProvisioningStateV1.HOST_VERIFIED,
        account_sha256=account_sha, layout_spec_sha256=layout_sha,
        layout_observation_sha256=observation_sha,
    )


__all__ = [
    "HOST_PROVISIONING_PROTOCOL_V1", "MAX_HOST_PROVISIONING_RECORD_BYTES_V1",
    "HostProvisioningIntentV1", "HostProvisioningJournalError",
    "HostProvisioningRecordV1", "HostProvisioningStateV1",
    "decode_host_provisioning_chain_v1", "decode_host_provisioning_record_v1",
    "encode_host_provisioning_record_v1", "plan_host_provisioning_v1",
    "record_account_ready_v1",
    "record_host_verified_v1", "record_layout_ready_v1",
]
