"""Generic execution bridge for admitted durable units.

The bridge knows only frozen runner contracts, schemas and owner-scoped store
rows.  It contains no domain imports or preset branches: OCR, VLM and every
other capability enter through the same registered runner interfaces.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from .compiler import (
    ApprovedOutputSchema,
    CompilationError,
    FrozenRunnerContract,
    OutputSchemaResolver,
    OutputValidationError,
    RunnerContractResolver,
    _internal_contract,
)
from .coordinator import (
    Lease,
    LeaseMutationStatus,
    StructuredAttemptError,
    ValidatedResult,
    instant_text,
    normalize_instant,
    parse_instant,
)
from .models import (
    AttemptState,
    DurableEffect,
    ExecutionContext,
    RunnerKind,
    SourceResolution,
)
from .schema import (
    MAX_EVENT_JSON_BYTES,
    MAX_SNAPSHOT_JSON_BYTES,
    SchemaValidationError,
    canonical_json,
    digest_json,
)
from .storage import DurableWorkloadStore, ModelUsageContractError
from .worker import (
    DurableWorker,
    ExecutionFailure,
    ExecutionResult,
    WorkerRunOutcome,
    WorkerRunStatus,
)


_TRANSIENT_ERRORS = frozenset({
    "timeout", "remote_timeout", "network", "rate_limited",
    "temporarily_unavailable", "provider_unavailable", "executor_transient",
})
_CONTRACT_ERRORS = frozenset({
    "non_json", "contract_violation", "invalid_output", "schema_mismatch",
})
_CAPABILITY_ERRORS = frozenset({
    "placement", "permission_denied", "capability_unavailable",
    "missing_source_context",
})
_SOURCE_AUTHORITY_RE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,63}")
_CONTROL_MUTATION_BATCH = 200


@dataclass(frozen=True, slots=True)
class _ScheduledRunner:
    """Minimal scheduler view of a frozen non-executor runner."""

    name: str
    execution_policy: dict[str, Any]
    execution_policy_declared: bool
    code_path: None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{context} must be an object")
    return value


def _json_type_matches(value: object, expected: str | None) -> bool:
    if expected is None:
        return True
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def _field(value: object, dotted: str) -> object:
    current = value
    for name in dotted.split("."):
        if not isinstance(current, Mapping) or name not in current:
            raise KeyError(dotted)
        current = current[name]
    return current


def _entry_shard(value: object) -> tuple[str, str, str] | None:
    """Decode the opaque identity selector of one per-entry unit."""
    if not isinstance(value, str) or not value.startswith("entry:"):
        return None
    parts = value.split(":", 3)
    if len(parts) != 4 or not parts[1] or not parts[2] or not parts[3]:
        return None
    return parts[1], parts[2], parts[3]


class DurableExecutionBridge:
    """Execute one admitted unit through a single, generic contract path."""

    def __init__(
        self,
        store: DurableWorkloadStore,
        *,
        runners: RunnerContractResolver,
        output_schemas: OutputSchemaResolver,
        source_resolver: Callable[
            [Mapping[str, Any], ExecutionContext], SourceResolution
        ] | None = None,
        executor_loader: Callable[[str], object] | None = None,
        executor_invoker: Callable[
            [object, Mapping[str, Any], ExecutionContext, int, str | None, str],
            object,
        ] | None = None,
        workload_invoker: Callable[[str, Mapping[str, Any], ExecutionContext], object]
        | None = None,
        internal_runners: Mapping[
            str, Callable[[Mapping[str, Any], ExecutionContext], object]
        ] | None = None,
        device_selector: Callable[[Mapping[str, Any] | None], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.runners = runners
        self.output_schemas = output_schemas
        self._source_resolver = source_resolver
        self._executor_loader = executor_loader or self._load_verified_executor
        self._executor_invoker = executor_invoker or self._invoke_executor
        self._workload_invoker = workload_invoker
        self._internal_runners = dict(internal_runners or {})
        self._device_selector = device_selector or self._source_device
        self._clock = clock or _now

    @staticmethod
    def _load_verified_executor(name: str) -> object:
        from loader import load_catalog

        executor = load_catalog(verify=True, lang="en").get(name)
        if executor is None:
            raise LookupError("verified executor is unavailable")
        return executor

    @staticmethod
    def _source_device(source: Mapping[str, Any] | None) -> str | None:
        if source is None:
            return None
        device_id = source.get("device_id")
        return str(device_id) if isinstance(device_id, str) and device_id else None

    @staticmethod
    def _invoke_executor(
        executor: object,
        args: Mapping[str, Any],
        context: ExecutionContext,
        timeout_s: int,
        device_id: str | None,
        autonomy: str,
    ) -> object:
        from agent_runtime import invoke_executor

        return invoke_executor(
            executor,
            dict(args),
            timeout_s=timeout_s,
            autonomy=autonomy,
            turn_id=f"durable-{context.attempt_id}",
            actor=context.owner_user_id,
            channel="durable_workload",
            target_device=device_id,
            owner_user_id=context.owner_user_id,
            execution_context=context,
        )

    def _failure(
        self,
        error_class: str,
        *,
        code: str,
        message_key: str,
        retry: str,
        details: Mapping[str, Any] | None = None,
        attempt_state: AttemptState = AttemptState.FAILED,
    ) -> ExecutionFailure:
        return ExecutionFailure(StructuredAttemptError.create(
            error_class,
            code=code,
            message_key=message_key,
            retry=retry,
            occurred_at=self._clock(),
            details_redacted=details or {},
        ), attempt_state=attempt_state)

    def _current_contract(
        self,
        stage: Mapping[str, Any],
        schema: ApprovedOutputSchema,
    ) -> FrozenRunnerContract:
        kind = str(stage["runner_kind"])
        name = str(stage["runner_name"])
        try:
            if kind == RunnerKind.INTERNAL.value:
                return _internal_contract(name, schema.name)
            return self.runners.resolve(kind, name)
        except (CompilationError, KeyError, LookupError, ValueError) as exc:
            raise self._failure(
                "capability_unavailable",
                code="execution.runner_unavailable",
                message_key="ERR_DURABLE_RUNNER_UNAVAILABLE",
                retry="manual",
                details={"runner_kind": kind, "runner_name": name},
            ) from exc

    def _verify_frozen_contract(
        self,
        facts: Mapping[str, Any],
    ) -> tuple[FrozenRunnerContract, ApprovedOutputSchema, Mapping[str, Any]]:
        stage = _mapping(facts["stage"], context="stage")
        schema_ref = _mapping(stage["output_schema"], context="output_schema")
        try:
            schema = self.output_schemas.resolve(str(schema_ref["name"]))
            contract = self._current_contract(stage, schema)
        except ExecutionFailure:
            raise
        except (CompilationError, ValueError, KeyError) as exc:
            raise self._failure(
                "contract_violation",
                code="execution.output_schema_unavailable",
                message_key="ERR_DURABLE_OUTPUT_SCHEMA_UNAVAILABLE",
                retry="never",
                details={"stage_key": str(stage["key"])},
            ) from exc
        entries = [
            entry for entry in _mapping(
                facts["catalog_snapshot"], context="catalog_snapshot"
            ).get("entries", ())
            if isinstance(entry, Mapping) and entry.get("stage_key") == stage["key"]
        ]
        if len(entries) != 1:
            raise self._failure(
                "contract_violation",
                code="execution.catalog_entry_missing",
                message_key="ERR_DURABLE_CONTRACT_CHANGED",
                retry="never",
                details={"stage_key": str(stage["key"])},
            )
        expected = entries[0]
        current = contract.snapshot(stage_key=str(stage["key"]), output_schema=schema)
        if current != expected:
            raise self._failure(
                "contract_violation",
                code="execution.contract_changed",
                message_key="ERR_DURABLE_CONTRACT_CHANGED",
                retry="never",
                details={"stage_key": str(stage["key"])},
            )
        return contract, schema, expected

    @staticmethod
    def _select_dependencies(
        facts: Mapping[str, Any],
        stage_key: str,
    ) -> tuple[Mapping[str, Any], ...]:
        stage = _mapping(facts["stage"], context="stage")
        source = facts.get("source")
        selected = [
            item for item in facts.get("dependencies", ())
            if isinstance(item, Mapping) and item.get("stage_key") == stage_key
        ]
        shard_key = stage.get("shard_key")
        if isinstance(shard_key, str) and shard_key.startswith("result:"):
            selected = [
                item for item in selected
                if item.get("result_id") == shard_key.removeprefix("result:")
            ]
        elif isinstance(shard_key, str) and shard_key.startswith("entry:"):
            entry_shard = _entry_shard(shard_key)
            if entry_shard is None:
                return ()
            selected = [
                item for item in selected
                if item.get("result_id") == entry_shard[0]
            ]
        elif isinstance(source, Mapping):
            selected = [
                item for item in selected
                if item.get("source_row_id") in {None, stage.get("source_row_id")}
                or item.get("source_id") == source.get("source_id")
            ]
        return tuple(selected)

    def _dependency_value(
        self,
        facts: Mapping[str, Any],
        reference: Mapping[str, Any],
        *,
        want_array: bool,
    ) -> tuple[object, tuple[str, ...]]:
        stage_key = str(reference["stage"])
        selected = self._select_dependencies(facts, stage_key)
        if not selected:
            raise self._failure(
                "contract_violation",
                code="execution.dependencies_unavailable",
                message_key="ERR_DURABLE_DEPENDENCIES_UNAVAILABLE",
                retry="never",
                details={"dependency_stage": stage_key},
            )
        result_ids = tuple(str(item["result_id"]) for item in selected)
        payloads: list[object] = []
        for item in selected:
            payload = item.get("payload")
            if not isinstance(payload, Mapping):
                raise self._failure(
                    "contract_violation",
                    code="execution.dependency_payload_unavailable",
                    message_key="ERR_DURABLE_DEPENDENCIES_UNAVAILABLE",
                    retry="never",
                    details={"dependency_stage": stage_key},
                )
            payloads.append(payload)
        reference_kind = str(reference["ref"])
        field = reference.get("field")
        if reference_kind == "dependency.entries":
            values: list[object] = []
            shard_key = _mapping(facts["stage"], context="stage").get("shard_key")
            entry_selector: tuple[str, str] | None = None
            if isinstance(shard_key, str) and shard_key.startswith("entry:"):
                parsed_shard = _entry_shard(shard_key)
                if parsed_shard is None:
                    raise self._failure(
                        "contract_violation",
                        code="execution.entry_shard_invalid",
                        message_key="ERR_DURABLE_DEPENDENCIES_UNAVAILABLE",
                        retry="never",
                    )
                entry_selector = (parsed_shard[1], parsed_shard[2])
            for item, payload in zip(selected, payloads, strict=True):
                if item.get("entry_selected") is True:
                    if entry_selector is None:
                        raise self._failure(
                            "contract_violation",
                            code="execution.entry_slice_unexpected",
                            message_key="ERR_DURABLE_DEPENDENCIES_UNAVAILABLE",
                            retry="never",
                            details={"dependency_stage": stage_key},
                        )
                    identity_field, expected_digest = entry_selector
                    identity = payload.get(identity_field)
                    if (
                        not isinstance(identity, str)
                        or digest_json(
                            "durable-entry-shard",
                            {"field": identity_field, "value": identity},
                            max_bytes=MAX_EVENT_JSON_BYTES,
                        ) != expected_digest
                    ):
                        raise self._failure(
                            "contract_violation",
                            code="execution.entry_shard_unavailable",
                            message_key="ERR_DURABLE_DEPENDENCIES_UNAVAILABLE",
                            retry="never",
                            details={"dependency_stage": stage_key},
                        )
                    values.append(payload)
                    continue
                entries = payload.get("entries") if isinstance(payload, Mapping) else None
                if not isinstance(entries, list):
                    raise self._failure(
                        "contract_violation",
                        code="execution.dependency_entries_invalid",
                        message_key="ERR_DURABLE_DEPENDENCIES_UNAVAILABLE",
                        retry="never",
                        details={"dependency_stage": stage_key},
                    )
                if entry_selector is None:
                    values.extend(entries)
                    continue
                identity_field, expected_digest = entry_selector
                matching = [
                    entry
                    for entry in entries
                    if isinstance(entry, Mapping)
                    and isinstance(entry.get(identity_field), str)
                    and digest_json(
                        "durable-entry-shard",
                        {"field": identity_field, "value": entry[identity_field]},
                        max_bytes=MAX_EVENT_JSON_BYTES,
                    ) == expected_digest
                ]
                if len(matching) != 1:
                    raise self._failure(
                        "contract_violation",
                        code="execution.entry_shard_unavailable",
                        message_key="ERR_DURABLE_DEPENDENCIES_UNAVAILABLE",
                        retry="never",
                        details={"dependency_stage": stage_key},
                    )
                values.extend(matching)
        else:
            values = payloads
        if field is not None:
            try:
                values = [_field(value, str(field)) for value in values]
            except KeyError as exc:
                raise self._failure(
                    "contract_violation",
                    code="execution.dependency_field_missing",
                    message_key="ERR_DURABLE_DEPENDENCIES_UNAVAILABLE",
                    retry="never",
                    details={"dependency_stage": stage_key},
                ) from exc
        if want_array:
            return values, result_ids
        if len(values) != 1:
            raise self._failure(
                "contract_violation",
                code="execution.dependency_cardinality_invalid",
                message_key="ERR_DURABLE_DEPENDENCIES_UNAVAILABLE",
                retry="never",
                details={"dependency_stage": stage_key},
            )
        return values[0], result_ids

    def _build_args(
        self,
        facts: Mapping[str, Any],
        contract: FrozenRunnerContract,
        context: ExecutionContext,
    ) -> tuple[dict[str, Any], tuple[str, ...], str | None]:
        stage = _mapping(facts["stage"], context="stage")
        bindings = _mapping(stage["input_bindings"], context="input_bindings")
        input_types = dict(contract.input_types)
        args: dict[str, Any] = {}
        dependency_ids: list[str] = []
        source = facts.get("source")
        resolved_source: SourceResolution | None = None
        source_resolution_digest: str | None = None
        for argument, raw_reference in bindings.items():
            reference = _mapping(raw_reference, context="input_binding")
            expected = input_types.get(str(argument))
            reference_kind = str(reference["ref"])
            if reference_kind == "revision.inventory":
                value: object = facts["inventory"]
            elif reference_kind == "source.record":
                if not isinstance(source, Mapping):
                    raise self._failure(
                        "source_missing",
                        code="execution.source_missing",
                        message_key="ERR_DURABLE_SOURCE_MISSING",
                        retry="manual",
                    )
                value = dict(source)
            elif reference_kind == "source.path":
                if not isinstance(source, Mapping) or self._source_resolver is None:
                    raise self._failure(
                        "source_missing",
                        code="execution.source_unavailable",
                        message_key="ERR_DURABLE_SOURCE_MISSING",
                        retry="manual",
                    )
                try:
                    if resolved_source is None:
                        candidate = self._source_resolver(source, context)
                        if not isinstance(candidate, SourceResolution):
                            raise TypeError("source resolution has no attestation")
                        observed = {
                            "source_id": candidate.source_id,
                            "device_id": candidate.device_id,
                            "content_digest": candidate.content_digest,
                            "size_bytes": candidate.size_bytes,
                            "mtime_ns": candidate.mtime_ns,
                        }
                        sealed = {
                            name: source.get(name) for name in observed
                        }
                        if (
                            not _SOURCE_AUTHORITY_RE.fullmatch(candidate.authority)
                            or not all(
                                isinstance(observed[name], str)
                                for name in (
                                    "source_id", "device_id", "content_digest",
                                )
                            )
                            or type(candidate.size_bytes) is not int
                            or type(candidate.mtime_ns) is not int
                        ):
                            raise TypeError("source attestation is malformed")
                        if observed != sealed:
                            raise ValueError("source attestation does not match inventory")
                        resolved_source = candidate
                        source_resolution_digest = digest_json(
                            "durable-source-resolution",
                            {"authority": candidate.authority, **observed},
                            max_bytes=MAX_EVENT_JSON_BYTES,
                        )
                    value = resolved_source.value
                except Exception as exc:  # source authority is external to the plan
                    raise self._failure(
                        "source_missing",
                        code="execution.source_attestation_invalid",
                        message_key="ERR_DURABLE_SOURCE_MISSING",
                        retry="manual",
                    ) from exc
                if expected == "array":
                    value = [value]
            elif reference_kind.startswith("dependency."):
                value, selected = self._dependency_value(
                    facts, reference, want_array=expected == "array",
                )
                dependency_ids.extend(selected)
            else:
                raise self._failure(
                    "contract_violation",
                    code="execution.binding_unknown",
                    message_key="ERR_DURABLE_CONTRACT_CHANGED",
                    retry="never",
                )
            if not _json_type_matches(value, expected):
                raise self._failure(
                    "contract_violation",
                    code="execution.binding_type_invalid",
                    message_key="ERR_DURABLE_CONTRACT_CHANGED",
                    retry="never",
                    details={"argument": str(argument)},
                )
            args[str(argument)] = value
        return (
            args,
            tuple(dict.fromkeys(dependency_ids)),
            source_resolution_digest,
        )

    def _semantic_arguments_digest(
        self,
        facts: Mapping[str, Any],
    ) -> str:
        """Hash semantic inputs without retaining paths or result content."""

        stage = _mapping(facts["stage"], context="stage")
        bindings = _mapping(stage["input_bindings"], context="input_bindings")
        source = facts.get("source")
        semantic: dict[str, Any] = {
            "stage_key": str(stage["key"]),
            "shard_key": stage.get("shard_key"),
            "arguments": {},
        }
        for argument, raw_reference in bindings.items():
            reference = _mapping(raw_reference, context="input_binding")
            reference_kind = str(reference["ref"])
            if reference_kind == "revision.inventory":
                inventory = _mapping(facts["inventory"], context="inventory")
                value: object = {
                    "digest": inventory.get("digest"),
                    "source_count": len(inventory.get("sources") or ()),
                }
            elif reference_kind in {"source.path", "source.record"}:
                source_value = _mapping(source, context="source")
                value = {
                    "source_id": source_value.get("source_id"),
                    "content_digest": source_value.get("content_digest"),
                    "size_bytes": source_value.get("size_bytes"),
                    "mtime_ns": source_value.get("mtime_ns"),
                    "device_id": source_value.get("device_id"),
                }
            else:
                selected = self._select_dependencies(
                    facts, str(reference.get("stage") or ""),
                )
                dependency_facts = [{
                    "result_id": item.get("result_id"),
                    "digest": item.get("digest"),
                    "entry_selected": bool(item.get("entry_selected")),
                } for item in selected]
                value = {
                    "count": len(dependency_facts),
                    "digest": digest_json(
                        "durable-execution-dependencies",
                        dependency_facts,
                        max_bytes=MAX_SNAPSHOT_JSON_BYTES,
                    ),
                }
            semantic["arguments"][str(argument)] = {
                "reference": dict(reference),
                "value": value,
            }
        return digest_json(
            "durable-execution-arguments",
            semantic,
            max_bytes=MAX_EVENT_JSON_BYTES,
        )

    def _context(
        self,
        lease: Lease,
        facts: Mapping[str, Any],
        contract: FrozenRunnerContract,
    ) -> ExecutionContext:
        stage = _mapping(facts["stage"], context="stage")
        stage_timeout = int(stage["timeout_s"])
        if stage_timeout != lease.timeout_s:
            raise self._failure(
                "contract_violation",
                code="execution.timeout_contract_changed",
                message_key="ERR_DURABLE_CONTRACT_CHANGED",
                retry="never",
            )
        execution_started_at = facts.get("execution_started_at")
        if not isinstance(execution_started_at, str):
            raise self._failure(
                "contract_violation",
                code="execution.start_time_missing",
                message_key="ERR_DURABLE_CONTRACT_CHANGED",
                retry="never",
            )
        stage_deadline = parse_instant(
            execution_started_at, name="execution_started_at",
        ) + timedelta(seconds=stage_timeout)
        return ExecutionContext(
            owner_user_id=lease.owner_user_id,
            workload_id=lease.workload_id,
            revision_id=lease.revision_id,
            stage_id=lease.stage_id,
            unit_key=lease.unit_key,
            attempt_id=lease.attempt_id,
            priority=str(facts["priority"]),
            resource_claims=lease.resource_claims,
            deadline_at=instant_text(stage_deadline, name="execution deadline"),
            language=contract.prompt_language,
        )

    def _remaining_timeout(self, context: ExecutionContext) -> float:
        if context.deadline_at is None:
            raise TimeoutError("durable execution has no deadline")
        remaining = (
            parse_instant(context.deadline_at, name="execution deadline")
            - normalize_instant(self._clock(), name="execution clock")
        ).total_seconds()
        if remaining <= 0:
            raise TimeoutError("durable execution deadline is exhausted")
        return remaining

    @staticmethod
    def _autonomy(effect: DurableEffect) -> str:
        return "readonly" if effect is DurableEffect.PURE else "supervised"

    def _invoke(
        self,
        contract: FrozenRunnerContract,
        facts: Mapping[str, Any],
        args: Mapping[str, Any],
        context: ExecutionContext,
        device_id: str | None,
    ) -> object:
        stage = _mapping(facts["stage"], context="stage")
        if contract.kind == RunnerKind.EXECUTOR.value:
            try:
                executor = self._executor_loader(contract.name)
            except Exception as exc:
                raise self._failure(
                    "capability_unavailable",
                    code="execution.executor_unavailable",
                    message_key="ERR_DURABLE_RUNNER_UNAVAILABLE",
                    retry="manual",
                    details={"runner_name": contract.name},
                ) from exc
            try:
                attestor = getattr(self.runners, "attest_executor", None)
                if callable(attestor):
                    loaded_contract = attestor(contract.name, executor)
                    if loaded_contract != contract:
                        raise ValueError("loaded executor contract changed")
                else:
                    # Lightweight compatibility check for test/custom
                    # resolvers that predate exact-object attestation.
                    loaded_name = getattr(executor, "name", contract.name)
                    loaded_digest = getattr(executor, "digest", None)
                    if loaded_name != contract.name or (
                        isinstance(loaded_digest, str)
                        and loaded_digest
                        and loaded_digest != contract.implementation_digest
                    ):
                        raise ValueError("loaded executor identity changed")
            except Exception as exc:
                raise self._failure(
                    "contract_violation",
                    code="execution.loaded_executor_changed",
                    message_key="ERR_DURABLE_CONTRACT_CHANGED",
                    retry="never",
                    details={"runner_name": contract.name},
                ) from exc
            return self._executor_invoker(
                executor, args, context, self._remaining_timeout(context), device_id,
                self._autonomy(DurableEffect(stage["effect_profile"])),
            )
        if contract.kind == RunnerKind.WORKLOAD.value:
            if self._workload_invoker is None:
                raise self._failure(
                    "capability_unavailable",
                    code="execution.workload_unavailable",
                    message_key="ERR_DURABLE_RUNNER_UNAVAILABLE",
                    retry="manual",
                    details={"runner_name": contract.name},
                )
            call = lambda: self._workload_invoker(contract.name, args, context)
        else:
            runner = self._internal_runners.get(contract.name)
            if runner is None:
                raise self._failure(
                    "capability_unavailable",
                    code="execution.internal_runner_unavailable",
                    message_key="ERR_DURABLE_RUNNER_UNAVAILABLE",
                    retry="manual",
                    details={"runner_name": contract.name},
                )
            call = lambda: runner(args, context)

        # Executor runners already enter the same choke point through
        # ``agent_runtime.invoke_executor``.  Registered and internal runners
        # have no subprocess wrapper, so admit them here with the exact policy
        # frozen in the revision.  The small envelope preserves scheduler
        # success metrics without changing the runner's output contract.
        from executor_scheduler import invoke_scheduled

        scheduled = _ScheduledRunner(
            name=f"{contract.kind}:{contract.name}",
            execution_policy=dict(contract.execution_policy),
            execution_policy_declared=contract.execution_policy_declared,
        )

        def admitted_call() -> dict[str, Any]:
            observation = call()
            failed = (
                isinstance(observation, Mapping)
                and observation.get("ok") is False
            )
            return {"ok": not failed, "observation": observation}

        envelope = invoke_scheduled(
            scheduled,
            admitted_call,
            admission_timeout_s=self._remaining_timeout(context),
            execution_context=context,
        )
        return envelope["observation"]

    def _observation_failure(self, observation: Mapping[str, Any]) -> ExecutionFailure:
        observed = observation.get("error_class")
        if observed in _TRANSIENT_ERRORS:
            error_class, retry = "executor_transient", "automatic"
        elif observed == "budget_exhausted":
            error_class, retry = "budget_exhausted", "manual"
        elif observed == "publication_ambiguous":
            error_class, retry = "publication_ambiguous", "manual"
        elif observed in _CONTRACT_ERRORS:
            error_class, retry = "contract_violation", "never"
        elif observed in _CAPABILITY_ERRORS:
            error_class, retry = "capability_unavailable", "manual"
        else:
            error_class, retry = "executor_permanent", "never"
        return self._failure(
            error_class,
            code="execution.runner_failed",
            message_key="ERR_DURABLE_EXECUTION_FAILED",
            retry=retry,
            details={"reported_error_class": str(observed or "unknown")[:64]},
        )

    @staticmethod
    def _consume_transport_usage(
        observation: object,
        usage_sink: object | None,
        context: ExecutionContext,
    ) -> object:
        """Remove and bind the reserved child-process model-usage envelope."""

        if not isinstance(observation, Mapping):
            return observation
        from llm_telemetry import (
            BoundedUsageSink,
            TRANSPORT_USAGE_KEY,
        )

        if TRANSPORT_USAGE_KEY not in observation:
            return observation
        cleaned = dict(observation)
        transport = cleaned.pop(TRANSPORT_USAGE_KEY)
        sink = usage_sink if isinstance(usage_sink, BoundedUsageSink) else BoundedUsageSink()
        sink.ingest_transport(
            transport,
            workload_id=context.workload_id,
            stage_id=context.stage_id,
            unit_key=context.unit_key,
            attempt_id=context.attempt_id,
        )
        if usage_sink is None:
            summary = sink.summary()
            if summary["records"] or summary["dropped"]:
                raise ValueError("runner used an undeclared model binding")
        return cleaned

    def __call__(self, lease: Lease) -> ExecutionResult:
        facts = self.store.execution_inputs(lease)
        budget_violation = self.store.budget_violation(lease)
        if budget_violation is not None:
            raise self._failure(
                "budget_exhausted",
                code="execution.budget_exhausted",
                message_key="ERR_DURABLE_BUDGET_EXHAUSTED",
                retry="manual",
                details=budget_violation,
            )
        contract, schema, expected = self._verify_frozen_contract(facts)
        context = self._context(lease, facts, contract)
        args, dependency_ids, source_resolution_digest = self._build_args(
            facts, contract, context,
        )
        if contract.model_binding_digest is not None:
            remaining = self.store.remaining_model_budget(lease)
            reserved_tokens = int(contract.model_max_calls or 0) * (
                int(contract.model_max_input_tokens or 0)
                + int(contract.model_max_output_tokens or 0)
            )
            if remaining["max_tokens"] < reserved_tokens:
                raise self._failure(
                    "budget_exhausted",
                    code="execution.model_token_reservation_unavailable",
                    message_key="ERR_DURABLE_BUDGET_EXHAUSTED",
                    retry="manual",
                    details={
                        "budget": "max_tokens",
                        "remaining": remaining["max_tokens"],
                        "required_token_reservation": reserved_tokens,
                    },
                )
        stage = _mapping(facts["stage"], context="stage")
        reduction_input = stage.get("reduction_input")
        reduction_limit = stage.get("reduction_max_input_bytes")
        if reduction_input is not None and reduction_limit is not None:
            try:
                canonical_json(
                    args[str(reduction_input)],
                    max_bytes=int(reduction_limit),
                )
            except (KeyError, SchemaValidationError, TypeError, ValueError) as exc:
                raise self._failure(
                    "contract_violation",
                    code="execution.reduction_input_too_large",
                    message_key="ERR_DURABLE_REDUCTION_INPUT_TOO_LARGE",
                    retry="never",
                    details={"stage_key": str(stage["key"])},
                ) from exc
        source = facts.get("source")
        device_id = self._device_selector(source if isinstance(source, Mapping) else None)
        executor_snapshot = {
            "schema_version": "metnos.durable-executor-snapshot/2",
            "mode": "verified",
            "contract": expected,
            "semantic_arguments_digest": self._semantic_arguments_digest(facts),
        }
        if source_resolution_digest is not None:
            executor_snapshot["source_resolution_digest"] = (
                source_resolution_digest
            )
        model_snapshot = {
            "schema_version": (
                "metnos.durable-model-snapshot/2"
                if contract.model_binding_digest is not None
                else "metnos.durable-model-snapshot/1"
            ),
            "mode": "llm" if contract.model_binding_digest is not None else "none",
            "runner_name": contract.name,
            "binding_digest": contract.model_binding_digest,
            "prompt_digest": contract.prompt_digest,
        }
        if contract.prompt_language is not None:
            model_snapshot.update({
                "prompt_language": contract.prompt_language,
                "provider": contract.model_provider,
                "model_digest": contract.model_digest,
                "tier": contract.model_tier,
                "kind": contract.model_kind,
                "max_calls": contract.model_max_calls,
                "max_input_tokens": contract.model_max_input_tokens,
                "max_output_tokens": contract.model_max_output_tokens,
                "cost_policy": contract.model_cost_policy,
            })
        recorded = self.store.record_execution_facts(
            lease,
            executor_snapshot=executor_snapshot,
            model_snapshot=model_snapshot,
            device_id=device_id,
            now=self._clock(),
        )
        if recorded is not LeaseMutationStatus.APPLIED:
            raise self._failure(
                "lease_lost",
                code="execution.fence_lost",
                message_key="ERR_DURABLE_LEASE_EXPIRED",
                retry="never",
            )

        usage_sink = None
        invocation_failure: ExecutionFailure | None = None
        observation: object = None
        try:
            if contract.model_binding_digest is not None:
                from llm_telemetry import BoundedUsageSink, attempt_context

                usage_sink = BoundedUsageSink()
                with attempt_context(
                    workload_id=context.workload_id,
                    stage_id=context.stage_id,
                    unit_key=context.unit_key,
                    attempt_id=context.attempt_id,
                    sink=usage_sink,
                ):
                    observation = self._invoke(
                        contract, facts, args, context, device_id,
                    )
            else:
                observation = self._invoke(contract, facts, args, context, device_id)
            observation = self._consume_transport_usage(
                observation, usage_sink, context,
            )
        except ExecutionFailure as exc:
            invocation_failure = exc
        except TimeoutError:
            invocation_failure = self._failure(
                "executor_transient",
                code="execution.timeout",
                message_key="ERR_DURABLE_EXECUTION_FAILED",
                retry="automatic",
                attempt_state=AttemptState.TIMED_OUT,
            )
        except Exception as exc:
            invocation_failure = self._failure(
                "executor_permanent",
                code="execution.unhandled_exception",
                message_key="ERR_DURABLE_EXECUTION_FAILED",
                retry="never",
                details={"exception_type": type(exc).__name__[:64]},
            )

        if usage_sink is not None:
            try:
                usage_status = self.store.record_attempt_usage(
                    lease, usage_sink.summary(), now=self._clock(),
                )
            except ModelUsageContractError as exc:
                raise self._failure(
                    "contract_violation",
                    code="execution.model_usage_contract_changed",
                    message_key="ERR_DURABLE_CONTRACT_CHANGED",
                    retry="never",
                ) from exc
            except Exception as exc:
                raise self._failure(
                    "budget_exhausted",
                    code="execution.usage_accounting_failed",
                    message_key="ERR_DURABLE_BUDGET_EXHAUSTED",
                    retry="manual",
                    details={"accounting_persisted": False},
                ) from exc
            if usage_status not in {
                LeaseMutationStatus.APPLIED,
                LeaseMutationStatus.ALREADY_APPLIED,
            }:
                raise self._failure(
                    "budget_exhausted",
                    code="execution.usage_accounting_rejected",
                    message_key="ERR_DURABLE_BUDGET_EXHAUSTED",
                    retry="manual",
                    details={"accounting_persisted": False},
                )
            budget_violation = self.store.budget_violation(lease)
            if budget_violation is not None:
                raise self._failure(
                    "budget_exhausted",
                    code="execution.budget_exhausted",
                    message_key="ERR_DURABLE_BUDGET_EXHAUSTED",
                    retry="manual",
                    details=budget_violation,
                )

        if invocation_failure is not None:
            raise invocation_failure

        if not isinstance(observation, Mapping):
            raise self._failure(
                "contract_violation",
                code="execution.output_not_object",
                message_key="ERR_DURABLE_RESULT_CONTRACT_VIOLATION",
                retry="never",
            )
        if observation.get("ok") is False:
            raise self._observation_failure(observation)
        invocation_id = observation.get("invocation_id")
        remote = observation.get("_remote")
        if isinstance(remote, Mapping):
            invocation_id = remote.get("invocation_id", invocation_id)
        if isinstance(invocation_id, str):
            self.store.record_execution_facts(
                lease,
                executor_snapshot=executor_snapshot,
                model_snapshot=model_snapshot,
                device_id=device_id,
                invocation_id=invocation_id,
                now=self._clock(),
            )
        payload = observation.get("payload", observation)
        try:
            schema.validate(payload)
            result = ValidatedResult.from_payload(schema.name, payload)
        except (OutputValidationError, ValueError, TypeError) as exc:
            raise self._failure(
                "contract_violation",
                code="execution.output_invalid",
                message_key="ERR_DURABLE_RESULT_CONTRACT_VIOLATION",
                retry="never",
                details={"stage_key": str(facts["stage"]["key"])},
            ) from exc
        return ExecutionResult(result, dependency_ids)

    def run_once(self, worker: DurableWorker) -> WorkerRunOutcome:
        """Recover, execute one unit, then materialize its generic descendants."""
        reconciliation = worker.coordinator.reconcile()
        control_progress = bool(
            getattr(reconciliation, "expired", 0)
            or getattr(reconciliation, "retry_promoted", 0)
        )
        control_progress |= self.store.settle_workloads() > 0
        remaining_control_mutations = _CONTROL_MUTATION_BATCH
        while remaining_control_mutations > 0:
            reused = self.store.adopt_reusable_results(
                limit=remaining_control_mutations,
            )
            control_progress |= reused > 0
            remaining_control_mutations -= reused
            if remaining_control_mutations == 0:
                self.store.complete_ready_workloads(
                    limit=_CONTROL_MUTATION_BATCH,
                )
                return WorkerRunOutcome(WorkerRunStatus.CONTROL_PROGRESS)
            materialized = self.store.materialize_all_ready_units(
                limit=remaining_control_mutations,
            )
            control_progress |= materialized > 0
            remaining_control_mutations -= materialized
            if reused == 0 and materialized == 0:
                break
            if remaining_control_mutations == 0:
                self.store.complete_ready_workloads(
                    limit=_CONTROL_MUTATION_BATCH,
                )
                return WorkerRunOutcome(WorkerRunStatus.CONTROL_PROGRESS)
        outcome = worker.run_once(self)
        lease = outcome.lease
        if lease is None:
            completed = self.store.complete_ready_workloads(
                limit=_CONTROL_MUTATION_BATCH,
            )
            control_progress |= completed > 0
            if (
                outcome.status is WorkerRunStatus.IDLE
                and control_progress
            ):
                return WorkerRunOutcome(WorkerRunStatus.CONTROL_PROGRESS)
            return outcome
        stage_terminal = (
            outcome.commit.stage_terminal
            if outcome.commit is not None and outcome.commit.result_id is not None
            else self.store.stage_is_terminal(lease)
        )
        if stage_terminal:
            self.store.materialize_ready_units(lease.owner_user_id, lease.workload_id)
            if not self.store.materialization_complete(
                lease.owner_user_id, lease.workload_id,
            ):
                return outcome
            self.store.refresh_usage_complete(lease.owner_user_id, lease.workload_id)
            self.store.evaluate_completion(
                lease.owner_user_id,
                lease.workload_id,
                now=self._clock(),
            )
        return outcome


__all__ = ["DurableExecutionBridge"]
