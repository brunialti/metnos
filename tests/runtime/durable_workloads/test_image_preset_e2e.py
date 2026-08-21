from __future__ import annotations

import os
from datetime import timedelta
from time import monotonic
from types import SimpleNamespace

from durable_workloads.admission import admit_candidate
from durable_workloads.artifacts import ArtifactRepository, ArtifactStore
from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.image_preset import (
    ImagePresetWorkloadInvoker,
    image_questions_plan,
    output_schemas,
)
from durable_workloads.internal_runners import approved_internal_runners
from durable_workloads.models import DurableEffect, RunnerKind, WorkloadState
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.worker import DurableWorker, WorkerRunStatus
from helpers import inventory, source, source_resolution
from llm_telemetry import TRANSPORT_USAGE_KEY, TRANSPORT_USAGE_SCHEMA_VERSION, record
from test_image_preset_contracts import _catalog
from durable_workloads.image_preset import runner_resolver
from durable_workloads.coordinator import WorkerCapabilities


def _resolver():
    return runner_resolver(
        catalog_loader=lambda **_kwargs: _catalog(),
        binding_resolver=lambda tier, *, level=None: {
            "provider": "llamacpp", "model": "fixture-model", "tier": tier, "level": level,
        },
    )


def _worker(store: DurableWorkloadStore) -> DurableWorker:
    workloads = (
        "durable.images.answer",
        "durable.images.assemble",
        "durable.images.deduplicate",
        "durable.images.extract_questions",
        "durable.images.reduce_formulae",
        "durable.images.reduce_notes",
        "durable.images.reduce_solutions",
        "durable.images.validate",
    )
    capabilities = WorkerCapabilities.create(
        (
            (RunnerKind.EXECUTOR, "read_files_ocr"),
            (RunnerKind.INTERNAL, "artifact_store_publish"),
            (RunnerKind.INTERNAL, "schema_and_coverage_validator"),
            (RunnerKind.INTERNAL, "sealed_inventory"),
            *((RunnerKind.WORKLOAD, name) for name in workloads),
        ),
        {"cpu": 1, "device": 1, "llm": 1, "local_io": 1, "network_io": 0, "vlm": 1},
        effect_profiles=(DurableEffect.PURE, DurableEffect.IDEMPOTENT),
    )
    return DurableWorker(
        store, "image-e2e", capabilities, lease_duration=timedelta(minutes=5),
    )


def _raw_model(name: str, _prompt: str, args: dict, _context: object) -> dict:
    record(
        provider="llamacpp",
        model="fixture-model",
        tier="wise",
        result=SimpleNamespace(in_tokens=5, out_tokens=3, latency_ms=1),
    )
    if name == "durable.images.extract_questions":
        ordinal = int(args["source"]["ordinal"])
        if ordinal % 11 == 0:
            return {"questions": []}
        questions = [{"text": "Qual è la risposta?", "confidence": 0.95}]
        if ordinal % 17 == 0:
            questions.append({"text": "Qual è la seconda risposta?", "confidence": 0.9})
        return {"questions": questions}
    if name == "durable.images.deduplicate":
        return {"merge_groups": []}
    if name == "durable.images.answer":
        return {"status": "answered", "answer": "Risposta sintetica.", "reason": "", "confidence": 0.9}
    if name == "durable.images.validate":
        return {"valid": True, "reason": ""}
    if name == "durable.images.reduce_notes":
        return {"markdown": "# Note\n"}
    if name == "durable.images.reduce_solutions":
        return {"markdown": "# Soluzioni\n"}
    if name == "durable.images.reduce_formulae":
        return {"markdown": "# Formulario\n"}
    if name == "durable.images.assemble":
        return {"artifacts": [
            {"logical_name": "solutions_markdown", "markdown": "# Soluzioni\n"},
            {"logical_name": "notes_markdown", "markdown": "# Note\n"},
            {"logical_name": "formulae_markdown", "markdown": "# Formulario\n"},
        ]}
    raise AssertionError(name)


def test_image_preset_processes_a_parametric_corpus_and_reuses_solutions(tmp_path):
    source_count = int(os.environ.get("METNOS_DURABLE_IMAGE_E2E_SOURCES", "98"))
    assert source_count >= 1
    database = tmp_path / "private" / "durable.sqlite3"
    resolver = _resolver()
    source_work = 2 * source_count
    restart_points = iter(sorted({
        max(1, source_work * 3 // 10),
        max(1, source_work * 6 // 10),
    }))
    next_restart = next(restart_points, None)
    # Harness deadline only: it is neither a plan nor an engine capacity cap.
    test_deadline = monotonic() + max(120.0, source_count * 0.25)
    with DurableWorkloadStore.open(database) as store:
        draft = store.create_draft(
            "owner-images", "images-corpus",
            redacted_request={"summary": "synthetic corpus"},
        )
        admitted = admit_candidate(
            store,
            "owner-images",
            draft.workload_id,
            image_questions_plan(),
            inventory([source(index) for index in range(source_count)]),
            expected_version=draft.version,
            runners=resolver,
            output_schemas=output_schemas(),
        )
        store.transition_workload(
            "owner-images", draft.workload_id, WorkloadState.QUEUED,
            expected_version=store.get_workload("owner-images", draft.workload_id).version,
        )
        repository = ArtifactRepository.open(database)
        artifact_store = None
        try:
            artifact_store = ArtifactStore(tmp_path / "artifacts", repository)
            workload_invoker = ImagePresetWorkloadInvoker(_raw_model)
            def make_bridge(open_store, open_artifacts):
                return DurableExecutionBridge(
                        open_store,
                        runners=resolver,
                        output_schemas=output_schemas(),
                        source_resolver=lambda item, _context: source_resolution(item),
                        executor_loader=lambda name: _catalog().get(name),
                        executor_invoker=lambda _executor, args, *_rest: {
                        "ok": True,
                        "ok_count": 1,
                        "fail_count": 0,
                        "source_id": args["source"]["source_id"],
                        "entries": [{
                            "source_id": args["source"]["source_id"],
                            "content": "fixture",
                            "char_count": 7,
                            "lang": "ita+eng",
                            }],
                            "failed": [],
                            TRANSPORT_USAGE_KEY: {
                                "schema_version": TRANSPORT_USAGE_SCHEMA_VERSION,
                                "records": [],
                                "dropped": 0,
                                "calls_started": 0,
                            },
                        },
                    workload_invoker=workload_invoker,
                    internal_runners=approved_internal_runners(open_artifacts),
                )

            bridge = make_bridge(store, artifact_store)
            worker = _worker(store)
            outcomes = []
            executed_units = 0
            while monotonic() < test_deadline:
                outcome = bridge.run_once(worker)
                outcomes.append(outcome.status)
                if outcome.lease is not None:
                    executed_units += 1
                if next_restart is not None and executed_units >= next_restart:
                    artifact_store.close()
                    store.close()
                    store = DurableWorkloadStore.open(database)
                    repository = ArtifactRepository.open(database)
                    artifact_store = ArtifactStore(tmp_path / "artifacts", repository)
                    bridge = make_bridge(store, artifact_store)
                    worker = _worker(store)
                    next_restart = next(restart_points, None)
                if outcome.status is WorkerRunStatus.IDLE:
                    break
            else:
                raise AssertionError(
                    "parametric image corpus did not become idle before the "
                    f"test deadline (sources={source_count}, cycles={len(outcomes)})"
                )

            assert WorkerRunStatus.FAILED not in outcomes
            assert store.get_workload("owner-images", draft.workload_id).state is WorkloadState.COMPLETED
            counts = dict(store._connection.execute(
                """
                SELECT stage.stage_key, COUNT(*)
                FROM units unit
                JOIN stages stage
                  ON stage.owner_user_id=unit.owner_user_id
                 AND stage.id=unit.stage_id
                 AND stage.revision_id=unit.revision_id
                WHERE unit.owner_user_id=? AND unit.revision_id=?
                GROUP BY stage.stage_key
                """,
                ("owner-images", admitted.revision.revision_id),
            ).fetchall())
            assert counts["ocr"] == source_count
            assert counts["questions"] == source_count
            assert counts["solutions"] == 2
            assert len(artifact_store.list_workload_artifacts("owner-images", draft.workload_id)) == 3
        finally:
            if artifact_store is None:
                repository.close()
            else:
                artifact_store.close()
