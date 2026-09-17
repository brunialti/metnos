"""Identity-boundary regressions without model calls or source indexing."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from pathlib import Path
import tomllib

import pytest

import lre_submission as submission
from durable_workloads import image_indexing as indexing
from durable_workloads.models import ExecutionContext
from durable_workloads.runtime_bindings import RuntimeRegistry
from durable_workloads.storage import DurableWorkloadStore, IdempotencyConflictError
from test_image_indexing_plan import _executor, _registration


def test_server_search_prerequisite_with_conversational_device_creates_one_job(tmp_path, monkeypatch):
    """Actual wrapper, guard, adapter, compiler and admission; no model calls."""
    import agent_runtime
    from executor_prerequisites import normalize_prerequisites

    writer = _executor()
    # The shipped writer has no explicit placement: ordinary dispatch defaults
    # to server. Keep that absence in the regression, not a stronger fixture.
    writer.placement = {}
    root = Path(__file__).resolve().parents[3]
    manifest = tomllib.loads((root / 'executors/find_images_indices/manifest.toml').read_text())
    reader = SimpleNamespace(
        name='find_images_indices', signed_by='fixture-authority', lre_plan='',
        placement=manifest['placement'],
        prerequisites=normalize_prerequisites(manifest['prerequisites'], owner='find_images_indices'),
    )
    catalog = {writer.name: writer, reader.name: reader}
    registry = RuntimeRegistry((_registration(writer),))
    database = tmp_path / 'state.sqlite3'
    source = tmp_path / 'photos'
    source.mkdir()
    monkeypatch.setattr(agent_runtime, 'load_catalog', lambda **kwargs: catalog)
    monkeypatch.setattr(agent_runtime, '_invoke_executor_impl', lambda *args, **kwargs: {
        'ok': False, 'error_class': 'index_missing', 'base_path': str(source),
    })
    monkeypatch.setattr(submission, '_require_ready', lambda: None)
    monkeypatch.setattr(submission, '_admission_boundary', lambda: nullcontext())
    monkeypatch.setattr(submission, 'default_runtime_registry', lambda: registry)
    monkeypatch.setattr(submission, 'DurableWorkloadStore', SimpleNamespace(
        open=lambda: DurableWorkloadStore.open(database)))

    receipts = [agent_runtime._invoke_executor_impl_optional_context(
        reader, {'query_text': 'mare'}, owner_user_id='fixture-owner',
        turn_id=turn, target_device='fixture-device',
    ) for turn in ('turn-one', 'turn-one', 'turn-two')]
    assert all(receipt['ok'] for receipt in receipts)
    assert len({receipt['workload_id'] for receipt in receipts}) == 1
    with DurableWorkloadStore.open(database) as store:
        jobs = store.list_workloads('fixture-owner')
        assert len(jobs) == 1
        assert not store.list_workloads('another-owner')


def test_direct_remote_index_request_is_still_rejected(tmp_path):
    from durable_workloads.direct_invocation import DirectInvocationUnsupported
    with pytest.raises(DirectInvocationUnsupported):
        indexing.normalize_request(_executor(), {'base_path': str(tmp_path)}, 'fixture-device')


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
