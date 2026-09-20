"""Closed runtime composition for the generic LRE service.

The service consumes registrations; it does not branch on presets or runner
names.  Each approved package contributes contracts, schemas and an invoker
through the same small interface, and duplicate authority fails at startup.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from .artifacts import ArtifactRepository, ArtifactStore
from .compiler import (
    ApprovedOutputSchema,
    CompilationError,
    FrozenRunnerContract,
    OutputSchemaRegistry,
    OutputSchemaResolver,
    RunnerContractResolver,
    core_output_schemas,
)
from .coordinator import WorkerCapabilities
from .execution import DurableExecutionBridge
from .internal_runners import approved_internal_runners
from .models import DurableEffect, RunnerKind
from .schema import validate_plan
from .source_authority import RemoteAttestor, SourceAuthority
from .storage import DurableWorkloadStore, StoreNotReadyError
from .worker import DurableWorker


_CORE_RUNNER_BINDINGS = (
    (RunnerKind.INTERNAL, "artifact_store_publish"),
    (RunnerKind.INTERNAL, "schema_and_coverage_validator"),
    (RunnerKind.INTERNAL, "sealed_inventory"),
)
_REGISTRATION_RE = re.compile(r"[a-z][a-z0-9_.-]{2,127}")
_RUNNER_RE = re.compile(r"[a-z_][a-z0-9_.-]{1,95}")
_RESOURCE_ENV = {
    "cpu": "METNOS_DURABLE_RESOURCE_CPU",
    "device": "METNOS_DURABLE_RESOURCE_DEVICE",
    "llm": "METNOS_DURABLE_RESOURCE_LLM",
    "local_io": "METNOS_DURABLE_RESOURCE_LOCAL_IO",
    "network_io": "METNOS_DURABLE_RESOURCE_NETWORK_IO",
    "vlm": "METNOS_DURABLE_RESOURCE_VLM",
}


@dataclass(frozen=True, slots=True)
class RuntimeRegistration:
    """One package contribution to the universal runtime registry."""

    name: str
    runner_bindings: tuple[tuple[RunnerKind | str, str], ...]
    runners: RunnerContractResolver
    output_schemas: OutputSchemaResolver
    output_schema_names: tuple[str, ...]
    workload_invoker: Callable[[str, Mapping[str, Any], object], object] | None = None
    candidate_plan_factory: Callable[[], Mapping[str, Any]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _REGISTRATION_RE.fullmatch(self.name):
            raise ValueError("runtime registration name is invalid")
        normalized = tuple(sorted(
            ((RunnerKind(kind), runner) for kind, runner in self.runner_bindings),
            key=lambda item: (item[0].value, item[1]),
        ))
        if normalized != tuple(
            (RunnerKind(kind), runner) for kind, runner in self.runner_bindings
        ):
            raise ValueError("runtime runner bindings are not canonical")
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("runtime runner bindings are empty or duplicated")
        if any(not isinstance(name, str) or not _RUNNER_RE.fullmatch(name)
               for _kind, name in normalized):
            raise ValueError("runtime runner binding name is invalid")
        if any(kind is RunnerKind.INTERNAL for kind, _name in normalized):
            raise ValueError("package registrations cannot replace core runners")
        workloads = {name for kind, name in normalized if kind is RunnerKind.WORKLOAD}
        if bool(workloads) != (self.workload_invoker is not None):
            raise ValueError("workload registrations need exactly one invoker")
        if tuple(sorted(self.output_schema_names, key=str.encode)) != self.output_schema_names:
            raise ValueError("runtime output schema names are not canonical")
        if len(self.output_schema_names) != len(set(self.output_schema_names)):
            raise ValueError("runtime output schema names are duplicated")
        if (
            self.candidate_plan_factory is not None
            and not callable(self.candidate_plan_factory)
        ):
            raise TypeError("runtime candidate plan factory must be callable")


class _RunnerRouter:
    def __init__(self, registrations: Sequence[RuntimeRegistration]) -> None:
        self._by_binding: dict[tuple[RunnerKind, str], RunnerContractResolver] = {}
        for registration in registrations:
            for raw_kind, name in registration.runner_bindings:
                binding = (RunnerKind(raw_kind), name)
                if binding in self._by_binding:
                    raise ValueError("duplicate runtime runner authority")
                self._resolve_checked(registration.runners, *binding)
                self._by_binding[binding] = registration.runners

    @staticmethod
    def _resolve_checked(
        resolver: RunnerContractResolver,
        kind: RunnerKind,
        name: str,
    ) -> FrozenRunnerContract:
        try:
            contract = resolver.resolve(kind.value, name)
        except Exception as exc:
            raise ValueError("runtime runner declaration cannot be resolved") from exc
        if (
            not isinstance(contract, FrozenRunnerContract)
            or contract.kind != kind.value
            or contract.name != name
        ):
            raise ValueError("runtime runner declaration resolves to another identity")
        return contract

    @property
    def bindings(self) -> tuple[tuple[RunnerKind, str], ...]:
        return tuple(sorted(
            self._by_binding,
            key=lambda item: (item[0].value, item[1]),
        ))

    def resolve(self, kind: str, name: str):
        try:
            selected_kind = RunnerKind(kind)
            resolver = self._by_binding[(selected_kind, name)]
        except (KeyError, ValueError) as exc:
            raise CompilationError(f"runner is not registered for LRE: {name}") from exc
        try:
            return self._resolve_checked(resolver, selected_kind, name)
        except ValueError as exc:
            raise CompilationError(
                f"runner contract is unavailable for LRE: {name}"
            ) from exc

    def attest_executor(self, name: str, executor: object):
        try:
            resolver = self._by_binding[(RunnerKind.EXECUTOR, name)]
        except KeyError as exc:
            raise CompilationError(f"executor is not registered for LRE: {name}") from exc
        attestor = getattr(resolver, "attest_executor", None)
        if not callable(attestor):
            raise CompilationError("registered executor resolver cannot attest objects")
        return attestor(name, executor)


class _SchemaRouter:
    def __init__(
        self,
        registrations: Sequence[RuntimeRegistration],
        *,
        core_schemas: OutputSchemaRegistry,
    ) -> None:
        self._by_name: dict[str, OutputSchemaResolver] = {
            name: core_schemas for name in core_schemas.names
        }
        for registration in registrations:
            for name in registration.output_schema_names:
                if name in self._by_name:
                    raise ValueError("duplicate runtime output schema authority")
                # Resolve eagerly so a false declaration fails before work is claimed.
                schema = registration.output_schemas.resolve(name)
                if not isinstance(schema, ApprovedOutputSchema) or schema.name != name:
                    raise ValueError(
                        "runtime output schema resolves to another identity"
                    )
                self._by_name[name] = registration.output_schemas

    def resolve(self, name: str) -> ApprovedOutputSchema:
        try:
            resolver = self._by_name[name]
        except KeyError as exc:
            raise CompilationError(f"output schema is not registered for LRE: {name}") from exc
        schema = resolver.resolve(name)
        if not isinstance(schema, ApprovedOutputSchema) or schema.name != name:
            raise CompilationError(
                f"output schema identity changed for LRE: {name}"
            )
        return schema


class RuntimeRegistry:
    """Validated union of all approved runtime contributions."""

    def __init__(self, registrations: Sequence[RuntimeRegistration]) -> None:
        if not registrations:
            raise ValueError("the LRE runtime registry cannot be empty")
        if len({item.name for item in registrations}) != len(registrations):
            raise ValueError("runtime registration names must be unique")
        self._registrations = tuple(registrations)
        self.runners = _RunnerRouter(self._registrations)
        self.output_schemas = _SchemaRouter(
            self._registrations,
            core_schemas=core_output_schemas(),
        )
        self._workload_invokers: dict[
            str, Callable[[str, Mapping[str, Any], object], object]
        ] = {}
        self._candidate_plan_json: dict[str, str] = {}
        for registration in self._registrations:
            for kind, name in registration.runner_bindings:
                if RunnerKind(kind) is not RunnerKind.WORKLOAD:
                    continue
                assert registration.workload_invoker is not None
                if name in self._workload_invokers:
                    raise ValueError("duplicate runtime workload invoker")
                self._workload_invokers[name] = registration.workload_invoker
            if registration.candidate_plan_factory is not None:
                try:
                    candidate = registration.candidate_plan_factory()
                    canonical = validate_plan(candidate)
                    validated = json.loads(canonical)
                except Exception as exc:
                    raise ValueError(
                        "runtime candidate plan declaration is invalid"
                    ) from exc
                if validated["plan_id"] != registration.name:
                    raise ValueError(
                        "runtime candidate plan identity does not match its registration"
                    )
                self._candidate_plan_json[registration.name] = canonical

    @property
    def admission_names(self) -> tuple[str, ...]:
        """Return the closed, canonical set of plans open to admission."""

        return tuple(sorted(self._candidate_plan_json, key=str.encode))

    def candidate_plan(self, name: str) -> dict[str, Any]:
        """Return a fresh admitted-plan candidate from the closed registry."""

        try:
            canonical = self._candidate_plan_json[name]
        except KeyError as exc:
            raise LookupError("candidate plan is not registered for LRE") from exc
        value = json.loads(canonical)
        if not isinstance(value, dict):  # guarded by validate_plan at registration
            raise RuntimeError("registered candidate plan is not an object")
        return value

    def invoke_workload(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: object,
    ) -> object:
        try:
            invoker = self._workload_invokers[name]
        except KeyError as exc:
            raise LookupError("workload is not registered for LRE") from exc
        return invoker(name, arguments, context)

    def capabilities(self, resource_limits: Mapping[str, int]) -> WorkerCapabilities:
        bindings = tuple(sorted(
            (*_CORE_RUNNER_BINDINGS, *self.runners.bindings),
            key=lambda item: (item[0].value, item[1]),
        ))
        return WorkerCapabilities.create(
            bindings,
            resource_limits,
            effect_profiles=tuple(DurableEffect),
        )


class BoundExecutionBridge:
    """Execution bridge plus the per-lane resources it exclusively owns."""

    def __init__(
        self,
        bridge: DurableExecutionBridge,
        resources: Sequence[object],
        *,
        maintenance: Sequence[Callable[[], object]] = (),
        maintenance_interval_s: float = 60.0,
    ) -> None:
        if (
            isinstance(maintenance_interval_s, bool)
            or not isinstance(maintenance_interval_s, (int, float))
            or not math.isfinite(maintenance_interval_s)
            or not 0 < maintenance_interval_s <= 86_400
        ):
            raise ValueError(
                "maintenance_interval_s must be finite and in (0, 86400]"
            )
        self._bridge = bridge
        self._resources = tuple(resources)
        self._maintenance = tuple(maintenance)
        if any(not callable(callback) for callback in self._maintenance):
            raise TypeError("maintenance callbacks must be callable")
        self._maintenance_interval_s = float(maintenance_interval_s)
        self._next_maintenance = 0.0
        self._closed = False

    def run_once(self, worker: DurableWorker):
        if self._closed:
            raise RuntimeError("execution bridge is closed")
        now = time.monotonic()
        if self._maintenance and now >= self._next_maintenance:
            for callback in self._maintenance:
                callback()
            self._next_maintenance = now + self._maintenance_interval_s
        return self._bridge.run_once(worker)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        for resource in reversed(self._resources):
            close = getattr(resource, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except BaseException as exc:  # close every owned handle before surfacing
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


def _resource_limits() -> dict[str, int]:
    limits: dict[str, int] = {}
    for key, environment_name in _RESOURCE_ENV.items():
        raw = os.environ.get(environment_name, "1")
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{environment_name} must be an integer") from exc
        maximum = 32 if key in {"llm", "vlm"} else 64
        if not 0 <= value <= maximum:
            raise ValueError(f"{environment_name} is outside the supported range")
        limits[key] = value
    return limits


class RuntimeFactory:
    """Lazily compose identical bindings for every service lane."""

    def __init__(
        self,
        *,
        registry_factory: Callable[[], RuntimeRegistry],
        source_authority_path: str | Path | None = None,
        remote_attestor: RemoteAttestor | None = None,
        artifact_root: str | Path | None = None,
        lease_duration: timedelta = timedelta(seconds=60),
    ) -> None:
        self._registry_factory = registry_factory
        self._source_authority_path = source_authority_path
        self._remote_attestor = remote_attestor
        self._artifact_root = artifact_root
        self._lease_duration = lease_duration
        self._registry: RuntimeRegistry | None = None
        self._guard = threading.Lock()

    def registry(self) -> RuntimeRegistry:
        with self._guard:
            if self._registry is None:
                self._registry = self._registry_factory()
            return self._registry

    def worker(self, store: DurableWorkloadStore) -> DurableWorker:
        return DurableWorker(
            store,
            f"lre-{os.getpid()}-{secrets.token_hex(8)}",
            self.registry().capabilities(_resource_limits()),
            lease_duration=self._lease_duration,
        )

    def bridge(self, store: DurableWorkloadStore) -> BoundExecutionBridge:
        database_path = store.database_path
        if database_path is None:
            raise StoreNotReadyError("runtime bindings require a file-backed store")
        from config import PATH_DURABLE_ARTIFACTS

        repository = ArtifactRepository.open(database_path)
        artifacts: ArtifactStore | None = None
        authority: SourceAuthority | None = None
        try:
            artifacts = ArtifactStore(
                self._artifact_root or PATH_DURABLE_ARTIFACTS,
                repository,
            )
            authority = SourceAuthority.open(
                self._source_authority_path,
                remote_attestor=self._remote_attestor,
            )
            registry = self.registry()
            bridge = DurableExecutionBridge(
                store,
                runners=registry.runners,
                output_schemas=registry.output_schemas,
                source_resolver=authority.resolve,
                workload_invoker=registry.invoke_workload,
                internal_runners=approved_internal_runners(artifacts),
            )

            def maintain_source_authority() -> None:
                authority.reconcile_workloads(
                    store.source_authority_active,
                    limit=100,
                )
                authority.prune(limit=1000)

            return BoundExecutionBridge(
                bridge,
                (authority, artifacts),
                maintenance=(maintain_source_authority,),
            )
        except BaseException:
            resources = (
                authority,
                artifacts if artifacts is not None else repository,
            )
            for resource in resources:
                if resource is None:
                    continue
                try:
                    resource.close()
                except BaseException:
                    pass
            raise


__all__ = [
    "BoundExecutionBridge",
    "RuntimeFactory",
    "RuntimeRegistration",
    "RuntimeRegistry",
]
