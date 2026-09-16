"""Real artifact/storage checks with synthetic model responses, not LLM E2E."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "executors/create_images_indices"))
import create_images_indices as builder
import image_index_build as storage
from index_schema import resolve_image_index_dir


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    photos = tmp_path / "photos"
    photos.mkdir()
    monkeypatch.setenv("METNOS_INDEX_ROOT", str(tmp_path / "index"))
    monkeypatch.setattr(builder, "analysis_identity", lambda _lang: "fixture-policy")
    monkeypatch.setattr("face_embedding.get_face_engine", lambda: SimpleNamespace(
        available=True, name="face-fixture", detect_faces=lambda _path: [{
            "bbox": [1, 1, 5, 5], "score": 0.99,
            "embedding": np.array([1, 0, 0], dtype="float32"),
        }]))
    models = {
        "text": SimpleNamespace(name="text-fixture", embed_texts=lambda _texts: np.array([[1, 0]], dtype="float32")),
        "image": SimpleNamespace(name="image-fixture", available=True,
                                 embed_images=lambda *_args, **_kwargs: np.array([[0, 1, 0]], dtype="float32")),
    }
    monkeypatch.setattr("virt.get_embedder", lambda role: models[role])
    monkeypatch.setattr("virt.local_models.local_embedding_spec", lambda _role: {"provider": "bge"})
    calls = []

    def describe(path, *, original_path):
        calls.append((Path(path), Path(original_path)))
        return {"description": f"A picture from {original_path.name}", "keywords": ["picture"],
                "location_hint": "studio", "activity_hint": ""}

    monkeypatch.setattr(builder, "_call_vlm", describe)
    return photos, calls


def _photo(root, name="portrait.jpg", color="blue"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 20), color).save(path)
    return path


def _invoke(root, generation, phase, **args):
    return builder.invoke({"base_path": str(root), "generation": generation, "phase": phase, **args})


def _discover(root, generation="build-1", **args):
    result = _invoke(root, generation, "discover", **args)
    assert result["ok"], result
    return result


def _analyze(root, discovery, generation="build-1", **args):
    receipts = []
    for group in discovery["entries"]:
        result = _invoke(root, generation, "analyze", entries=[{
            "part": group["part"], "folder_contexts": {label: f"Photos: {label}." for label in group["folder_labels"]},
        }], **args)
        assert result["ok"], result
        receipts.extend(result["entries"])
    return receipts


def _publish(root, discovery, receipts, generation="build-1"):
    result = _invoke(root, generation, "publish", entries=receipts, expected_count=discovery["source_count"])
    assert result["ok"], result
    return result


def test_phase_omitted_never_builds_or_launches_processes(corpus, monkeypatch):
    root, calls = corpus
    _photo(root)
    monkeypatch.setattr("subprocess.Popen", lambda *_args, **_kwargs: pytest.fail("must not spawn"))
    result = builder.invoke({"base_path": str(root)})
    assert result["error_code"] == "requires_lre"
    assert not calls
    assert not builder._index_dir(root).exists()


def test_discovery_groups_are_bounded_and_not_published(corpus):
    root, calls = corpus
    for number in range(storage.GROUP_SIZE + 1):
        _photo(root, f"event/person-{number:03d}.jpg")
    (root / "note.txt").write_text("not an image")
    result = _discover(root)
    assert result["source_count"] == storage.GROUP_SIZE + 1
    assert len(result["entries"]) == 2
    build = storage.ImageIndexBuild(root, "build-1")
    assert [build._part(item)["count"] for item in result["entries"]] == [storage.GROUP_SIZE, 1]
    assert result["entries"][0]["folder_labels"] == ["event"]
    assert not calls
    assert not (build.root / "meta.json").exists()
    assert "original_path" not in json.dumps(result)


def test_discovery_overflow_and_nonrecursive_policy_are_honest(corpus):
    root, calls = corpus
    _photo(root, "one.jpg")
    _photo(root, "sub/two.jpg")
    failed = _invoke(root, "too-small", "discover", max_files=1)
    assert not failed["ok"]
    assert not calls
    assert not (builder._index_dir(root) / "meta.json").exists()
    assert _discover(root, "flat", recursive=False, max_files=1)["source_count"] == 1


def test_analysis_uses_snapshot_bytes_and_original_metadata(corpus, monkeypatch):
    root, calls = corpus
    original = _photo(root, "Birthday 2024/Ada at home.jpg")
    discovery = _discover(root)
    monkeypatch.setattr(storage, "classify_folder_context", lambda *_args: pytest.fail("folder LLM inside analyze"))
    receipts = _analyze(root, discovery)
    assert len(calls) == 1
    snapshot, authoritative = calls[0]
    assert snapshot != original and authoritative == original
    assert snapshot.read_bytes() == original.read_bytes()
    assert not (builder._index_dir(root) / "meta.json").exists()
    result = _publish(root, discovery, receipts)
    entry = json.loads((Path(result["index_path"]) / "entries.jsonl").read_text())
    assert entry["path"] == str(original)
    assert entry["name"] == original.name
    assert entry["path_context"] == "Photos: Birthday."
    assert "ada" in entry["path_tokens"] and snapshot.name not in entry["path_tokens"]
    assert entry["mtime_ns"] == original.stat().st_mtime_ns
    assert entry["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()


def test_changed_original_after_discovery_is_rejected(corpus):
    root, calls = corpus
    original = _photo(root)
    discovery = _discover(root)
    original.write_bytes(b"changed since sealing")
    group = discovery["entries"][0]
    result = _invoke(root, "build-1", "analyze", entries=[{
        "part": group["part"], "folder_contexts": {label: "" for label in group["folder_labels"]},
    }])
    assert not result["ok"]
    assert not calls
    assert not (builder._index_dir(root) / "meta.json").exists()


def test_publish_is_atomic_retryable_and_keeps_previous_generation(corpus):
    root, calls = corpus
    _photo(root)
    first = _discover(root)
    receipts = _analyze(root, first)
    initial = _publish(root, first, receipts)
    old = resolve_image_index_dir(builder._index_dir(root))
    assert resolve_image_index_dir(old) == old
    before = (old / "entries.jsonl").read_bytes()
    retried = _publish(root, first, receipts)
    assert initial["last_refresh_at"] == retried["last_refresh_at"]
    _photo(root, "new.jpg")
    second = _discover(root, "build-2")
    next_receipts = _analyze(root, second, "build-2")
    assert resolve_image_index_dir(builder._index_dir(root)) == old
    result = _publish(root, second, next_receipts, "build-2")
    assert result["n_entries_total"] == 2
    assert (old / "entries.jsonl").read_bytes() == before
    assert resolve_image_index_dir(builder._index_dir(root)) != old
    # A delayed old retry must never reactivate an earlier generation.
    stale = _invoke(root, "build-1", "publish", entries=receipts, expected_count=1)
    assert not stale["ok"]
    assert stale["error_code"] == "active_generation_changed"


def test_incremental_reuse_requires_exact_digest_and_force_reanalyzes(corpus):
    root, calls = corpus
    original = _photo(root)
    first = _discover(root)
    _publish(root, first, _analyze(root, first))
    assert len(calls) == 1
    second = _discover(root, "same")
    reused = _publish(root, second, _analyze(root, second, "same"), "same")
    assert len(calls) == 1
    assert reused["refreshed_count"] == 0
    forced = _discover(root, "forced")
    _publish(root, forced, _analyze(root, forced, "forced", force=True), "forced")
    assert len(calls) == 2
    before = original.stat()
    data = bytearray(original.read_bytes())
    # Change an image byte without changing size or mtime; a full digest must
    # invalidate reuse even when the old metadata-only shortcut would pass.
    data[-3] ^= 1
    original.write_bytes(data)
    os.utime(original, ns=(before.st_atime_ns, before.st_mtime_ns))
    changed = _discover(root, "changed")
    _analyze(root, changed, "changed")
    assert len(calls) == 3


def test_legacy_reuse_remaps_original_paths_and_rechecks_exact_content(corpus):
    root, calls = corpus
    original = _photo(root, "event/portrait.jpg")
    first = _discover(root)
    previous = _publish(root, first, _analyze(root, first))
    index, generation = builder._index_dir(root), Path(previous["index_path"])
    metadata = json.loads((generation / "meta.json").read_text())
    metadata.pop("active_generation")
    metadata.pop("root_part")
    legacy_base = root.parent / "previous-physical-mount"
    metadata["base_path"] = str(legacy_base)
    entry = json.loads((generation / "entries.jsonl").read_text())
    entry["path"] = str(legacy_base / original.relative_to(root))
    entry.pop("_analysis_identity")
    entry.pop("_vector_digests")
    (index / "entries.jsonl").write_text(json.dumps(entry) + "\n")
    for axis in ("text", "face", "image"):
        shutil.copyfile(generation / f"embeddings_{axis}.npy", index / f"embeddings_{axis}.npy")
    (index / "meta.json").write_text(json.dumps(metadata))
    before = original.stat()
    os.utime(original, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))

    found = _discover(root, "legacy-reused")
    result = _publish(root, found, _analyze(root, found, "legacy-reused"), "legacy-reused")
    assert len(calls) == 1
    assert result["refreshed_count"] == 0
    reused = json.loads((Path(result["index_path"]) / "entries.jsonl").read_text())
    assert reused["path"] == str(original)
    assert reused["mtime_ns"] == original.stat().st_mtime_ns
    assert reused["_analysis_identity"] == "fixture-policy"
    assert (index / "entries.jsonl").read_text() == json.dumps(entry) + "\n"


def test_discovery_output_limit_stops_before_accumulating_unbounded_receipts(corpus, monkeypatch):
    root, calls = corpus
    _photo(root, "one.jpg")
    _photo(root, "two.jpg")
    monkeypatch.setattr(storage, "GROUP_SIZE", 1)
    monkeypatch.setattr("durable_workloads.schema.MAX_RESULT_JSON_BYTES", 65_536 + 180)
    result = _invoke(root, "bounded-output", "discover")
    assert result["error_code"] == "discovery_output_too_large"
    assert not calls
    assert not (builder._index_dir(root) / "meta.json").exists()


def test_completed_publication_retry_does_not_rebuild_vectors(corpus, monkeypatch):
    root, _calls = corpus
    _photo(root)
    found = _discover(root)
    receipts = _analyze(root, found)
    first = _publish(root, found, receipts)
    monkeypatch.setattr(storage.ImageIndexBuild, "_leaves", lambda *_args: pytest.fail("completed tree must not be recopied"))
    assert _publish(root, found, receipts) == first


def test_hierarchical_reduction_preserves_exact_published_entries_and_vectors(corpus):
    root, _calls = corpus
    for name in ("z.jpg", "a.jpg", "m.jpg"):
        _photo(root, name)
    publications = []
    for generation, hierarchical in (("flat", False), ("tree", True)):
        found = _discover(root, generation)
        analyzed = _analyze(root, found, generation, force=True)
        build = storage.ImageIndexBuild(root, generation)
        leaves = [{"part": value} for value in build._part(analyzed[0])["children"]]
        if hierarchical:
            leaves = [build.merge(leaves[:1]), build.merge(leaves[1:])]
        else:
            leaves.reverse()
        result = _publish(root, found, leaves, generation)
        publications.append(Path(result["index_path"]))
    for filename in ("entries.jsonl", "embeddings_text.npy", "embeddings_face.npy", "embeddings_image.npy"):
        assert (publications[0] / filename).read_bytes() == (publications[1] / filename).read_bytes()


def test_reduction_depth_and_fanout_fail_before_publication(corpus):
    root, _calls = corpus
    build = storage.ImageIndexBuild(root, "depth")
    with pytest.raises(storage.ImageIndexBuildError, match="reduction_fanout_invalid"):
        build.merge([{"part": "a" * 64}] * (storage.MAX_CHILDREN + 1))
    receipt = build.merge([])
    for _level in range(storage.MAX_DEPTH + 1):
        receipt = build.merge([receipt])
    result = _invoke(root, "depth", "publish", entries=[receipt], expected_count=0)
    assert result["error_code"] == "reduction_depth_invalid"
    assert not (build.root / "meta.json").exists()


def test_failed_analysis_or_incomplete_coverage_never_replaces_index(corpus, monkeypatch):
    root, calls = corpus
    _photo(root)
    first = _discover(root)
    _publish(root, first, _analyze(root, first))
    pointer = builder._index_dir(root) / "meta.json"
    before = pointer.read_bytes()
    _photo(root, "other.jpg")
    second = _discover(root, "fail")
    monkeypatch.setattr(builder, "_call_vlm", lambda *_args, **_kwargs: {"_vlm_error": "simulated outage"})
    group = second["entries"][0]
    failed = _invoke(root, "fail", "analyze", entries=[{
        "part": group["part"], "folder_contexts": {label: f"Photos: {label}." for label in group["folder_labels"]},
    }])
    assert not failed["ok"]
    assert pointer.read_bytes() == before
    incomplete = _invoke(root, "fail", "publish", entries=[], expected_count=2)
    assert incomplete["error_code"] == "coverage_mismatch"
    assert pointer.read_bytes() == before


@pytest.mark.parametrize("invalid", ["../escape", "/absolute", "a/b", "", "x" * 129])
def test_generation_cannot_escape_managed_storage(corpus, invalid):
    root, _calls = corpus
    result = _invoke(root, invalid, "merge", entries=[])
    assert result["error_code"] == "generation_invalid"


def test_receipt_hash_tampering_and_symlinks_fail_closed(corpus, tmp_path):
    root, _calls = corpus
    build = storage.ImageIndexBuild(root, "hashes")
    receipt = build.merge([])
    path = build.parts / f"{receipt['part']}.json"
    path.write_text("{}")
    with pytest.raises(storage.ImageIndexBuildError, match="part_digest_mismatch"):
        build.merge([receipt])
    path.unlink()
    secret = tmp_path / "private.json"
    secret.write_text("{}")
    path.symlink_to(secret)
    with pytest.raises(OSError):
        build.merge([receipt])
    with pytest.raises(storage.ImageIndexBuildError, match="part_invalid"):
        build.merge([{"part": "../private"}])


def test_duplicate_subtrees_and_paths_are_not_published(corpus):
    root, _calls = corpus
    _photo(root)
    found = _discover(root)
    receipt = _analyze(root, found)[0]
    build = storage.ImageIndexBuild(root, "build-1")
    with pytest.raises(storage.ImageIndexBuildError, match="duplicate_part"):
        build.merge([receipt, receipt])
    parent = build.merge([receipt])
    result = _invoke(root, "build-1", "publish", entries=[parent, receipt], expected_count=2)
    assert result["error_code"] == "duplicate_part"
    assert not (build.root / "meta.json").exists()


def test_legacy_index_remains_readable_and_is_never_deleted(corpus):
    root, _calls = corpus
    index = builder._index_dir(root)
    index.mkdir(parents=True)
    (index / "meta.json").write_text(json.dumps({"schema_version": 4, "base_path": str(root), "n_entries": 0}))
    (index / "entries.jsonl").write_text("")
    assert resolve_image_index_dir(index) == index
    _photo(root)
    found = _discover(root)
    result = _publish(root, found, _analyze(root, found))
    assert Path(result["index_path"]) != index
    assert (index / "entries.jsonl").exists()
    assert not result["index_created"]


def test_folder_classification_is_one_registered_bounded_call(monkeypatch):
    calls = []
    monkeypatch.setattr("llm_router.LLMRouter.chat", lambda self, *args, **kwargs: (
        calls.append((args, kwargs)) or SimpleNamespace(text="VIAGGIO|Malta")))
    assert storage.classify_folder_context("Malta", "en") == "Photos of a trip to Malta."
    assert len(calls) == 1
    assert calls[0][1]["tier"] == "middle"
    assert calls[0][1]["max_tokens"] == 512
    assert calls[0][1]["request_timeout_s"] == 60


def test_index_prompt_uses_original_filename_and_disables_service_start(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr("vlm_client.describe_image", lambda path, **kwargs: seen.update(path=path, **kwargs) or {})
    snapshot = tmp_path / "opaque-snapshot"
    original = Path("/photos/Graduation 2024/Ada.jpg")
    builder._call_vlm(snapshot, original_path=original)
    assert seen["path"] == snapshot
    assert "Ada.jpg" in seen["prompt"] and "Graduation 2024" in seen["prompt"]
    assert "opaque-snapshot" not in seen["prompt"]
    assert seen["allow_lazy_start"] is False
    assert seen["response_schema"] == storage.DESCRIPTION_SCHEMA


def test_analysis_identity_covers_the_response_schema(monkeypatch):
    monkeypatch.setattr("virt.local_models.model_spec", lambda _role: {"provider": "fixture"})
    monkeypatch.setattr("virt.local_models.projected_embedding_spec", lambda _role: {"provider": "fixture"})
    monkeypatch.setattr("virt.local_models.model_artifacts", lambda _spec: [])
    monkeypatch.setattr("prompt_loader.prompt_identity", lambda *_args: SimpleNamespace(digest="fixture-prompt"))
    monkeypatch.setattr("vlm_client.model_binding_facts", lambda: {"model": "fixture"})
    before = storage.analysis_identity("it")
    changed = json.loads(json.dumps(storage.DESCRIPTION_SCHEMA))
    changed["properties"]["description"]["maxLength"] += 1
    monkeypatch.setattr(storage, "DESCRIPTION_SCHEMA", changed)
    assert storage.analysis_identity("it") != before


def test_vlm_connection_failure_cannot_start_services_or_retry(monkeypatch, tmp_path):
    import urllib.error
    import vlm_client

    image = _photo(tmp_path)
    attempts = []

    def refused(*_args, **_kwargs):
        attempts.append(1)
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(vlm_client.urllib.request, "urlopen", refused)
    monkeypatch.setattr(vlm_client, "_lazy_start_vlm", lambda **_kwargs: pytest.fail("must not start a service"))
    result = vlm_client.describe_image(image, allow_lazy_start=False)
    assert result["_vlm_error"]
    assert len(attempts) == 1


def test_failed_atomic_activation_keeps_old_index_and_retry_uses_complete_generation(corpus, monkeypatch):
    root, _calls = corpus
    _photo(root)
    first = _discover(root)
    _publish(root, first, _analyze(root, first))
    pointer = builder._index_dir(root) / "meta.json"
    previous = pointer.read_bytes()
    second = _discover(root, "interrupted")
    receipts = _analyze(root, second, "interrupted")
    write = storage._write_bytes

    def interrupted(path, data, *, immutable):
        if path == pointer:
            raise OSError("simulated interruption before reference replacement")
        write(path, data, immutable=immutable)

    monkeypatch.setattr(storage, "_write_bytes", interrupted)
    result = _invoke(root, "interrupted", "publish", entries=receipts, expected_count=1)
    assert not result["ok"]
    assert pointer.read_bytes() == previous
    assert (pointer.parent / ".generations/interrupted/meta.json").is_file()
    monkeypatch.setattr(storage, "_write_bytes", write)
    _publish(root, second, receipts, "interrupted")
    assert resolve_image_index_dir(pointer.parent).name == "interrupted"


def test_two_distinct_analysis_attempts_for_one_path_cannot_be_published(corpus):
    root, _calls = corpus
    _photo(root)
    found = _discover(root)
    receipts = _analyze(root, found)
    build = storage.ImageIndexBuild(root, "build-1")
    parent = build._part(receipts[0])
    leaf = build._part({"part": parent["children"][0]})
    leaf["entry"]["description"] += " Another attempt."
    distinct = build._store_part(leaf)
    result = _invoke(root, "build-1", "publish", entries=[receipts[0], distinct], expected_count=2)
    assert result["error_code"] == "duplicate_source_path"
    assert not (build.root / "meta.json").exists()


def test_snapshot_source_outside_root_is_rejected_before_reading(corpus, tmp_path, monkeypatch):
    root, _calls = corpus
    source = _photo(tmp_path)
    build = storage.ImageIndexBuild(root, "escape")
    monkeypatch.setattr("durable_workloads.inventory._stable_file_digest", lambda *_args, **_kwargs: pytest.fail("must not read outside the source root"))
    with pytest.raises(storage.ImageIndexBuildError, match="source_path_invalid"):
        build.snapshot({"original_path": str(source), "source": {
            "content_digest": "sha256:" + "a" * 64, "size_bytes": 1, "mtime_ns": 1,
        }})


def test_malformed_count_or_model_metadata_cannot_change_publication(corpus):
    root, _calls = corpus
    _photo(root)
    found = _discover(root)
    receipts = _analyze(root, found)
    build = storage.ImageIndexBuild(root, "build-1")
    bad_count = build._store_part({"kind": "merge", "children": [receipts[0]["part"]], "count": 2})
    result = _invoke(root, "build-1", "publish", entries=[bad_count], expected_count=2)
    assert result["error_code"] == "part_count_mismatch"
    parent = build._part(receipts[0])
    leaf = build._part({"part": parent["children"][0]})
    leaf["models"]["active_generation"] = "unrelated"
    bad_models = build._store_part(leaf)
    result = _invoke(root, "build-1", "publish", entries=[bad_models], expected_count=1)
    assert result["error_code"] == "model_metadata_invalid"
    assert not (build.root / "meta.json").exists()


def test_group_snapshots_and_vectors_are_bounded(corpus, monkeypatch):
    root, _calls = corpus
    _photo(root)
    found = _discover(root)
    group = found["entries"][0]
    result = _invoke(root, "build-1", "analyze", entries=[group, group])
    assert result["error_code"] == "analysis_group_invalid"
    monkeypatch.setattr("virt.get_embedder", lambda role: SimpleNamespace(
        name=f"{role}-fixture", available=True,
        embed_texts=lambda *_args: np.array([[float("nan"), 0]], dtype="float32"),
        embed_images=lambda *_args, **_kwargs: np.array([[1, 0]], dtype="float32"),
    ))
    result = _invoke(root, "build-1", "analyze", entries=[{
        "part": group["part"], "folder_contexts": {label: "" for label in group["folder_labels"]},
    }])
    assert result["error_code"] == "vectors_invalid"
    assert not (builder._index_dir(root) / "meta.json").exists()
