"""LRE pipeline integration with real storage/worker and synthetic models.

This is deliberately not a real-model, HTTP-chat or sandbox E2E. The injected
executor transport isolates telemetry context like a child process, while the
actual builder, inventory, contracts, bridge, reduction and publication run.
"""
from __future__ import annotations

from collections import Counter
from contextvars import Context
from datetime import timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tomllib

import numpy as np
import pytest
from PIL import Image

from durable_workloads import image_indexing as indexing
from durable_workloads.admission import submit_candidate
from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.internal_runners import sealed_inventory
from durable_workloads.models import RESOURCE_KEYS, WorkloadState
from durable_workloads.runtime_bindings import RuntimeRegistry
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.worker import DurableWorker, WorkerRunStatus
from executor_scheduler import ExecutorScheduler
from image_index_build import GROUP_SIZE
from index_schema import resolve_image_index_dir
from llm_telemetry import (
    BoundedTransportUsageSink, TRANSPORT_USAGE_KEY, record, transport_usage_context,
)

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "executors/create_images_indices"))
sys.path.insert(0, str(_ROOT / "executors/find_images_indices"))
import create_images_indices as builder
import find_images_indices as search


def _executor():
    manifest = tomllib.loads((_ROOT / "executors/create_images_indices/manifest.toml").read_text())
    return SimpleNamespace(
        name=manifest["name"], version=manifest["version"],
        signed_by="isolated-synthetic-catalog", digest=manifest["code"]["digest"],
        lifecycle="active", dormant=False, lre_plan=manifest["lre_plan"],
        transport="local-or-remote", intelligence=manifest["intelligence"],
        timeout_s=manifest["timeout_s"], args_schema=manifest["args"],
        capabilities=manifest["capabilities"], placement={"scope": "server"},
        execution_policy_declared=True, execution_policy=manifest["execution"],
    )


def _registry(executor, vision_binding):
    return RuntimeRegistry((indexing.registration(
        catalog_loader=lambda **_kwargs: {executor.name: executor}, language="en",
        binding_resolver=lambda tier, **_kwargs: {
            "provider": "llamacpp", "model": "fixture-chat", "tier": tier},
        vlm_binding=vision_binding,
    ),))


def _model_usage(*, model, kind="chat", tier="middle"):
    record(provider="llamacpp", model=model, kind=kind, tier=tier,
           result=SimpleNamespace(text="fixture", in_tokens=7, out_tokens=3, latency_ms=1))


@pytest.mark.parametrize("with_unreadable_photo", [False, True])
def test_new_plan_publishes_and_searches_after_restart_without_repeating_accepted_groups(
    tmp_path, monkeypatch, with_unreadable_photo,
):
    photos = tmp_path / "photos"
    folder = photos / "event"
    folder.mkdir(parents=True)
    originals = []
    for number in range(GROUP_SIZE + 1):
        name = "workstation-computer.jpg" if number == GROUP_SIZE else f"landscape-{number:03d}.jpg"
        path = folder / name
        Image.new("RGB", (20, 20), "blue").save(path)
        originals.append(path)
    if with_unreadable_photo:
        originals[0].write_bytes(b"not a decodable image")
    indexed_count = len(originals) - int(with_unreadable_photo)
    expected_state = (WorkloadState.COMPLETED_WITH_ERRORS if with_unreadable_photo
                      else WorkloadState.COMPLETED)
    monkeypatch.setenv("METNOS_INDEX_ROOT", str(tmp_path / "index"))
    monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path / "user-data"))
    monkeypatch.setattr(builder, "analysis_identity", lambda _lang: "fixture-model-policy")
    monkeypatch.setattr("i18n.current_lang", lambda: "en")
    monkeypatch.setattr(search, "_split_persons_from_query", lambda query, _lang: (query, [], "and"))
    vision_binding = {
        "provider": "llamacpp", "model": "fixture-vision", "usage_tier": "vlm:default",
        "usage_kind": "vision", "max_input_tokens": 8192, "max_tokens": 512,
    }
    monkeypatch.setattr("vlm_client.model_binding_facts", lambda: dict(vision_binding))
    calls = {"vision": [], "folder": [], "phases": []}

    def describe(snapshot, *, prompt, max_tokens, allow_lazy_start, response_schema):
        from image_index_build import DESCRIPTION_SCHEMA
        assert response_schema == DESCRIPTION_SCHEMA
        assert Path(snapshot).is_file() and not allow_lazy_start and max_tokens == 512
        computer = "workstation-computer.jpg" in prompt
        calls["vision"].append("computer" if computer else "landscape")
        _model_usage(model="fixture-vision", kind="vision", tier="vlm:default")
        return {"description": "A computer on a desk." if computer else "A natural mountain landscape.",
                "keywords": ["computer"] if computer else ["mountain"],
                "location_hint": "", "activity_hint": ""}

    def classify(_self, _system, label, **kwargs):
        calls["folder"].append(label)
        assert kwargs["max_tokens"] == 512 and kwargs["request_timeout_s"] == 60
        _model_usage(model="fixture-chat", tier=kwargs["tier"])
        return SimpleNamespace(text="ALTRO|")

    text_model = SimpleNamespace(name="text-fixture", embed_texts=lambda texts: np.asarray([
        [1.0, 0.0] if "computer" in text.lower() else [0.0, 1.0] for text in texts
    ], dtype="float32"))
    image_model = SimpleNamespace(name="image-fixture", available=True,
                                  embed_images=lambda *_args, **_kwargs: np.array([[1, 0]], dtype="float32"))
    monkeypatch.setattr("virt.get_embedder", lambda role: {"text": text_model, "image": image_model}[role])
    monkeypatch.setattr("virt.get_local_embedder", lambda _role: text_model)
    monkeypatch.setattr("virt.local_models.local_embedding_spec", lambda _role: {"provider": "bge"})
    monkeypatch.setattr("face_embedding.get_face_engine", lambda: SimpleNamespace(
        name="face-fixture", available=True, detect_faces=lambda _path: []))
    monkeypatch.setattr("vlm_client.describe_image", describe)
    monkeypatch.setattr("llm_router.LLMRouter.chat", classify)
    scheduler = ExecutorScheduler(max_workers=2, max_in_flight=4, parallel_enabled=True,
                                  hardware_threads=2, resource_limits={key: 2 for key in (*RESOURCE_KEYS, "default")})
    monkeypatch.setattr("executor_scheduler._DEFAULT_SCHEDULER", scheduler)
    executor = _executor()
    registry = _registry(executor, vision_binding)

    def invoke_executor(loaded, args, context, timeout_s, _device_id, autonomy):
        assert loaded is executor and context.owner_user_id == "fixture-owner"
        assert timeout_s > 0 and autonomy == "supervised"
        calls["phases"].append((args["phase"], json.dumps(args.get("entries", []), sort_keys=True)))

        def child_process_scope():
            sink = BoundedTransportUsageSink()
            with transport_usage_context(sink):
                output = builder.invoke(dict(args))
            return {**output, TRANSPORT_USAGE_KEY: sink.export()}

        return Context().run(child_process_scope)

    def bindings(store, selected_registry, worker_id):
        worker = DurableWorker(store, worker_id,
                               selected_registry.capabilities({key: 2 for key in RESOURCE_KEYS}),
                               lease_duration=timedelta(seconds=120))
        bridge = DurableExecutionBridge(
            store, runners=selected_registry.runners, output_schemas=selected_registry.output_schemas,
            executor_loader=lambda _name: executor, executor_invoker=invoke_executor,
            workload_invoker=selected_registry.invoke_workload,
            internal_runners={"sealed_inventory": sealed_inventory},
        )
        return bridge, worker

    database = tmp_path / "state/durable.sqlite3"
    request = indexing.normalize_request(executor, {"base_path": str(photos), "max_files": len(originals)})
    candidate, inventory = indexing.build_candidate(request, "fixture-generation", registry.runners, max_concurrency=2)
    index = builder._index_dir(photos)
    try:
        with DurableWorkloadStore.open(database) as store:
            submitted = submit_candidate(store, registry, "fixture-owner", "fixture-request", candidate, inventory,
                                         redacted_request={"summary": "synthetic photo indexing"})
            workload_id = submitted.workload.workload_id
            assert submitted.workload.state is WorkloadState.QUEUED
            assert not index.exists() and not calls["vision"]
            bridge, worker = bindings(store, registry, "fixture-before-restart")
            for _step in range(20):
                outcome = bridge.run_once(worker)
                assert outcome.status in {WorkerRunStatus.COMMITTED, WorkerRunStatus.CONTROL_PROGRESS}, outcome
                if any(phase == "analyze" for phase, _entries in calls["phases"]):
                    break
            else:
                raise AssertionError("first analysis unit never committed")
            accepted = list(calls["phases"])
            assert not (index / "meta.json").exists()
            assert 0 < len(calls["vision"]) < len(originals)
            worker.request_stop()

        # Recreate the registry, bridge and worker: only durable accepted results
        # carry progress across this boundary, not an in-memory unit queue.
        resumed_registry = _registry(executor, vision_binding)
        with DurableWorkloadStore.open(database) as store:
            duplicate = submit_candidate(store, resumed_registry, "fixture-owner", "fixture-request",
                                         candidate, inventory, redacted_request={"summary": "synthetic photo indexing"})
            assert duplicate.workload.workload_id == workload_id
            bridge, worker = bindings(store, resumed_registry, "fixture-after-restart")
            for _step in range(30):
                outcome = bridge.run_once(worker)
                assert outcome.status in {WorkerRunStatus.COMMITTED, WorkerRunStatus.CONTROL_PROGRESS}, outcome
                if store.get_workload("fixture-owner", workload_id).state is expected_state:
                    break
            else:
                raise AssertionError("resumed image pipeline never completed")
            worker.request_stop()
            assert len(calls["vision"]) == indexed_count
            assert len(calls["folder"]) == 1
            assert all(Counter(calls["phases"])[item] == 1 for item in accepted)
            assert Counter(phase for phase, _entries in calls["phases"]) == {
                "discover": 1, "analyze": 2, "merge": 1, "publish": 1,
            }
            rows = store._connection.execute(
                "SELECT stages.stage_key, attempts.metrics_json FROM attempts "
                "JOIN units ON units.id=attempts.unit_id JOIN stages ON stages.id=units.stage_id "
                "WHERE attempts.owner_user_id=?", ("fixture-owner",),
            ).fetchall()
            usage = [(row["stage_key"], json.loads(row["metrics_json"])["llm_usage"])
                     for row in rows if row["stage_key"] != "inventory"]
            assert all(not item["usage_missing"] for _stage, item in usage)
            assert sum(len(item["records"]) for stage, item in usage if stage == "analyze") == indexed_count
            assert sum(len(item["records"]) for stage, item in usage if stage == "folders") == 1
            assert all(item["zero_calls_verified"] for stage, item in usage if stage in {"discover", "merge", "publish"})

        # Persist the item-level error ledger at completion, independently of
        # technical attempts and without recounting merge/publication summaries.
        with DurableWorkloadStore.open(database) as store:
            summary = store.execution_summary("fixture-owner", workload_id)
            assert summary["domain_errors"] == {
                "nitems": int(with_unreadable_photo),
                "categories": [{"error_code": "image_format_unreadable", "count": 1}]
                if with_unreadable_photo else [],
                "truncated": False,
            }
            assert summary["error_categories"] == []

        active = resolve_image_index_dir(index)
        assert active.name == "fixture-generation"
        assert len((active / "entries.jsonl").read_text().splitlines()) == len(originals)
        result = search.invoke({"base_path": str(photos), "query_text": "computer"})
        assert result["ok"], result
        assert [entry["path"] for entry in result["entries"]] == [str(originals[-1])]
        assert len(calls["vision"]) == indexed_count
        diagnostics = search.invoke({"base_path": str(photos), "query_text": "IMAGE_NOT_INDEXED"})
        assert diagnostics["ok"], diagnostics
        assert [entry["path"] for entry in diagnostics["entries"]] == (
            [str(originals[0])] if with_unreadable_photo else [])
        assert not diagnostics.get("attachments")
        metadata = json.loads((active / "meta.json").read_text())
        assert (metadata["n_indexed"], metadata["n_not_indexed"]) == (
            indexed_count, int(with_unreadable_photo))
    finally:
        scheduler.shutdown()
