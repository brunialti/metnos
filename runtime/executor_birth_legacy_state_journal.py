"""Canonical closed journal for first-transition legacy state adoption."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re

from executor_birth_canonical import (
    CanonicalDocumentError,
    decode_canonical_ascii_v1,
    encode_canonical_ascii_v1,
)
from executor_birth_crypto_framing import framed_sha256_v1, is_framed_sha256_v1
import executor_birth_legacy_state_wire as wire
from executor_birth_legacy_state_request import (
    LegacyStateError,
    LegacyStateRequestV1,
    require_canonical_legacy_state_request_v1,
)
from executor_birth_legacy_state_policy import (
    LEGACY_STATE_PROTOCOL_V1,
    LEGACY_STATE_FSM_V1,
    LegacyStateDispositionV1,
    LegacyStateObservationV1,
    classify_legacy_state_v1,
    legacy_state_adoption_target_sha256_v1,
    legacy_state_policy_sha256_v1,
)


MAX_LEGACY_STATE_RECORD_BYTES_V1 = 64 * 1024
_RECORD_DOMAIN = b"metnos.executor-birth.legacy-state-record/v1\0"
_RECORD_SEAL = object()
_WIRE_DIGEST_RE_V1 = re.compile(r"sha256:[0-9a-f]{64}\Z")


class LegacyStateV1(str, Enum):
    PLANNED = "PLANNED"
    INVENTORIED = "INVENTORIED"
    AUTHORING_ADOPTED = "AUTHORING_ADOPTED"
    LEGACY_STATE_READY = "LEGACY_STATE_READY"


class LegacyStateIntentV1(str, Enum):
    INVENTORY = "INVENTORY"
    ADOPT_AUTHORING = "ADOPT_AUTHORING"
    CONVERGE_CONTRACTS_AND_VERIFY = "CONVERGE_CONTRACTS_AND_VERIFY"


_STATES = tuple(LegacyStateV1)
_INTENTS = (*tuple(LegacyStateIntentV1), None)
_RECORD_KEYS = frozenset({
    "adoption_target_sha256", "authoring_sha256", "intent", "inventory_disposition",
    "inventory_sha256", "policy_sha256", "previous_record_sha256",
    "protocol", "ready_sha256", "record_sha256", "request_id",
    "schema_version", "sequence", "state",
})


def _invalid(detail: str) -> LegacyStateError:
    return LegacyStateError(detail)


def _require_fsm_v1() -> None:
    actual = tuple(
        (state.value, None if intent is None else intent.value)
        for state, intent in zip(_STATES, _INTENTS)
    )
    if actual != LEGACY_STATE_FSM_V1:
        raise _invalid("fsm_policy")


_require_fsm_v1()


def legacy_state_wire_profile_v1() -> dict[str, object]:
    return {
        "dispositions": frozenset(
            item.value for item in LegacyStateDispositionV1
            if item is not LegacyStateDispositionV1.invalid
        ),
        "fsm": LEGACY_STATE_FSM_V1,
        "maximum_record_bytes": MAX_LEGACY_STATE_RECORD_BYTES_V1,
        "output_fields": wire.LEGACY_STATE_WIRE_OUTPUT_FIELDS_V1,
        "policy_sha256": legacy_state_policy_sha256_v1(),
        "protocol": LEGACY_STATE_PROTOCOL_V1,
        "record_domain": _RECORD_DOMAIN,
        "record_keys": _RECORD_KEYS,
    }


def _decode_wire_canonical_v1(raw: bytes, maximum: int) -> object:
    return decode_canonical_ascii_v1(raw, maximum=maximum)


@dataclass(frozen=True, slots=True)
class LegacyStateRecordV1:
    sequence: int
    state: LegacyStateV1
    intent: LegacyStateIntentV1 | None
    previous_record_sha256: str | None
    request_id: str
    policy_sha256: str
    inventory_sha256: str | None
    inventory_disposition: LegacyStateDispositionV1 | None
    adoption_target_sha256: str | None
    authoring_sha256: str | None
    ready_sha256: str | None
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_record(self)

    @property
    def record_sha256(self) -> str:
        payload = encode_canonical_ascii_v1(_record_material(self))
        return framed_sha256_v1(_RECORD_DOMAIN, payload)


def _record_material(record: LegacyStateRecordV1) -> dict[str, object]:
    return {
        "adoption_target_sha256": record.adoption_target_sha256,
        "authoring_sha256": record.authoring_sha256,
        "intent": None if record.intent is None else record.intent.value,
        "inventory_disposition": (
            None if record.inventory_disposition is None
            else record.inventory_disposition.value
        ),
        "inventory_sha256": record.inventory_sha256,
        "policy_sha256": record.policy_sha256,
        "previous_record_sha256": record.previous_record_sha256,
        "protocol": LEGACY_STATE_PROTOCOL_V1,
        "ready_sha256": record.ready_sha256,
        "request_id": record.request_id, "schema_version": 1,
        "sequence": record.sequence, "state": record.state.value,
    }


def _validate_record(record: LegacyStateRecordV1) -> None:
    sequence = record.sequence
    expected = (
        sequence >= 1, sequence >= 1, sequence >= 2, sequence >= 3,
    ) if type(sequence) is int else ()
    values = (
        record.inventory_sha256, record.adoption_target_sha256,
        record.authoring_sha256, record.ready_sha256,
    )
    disposition_present = (
        type(record.inventory_disposition) is LegacyStateDispositionV1
        and record.inventory_disposition is not LegacyStateDispositionV1.invalid
    )
    if (
        record._seal is not _RECORD_SEAL or type(sequence) is not int
        or not 0 <= sequence < 4 or record.state is not _STATES[sequence]
        or record.intent is not _INTENTS[sequence]
        or not is_framed_sha256_v1(record.request_id)
        or record.policy_sha256 != legacy_state_policy_sha256_v1()
        or any(flag != is_framed_sha256_v1(value) for flag, value in zip(expected, values))
        or (sequence >= 2 and record.authoring_sha256 != record.adoption_target_sha256)
        or (sequence >= 1) != disposition_present
        or (sequence == 0) != (record.previous_record_sha256 is None)
        or (sequence > 0 and not is_framed_sha256_v1(record.previous_record_sha256))
    ):
        raise _invalid("record_grammar")


def encode_legacy_state_record_v1(record: LegacyStateRecordV1) -> bytes:
    if type(record) is not LegacyStateRecordV1 or record._seal is not _RECORD_SEAL:
        raise _invalid("record_type")
    value = _record_material(record)
    value["record_sha256"] = record.record_sha256
    encoded = encode_canonical_ascii_v1(value)
    if len(encoded) > MAX_LEGACY_STATE_RECORD_BYTES_V1:
        raise _invalid("record_size")
    return encoded


def decode_legacy_state_record_v1(raw: bytes) -> LegacyStateRecordV1:
    try:
        value = wire.legacy_state_wire_record_v1(
            raw, legacy_state_wire_profile_v1(), _WIRE_DIGEST_RE_V1,
            _decode_wire_canonical_v1, encode_canonical_ascii_v1,
            framed_sha256_v1, _invalid,
        )
        disposition = value["inventory_disposition"]
        record = LegacyStateRecordV1(
            value["sequence"], LegacyStateV1(value["state"]),
            None if value["intent"] is None else LegacyStateIntentV1(value["intent"]),
            value["previous_record_sha256"], value["request_id"],
            value["policy_sha256"], value["inventory_sha256"],
            None if disposition is None else LegacyStateDispositionV1(disposition),
            value["adoption_target_sha256"], value["authoring_sha256"],
            value["ready_sha256"], _RECORD_SEAL,
        )
    except LegacyStateError:
        raise
    except (CanonicalDocumentError, KeyError, TypeError, ValueError) as exc:
        raise _invalid("record_decode") from exc
    return record


def _wire_record_value_v1(record: LegacyStateRecordV1) -> dict[str, object]:
    value = _record_material(record)
    value["record_sha256"] = record.record_sha256
    return value


def _linked(previous: LegacyStateRecordV1, current: LegacyStateRecordV1) -> bool:
    return wire.legacy_state_wire_records_linked_v1(
        _wire_record_value_v1(previous), _wire_record_value_v1(current),
    )


def decode_legacy_state_chain_v1(
    raw: tuple[bytes, ...],
) -> tuple[LegacyStateRecordV1, ...]:
    if type(raw) is not tuple or not 1 <= len(raw) <= 4:
        raise _invalid("chain_size")
    records = tuple(decode_legacy_state_record_v1(item) for item in raw)
    if records[0].sequence != 0:
        raise _invalid("chain_start")
    if any(not _linked(previous, current) for previous, current in zip(records, records[1:])):
        raise _invalid("record_chain")
    return records


def plan_legacy_state_v1(request: LegacyStateRequestV1) -> LegacyStateRecordV1:
    require_canonical_legacy_state_request_v1(request)
    return LegacyStateRecordV1(
        0, LegacyStateV1.PLANNED, LegacyStateIntentV1.INVENTORY, None,
        request.request_id, legacy_state_policy_sha256_v1(),
        None, None, None, None, None, _RECORD_SEAL,
    )


def _advance(previous, request, state, observation) -> LegacyStateRecordV1:
    require_canonical_legacy_state_request_v1(request)
    if previous.request_id != request.request_id:
        raise _invalid("transition_request")
    disposition = classify_legacy_state_v1(request, observation)
    if disposition is LegacyStateDispositionV1.invalid:
        raise _invalid("transition_observation")
    sequence = previous.sequence + 1
    digest = observation.observation_sha256
    target = legacy_state_adoption_target_sha256_v1(request, observation)
    record = LegacyStateRecordV1(
        sequence, state, _INTENTS[sequence], previous.record_sha256,
        previous.request_id, previous.policy_sha256,
        digest if sequence == 1 else previous.inventory_sha256,
        disposition if sequence == 1 else previous.inventory_disposition,
        target if sequence == 1 else previous.adoption_target_sha256,
        digest if sequence == 2 else previous.authoring_sha256,
        digest if sequence == 3 else None, _RECORD_SEAL,
    )
    if not _linked(previous, record):
        raise _invalid("record_chain")
    return record


def record_legacy_state_inventoried_v1(planned, request, observation):
    require_canonical_legacy_state_request_v1(request)
    if type(planned) is not LegacyStateRecordV1 or planned.state is not LegacyStateV1.PLANNED:
        raise _invalid("transition_predecessor")
    return _advance(planned, request, LegacyStateV1.INVENTORIED, observation)


def _same_adoption_entry_v1(request, before, after) -> bool:
    authoring = before.relative_path.parts[0] == "contract-authoring"
    owner = (before.uid, before.gid)
    expected_owner = (
        (request.service_uid, request.service_gid)
        if authoring and owner == (0, 0) else owner
    )
    return (
        before.relative_path == after.relative_path
        and (after.uid, after.gid) == expected_owner
        and before.node_kind is after.node_kind and before.mode == after.mode
        and before.nlink == after.nlink and before.size == after.size
        and before.content_sha256 == after.content_sha256
        and before.has_access_acl == after.has_access_acl
        and before.has_default_acl == after.has_default_acl
        and before.device == after.device and before.inode == after.inode
    )


def legacy_state_adoption_delta_valid_v1(request, before, after) -> bool:
    """Admit no-op or root-to-service ownership change in authoring only."""
    require_canonical_legacy_state_request_v1(request)
    if not all((
        type(request) is LegacyStateRequestV1,
        type(before) is LegacyStateObservationV1,
        type(after) is LegacyStateObservationV1,
    )):
        raise _invalid("adoption_delta_type")
    disposition = classify_legacy_state_v1(request, before)
    if disposition in {
        LegacyStateDispositionV1.fresh, LegacyStateDispositionV1.exact_service,
    }:
        return before == after
    valid = (
        disposition is LegacyStateDispositionV1.root_adoption_required
        and classify_legacy_state_v1(request, after)
        is LegacyStateDispositionV1.exact_service
        and len(before.entries) == len(after.entries)
    )
    return valid and all(
        _same_adoption_entry_v1(request, old, new)
        for old, new in zip(before.entries, after.entries)
    )


def legacy_state_adoption_resume_valid_v1(
    request: LegacyStateRequestV1, target_sha256: str,
    observation: LegacyStateObservationV1,
) -> bool:
    """Accept only states whose normalized adoption projection is unchanged."""
    require_canonical_legacy_state_request_v1(request)
    if (
        not is_framed_sha256_v1(target_sha256)
        or type(observation) is not LegacyStateObservationV1
    ):
        raise _invalid("adoption_resume_type")
    try:
        projected = legacy_state_adoption_target_sha256_v1(
            request, observation,
        )
    except LegacyStateError:
        return False
    return projected == target_sha256


def record_authoring_adopted_v1(inventoried, request, before, after):
    require_canonical_legacy_state_request_v1(request)
    if type(inventoried) is not LegacyStateRecordV1 or inventoried.state is not LegacyStateV1.INVENTORIED:
        raise _invalid("transition_predecessor")
    if (
        type(before) is not LegacyStateObservationV1
        or not legacy_state_adoption_resume_valid_v1(
            request, inventoried.adoption_target_sha256, before,
        )
        or not legacy_state_adoption_delta_valid_v1(request, before, after)
        or after.observation_sha256 != inventoried.adoption_target_sha256
    ):
        raise _invalid("authoring_not_adopted")
    return _advance(
        inventoried, request, LegacyStateV1.AUTHORING_ADOPTED, after,
    )


def record_legacy_state_ready_v1(adopted, request, observation):
    require_canonical_legacy_state_request_v1(request)
    if type(adopted) is not LegacyStateRecordV1 or adopted.state is not LegacyStateV1.AUTHORING_ADOPTED:
        raise _invalid("transition_predecessor")
    if (
        type(observation) is not LegacyStateObservationV1
        or classify_legacy_state_v1(request, observation)
        is not LegacyStateDispositionV1.exact_service
    ):
        raise _invalid("legacy_state_changed")
    return _advance(
        adopted, request, LegacyStateV1.LEGACY_STATE_READY, observation,
    )


__all__ = [
    "MAX_LEGACY_STATE_RECORD_BYTES_V1", "LegacyStateIntentV1",
    "LegacyStateRecordV1", "LegacyStateV1", "decode_legacy_state_chain_v1",
    "decode_legacy_state_record_v1", "encode_legacy_state_record_v1",
    "legacy_state_adoption_delta_valid_v1",
    "legacy_state_adoption_resume_valid_v1", "plan_legacy_state_v1",
    "legacy_state_wire_profile_v1",
    "record_authoring_adopted_v1",
    "record_legacy_state_inventoried_v1", "record_legacy_state_ready_v1",
]
