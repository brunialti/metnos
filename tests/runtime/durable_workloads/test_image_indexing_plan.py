"""Indexing admission and compilation without scanning sources or calling models."""
from __future__ import annotations

from contextlib import nullcontext
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

import lre_submission as submission
from durable_workloads import image_indexing as indexing
from durable_workloads.compiler import ApprovedOutputSchema, CompilationError, compile_plan
from durable_workloads.direct_invocation import DirectInvocationUnsupported
from durable_workloads.runtime_bindings import RuntimeRegistry
from durable_workloads.storage import DurableWorkloadStore
from engine.types import Framework, StepSpec


def test_image_diagnostic_enum_covers_literal_failures_and_versions_the_schemas():
    registry = indexing.output_schemas()
    codes = set(registry.resolve(indexing.PART_SCHEMA).field_schema("error_code")["enum"])
    root = Path(__file__).resolve().parents[3]
    emitted = {"image_index_phase_failed"}
    for path in (
        root / "runtime/image_index_build.py",
        root / "executors/create_images_indices/create_images_indices.py",
    ):
        tree = ast.parse(path.read_text())
        emitted.update(
            node.args[0].value for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"ImageIndexBuildError", "_error"}
            and node.args and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        )
    assert emitted == codes
    for name in (indexing.DISCOVERY_SCHEMA, indexing.PART_SCHEMA, indexing.PUBLISHED_SCHEMA):
        assert name.endswith("/2")
        current = registry.resolve(name)
        assert set(current.field_schema("error_code")["enum"]) == codes
        previous = json.loads(json.dumps(current.schema))
        del previous["properties"]["error_code"]
        old_name = name.removesuffix("/2") + "/1"
        assert ApprovedOutputSchema.create(old_name, previous).digest != current.digest
        with pytest.raises(CompilationError):
            registry.resolve(old_name)
    assert indexing.FOLDER_SCHEMA.endswith("/1")
    assert registry.resolve(indexing.FOLDER_SCHEMA).field_schema("error_code") is None


def _executor():
    path = Path(__file__).resolve().parents[3] / "executors/create_images_indices/manifest.toml"
    manifest = tomllib.loads(path.read_text())
    return SimpleNamespace(
        name=manifest["name"], version=manifest["version"],
        signed_by="independent-fixture-authority", digest="sha256:" + "a" * 64,
        lifecycle="active", dormant=False, lre_plan=indexing.PLAN_ID,
        transport="local-or-remote", intelligence=manifest["intelligence"],
        timeout_s=manifest["timeout_s"], args_schema=manifest["args"],
        capabilities=manifest.get("capabilities", ()), placement={"scope": "server"},
        execution_policy_declared=True, execution_policy=manifest["execution"],
    )


def _registration(executor):
    return indexing.registration(
        catalog_loader=lambda **_kw: {executor.name: executor}, language="en",
        binding_resolver=lambda tier, **_kw: {
            "provider": "llamacpp", "model": "fixture-chat", "tier": tier},
        vlm_binding={"provider": "llamacpp", "model": "fixture-vision", "usage_tier": "vlm:default",
                     "usage_kind": "vision", "max_input_tokens": 8192, "max_tokens": 512},
        prompt_digests={indexing.EXECUTOR: "sha256:" + "b" * 64,
                        indexing.FOLDER_WORKLOAD: "sha256:" + "c" * 64},
    )


def test_plan_compiles_with_discovery_in_background_and_bounded_groups(tmp_path):
    executor = _executor()
    registration = RuntimeRegistry((_registration(executor),))
    request = indexing.normalize_request(executor, {"base_path": str(tmp_path)})
    plan, inventory = indexing.build_candidate(
        request, "wrk_fixture_01", registration.runners, max_concurrency=3)
    compiled = compile_plan(plan, inventory, runners=registration.runners,
                            output_schemas=registration.output_schemas)
    assert inventory["sources"] == []
    assert [stage["key"] for stage in plan["stages"]] == [
        "inventory", "discover", "folders", "analyze", "merge", "publish"]
    assert plan["stages"][2]["cardinality"]["mode"] == "per_dependency"
    assert plan["stages"][4]["cardinality"]["fan_in"] == 32
    assert plan["stages"][5]["input_bindings"]["expected_count"] == {
        "ref": "dependency.result", "stage": "discover", "field": "source_count"}
    assert compiled.plan["budgets"]["max_concurrency"] == 3


def test_hierarchical_reducer_allows_constants_not_extra_variable_inputs(tmp_path):
    executor = _executor()
    registration = RuntimeRegistry((_registration(executor),))
    plan, inventory = indexing.build_candidate(
        indexing.normalize_request(executor, {"base_path": str(tmp_path)}),
        "wrk_fixture_01", registration.runners, max_concurrency=1)
    plan["stages"][4]["input_bindings"]["phase"] = {
        "ref": "dependency.result", "stage": "analyze", "field": "phase"}
    with pytest.raises(CompilationError):
        compile_plan(plan, inventory, runners=registration.runners,
                     output_schemas=registration.output_schemas)


@pytest.mark.parametrize("extra", [
    {"phase": "publish"}, {"generation": "caller-chosen"}, {"entries": []},
    {"dry_run": "true"}, {"max_files": True}, {"recursive": 1},
])
def test_public_invocation_cannot_select_worker_phases(tmp_path, extra):
    with pytest.raises(DirectInvocationUnsupported):
        indexing.normalize_request(_executor(), {"base_path": str(tmp_path), **extra})


def test_logical_corpus_key_survives_a_symlink_mount(tmp_path, monkeypatch):
    data = tmp_path / "user-data"
    data.mkdir()
    photos = tmp_path / "mounted-photos"
    photos.mkdir()
    logical = data / "Immagini"
    logical.symlink_to(photos, target_is_directory=True)
    monkeypatch.setenv("METNOS_USER_DATA", str(data))
    from index_schema import corpus_digest
    request = indexing.normalize_request(_executor(), {"base_path": str(photos)})
    assert request["base_path"] == str(logical)
    expected = corpus_digest(photos)
    logical.unlink()
    logical.mkdir()
    assert corpus_digest(request["base_path"]) == expected


def _ready(monkeypatch, tmp_path):
    executor = _executor()
    registry = RuntimeRegistry((_registration(executor),))
    database = tmp_path / "state" / "durable.sqlite3"
    monkeypatch.setattr(submission, "_require_ready", lambda: None)
    monkeypatch.setattr(submission, "_admission_boundary", lambda: nullcontext())
    monkeypatch.setattr(submission, "default_runtime_registry", lambda: registry)
    monkeypatch.setattr(submission, "DurableWorkloadStore", SimpleNamespace(
        open=lambda: DurableWorkloadStore.open(database)))
    monkeypatch.setattr(submission, "_msg", lambda key, **_kwargs: key)
    return executor, database


def test_repeated_queries_share_one_active_owner_scoped_job(tmp_path, monkeypatch):
    executor, database = _ready(monkeypatch, tmp_path)
    photos = tmp_path / "photos"
    photos.mkdir()
    framework = Framework(steps=[StepSpec(executor.name, {"base_path": str(photos)})])
    call = dict(catalog=[executor], owner_user_id="owner-a")
    first = submission.submit_automatic_lre(framework, turn_id="turn-first", **call)
    assert first["decision"] == "accepted", first
    again = submission.submit_automatic_lre(framework, turn_id="turn-again", **call)
    other = submission.submit_automatic_lre(
        framework, turn_id="turn-other", **{**call, "owner_user_id": "owner-b"})
    assert first["workload_id"] == again["workload_id"]
    assert first["workload_id"] != other["workload_id"]
    assert first["final_message_hint"] == "MSG_LRE_SUBMITTED"
    with DurableWorkloadStore.open(database) as store:
        assert len(store.list_workloads("owner-a")) == 1
        row = store._connection.execute(
            "SELECT redacted_request_json FROM workloads WHERE owner_user_id='owner-a'").fetchone()
        assert str(photos) not in row[0]
        assert json.loads(row[0])["payload"]["submission_scope"].startswith("sha256:")


def test_dry_run_remains_inline_without_opening_the_worker(tmp_path, monkeypatch):
    executor = _executor()
    monkeypatch.setattr(submission, "_require_ready", lambda: pytest.fail("no worker needed"))
    assert submission.submit_automatic_lre(
        Framework(steps=[StepSpec(executor.name, {"base_path": str(tmp_path), "dry_run": True})]),
        catalog=[executor], owner_user_id="owner", turn_id="dry-run") is None
