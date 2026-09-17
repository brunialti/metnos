"""Subprocess fixture: load exactly one runtime tree, never production state.

Used by test_release_resume.py; this is not a production worker entry point.
The registered capability writes one real, disjoint file per durable unit.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone


def main():
    tree, root = (Path(value).resolve() for value in sys.argv[1:3])
    mode = sys.argv[3]
    assert mode in {"queued", "interrupted", "resume"}
    sys.path[:0] = [str(tree / "runtime"),
                   str(tree / "tests/runtime/durable_workloads"), str(tree)]
    import config
    for selected in (config.PATH_USER_DATA, config.PATH_USER_STATE, config.PATH_USER_CONFIG):
        assert selected.is_relative_to(root), selected
    from durable_workloads.admission import admit_candidate
    from durable_workloads.compiler import (
        ApprovedOutputSchema, FrozenRunnerContract, OutputSchemaRegistry,
        _freeze_execution_policy,
    )
    from durable_workloads.inventory import InventoryLimits
    from durable_workloads.models import WorkloadState
    from durable_workloads.runtime_bindings import RuntimeFactory, RuntimeRegistration, RuntimeRegistry
    from durable_workloads.service import DurableWorkerService
    from durable_workloads.source_authority import SourceAuthority
    from durable_workloads.storage import DurableWorkloadStore
    import executor_scheduler
    from helpers import plan

    # Select a genuinely absent marker in the fixture, not a mocked verdict.
    # RuntimeFactory must still compose and exercise the real F5 guard reader.
    if (tree / "runtime/executor_birth_activation_mode.py").exists():
        import executor_birth_activation_mode as activation
        activation.ACTIVATION_DIRECTORY = root / "absent-f5-activation"
        assert activation.read_birth_activation_state().owner is activation.BirthStateOwner.LEGACY

    scheduler = executor_scheduler.ExecutorScheduler(
        max_workers=8, max_in_flight=8, hardware_threads=8, parallel_enabled=True,
        resource_limits={"cpu": 4 if mode == "resume" else 1, "default": 8},
    )
    executor_scheduler._DEFAULT_SCHEDULER = scheduler
    schema_name, runner_name = "tests.release-resume/1", "fixture.release_resume"
    contract = FrozenRunnerContract(
        kind="workload", name=runner_name,
        contract_digest="sha256:" + "1" * 64,
        implementation_digest="sha256:" + "2" * 64,
        allowed_effects=("idempotent",), input_names=("paths",),
        required_input_names=("paths",), input_types=(("paths", "array"),),
        output_schema_names=(schema_name,),
        execution_policy=_freeze_execution_policy({
            "effect": "mutating", "parallelism_class": 3, "resource_class": "cpu",
            "concurrency_key": "path", "equivalence_gate": "verified",
        }), execution_policy_declared=True,
    )
    class Resolver:
        def resolve(self, kind, name):
            assert (kind, name) == (contract.kind, contract.name)
            return contract

    schema = ApprovedOutputSchema.create(schema_name, {
        "type": "object", "properties": {"value": {"type": "string"}},
        "required": ["value"], "additionalProperties": False,
    })
    guard = threading.Lock()
    overlap = threading.Barrier(4)
    active = peak = calls = 0

    def destination(context):
        return root / "outputs" / hashlib.sha256(context.unit_key.encode()).hexdigest()

    def ready():
        (root / "ready.json").write_text(json.dumps({"mode": mode, "tree": str(tree)}))

    def invoke(_name, arguments, context):
        nonlocal active, peak, calls
        with guard:
            active += 1
            peak = max(peak, active)
            calls += 1
            crash_here = mode == "interrupted" and calls == 3
        try:
            if crash_here:
                ready()
                assert threading.Event().wait(40), "parent did not interrupt the fixture"
            if mode == "resume":
                overlap.wait(timeout=10)
            value = Path(arguments["paths"][0]).read_text()
            destination(context).write_text(value)
            with guard, (root / "calls.jsonl").open("a") as stream:
                stream.write(json.dumps({"mode": mode, "unit": context.unit_key}) + "\n")
            return {"value": value}
        finally:
            with guard:
                active -= 1

    options = {}
    if mode == "resume":
        options["concurrency_targets_resolver"] = lambda _contract, _args, context, _device: (
            str(destination(context)),)
    registration = RuntimeRegistration(
        name="tests.release-resume.v1", runner_bindings=(("workload", runner_name),),
        runners=Resolver(), output_schemas=OutputSchemaRegistry((schema,)),
        output_schema_names=(schema_name,), workload_invoker=invoke, **options,
    )
    registry = RuntimeRegistry((registration,))
    database, authority_path = root / "state.sqlite3", root / "authority.sqlite3"
    factory = RuntimeFactory(
        registry_factory=lambda: registry, source_authority_path=authority_path,
        artifact_root=root / "artifacts", lease_duration=timedelta(seconds=1),
    )
    if mode != "resume":
        (root / "outputs").mkdir()
        inputs = root / "inputs"
        inputs.mkdir()
        originals = [inputs / f"source-{index}.txt" for index in range(6)]
        for index, original in enumerate(originals):
            original.write_text(f"synthetic payload {index}")
        with DurableWorkloadStore.open(database) as store:
            draft = store.create_draft("fixture-owner", "release-resume", redacted_request={})
            with SourceAuthority.open(authority_path) as authority:
                inventory = authority.seal_and_register(
                    originals, owner_user_id="fixture-owner", workload_id=draft.workload_id,
                    device_id="server", limits=InventoryLimits(
                        max_sources=6, max_total_bytes=4096, max_depth=1),
                    valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
                )
            candidate = plan(with_map=True)
            candidate["budgets"]["max_concurrency"] = 4
            stage = candidate["stages"][1]
            stage.update(runner={"kind": "workload", "name": runner_name},
                         effect_profile="idempotent")
            stage["resources"]["cpu"] = 1
            stage["retry"]["max_attempts"] = 3
            stage["output_schema"]["name"] = schema_name
            admitted = admit_candidate(
                store, "fixture-owner", draft.workload_id, candidate, inventory,
                expected_version=draft.version, runners=registry.runners,
                output_schemas=registry.output_schemas, usage_complete=True,
            )
            store.transition_workload(
                "fixture-owner", draft.workload_id, WorkloadState.QUEUED,
                expected_version=store.get_workload("fixture-owner", draft.workload_id).version,
            )
            (root / "identity.json").write_text(json.dumps({
                "job": draft.workload_id, "revision": admitted.revision.revision_id,
            }))
            worker, bridge = factory.worker(store), factory.bridge(store)
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            try:
                for _ in range(20):
                    bridge.run_once(worker)
                    if mode == "queued" and calls == 2:
                        ready()
                        assert threading.Event().wait(40), "parent did not stop the fixture"
                    if calls > 2:
                        raise AssertionError("missed the interruption point")
                raise AssertionError("fixture did not make progress")
            finally:
                bridge.close()
    else:
        identity = json.loads((root / "identity.json").read_text())
        service = DurableWorkerService(
            enabled=True, store_path=database, health_path=root / "health.json",
            worker_factory=factory.worker, bridge_factory=factory.bridge,
            poll_interval_s=0.05, parallel_workers=4,
        )
        terminal = []
        finished = threading.Event()
        def monitor():
            try:
                deadline = time.monotonic() + 30
                with DurableWorkloadStore.open(database) as store:
                    while not finished.is_set() and time.monotonic() < deadline:
                        state = store.get_workload("fixture-owner", identity["job"]).state
                        if state in {WorkloadState.COMPLETED, WorkloadState.FAILED,
                                     WorkloadState.NEEDS_ATTENTION}:
                            terminal.append(state.value)
                            return
                        time.sleep(0.02)
            finally:
                service.request_stop()
        watcher = threading.Thread(target=monitor)
        watcher.start()
        try:
            exit_code = service.run_forever()
        finally:
            finished.set()
            service.request_stop()
            watcher.join(timeout=5)
        assert terminal == ["completed"], terminal
        assert exit_code == 0 and calls == 4 and peak == 4, (exit_code, calls, peak)
        (root / "resumed.json").write_text(json.dumps({
            "tree": str(tree), "calls": calls, "peak": peak, "terminal": terminal,
        }))
    scheduler.shutdown()


if __name__ == "__main__":
    main()
