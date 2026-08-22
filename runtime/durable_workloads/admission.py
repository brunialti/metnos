"""Transactional admission facade for compiled durable-plan v1 revisions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Protocol

from .compiler import (
    CompiledPlan,
    OutputSchemaResolver,
    RunnerContractResolver,
    compile_plan,
)
from .inventory import InventoryLimits
from .models import RevisionRecord, WorkloadRecord, WorkloadState
from .source_authority import SourceAuthority
from .storage import DurableWorkloadStore, VersionConflictError


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    revision: RevisionRecord
    compiled: CompiledPlan


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    """Stable outcome of one idempotent registered-plan submission."""

    workload: WorkloadRecord
    revision: RevisionRecord | None


class RegisteredPlanRegistry(Protocol):
    """Small structural boundary consumed by the generic submit path."""

    runners: RunnerContractResolver
    output_schemas: OutputSchemaResolver

    def candidate_plan(self, name: str) -> dict[str, Any]: ...


def submit_candidate(
    store: DurableWorkloadStore,
    registry: RegisteredPlanRegistry,
    owner_user_id: str,
    request_key: str,
    candidate: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    redacted_request: Mapping[str, Any],
    admission_boundary: Callable[[], AbstractContextManager[Any]] | None = None,
) -> SubmissionResult:
    """Admit and queue one already-built candidate exactly once.

    Inventory construction remains a caller concern.  This function owns the
    sole draft/admit/queue sequence shared by registered and dynamic plans.
    """

    workload = store.create_draft(
        owner_user_id,
        request_key,
        redacted_request=redacted_request,
    )
    revision = None
    if workload.state in {WorkloadState.DRAFT, WorkloadState.ADMITTED}:
        boundary = (
            admission_boundary() if admission_boundary is not None
            else nullcontext()
        )
        with boundary:
            # Each successful operation advances exactly one state.  Bounded
            # owner-scoped rereads make concurrent identical deliveries
            # converge even when the optional outer lock is absent.
            for _step in range(4):
                workload = store.get_workload(
                    owner_user_id, workload.workload_id,
                )
                try:
                    if workload.state is WorkloadState.DRAFT:
                        revision = admit_candidate(
                            store,
                            owner_user_id,
                            workload.workload_id,
                            candidate,
                            inventory,
                            expected_version=workload.version,
                            runners=registry.runners,
                            output_schemas=registry.output_schemas,
                        ).revision
                        continue
                    if workload.state is WorkloadState.ADMITTED:
                        workload = store.transition_workload(
                            owner_user_id,
                            workload.workload_id,
                            WorkloadState.QUEUED,
                            expected_version=workload.version,
                        )
                    break
                except VersionConflictError:
                    continue
            else:
                raise VersionConflictError(
                    "concurrent submission did not converge"
                )
    workload = store.get_workload(owner_user_id, workload.workload_id)
    if revision is None and workload.active_revision_id is not None:
        revision = store.get_revision(owner_user_id, workload.active_revision_id)
    return SubmissionResult(workload=workload, revision=revision)


def admit_candidate(
    store: DurableWorkloadStore,
    owner_user_id: str,
    workload_id: str,
    candidate: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    expected_version: int,
    runners: RunnerContractResolver,
    output_schemas: OutputSchemaResolver,
    caps_truncated: bool = False,
    partial_output_accepted: bool = False,
    usage_complete: bool = False,
    revision_id: str | None = None,
) -> AdmissionResult:
    """Compile, then atomically persist; no execution is reachable here."""

    compiled = compile_plan(
        candidate,
        inventory,
        runners=runners,
        output_schemas=output_schemas,
    )
    revision = store.admit_revision(
        owner_user_id,
        workload_id,
        compiled.plan,
        inventory,
        expected_version=expected_version,
        catalog_snapshot=compiled.catalog_snapshot,
        policy_snapshot=compiled.policy_snapshot,
        caps_truncated=caps_truncated,
        partial_output_accepted=partial_output_accepted,
        usage_complete=usage_complete,
        revision_id=revision_id,
    )
    return AdmissionResult(revision=revision, compiled=compiled)


def _inventory_limits(candidate: Mapping[str, Any]) -> InventoryLimits:
    contract = candidate.get("inventory")
    if not isinstance(contract, Mapping):
        raise ValueError("registered plan has no inventory contract")
    return InventoryLimits(
        max_sources=contract.get("max_sources"),
        max_total_bytes=contract.get("max_total_bytes"),
        max_depth=contract.get("max_depth"),
    )


def _authority_expiry(candidate: Mapping[str, Any]) -> datetime:
    budgets = candidate.get("budgets")
    if not isinstance(budgets, Mapping):
        raise ValueError("registered plan has no budget contract")
    wall_time = budgets.get("max_wall_time_s")
    if isinstance(wall_time, bool) or not isinstance(wall_time, int):
        raise ValueError("registered plan has an invalid wall-time budget")
    # The mandate outlives the plan deadline just enough for reconciliation;
    # it never exceeds SourceAuthority's closed one-year boundary.
    lifetime = min(timedelta(days=365), timedelta(seconds=wall_time, days=1))
    return datetime.now(timezone.utc) + lifetime


def submit_registered_local_sources(
    store: DurableWorkloadStore,
    authority: SourceAuthority,
    registry: RegisteredPlanRegistry,
    registration_name: str,
    owner_user_id: str,
    request_key: str,
    roots: Sequence[str | os.PathLike[str]],
    *,
    redacted_request: Mapping[str, Any],
    device_id: str = "server",
    admission_boundary: Callable[[], AbstractContextManager[Any]] | None = None,
) -> SubmissionResult:
    """Seal local sources, admit a registered plan, and queue it exactly once.

    The function contains no task-domain branch.  A deployment registration
    supplies the immutable candidate plan; the shared compiler re-attests all
    runners and schemas before the first executable state becomes reachable.
    Replaying the same request key converges on the existing workload.
    """

    if isinstance(roots, (str, bytes)) or not isinstance(roots, Sequence):
        raise TypeError("roots must be a sequence")
    selected_roots = tuple(roots)
    if not 1 <= len(selected_roots) <= 1024:
        raise ValueError("roots must contain 1..1024 entries")
    candidate = registry.candidate_plan(registration_name)
    if candidate.get("plan_id") != registration_name:
        raise ValueError("registered plan identity changed")

    workload = store.create_draft(
        owner_user_id,
        request_key,
        redacted_request=redacted_request,
    )
    inventory: Mapping[str, Any] | None = None
    try:
        if workload.state is WorkloadState.DRAFT:
            inventory = authority.seal_and_register(
                selected_roots,
                owner_user_id=owner_user_id,
                workload_id=workload.workload_id,
                device_id=device_id,
                limits=_inventory_limits(candidate),
                valid_until=_authority_expiry(candidate),
            )

        if workload.state is WorkloadState.DRAFT and inventory is None:
            raise RuntimeError("draft admission lost its sealed inventory")
        result = submit_candidate(
            store,
            registry,
            owner_user_id,
            request_key,
            candidate,
            inventory or {},
            redacted_request=redacted_request,
            admission_boundary=admission_boundary,
        )
    finally:
        if inventory is not None:
            close = getattr(inventory, "close", None)
            if callable(close):
                close()

    return result
