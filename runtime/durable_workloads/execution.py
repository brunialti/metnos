"""Generic execution bridge for admitted durable units.

The bridge knows only frozen runner contracts, schemas and owner-scoped store
rows.  It contains no domain imports or preset branches: OCR, VLM and every
other capability enter through the same registered runner interfaces.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
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
from .coordinator import Lease, LeaseMutationStatus, StructuredAttemptError, ValidatedResult
from .models import DurableEffect, ExecutionContext, RunnerKind
from .storage import DurableWorkloadStore
from .worker import (
    DurableWorker,
    ExecutionFailure,
    ExecutionResult,
    WorkerRunOutcome,
)


log = logging.getLogger("metnos.durable_workloads.execution")

_TRANSIENT_ERRORS = frozenset({
    "timeout", "remote_timeout", "network", "rate_limited",
    "temporarily_unavailable", "executor_transient",
})
_CONTRACT_ERRORS = frozenset({
    "non_json", "contract_violation", "invalid_output", "schema_mismatch",
})
_CAPABILITY_ERRORS = frozenset({
    "placement", "permission_denied", "capability_unavailable",
    "missing_source_context",
})


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


class DurableExecutionBridge:
    """Execute one admitted unit through a single, generic contract path."""

    def __init__(
        self,
        store: DurableWorkloadStore,
        *,
        runners: RunnerContractResolver,
        output_schemas: OutputSchemaResolver,
        source_resolver: Callable[[Mapping[str, Any]], object] | None = None,
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
    ) -> ExecutionFailure:
        return ExecutionFailure(StructuredAttemptError.create(
            error_class,
            code=code,
            message_key=message_key,
            retry=retry,
            occurred_at=self._clock(),
            details_redacted=details or {},
        ))

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
                message_key="DURABLE_RUNNER_UNAVAILABLE",
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
                message_key="DURABLE_OUTPUT_SCHEMA_UNAVAILABLE",
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
                message_key="DURABLE_CONTRACT_CHANGED",
                retry="never",
                details={"stage_key": str(stage["key"])},
            )
        expected = entries[0]
        current = contract.snapshot(stage_key=str(stage["key"]), output_schema=schema)
        if current != expected:
            raise self._failure(
                "contract_violation",
                code="execution.contract_changed",
                message_key="DURABLE_CONTRACT_CHANGED",
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
                message_key="DURABLE_DEPENDENCIES_UNAVAILABLE",
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
                    message_key="DURABLE_DEPENDENCIES_UNAVAILABLE",
                    retry="never",
                    details={"dependency_stage": stage_key},
                )
            payloads.append(payload)
        reference_kind = str(reference["ref"])
        field = reference.get("field")
        if reference_kind == "dependency.entries":
            values: list[object] = []
            for payload in payloads:
                entries = payload.get("entries") if isinstance(payload, Mapping) else None
                if not isinstance(entries, list):
                    raise self._failure(
                        "contract_violation",
                        code="execution.dependency_entries_invalid",
                        message_key="DURABLE_DEPENDENCIES_UNAVAILABLE",
                        retry="never",
                        details={"dependency_stage": stage_key},
                    )
                values.extend(entries)
        else:
            values = payloads
        if field is not None:
            try:
                values = [_field(value, str(field)) for value in values]
            except KeyError as exc:
                raise self._failure(
                    "contract_violation",
                    code="execution.dependency_field_missing",
                    message_key="DURABLE_DEPENDENCIES_UNAVAILABLE",
                    retry="never",
                    details={"dependency_stage": stage_key},
                ) from exc
        if want_array:
            return values, result_ids
        if len(values) != 1:
            raise self._failure(
                "contract_violation",
                code="execution.dependency_cardinality_invalid",
                message_key="DURABLE_DEPENDENCIES_UNAVAILABLE",
                retry="never",
                details={"dependency_stage": stage_key},
            )
        return values[0], result_ids

    def _build_args(
        self,
        facts: Mapping[str, Any],
        contract: FrozenRunnerContract,
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        stage = _mapping(facts["stage"], context="stage")
        bindings = _mapping(stage["input_bindings"], context="input_bindings")
        input_types = dict(contract.input_types)
        args: dict[str, Any] = {}
        dependency_ids: list[str] = []
        source = facts.get("source")
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
                        message_key="DURABLE_SOURCE_MISSING",
                        retry="manual",
                    )
                value = dict(source)
            elif reference_kind == "source.path":
                if not isinstance(source, Mapping) or self._source_resolver is None:
                    raise self._failure(
                        "source_missing",
                        code="execution.source_unavailable",
                        message_key="DURABLE_SOURCE_MISSING",
                        retry="manual",
                    )
                try:
                    value = self._source_resolver(source)
                except Exception as exc:  # source authority is external to the plan
                    raise self._failure(
                        "source_missing",
                        code="execution.source_unavailable",
                        message_key="DURABLE_SOURCE_MISSING",
                        retry="manual",
                    ) from exc
                if expected == "array" and not isinstance(value, list):
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
                    message_key="DURABLE_CONTRACT_CHANGED",
                    retry="never",
                )
            if not _json_type_matches(value, expected):
                raise self._failure(
                    "contract_violation",
                    code="execution.binding_type_invalid",
                    message_key="DURABLE_CONTRACT_CHANGED",
                    retry="never",
                    details={"argument": str(argument)},
                )
            args[str(argument)] = value
        return args, tuple(dict.fromkeys(dependency_ids))

    @staticmethod
    def _context(lease: Lease, facts: Mapping[str, Any]) -> ExecutionContext:
        return ExecutionContext(
            owner_user_id=lease.owner_user_id,
            workload_id=lease.workload_id,
            revision_id=lease.revision_id,
            stage_id=lease.stage_id,
            unit_key=lease.unit_key,
            attempt_id=lease.attempt_id,
            priority=str(facts["priority"]),
            resource_claims=lease.resource_claims,
            deadline_at=lease.lease_expires_at,
        )

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
                    message_key="DURABLE_RUNNER_UNAVAILABLE",
                    retry="manual",
                    details={"runner_name": contract.name},
                ) from exc
            return self._executor_invoker(
                executor, args, context, int(stage["timeout_s"]), device_id,
                self._autonomy(DurableEffect(stage["effect_profile"])),
            )
        if contract.kind == RunnerKind.WORKLOAD.value:
            if self._workload_invoker is None:
                raise self._failure(
                    "capability_unavailable",
                    code="execution.workload_unavailable",
                    message_key="DURABLE_RUNNER_UNAVAILABLE",
                    retry="manual",
                    details={"runner_name": contract.name},
                )
            return self._workload_invoker(contract.name, args, context)
        runner = self._internal_runners.get(contract.name)
        if runner is None:
            raise self._failure(
                "capability_unavailable",
                code="execution.internal_runner_unavailable",
                message_key="DURABLE_RUNNER_UNAVAILABLE",
                retry="manual",
                details={"runner_name": contract.name},
            )
        return runner(args, context)

    def _observation_failure(self, observation: Mapping[str, Any]) -> ExecutionFailure:
        observed = observation.get("error_class")
        if observed in _TRANSIENT_ERRORS:
            error_class, retry = "executor_transient", "automatic"
        elif observed in _CONTRACT_ERRORS:
            error_class, retry = "contract_violation", "never"
        elif observed in _CAPABILITY_ERRORS:
            error_class, retry = "capability_unavailable", "manual"
        else:
            error_class, retry = "executor_permanent", "never"
        return self._failure(
            error_class,
            code="execution.runner_failed",
            message_key="DURABLE_EXECUTION_FAILED",
            retry=retry,
            details={"reported_error_class": str(observed or "unknown")[:64]},
        )

    def __call__(self, lease: Lease) -> ExecutionResult:
        facts = self.store.execution_inputs(lease)
        contract, schema, expected = self._verify_frozen_contract(facts)
        context = self._context(lease, facts)
        args, dependency_ids = self._build_args(facts, contract)
        source = facts.get("source")
        device_id = self._device_selector(source if isinstance(source, Mapping) else None)
        executor_snapshot = {
            "schema_version": "metnos.durable-executor-snapshot/1",
            "mode": "verified",
            "contract": expected,
        }
        model_snapshot = {
            "schema_version": "metnos.durable-model-snapshot/1",
            "mode": "llm" if contract.model_binding_digest is not None else "none",
            "runner_name": contract.name,
            "binding_digest": contract.model_binding_digest,
            "prompt_digest": contract.prompt_digest,
        }
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
                message_key="DURABLE_LEASE_EXPIRED",
                retry="never",
            )

        usage_sink = None
        try:
            if contract.kind == RunnerKind.WORKLOAD.value:
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
        except ExecutionFailure:
            raise
        except TimeoutError as exc:
            raise self._failure(
                "executor_transient",
                code="execution.timeout",
                message_key="DURABLE_EXECUTION_FAILED",
                retry="automatic",
            ) from exc
        except Exception as exc:
            raise self._failure(
                "executor_permanent",
                code="execution.unhandled_exception",
                message_key="DURABLE_EXECUTION_FAILED",
                retry="never",
                details={"exception_type": type(exc).__name__[:64]},
            ) from exc
        finally:
            if usage_sink is not None:
                try:
                    self.store.record_attempt_usage(
                        lease, usage_sink.summary(), now=self._clock(),
                    )
                except Exception:
                    log.debug("durable LLM usage persistence failed", exc_info=True)

        if not isinstance(observation, Mapping):
            raise self._failure(
                "contract_violation",
                code="execution.output_not_object",
                message_key="DURABLE_RESULT_CONTRACT_VIOLATION",
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
                message_key="DURABLE_RESULT_CONTRACT_VIOLATION",
                retry="never",
                details={"stage_key": str(facts["stage"]["key"])},
            ) from exc
        return ExecutionResult(result, dependency_ids)

    def run_once(self, worker: DurableWorker) -> WorkerRunOutcome:
        """Recover, execute one unit, then materialize its generic descendants."""
        worker.coordinator.reconcile()
        self.store.materialize_all_ready_units()
        outcome = worker.run_once(self)
        lease = outcome.lease
        if lease is None:
            return outcome
        if outcome.commit is not None and outcome.commit.result_id is not None:
            self.store.materialize_ready_units(lease.owner_user_id, lease.workload_id)
        self.store.refresh_usage_complete(lease.owner_user_id, lease.workload_id)
        self.store.evaluate_completion(lease.owner_user_id, lease.workload_id)
        return outcome


__all__ = ["DurableExecutionBridge"]
