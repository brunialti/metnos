"""Closed coordination contracts for the dormant durable-workload kernel.

The coordinator contains no thread or process pool.  It validates worker
capabilities, computes deterministic retry decisions and delegates the one
authoritative claim transaction to :mod:`storage`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .models import ClosedStringEnum, DurableEffect, RESOURCE_KEYS, RunnerKind
from .schema import (
    ERROR_SCHEMA_VERSION,
    MAX_ERROR_JSON_BYTES,
    MAX_RESULT_JSON_BYTES,
    SchemaValidationError,
    canonical_json,
    digest_json,
)

if TYPE_CHECKING:
    from .storage import DurableWorkloadStore


_WORKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_RUNNER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{1,95}$")
_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,127}/[1-9][0-9]*$")
_ERROR_CLASS_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_MESSAGE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,95}$")
_ERROR_CLASSES = frozenset({
    "invalid_plan",
    "inventory_unstable",
    "source_missing",
    "executor_transient",
    "executor_permanent",
    "contract_violation",
    "budget_exhausted",
    "lease_lost",
    "publication_ambiguous",
    "capability_unavailable",
    "cancelled",
})
_ERROR_RETRY_MODES = frozenset({"automatic", "reconcile", "manual", "never"})
MAX_LEASE_DURATION = timedelta(days=1)


class LeaseMutationStatus(ClosedStringEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    STALE_FENCE = "stale_fence"
    LEASE_EXPIRED = "lease_expired"
    INVALID_STATE = "invalid_state"
    STOP_REQUESTED = "stop_requested"


class CommitStatus(ClosedStringEnum):
    COMMITTED = "committed"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    STALE_FENCE = "stale_fence"
    LEASE_EXPIRED = "lease_expired"
    DEADLINE_EXPIRED = "deadline_expired"
    DIGEST_CONFLICT = "digest_conflict"
    INVALID_STATE = "invalid_state"


class RetryDecision(ClosedStringEnum):
    RETRY = "retry"
    FAIL_PERMANENT = "fail_permanent"
    NEEDS_ATTENTION = "needs_attention"


class FailureStatus(ClosedStringEnum):
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED_PERMANENT = "failed_permanent"
    NEEDS_ATTENTION = "needs_attention"
    STALE_FENCE = "stale_fence"
    LEASE_EXPIRED = "lease_expired"
    INVALID_STATE = "invalid_state"


def require_worker_id(worker_id: str) -> str:
    if not isinstance(worker_id, str) or not _WORKER_RE.fullmatch(worker_id):
        raise ValueError(
            "worker_id must contain 1..128 ASCII letters, digits, '.', ':', '_' or '-'"
        )
    return worker_id


def normalize_instant(value: datetime, *, name: str = "instant") -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def instant_text(value: datetime, *, name: str = "instant") -> str:
    return normalize_instant(value, name=name).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def parse_instant(value: str, *, name: str = "instant") -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{name} must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid timestamp") from exc
    return normalize_instant(parsed, name=name)


def require_lease_duration(value: timedelta) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError("lease_duration must be a timedelta")
    if value <= timedelta(0) or value > MAX_LEASE_DURATION:
        raise ValueError("lease_duration must be greater than zero and at most one day")
    return value


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_delay_ms: int
    max_delay_ms: int
    retryable_error_classes: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RetryPolicy":
        if not isinstance(value, Mapping):
            raise ValueError("retry policy must be an object")
        expected = {
            "max_attempts", "base_delay_ms", "max_delay_ms",
            "retryable_error_classes",
        }
        if set(value) != expected:
            raise ValueError("retry policy fields do not match the v1 contract")
        attempts = value["max_attempts"]
        base = value["base_delay_ms"]
        maximum = value["max_delay_ms"]
        errors = value["retryable_error_classes"]
        if (
            isinstance(attempts, bool) or not isinstance(attempts, int)
            or not 1 <= attempts <= 32
        ):
            raise ValueError("retry max_attempts must be an integer in 1..32")
        if (
            isinstance(base, bool) or not isinstance(base, int)
            or not 0 <= base <= 86_400_000
            or isinstance(maximum, bool) or not isinstance(maximum, int)
            or not base <= maximum <= 86_400_000
        ):
            raise ValueError("retry delays are outside the v1 bounds")
        if (
            isinstance(errors, (str, bytes))
            or not isinstance(errors, (list, tuple))
            or len(errors) > 32
        ):
            raise ValueError("retryable_error_classes must be a bounded array")
        normalized = tuple(errors)
        if (
            any(not isinstance(item, str) or not _ERROR_CLASS_RE.fullmatch(item)
                for item in normalized)
        ):
            raise ValueError("retryable_error_classes contains an invalid class")
        if len(normalized) != len(set(normalized)):
            raise ValueError("retryable_error_classes contains duplicates")
        return cls(attempts, base, maximum, normalized)


@dataclass(frozen=True, slots=True)
class WorkerCapabilities:
    """Explicit runner bindings and finite resource ceilings for one worker."""

    runner_bindings: tuple[tuple[RunnerKind, str], ...]
    resource_limits: tuple[tuple[str, int], ...]
    effect_profiles: tuple[DurableEffect, ...] = (DurableEffect.PURE,)

    def __post_init__(self) -> None:
        if not 1 <= len(self.runner_bindings) <= 256:
            raise ValueError("runner_bindings must contain 1..256 entries")
        normalized_bindings = tuple(sorted(self.runner_bindings, key=lambda item: (
            str(item[0]), item[1],
        )))
        if normalized_bindings != self.runner_bindings:
            raise ValueError("runner_bindings must be sorted canonically")
        if len(normalized_bindings) != len(set(normalized_bindings)):
            raise ValueError("runner_bindings must not contain duplicates")
        for kind, name in normalized_bindings:
            if not isinstance(kind, RunnerKind):
                raise TypeError("runner binding kind must be RunnerKind")
            if not isinstance(name, str) or not _RUNNER_RE.fullmatch(name):
                raise ValueError("runner binding name is invalid")

        normalized_effects = tuple(sorted(self.effect_profiles, key=str))
        if normalized_effects != self.effect_profiles or not normalized_effects:
            raise ValueError("effect_profiles must be non-empty and canonical")
        if len(normalized_effects) != len(set(normalized_effects)):
            raise ValueError("effect_profiles must not contain duplicates")
        if any(not isinstance(effect, DurableEffect) for effect in normalized_effects):
            raise TypeError("effect_profiles must contain DurableEffect values")

        if tuple(key for key, _amount in self.resource_limits) != RESOURCE_KEYS:
            raise ValueError("resource_limits must contain every v1 key canonically")
        for key, amount in self.resource_limits:
            maximum = 32 if key in {"llm", "vlm"} else 64
            if (
                key not in RESOURCE_KEYS or isinstance(amount, bool)
                or not isinstance(amount, int) or not 0 <= amount <= maximum
            ):
                raise ValueError("resource limit is outside the v1 bounds")

    @classmethod
    def create(
        cls,
        runner_bindings: tuple[tuple[RunnerKind | str, str], ...],
        resource_limits: Mapping[str, int],
        *,
        effect_profiles: tuple[DurableEffect | str, ...] = (DurableEffect.PURE,),
    ) -> "WorkerCapabilities":
        bindings = tuple(sorted(
            ((RunnerKind(kind), name) for kind, name in runner_bindings),
            key=lambda item: (item[0].value, item[1]),
        ))
        if not isinstance(resource_limits, Mapping):
            raise TypeError("resource_limits must be a mapping")
        if set(resource_limits) != set(RESOURCE_KEYS):
            raise ValueError("resource_limits must contain exactly the v1 keys")
        supplied = dict(resource_limits)
        resources = tuple((key, supplied[key]) for key in RESOURCE_KEYS)
        effects = tuple(sorted(
            (DurableEffect(effect) for effect in effect_profiles), key=str,
        ))
        return cls(bindings, resources, effects)

    def resource_map(self) -> dict[str, int]:
        return dict(self.resource_limits)

    def accepted_effects(self) -> tuple[str, ...]:
        return tuple(effect.value for effect in self.effect_profiles)


@dataclass(frozen=True, slots=True)
class Lease:
    owner_user_id: str
    workload_id: str
    revision_id: str
    stage_id: str
    stage_key: str
    unit_id: str
    unit_key: str
    attempt_id: str
    attempt_number: int
    fence: int
    worker_id: str
    lease_expires_at: str
    runner_kind: RunnerKind
    runner_name: str
    effect_profile: DurableEffect
    output_schema_version: str
    resource_claims: tuple[tuple[str, int], ...]
    retry_policy: RetryPolicy
    timeout_s: int
    manual_retry: bool = False

    def __post_init__(self) -> None:
        require_worker_id(self.worker_id)
        if any(
            not isinstance(value, str) or not value
            for value in (
                self.owner_user_id, self.workload_id, self.revision_id,
                self.stage_id, self.stage_key, self.unit_id, self.unit_key,
                self.attempt_id, self.runner_name,
            )
        ):
            raise ValueError("lease identities must be non-empty strings")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (self.attempt_number, self.fence)
        ):
            raise ValueError("lease attempt and fence must be positive")
        parse_instant(self.lease_expires_at, name="lease_expires_at")
        if not isinstance(self.runner_kind, RunnerKind):
            raise TypeError("lease runner_kind must be RunnerKind")
        if not isinstance(self.effect_profile, DurableEffect):
            raise TypeError("lease effect_profile must be DurableEffect")
        if not _SCHEMA_RE.fullmatch(self.output_schema_version):
            raise ValueError("lease output schema version is invalid")
        if tuple(key for key, _amount in self.resource_claims) != RESOURCE_KEYS:
            raise ValueError("lease resource claims are not canonical")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, int)
            or not 1 <= self.timeout_s <= 86_400
        ):
            raise ValueError("lease timeout_s must be an integer in 1..86400")
        if not isinstance(self.manual_retry, bool):
            raise TypeError("lease manual_retry must be boolean")


def _canonical_result(
    schema_version: str, payload: Mapping[str, Any],
) -> tuple[str, str]:
    if not isinstance(schema_version, str) or not _SCHEMA_RE.fullmatch(schema_version):
        raise SchemaValidationError("result schema_version is invalid")
    payload_json = canonical_json(payload, max_bytes=MAX_RESULT_JSON_BYTES)
    digest = digest_json(
        "durable-result",
        {"schema_version": schema_version, "payload": payload},
        max_bytes=MAX_RESULT_JSON_BYTES,
    )
    return payload_json, digest


@dataclass(frozen=True, slots=True)
class ValidatedResult:
    schema_version: str
    payload_json: str
    digest: str

    @classmethod
    def from_payload(
        cls, schema_version: str, payload: Mapping[str, Any],
    ) -> "ValidatedResult":
        if not isinstance(schema_version, str) or not _SCHEMA_RE.fullmatch(schema_version):
            raise SchemaValidationError("result schema_version is invalid")
        if not isinstance(payload, Mapping):
            raise SchemaValidationError("v1 result payload must be an object")
        payload_json, digest = _canonical_result(schema_version, payload)
        return cls(schema_version, payload_json, digest)

    def __post_init__(self) -> None:
        try:
            payload = json.loads(self.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SchemaValidationError("result payload_json is invalid") from exc
        if not isinstance(payload, dict):
            raise SchemaValidationError("v1 result payload must be an object")
        rebuilt_json, rebuilt_digest = _canonical_result(self.schema_version, payload)
        if rebuilt_json != self.payload_json or rebuilt_digest != self.digest:
            raise SchemaValidationError("validated result is not canonical or digest-bound")


@dataclass(frozen=True, slots=True)
class StructuredAttemptError:
    error_class: str
    payload_json: str

    @classmethod
    def create(
        cls,
        error_class: str,
        *,
        code: str,
        message_key: str,
        retry: str,
        occurred_at: datetime,
        details_redacted: Mapping[str, Any] | None = None,
    ) -> "StructuredAttemptError":
        if error_class not in _ERROR_CLASSES:
            raise SchemaValidationError("error_class is outside the frozen vocabulary")
        if not isinstance(code, str) or not _ERROR_CODE_RE.fullmatch(code):
            raise SchemaValidationError("error code is invalid")
        if not isinstance(message_key, str) or not _MESSAGE_KEY_RE.fullmatch(message_key):
            raise SchemaValidationError("error message_key is invalid")
        if retry not in _ERROR_RETRY_MODES:
            raise SchemaValidationError("error retry mode is invalid")
        details: Mapping[str, Any] = (
            {} if details_redacted is None else details_redacted
        )
        if not isinstance(details, Mapping) or len(details) > 32:
            raise SchemaValidationError("details_redacted must be a bounded object")
        payload = {
            "schema_version": ERROR_SCHEMA_VERSION,
            "error_class": error_class,
            "code": code,
            "message_key": message_key,
            "retry": retry,
            "scope": "attempt",
            "details_redacted": details,
            "occurred_at": instant_text(occurred_at, name="occurred_at"),
        }
        return cls(
            error_class,
            canonical_json(payload, max_bytes=MAX_ERROR_JSON_BYTES),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.error_class, str) or not _ERROR_CLASS_RE.fullmatch(
            self.error_class
        ):
            raise SchemaValidationError("error_class is invalid")
        try:
            value = json.loads(self.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SchemaValidationError("structured error payload is invalid") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {
                "schema_version", "error_class", "code", "message_key",
                "retry", "scope", "details_redacted", "occurred_at",
            }
            or value.get("schema_version") != ERROR_SCHEMA_VERSION
            or value.get("error_class") != self.error_class
            or self.error_class not in _ERROR_CLASSES
            or not isinstance(value.get("code"), str)
            or not _ERROR_CODE_RE.fullmatch(value["code"])
            or not isinstance(value.get("message_key"), str)
            or not _MESSAGE_KEY_RE.fullmatch(value["message_key"])
            or value.get("retry") not in _ERROR_RETRY_MODES
            or value.get("scope") != "attempt"
            or not isinstance(value.get("details_redacted"), dict)
            or len(value["details_redacted"]) > 32
            or canonical_json(value, max_bytes=MAX_ERROR_JSON_BYTES) != self.payload_json
        ):
            raise SchemaValidationError("structured error is not canonical or class-bound")
        parse_instant(value["occurred_at"], name="occurred_at")


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    status: CommitStatus
    result_id: str | None
    winning_digest: str | None
    proposed_digest: str
    stage_terminal: bool = False


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    status: FailureStatus
    next_attempt_at: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    expired: int = 0
    returned_pending: int = 0
    retry_scheduled: int = 0
    failed_permanent: int = 0
    needs_attention: int = 0
    retry_promoted: int = 0


def deterministic_retry_delay_ms(
    unit_key: str,
    attempt_number: int,
    *,
    base_delay_ms: int,
    max_delay_ms: int,
) -> int:
    """Return capped exponential delay with stable 75..100% jitter."""
    if not isinstance(unit_key, str) or not unit_key:
        raise ValueError("unit_key must be a non-empty string")
    if (
        isinstance(attempt_number, bool) or not isinstance(attempt_number, int)
        or attempt_number < 1
    ):
        raise ValueError("attempt_number must be positive")
    if (
        isinstance(base_delay_ms, bool) or not isinstance(base_delay_ms, int)
        or isinstance(max_delay_ms, bool) or not isinstance(max_delay_ms, int)
        or not 0 <= base_delay_ms <= max_delay_ms
    ):
        raise ValueError("retry delay bounds are invalid")
    if base_delay_ms == 0 or max_delay_ms == 0:
        return 0
    exponent = min(attempt_number - 1, 62)
    nominal = min(max_delay_ms, base_delay_ms * (1 << exponent))
    lower = (nominal * 3) // 4
    span = nominal - lower + 1
    material = f"{unit_key}\x00{attempt_number}".encode("utf-8")
    jitter = int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % span
    return lower + jitter


def decide_retry(
    *,
    effect_profile: DurableEffect,
    retry_policy: RetryPolicy,
    attempt_number: int,
    error_class: str,
    manual_retry: bool = False,
) -> RetryDecision:
    """Derive retry disposition only from the frozen stage contract."""
    if not isinstance(effect_profile, DurableEffect):
        raise TypeError("effect_profile must be DurableEffect")
    if not isinstance(error_class, str) or not _ERROR_CLASS_RE.fullmatch(error_class):
        raise ValueError("error_class is invalid")
    if not isinstance(manual_retry, bool):
        raise TypeError("manual_retry must be boolean")
    if error_class in {
        "budget_exhausted",
        "capability_unavailable",
        "publication_ambiguous",
        "source_missing",
    }:
        # These conditions need restored authority, reconciliation or a new
        # revision.  Retrying the same frozen attempt automatically would
        # either repeat an ambiguous effect or fail without changing facts.
        return RetryDecision.NEEDS_ATTENTION
    if effect_profile in {DurableEffect.RECONCILABLE, DurableEffect.MANUAL_ONLY}:
        return RetryDecision.NEEDS_ATTENTION
    if manual_retry and error_class in retry_policy.retryable_error_classes:
        # The recorded owner decision grants exactly this attempt.  A further
        # retryable failure needs another explicit grant; it never turns into
        # an unbounded automatic loop.
        return RetryDecision.NEEDS_ATTENTION
    if (
        error_class in retry_policy.retryable_error_classes
        and attempt_number < retry_policy.max_attempts
    ):
        return RetryDecision.RETRY
    return RetryDecision.FAIL_PERMANENT


class DurableCoordinator:
    """Small dormant façade; callers own its store and scheduling loop."""

    def __init__(
        self,
        store: "DurableWorkloadStore",
        capabilities: WorkerCapabilities,
        *,
        lease_duration: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self.capabilities = capabilities
        self.lease_duration = require_lease_duration(lease_duration)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def claim(self, worker_id: str) -> Lease | None:
        return self._store.claim_next(
            worker_id,
            self._clock(),
            self.lease_duration,
            self.capabilities,
        )

    def reconcile(self, *, batch_size: int = 100) -> ReconcileOutcome:
        return self._store.reconcile_expired(self._clock(), batch_size)


__all__ = [
    "CommitOutcome",
    "CommitStatus",
    "DurableCoordinator",
    "FailureOutcome",
    "FailureStatus",
    "Lease",
    "LeaseMutationStatus",
    "ReconcileOutcome",
    "RetryDecision",
    "RetryPolicy",
    "StructuredAttemptError",
    "ValidatedResult",
    "WorkerCapabilities",
    "decide_retry",
    "deterministic_retry_delay_ms",
    "instant_text",
    "normalize_instant",
    "parse_instant",
    "require_lease_duration",
    "require_worker_id",
]
