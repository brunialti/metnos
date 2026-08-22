"""F13 admission tests for registered, domain-neutral local-source plans."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from durable_workloads.admission import submit_registered_local_sources
from durable_workloads.compiler import (
    ApprovedOutputSchema,
    FrozenRunnerContract,
    OutputSchemaRegistry,
)
from durable_workloads.models import WorkloadState
from durable_workloads.runtime_bindings import RuntimeRegistration, RuntimeRegistry
from durable_workloads.source_authority import SourceAuthority
from durable_workloads.storage import DurableWorkloadStore
from helpers import plan


_DIGEST = "sha256:" + "b" * 64
_OWNER = "owner-f13"
_PLAN_ID = "tests.registered-submit.v1"


class _Runners:
    def __init__(self, contract: FrozenRunnerContract):
        self._contract = contract

    def resolve(self, kind: str, name: str) -> FrozenRunnerContract:
        if (kind, name) != (self._contract.kind, self._contract.name):
            raise LookupError(name)
        return self._contract

    def attest_executor(self, name: str, executor: object) -> FrozenRunnerContract:
        if executor is None:
            raise ValueError("executor is required")
        return self.resolve("executor", name)


def _registry() -> RuntimeRegistry:
    schema_name = "tests.registered-unused/1"
    schema = ApprovedOutputSchema.create(
        schema_name,
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    contract = FrozenRunnerContract(
        kind="executor",
        name="registered_unused_runner",
        contract_digest=_DIGEST,
        implementation_digest=_DIGEST,
        allowed_effects=("pure",),
        input_names=(),
        output_schema_names=(schema_name,),
    )
    candidate = plan()
    candidate["plan_id"] = _PLAN_ID
    return RuntimeRegistry((RuntimeRegistration(
        name=_PLAN_ID,
        runner_bindings=(("executor", contract.name),),
        runners=_Runners(contract),
        output_schemas=OutputSchemaRegistry((schema,)),
        output_schema_names=(schema_name,),
        candidate_plan_factory=lambda: candidate,
    ),))


def _submit(store, authority, registry, source, *, boundary=None):
    return submit_registered_local_sources(
        store,
        authority,
        registry,
        _PLAN_ID,
        _OWNER,
        "turn:f13",
        [source],
        redacted_request={"profile": _PLAN_ID, "source_root_count": 1},
        admission_boundary=boundary,
    )


def test_registered_submission_queues_once_and_keeps_roots_out_of_workload_db(
    tmp_path,
):
    source = tmp_path / "private corpus" / "source.bin"
    source.parent.mkdir()
    source.write_bytes(b"synthetic F13 source")
    database = tmp_path / "workloads.sqlite3"
    authority_database = tmp_path / "authority.sqlite3"

    with DurableWorkloadStore.open(database) as store:
        with SourceAuthority.open(authority_database) as authority:
            first = _submit(store, authority, _registry(), source)
            replay = _submit(store, authority, _registry(), source)

            assert first.workload.state is WorkloadState.QUEUED
            assert replay.workload.workload_id == first.workload.workload_id
            assert replay.workload.version == first.workload.version
            assert replay.revision is not None
            assert replay.revision.revision_id == first.revision.revision_id
            assert store._connection.execute(
                "SELECT COUNT(*) FROM workloads"
            ).fetchone()[0] == 1
            assert store._connection.execute(
                "SELECT COUNT(*) FROM revisions"
            ).fetchone()[0] == 1

    assert str(source).encode() not in database.read_bytes()


def test_failed_final_boundary_leaves_a_non_executable_draft_then_retries(
    tmp_path,
):
    source = tmp_path / "source.bin"
    source.write_bytes(b"stable source")

    @contextmanager
    def reject():
        raise RuntimeError("feature_disabled")
        yield  # pragma: no cover

    with DurableWorkloadStore.open(tmp_path / "workloads.sqlite3") as store:
        with SourceAuthority.open(tmp_path / "authority.sqlite3") as authority:
            with pytest.raises(RuntimeError, match="feature_disabled"):
                _submit(store, authority, _registry(), source, boundary=reject)

            draft = store.list_workloads(_OWNER)[0]
            assert draft.state is WorkloadState.DRAFT
            assert draft.active_revision_id is None
            assert store._connection.execute(
                "SELECT COUNT(*) FROM revisions"
            ).fetchone()[0] == 0

            retried = _submit(store, authority, _registry(), source)
            assert retried.workload.workload_id == draft.workload_id
            assert retried.workload.state is WorkloadState.QUEUED
            assert retried.revision is not None


def test_overlapping_delivery_converges_after_inventory_sealing(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"same request, same source")
    nested = []

    with DurableWorkloadStore.open(tmp_path / "workloads.sqlite3") as store:
        with SourceAuthority.open(tmp_path / "authority.sqlite3") as authority:

            @contextmanager
            def overlapping_delivery():
                nested.append(_submit(store, authority, _registry(), source))
                yield

            outer = _submit(
                store,
                authority,
                _registry(),
                source,
                boundary=overlapping_delivery,
            )

            assert len(nested) == 1
            assert outer.workload.state is WorkloadState.QUEUED
            assert outer.workload.workload_id == nested[0].workload.workload_id
            assert outer.revision is not None
            assert nested[0].revision is not None
            assert outer.revision.revision_id == nested[0].revision.revision_id
            assert store._connection.execute(
                "SELECT COUNT(*) FROM revisions"
            ).fetchone()[0] == 1
