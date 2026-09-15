"""Identity-boundary regressions without model calls or source indexing."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import lre_submission as submission
from durable_workloads import image_indexing as indexing
from durable_workloads.models import ExecutionContext
from durable_workloads.runtime_bindings import RuntimeRegistry
from durable_workloads.storage import DurableWorkloadStore, IdempotencyConflictError
from test_image_indexing_plan import _executor, _registration


def test_folder_cache_changes_with_the_resolved_model_binding():
    executor = _executor()
    selected = {"model": "fixture-chat-a"}
    registration = indexing.registration(
        catalog_loader=lambda **_kwargs: {executor.name: executor}, language="en",
        binding_resolver=lambda tier, **_kwargs: {
            "provider": "llamacpp", "model": selected["model"], "tier": tier},
        vlm_binding={"provider": "llamacpp", "model": "fixture-vision", "usage_tier": "vlm:default",
                     "usage_kind": "vision", "max_input_tokens": 8192, "max_tokens": 512},
    )
    calls = []
    registration.workload_invoker.classifier = lambda label, lang: (
        calls.append((label, lang, selected["model"])) or selected["model"])
    registry = RuntimeRegistry((registration,))
    context = ExecutionContext("owner", "workload", "revision", "stage", "unit", "attempt",
                               "normal", (), None, "en")
    args = {"entries": [{"part": "a" * 64, "folder_labels": ["holiday"]}]}
    first = registry.invoke_workload(indexing.FOLDER_WORKLOAD, args, context)
    assert registry.invoke_workload(indexing.FOLDER_WORKLOAD, args, context) == first
    assert len(calls) == 1
    selected["model"] = "fixture-chat-b"
    second = registry.invoke_workload(indexing.FOLDER_WORKLOAD, args, context)
    assert second != first and len(calls) == 2


def test_read_only_delivery_lookup_does_not_create_drafts_and_checks_payload(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        assert store.find_submission_by_request_key("owner", "key", redacted_request={"x": 1}) is None
        assert not store.list_workloads("owner")
        draft = store.create_draft("owner", "key", redacted_request={"x": 1})
        assert store.find_submission_by_request_key("owner", "key", redacted_request={"x": 1}) == draft
        with pytest.raises(IdempotencyConflictError):
            store.find_submission_by_request_key("owner", "key", redacted_request={"x": 2})
        assert store.find_submission_by_request_key("other-owner", "key", redacted_request={"x": 2}) is None
        assert len(store.list_workloads("owner")) == 1


def test_scope_coalescing_cannot_hide_an_existing_delivery_payload_conflict(tmp_path, monkeypatch):
    executor = _executor()
    registry = RuntimeRegistry((_registration(executor),))
    database = tmp_path / "state.sqlite3"
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    monkeypatch.setattr(submission, "_require_ready", lambda: None)
    monkeypatch.setattr(submission, "_admission_boundary", lambda: nullcontext())
    monkeypatch.setattr(submission, "default_runtime_registry", lambda: registry)
    monkeypatch.setattr(submission, "DurableWorkloadStore", SimpleNamespace(
        open=lambda: DurableWorkloadStore.open(database)))

    def submit(root, key):
        return submission._submit_adapted_invocation(
            indexing, executor, {"base_path": str(root)}, None,
            owner_user_id="owner", request_key=key)

    first = submit(first_root, "delivery-a")
    second = submit(second_root, "delivery-b")
    assert first["workload_id"] != second["workload_id"]
    with pytest.raises(IdempotencyConflictError):
        submit(second_root, "delivery-a")
    with DurableWorkloadStore.open(database) as store:
        assert len(store.list_workloads("owner")) == 2
