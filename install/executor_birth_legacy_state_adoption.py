"""Crash-resumable orchestration for the one-time legacy-state adoption."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import executor_birth_legacy_state_journal as journal
from executor_birth_legacy_state_policy import LegacyStateObservationV1
from executor_birth_legacy_state_request import (
    LegacyStateError,
    LegacyStateRequestV1,
    require_canonical_legacy_state_request_v1,
)


class LegacyStateAdoptionError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(code)


def _fail(detail: str, *, recovery: bool = False):
    code = (
        "birth_legacy_state_recovery_required"
        if recovery else "birth_legacy_state_invalid"
    )
    return LegacyStateAdoptionError(code, detail)


class LegacyStateEffectsV1(Protocol):
    def load_records(self) -> tuple[bytes, ...]: ...
    def append_record(self, sequence: int, encoded: bytes) -> None: ...
    def observe(self) -> LegacyStateObservationV1: ...
    def adopt_authoring(
        self, before: LegacyStateObservationV1,
    ) -> LegacyStateObservationV1: ...
    def checkpoint(self, name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class LegacyStateAdoptionResultV1:
    record_sha256: str
    changed: bool
    ready: bool


def _records_v1(effects: LegacyStateEffectsV1):
    raw = effects.load_records()
    if type(raw) is not tuple:
        raise _fail("journal container", recovery=True)
    if not raw:
        return ()
    try:
        return journal.decode_legacy_state_chain_v1(raw)
    except LegacyStateError as exc:
        raise _fail("journal chain", recovery=True) from exc


def _append_v1(effects: LegacyStateEffectsV1, record) -> None:
    try:
        effects.append_record(
            record.sequence, journal.encode_legacy_state_record_v1(record),
        )
    except LegacyStateAdoptionError:
        raise
    except Exception as exc:
        raise _fail("journal append", recovery=True) from exc
    effects.checkpoint("record_" + record.state.value.lower())


def _planned_v1(request, effects, records):
    if records:
        return records
    record = journal.plan_legacy_state_v1(request)
    _append_v1(effects, record)
    return (record,)


def _inventoried_v1(request, effects, latest):
    observation = effects.observe()
    record = journal.record_legacy_state_inventoried_v1(
        latest, request, observation,
    )
    _append_v1(effects, record)
    return record


def _adopted_v1(request, effects, latest):
    before = effects.observe()
    if not journal.legacy_state_adoption_resume_valid_v1(
        request, latest.adoption_target_sha256, before,
    ):
        raise _fail("adoption target changed", recovery=True)
    after = effects.adopt_authoring(before)
    record = journal.record_authoring_adopted_v1(
        latest, request, before, after,
    )
    _append_v1(effects, record)
    return record


def _ready_v1(request, effects, latest):
    observation = effects.observe()
    record = journal.record_legacy_state_ready_v1(
        latest, request, observation,
    )
    _append_v1(effects, record)
    return record


def prepare_legacy_state_authoring_v1(
    request: LegacyStateRequestV1, effects: LegacyStateEffectsV1,
) -> LegacyStateAdoptionResultV1:
    """Reach AUTHORING_ADOPTED; READY is reserved for post-convergence."""
    try:
        require_canonical_legacy_state_request_v1(request)
        records = _planned_v1(request, effects, _records_v1(effects))
        changed = len(records) < 3
        latest = records[-1]
        if latest.request_id != request.request_id:
            raise _fail("journal request changed", recovery=True)
        if latest.state is journal.LegacyStateV1.LEGACY_STATE_READY:
            return LegacyStateAdoptionResultV1(
                latest.record_sha256, changed, True,
            )
        if latest.state is journal.LegacyStateV1.PLANNED:
            latest = _inventoried_v1(request, effects, latest)
        if latest.state is journal.LegacyStateV1.INVENTORIED:
            latest = _adopted_v1(request, effects, latest)
        if latest.state is not journal.LegacyStateV1.AUTHORING_ADOPTED:
            raise _fail("journal terminal", recovery=True)
        return LegacyStateAdoptionResultV1(
            latest.record_sha256, changed, False,
        )
    except LegacyStateAdoptionError:
        raise
    except LegacyStateError as exc:
        raise _fail(exc.detail, recovery=True) from exc


def _complete_legacy_state_ready_v1(
    request: LegacyStateRequestV1, effects: LegacyStateEffectsV1, *,
    expected_record_sha256: str,
) -> LegacyStateAdoptionResultV1:
    """Record exact-service READY only after contract convergence."""
    try:
        require_canonical_legacy_state_request_v1(request)
        records = _records_v1(effects)
        latest = records[-1]
        if (
            latest.request_id != request.request_id
            or latest.record_sha256 != expected_record_sha256
        ):
            raise _fail("journal changed", recovery=True)
        changed = latest.state is journal.LegacyStateV1.AUTHORING_ADOPTED
        if changed:
            latest = _ready_v1(request, effects, latest)
        if latest.state is not journal.LegacyStateV1.LEGACY_STATE_READY:
            raise _fail("journal terminal", recovery=True)
        return LegacyStateAdoptionResultV1(
            latest.record_sha256, changed, True,
        )
    except LegacyStateAdoptionError:
        raise
    except (IndexError, LegacyStateError) as exc:
        raise _fail("journal chain", recovery=True) from exc


__all__ = [
    "LegacyStateAdoptionError", "LegacyStateAdoptionResultV1",
    "LegacyStateEffectsV1", "prepare_legacy_state_authoring_v1",
]
