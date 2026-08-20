"""Transactional admission facade for compiled durable-plan v1 revisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .compiler import (
    CompiledPlan,
    OutputSchemaResolver,
    RunnerContractResolver,
    compile_plan,
)
from .models import RevisionRecord
from .storage import DurableWorkloadStore


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    revision: RevisionRecord
    compiled: CompiledPlan


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
