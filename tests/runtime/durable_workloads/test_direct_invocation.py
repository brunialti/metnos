from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest

from durable_workloads.admission import admit_candidate, submit_candidate
from durable_workloads.compiler import CompilationError, compile_plan
from durable_workloads.direct_invocation import (
    DirectInvocationUnsupported,
    build_direct_candidate,
    direct_runtime_registration,
    is_intrinsically_long,
)
from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.internal_runners import sealed_inventory
from durable_workloads.models import WorkloadState
from durable_workloads.runtime_bindings import RuntimeRegistry
from durable_workloads.schema import SchemaValidationError, validate_plan
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.worker import DurableWorker, WorkerRunStatus


_DIGEST = "sha256:" + "a" * 64


def _executor(
    *,
    timeout_s: int = 600,
    effect: str = "read_only",
    intelligence: str = "deterministic",
    runtime_resolved: bool = False,
    resource_class: str = "local_io",
):
    properties = {
        "root": {"type": "string"},
        "recursive": {"type": "boolean"},
    }
    if runtime_resolved:
        properties["authority"] = {
            "type": "string",
            "runtime_resolved": True,
        }
    return SimpleNamespace(
        name="scan_fixture",
        version="1.0.0",
        signed_by="fixture-authority",
        digest=_DIGEST,
        lifecycle="active",
        dormant=False,
        transport="local-or-remote",
        intelligence=intelligence,
        timeout_s=timeout_s,
        args_schema={
            "type": "object",
            "required": ["root"],
            "properties": properties,
        },
        capabilities=(),
        placement={"scope": "any", "device_ok": True},
        execution_policy_declared=True,
        execution_policy={
            "effect": effect,
            "parallelism_class": 1 if effect == "read_only" else 0,
            "resource_class": resource_class,
            "concurrency_key": "none" if effect == "read_only" else "path",
            "equivalence_gate": "verified",
        },
    )


def test_threshold_and_registration_are_catalog_derived_and_fail_closed():
    short = _executor(timeout_s=599)
    long = _executor(timeout_s=600)
    intelligent = _executor(timeout_s=600, intelligence="llm")
    runtime_bound = _executor(timeout_s=600, runtime_resolved=True)
    agentic = _executor(timeout_s=600, intelligence="agentic")
    interactive = _executor(timeout_s=600, effect="interactive")

    assert is_intrinsically_long(short) is False
    assert is_intrinsically_long(long) is True
    assert direct_runtime_registration([short, intelligent, runtime_bound]) is None
    assert direct_runtime_registration([agentic, interactive]) is None

    registration = direct_runtime_registration([short, long])
    assert registration is not None
    assert registration.runner_bindings == (("executor", "scan_fixture"),)


def test_direct_plan_freezes_literals_placement_effect_and_digest():
    executor = _executor()
    registration = direct_runtime_registration([executor])
    assert registration is not None
    args = {"root": "/fixture", "recursive": True}
    candidate, selected_inventory = build_direct_candidate(
        executor, args, "PC-FIXTURE",
    )
    args["root"] = "/changed-after-build"

    assert candidate["stages"][1]["input_bindings"] == {
        "recursive": {"ref": "literal", "value": True},
        "root": {"ref": "literal", "value": "/fixture"},
    }
    assert candidate["stages"][1]["placement"] == {
        "target": "device", "device": "PC-FIXTURE",
    }
    assert candidate["stages"][1]["effect_profile"] == "pure"
    compiled = compile_plan(
        candidate,
        selected_inventory,
        runners=registration.runners,
        output_schemas=registration.output_schemas,
    )
    changed, same_inventory = build_direct_candidate(
        executor, {"root": "/other", "recursive": True}, "PC-FIXTURE",
    )
    changed_compiled = compile_plan(
        changed,
        same_inventory,
        runners=registration.runners,
        output_schemas=registration.output_schemas,
    )
    assert compiled.plan_digest != changed_compiled.plan_digest


def test_direct_plan_freezes_only_a_contract_compatible_placement():
    server_only = _executor()
    server_only.placement = {"scope": "server"}
    candidate, _inventory = build_direct_candidate(
        server_only, {"root": "/fixture"}, "PC-FIXTURE",
    )
    assert candidate["stages"][1]["placement"] == {"target": "server"}

    device_only = _executor()
    device_only.placement = {"scope": "device"}
    with pytest.raises(DirectInvocationUnsupported, match="frozen target"):
        build_direct_candidate(device_only, {"root": "/fixture"}, None)
    candidate, _inventory = build_direct_candidate(
        device_only, {"root": "/fixture"}, "PC-FIXTURE",
    )
    assert candidate["stages"][1]["placement"] == {
        "target": "device", "device": "PC-FIXTURE",
    }

    provider_backed = _executor()
    provider_backed.standard_state = "declared"
    provider_backed.args_schema["properties"]["client"] = {
        "type": "string",
        "enum": ["local", "google_workspace"],
    }
    provider_backed.capabilities = ({
        "name": "provider:access",
        "hint": ["google-workspace"],
        "when": {"arg": "client", "values": ["google_workspace"]},
    },)
    candidate, _inventory = build_direct_candidate(
        provider_backed,
        {"root": "/fixture", "client": "google_workspace"},
        "PC-FIXTURE",
    )
    assert candidate["stages"][1]["placement"] == {"target": "server"}


def test_direct_plan_rejects_unknown_missing_runtime_and_placeholder_args():
    executor = _executor()
    with pytest.raises(DirectInvocationUnsupported, match="match the schema"):
        build_direct_candidate(executor, {"unknown": 1}, None)
    with pytest.raises(DirectInvocationUnsupported, match="runtime reference"):
        build_direct_candidate(executor, {"root": "${step1.path}"}, None)
    with pytest.raises(DirectInvocationUnsupported, match="runtime reference"):
        build_direct_candidate(executor, {"root": "{{step1.path}}"}, None)
    with pytest.raises(DirectInvocationUnsupported, match="runtime reference"):
        build_direct_candidate(executor, {"root": "from_step:2"}, None)
    executor.args_schema["properties"]["from_step"] = {"type": "integer"}
    with pytest.raises(DirectInvocationUnsupported, match="runtime reference"):
        build_direct_candidate(executor, {"root": "/x", "from_step": 1}, None)
    with pytest.raises(DirectInvocationUnsupported, match="not admissible"):
        build_direct_candidate(_executor(runtime_resolved=True), {"root": "/x"}, None)
    with pytest.raises(DirectInvocationUnsupported, match="names are invalid"):
        build_direct_candidate(executor, {1: "/x"}, None)
    with pytest.raises(DirectInvocationUnsupported, match="not JSON"):
        build_direct_candidate(executor, {"root": object()}, None)
    with pytest.raises(DirectInvocationUnsupported, match="not JSON"):
        build_direct_candidate(executor, {"root": "x" * 1_048_577}, None)


def test_registration_excludes_unknown_resource_contracts():
    executor = _executor(resource_class="quantum_fixture")

    assert direct_runtime_registration([executor]) is None
    with pytest.raises(DirectInvocationUnsupported, match="not admissible"):
        build_direct_candidate(executor, {"root": "/fixture"}, None)


@pytest.mark.parametrize("resource_class", ["llm", "vlm"])
def test_registration_excludes_model_resources_without_frozen_bindings(
    resource_class,
):
    executor = _executor(resource_class=resource_class)

    assert direct_runtime_registration([executor]) is None
    with pytest.raises(DirectInvocationUnsupported, match="not admissible"):
        build_direct_candidate(executor, {"root": "/fixture"}, None)


def test_literal_type_and_placement_shapes_are_validated_before_admission():
    executor = _executor()
    registration = direct_runtime_registration([executor])
    assert registration is not None
    candidate, selected_inventory = build_direct_candidate(
        executor, {"root": "/fixture", "recursive": True}, None,
    )
    candidate["stages"][1]["input_bindings"]["recursive"]["value"] = 1
    with pytest.raises(CompilationError, match="incompatible reference type"):
        compile_plan(
            candidate,
            selected_inventory,
            runners=registration.runners,
            output_schemas=registration.output_schemas,
        )

    candidate, _inventory = build_direct_candidate(
        executor, {"root": "/fixture"}, None,
    )
    candidate["stages"][1]["placement"] = {
        "target": "server", "device": "PC-FIXTURE",
    }
    with pytest.raises(SchemaValidationError, match="invalid for server"):
        validate_plan(candidate)


def test_manual_only_direct_plan_has_one_attempt():
    executor = _executor(effect="mutating")
    candidate, _inventory = build_direct_candidate(
        executor, {"root": "/fixture"}, None,
    )
    stage = candidate["stages"][1]
    assert stage["effect_profile"] == "manual_only"
    assert stage["retry"] == {
        "max_attempts": 1,
        "base_delay_ms": 0,
        "max_delay_ms": 0,
        "retryable_error_classes": [],
    }


def test_direct_plan_executes_literal_once_on_the_frozen_device(tmp_path):
    executor = _executor()
    registration = direct_runtime_registration([executor])
    assert registration is not None
    registry = RuntimeRegistry((registration,))
    candidate, selected_inventory = build_direct_candidate(
        executor,
        {"root": "/fixture", "recursive": True},
        "PC-FIXTURE",
    )
    store = DurableWorkloadStore.open(tmp_path / "private" / "state.sqlite3")
    calls: list[tuple[dict, str | None]] = []
    try:
        draft = store.create_draft(
            "owner-direct", "direct-request", redacted_request={"summary": "fixture"},
        )
        admitted = admit_candidate(
            store,
            "owner-direct",
            draft.workload_id,
            candidate,
            selected_inventory,
            expected_version=draft.version,
            runners=registry.runners,
            output_schemas=registry.output_schemas,
        )
        store.transition_workload(
            "owner-direct",
            draft.workload_id,
            WorkloadState.QUEUED,
            expected_version=store.get_workload(
                "owner-direct", draft.workload_id,
            ).version,
        )
        row = store._connection.execute(
            "SELECT target_kind, target_device FROM stage_placements "
            "WHERE owner_user_id=? AND revision_id=?",
            ("owner-direct", admitted.revision.revision_id),
        ).fetchone()
        assert tuple(row) == ("device", "PC-FIXTURE")

        worker = DurableWorker(
            store,
            "direct-worker",
            registry.capabilities({
                "cpu": 1, "device": 1, "llm": 1, "local_io": 1,
                "network_io": 1, "vlm": 1,
            }),
            lease_duration=timedelta(seconds=120),
        )

        def invoke(_executor, args, _context, _timeout, device, _autonomy):
            calls.append((dict(args), device))
            return {"ok": True, "count": 1}

        bridge = DurableExecutionBridge(
            store,
            runners=registry.runners,
            output_schemas=registry.output_schemas,
            executor_loader=lambda _name: executor,
            executor_invoker=invoke,
            internal_runners={"sealed_inventory": sealed_inventory},
        )
        outcomes = [bridge.run_once(worker) for _index in range(8)]
        assert any(item.status is WorkerRunStatus.COMMITTED for item in outcomes)
        assert calls == [({"recursive": True, "root": "/fixture"}, "PC-FIXTURE")]
        usage = store._connection.execute(
            "SELECT input_bytes FROM revision_usage WHERE owner_user_id=? "
            "AND revision_id=?",
            ("owner-direct", admitted.revision.revision_id),
        ).fetchone()
        assert int(usage[0]) > 0
        assert store.purge_owner("owner-direct") == 1
        assert store._connection.execute(
            "SELECT COUNT(*) FROM stage_placements WHERE owner_user_id=?",
            ("owner-direct",),
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_concurrent_identical_direct_submissions_converge_without_outer_lock(
    tmp_path,
):
    executor = _executor()
    registration = direct_runtime_registration([executor])
    assert registration is not None
    registry = RuntimeRegistry((registration,))
    candidate, selected_inventory = build_direct_candidate(
        executor, {"root": "/fixture"}, None,
    )
    database = tmp_path / "private" / "concurrent.sqlite3"
    barrier = Barrier(2)

    @contextmanager
    def simultaneous_boundary():
        barrier.wait(timeout=5)
        yield

    def submit_once():
        with DurableWorkloadStore.open(database) as store:
            return submit_candidate(
                store,
                registry,
                "owner-concurrent",
                "request-concurrent",
                candidate,
                selected_inventory,
                redacted_request={"summary": "fixture"},
                admission_boundary=simultaneous_boundary,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(lambda _index: submit_once(), range(2)))

    assert first.workload.workload_id == second.workload.workload_id
    assert first.revision.revision_id == second.revision.revision_id
    with DurableWorkloadStore.open(database) as store:
        counts = store._connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM workloads), "
            "(SELECT COUNT(*) FROM revisions), "
            "(SELECT COUNT(*) FROM units WHERE stage_id IN ("
            "  SELECT id FROM stages WHERE stage_key='execute'"
            "))"
        ).fetchone()
        assert tuple(counts) == (1, 1, 1)
