"""Negative image records are evidence, not searchable photos or retry requests."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "executors" / "find_images_indices"))
sys.path.insert(0, str(_ROOT / "executors" / "get_images_indices"))
import find_images_indices as search
import get_images_indices as status


def record(path, *, code=None, description="A sunny beach"):
    entry = {"path": str(path), "name": Path(path).name, "size": 12,
             "description": description, "keywords": [], "faces": []}
    if code:
        entry.update(indexing_status="not_indexed", indexing_error_code=code,
                     description=f"IMAGE_NOT_INDEXED:{code} Unreadable image")
    return entry


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("METNOS_INDEX_ROOT", str(tmp_path / "index"))
    base = tmp_path / "photos"
    base.mkdir()
    return base


def persist(base, records, *, legacy=False):
    directory = search._index_dir(base)
    directory.mkdir(parents=True, exist_ok=True)
    negative = sum(entry.get("indexing_status") == "not_indexed" for entry in records)
    metadata = {"schema_version": 4, "base_path": str(base), "n_entries": len(records)}
    if not legacy:
        metadata.update(n_indexed=len(records) - negative, n_not_indexed=negative)
    (directory / "meta.json").write_text(json.dumps(metadata))
    (directory / "entries.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in records))
    for axis in ("text", "face", "image"):
        np.save(directory / f"embeddings_{axis}.npy", np.empty((0, 0), dtype="float32"))
    return directory


def no_models(monkeypatch):
    def reject(*_args, **_kwargs):
        pytest.fail("diagnostic enumeration must not load models or vectors")
    monkeypatch.setattr("virt.get_local_embedder", reject)
    monkeypatch.setattr(search, "_corpus_token_embs", reject)
    monkeypatch.setattr(search, "_split_persons_from_query", reject)
    monkeypatch.setattr(search, "_split_temporal_from_query", reject)
    monkeypatch.setattr(np, "load", reject)


@pytest.mark.parametrize("arguments", [
    {"match_all": True}, {"max_face_count": 0}, {"paths_filter": ["/photos/good.jpg", "/photos/bad.jpg"]},
])
def test_ordinary_search_never_projects_a_negative_record(arguments):
    result = search._filter_unified([
        record("/photos/good.jpg"), record("/photos/bad.jpg", code="image_decode_failed"),
    ], None, None, {}, arguments)
    assert [entry["path"] for entry in result["entries"]] == ["/photos/good.jpg"]
    assert result["metadata"]["total_count"] == 1
    assert len(search._build_attachments_from_entries(result["entries"])) == 1


def test_ordinary_bm25_cannot_match_negative_description(monkeypatch):
    monkeypatch.setattr("virt.get_local_embedder", lambda *_: SimpleNamespace(
        embed_texts=lambda *_: np.zeros((1, 2), dtype="float32")))
    result = search._filter_unified([
        record("/good.jpg"), record("/bad.jpg", code="image_decode_failed"),
    ], None, None, {}, {"query_text": "Unreadable image"})
    assert not result["entries"]


@pytest.mark.parametrize("marker,expected", [
    ("IMAGE_NOT_INDEXED", ["bad.jpg", "format.jpg"]),
    ("IMAGE_NOT_INDEXED:image_decode_failed", ["bad.jpg"]),
    ("IMAGE_NOT_INDEXED:image_format_unreadable", ["format.jpg"]),
])
def test_exact_marker_lists_only_authoritative_negatives_without_models(corpus, monkeypatch, marker, expected):
    persist(corpus, [record(corpus / "good.jpg"),
                     record(corpus / "bad.jpg", code="image_decode_failed"),
                     record(corpus / "format.jpg", code="image_format_unreadable"),
                     record(corpus / "literal.jpg", description="IMAGE_NOT_INDEXED:image_decode_failed printed text")])
    no_models(monkeypatch)
    result = search.invoke({"base_path": str(corpus), "query_text": marker})
    assert result["ok"]
    assert [entry["name"] for entry in result["entries"]] == expected
    assert all(entry["match_type"] == "indexing_failure" and entry["indexing_status"] == "not_indexed"
               for entry in result["entries"])
    assert result["attachments"] == []
    assert "recommended_action" not in result


def test_marker_in_caption_does_not_make_a_normal_photo_negative():
    entry = record("/literal.jpg", description="IMAGE_NOT_INDEXED:image_decode_failed printed on a sign")
    result = search._filter_unified([entry], None, None, {}, {"match_all": True})
    assert len(result["entries"]) == 1
    assert search._filter_unified([entry], None, None, {}, {"query_text": "IMAGE_NOT_INDEXED"})["entries"] == []


def test_negative_path_filter_is_exact_and_does_not_need_source_files(corpus, monkeypatch):
    persist(corpus, [record(corpus / "first.jpg", code="image_decode_failed"),
                     record(corpus / "second.jpg", code="image_decode_failed")])
    no_models(monkeypatch)
    result = search.invoke({"base_path": str(corpus), "query_text": "IMAGE_NOT_INDEXED",
                            "paths_filter": [str(corpus / "second.jpg")]})
    assert [entry["name"] for entry in result["entries"]] == ["second.jpg"]
    assert result["applied_paths_filter"] == 1


def test_diagnostic_description_uses_current_translation_not_stored_language(monkeypatch):
    monkeypatch.setattr("messages.get", lambda _key: "Current language explanation")
    result = search._filter_unified([record("/bad.jpg", code="image_decode_failed")],
                                    None, None, {}, {"query_text": "IMAGE_NOT_INDEXED"})
    assert result["entries"][0]["description"] == "IMAGE_NOT_INDEXED:image_decode_failed Current language explanation"


@pytest.mark.parametrize("extra", [
    {"name": "Alice"}, {"names": ["Alice"]}, {"reference_images": ["/reference.jpg"]},
    {"near_lat": 1}, {"near_lon": 2}, {"min_face_pixels": 0}, {"min_face_count": 0},
    {"max_face_count": 0}, {"time_window": "2025"}, {"text_score_min": 0},
    {"similarity_threshold": 0.4}, {"query_text": "IMAGE_NOT_INDEXED:unknown"},
    {"query_text": "IMAGE_NOT_INDEXED image_decode_failed"},
])
def test_diagnostic_query_rejects_ambiguous_combinations_before_loading(corpus, monkeypatch, extra):
    no_models(monkeypatch)
    result = search.invoke({"base_path": str(corpus), "query_text": "IMAGE_NOT_INDEXED", **extra})
    assert not result["ok"] and result["error_code"] == "diagnostic_query_invalid"
    assert not result["entries"]


def test_all_negative_index_is_valid_and_does_not_trigger_reindex(corpus, monkeypatch):
    persist(corpus, [record(corpus / "bad.jpg", code="image_decode_failed")])
    monkeypatch.setattr("virt.get_local_embedder", lambda *_: pytest.fail("no usable photos"))
    result = search.invoke({"base_path": str(corpus), "match_all": True})
    assert result["ok"] and result["entries"] == [] and result["metadata"]["total_count"] == 0
    assert result["attachments"] == [] and "recommended_action" not in result
    summary = status.invoke({"base_path": str(corpus)})
    assert summary["ok"] and summary["entries"][0]["exists"]
    assert (summary["total_records"], summary["indexed_entries_total"], summary["not_indexed_entries_total"]) == (1, 0, 1)


@pytest.mark.parametrize("explicit", [False, True])
def test_missing_diagnostic_index_never_requests_a_new_build(corpus, monkeypatch, explicit):
    no_models(monkeypatch)
    args = {"query_text": "IMAGE_NOT_INDEXED"}
    if explicit:
        args["base_path"] = str(corpus)
    result = search.invoke(args)
    assert not result["ok"] and result["error_class"] == "not_found"
    assert result["error_code"] == "diagnostic_index_missing"
    assert "recommended_action" not in result


def test_negative_tokens_do_not_enter_query_expansion(corpus, monkeypatch):
    entry = record(corpus / "bad.jpg", code="image_decode_failed")
    entry.update(keywords=["unreadable"], path_tokens=["mountains"])
    directory = persist(corpus, [entry])
    monkeypatch.setattr("virt.get_local_embedder", lambda *_: pytest.fail("no positive vocabulary"))
    assert search._corpus_token_embs(directory) == ([], None)


def test_multicorpus_diagnostic_totals_and_truncation(corpus, monkeypatch):
    other = corpus.parent / "other"
    other.mkdir()
    for base in (corpus, other):
        persist(base, [record(base / f"bad-{number:03d}.jpg", code="image_decode_failed") for number in range(101)])
    no_models(monkeypatch)
    result = search.invoke({"query_text": "IMAGE_NOT_INDEXED", "max_results": 200})
    assert result["ok"] and len(result["entries"]) == 200
    assert result["metadata"]["total_count"] == result["available_total"] == 202
    assert result["truncated"] and result["attachments"] == []
    summary = status.invoke({})
    assert (summary["total_records"], summary["indexed_entries_total"], summary["not_indexed_entries_total"]) == (202, 0, 202)


@pytest.mark.parametrize("legacy", [False, True])
def test_status_preserves_existing_positive_counts(corpus, legacy):
    persist(corpus, [record(corpus / "good.jpg")], legacy=legacy)
    result = status.invoke({"base_path": str(corpus)})
    assert result["ok"]
    assert (result["total_records"], result["indexed_entries_total"], result["not_indexed_entries_total"]) == (1, 1, 0)


def test_mixed_status_separates_successes_from_coverage(corpus):
    persist(corpus, [record(corpus / "good.jpg"), record(corpus / "bad.jpg", code="image_decode_failed")])
    result = status.invoke({"base_path": str(corpus)})
    assert result["ok"]
    assert (result["entries"][0]["n_entries"], result["indexed_entries_total"], result["not_indexed_entries_total"]) == (2, 1, 1)


@pytest.mark.parametrize("counters", [
    {"n_indexed": 2, "n_not_indexed": 1}, {"n_not_indexed": -1},
    {"n_indexed": True}, {"n_entries": "1"},
])
def test_status_rejects_inconsistent_counters(corpus, counters):
    directory = persist(corpus, [record(corpus / "good.jpg")])
    path = directory / "meta.json"
    metadata = json.loads(path.read_text())
    path.write_text(json.dumps({**metadata, **counters}))
    result = status.invoke({"base_path": str(corpus)})
    assert not result["ok"] and result["failed"][0]["error_code"] == "index_metadata_invalid"
