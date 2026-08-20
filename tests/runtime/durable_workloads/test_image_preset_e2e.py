from __future__ import annotations

from datetime import timedelta
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
from helpers import inventory, source
from llm_telemetry import record
from test_image_preset_contracts import _catalog, _digest
from durable_workloads.image_preset import runner_resolver
from durable_workloads.coordinator import WorkerCapabilities


def _resolver():
    return runner_resolver(
        catalog_loader=lambda **_kwargs: _catalog(),
        binding_resolver=lambda tier, *, level=None: {
            "provider": "fixture", "model": "fixture-model", "tier": tier, "level": level,
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


def _raw_model(name: str, _prompt: str, args: dict) -> dict:
    record(
        provider="fixture",
        model="fixture-model",
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
    if name == "durable.images.reduce_formulae":
        return {"markdown": "# Formulario\n"}
    if name == "durable.images.assemble":
        return {"artifacts": [
            {"logical_name": "solutions_markdown", "markdown": "# Soluzioni\n"},
            {"logical_name": "notes_markdown", "markdown": "# Note\n"},
            {"logical_name": "formulae_markdown", "markdown": "# Formulario\n"},
        ]}
    raise AssertionError(name)


def test_image_preset_processes_98_sources_and_reuses_duplicate_solutions(tmp_path):
    database = tmp_path / "private" / "durable.sqlite3"
    resolver = _resolver()
    with DurableWorkloadStore.open(database) as store:
        draft = store.create_draft(
            "owner-images", "images-98", redacted_request={"summary": "synthetic corpus"},
        )
        admitted = admit_candidate(
            store,
            "owner-images",
            draft.workload_id,
            image_questions_plan(),
            inventory([source(index) for index in range(98)]),
            expected_version=draft.version,
            runners=resolver,
            output_schemas=output_schemas(),
        )
        store.transition_workload(
            "owner-images", draft.workload_id, WorkloadState.QUEUED,
            expected_version=store.get_workload("owner-images", draft.workload_id).version,
        )
        repository = ArtifactRepository.open(database)
        try:
            artifact_store = ArtifactStore(tmp_path / "artifacts", repository)
            workload_invoker = ImagePresetWorkloadInvoker(_raw_model)
            bridge = DurableExecutionBridge(
                store,
                runners=resolver,
                output_schemas=output_schemas(),
                source_resolver=lambda item: "/authorized/" + item["source_id"] + ".png",
                executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
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
                },
                workload_invoker=workload_invoker,
                internal_runners=approved_internal_runners(artifact_store),
            )
            worker = _worker(store)
            outcomes = []
            for _ in range(400):
                outcome = bridge.run_once(worker)
                outcomes.append(outcome.status)
                if outcome.status is WorkerRunStatus.IDLE:
                    break
            else:
                raise AssertionError("image preset did not become idle")

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
            assert counts["ocr"] == 98
            assert counts["questions"] == 98
            assert counts["solutions"] == 2
            assert len(artifact_store.list_workload_artifacts("owner-images", draft.workload_id)) == 3
        finally:
            repository.close()
