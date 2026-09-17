"""Handled decoder outcomes preserve exact coverage without invented content."""
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

import image_index_build as storage
from image_index_outcomes import FAILURE_MARKER, failure_description
from test_image_index_build_phases import builder, corpus, _analyze, _discover, _invoke, _photo, _publish


@pytest.mark.parametrize("numerator", [0, 1])
def test_invalid_gps_rational_does_not_discard_a_valid_photo_or_repeat_models(corpus, monkeypatch, numerator):
    from PIL.TiffImagePlugin import IFDRational

    root, calls = corpus
    _photo(root)
    original_open = builder._open_image_with_exif

    def invalid_gps(path):
        image, exif = original_open(path)
        exif["GPSInfo"] = {"GPSLatitude": (IFDRational(numerator, 0), 0, 0),
                           "GPSLongitude": (12, 30, 0)}
        return image, exif

    monkeypatch.setattr(builder, "_open_image_with_exif", invalid_gps)
    discovery = _discover(root)
    receipts = _analyze(root, discovery)
    assert _analyze(root, discovery) == receipts
    result = _publish(root, discovery, receipts)
    assert len(calls) == 1
    assert result["n_indexed"] == 1 and result["n_not_indexed"] == 0
    assert _entries(result)[0]["exif_gps"] is None


def test_valid_gps_rationals_preserve_coordinates():
    from PIL.TiffImagePlugin import IFDRational

    assert builder._exif_gps({"GPSInfo": {
        "GPSLatitude": (IFDRational(45, 1), 30, 0), "GPSLatitudeRef": "S",
        "GPSLongitude": (12, 15, 0), "GPSLongitudeRef": "W",
    }}) == {"lat": -45.5, "lon": -12.25}


def _entries(result):
    return [json.loads(line) for line in (Path(result["index_path"]) / "entries.jsonl").read_text().splitlines()]


def test_mixed_corpus_counts_each_outcome_without_semantic_vectors_for_failures(corpus):
    root, calls = corpus
    _photo(root, "good-a.jpg")
    _photo(root, "good-c.jpg")
    broken = root / "bad-b.jpg"
    broken.write_bytes(b"not an image")
    before = broken.read_bytes()
    discovered = _discover(root)
    receipts = _analyze(root, discovered)
    result = _publish(root, discovered, receipts)
    assert result["n_entries_total"] == result["n_indexed"] + result["n_not_indexed"] == 3
    assert (result["ok_count"], result["fail_count"], result["refreshed_count"]) == (2, 1, 2)
    assert result["indexing_error_counts"] == {"image_format_unreadable": 1}
    assert "domain_outcome" not in result  # Only originating analysis units count errors in LRE.
    assert len(calls) == 2 and broken.read_bytes() == before
    entries = _entries(result)
    negative = next(entry for entry in entries if entry["path"] == str(broken))
    assert negative["description"].startswith(FAILURE_MARKER + ":image_format_unreadable ")
    assert "embedding_text_idx" not in negative and "embedding_image_idx" not in negative
    assert negative["keywords"] == negative["faces"] == []
    valid = [entry for entry in entries if entry["indexing_status"] == "indexed"]
    assert [entry["embedding_text_idx"] for entry in valid] == [0, 1]
    assert np.load(Path(result["index_path"]) / "embeddings_text.npy").shape[0] == 2
    repeated = _publish(root, discovered, receipts)
    assert repeated == result


@pytest.mark.parametrize("code", ["image_format_unreadable", "image_decode_failed"])
def test_both_decoder_outcomes_are_checkpoints_but_retried_in_a_new_generation(corpus, monkeypatch, code):
    root, calls = corpus
    _photo(root)
    opened = []
    original_open = builder._open_image_with_exif

    def unavailable(path):
        opened.append(path)
        raise storage.ImageIndexBuildError(code)

    monkeypatch.setattr(builder, "_open_image_with_exif", unavailable)
    discovered = _discover(root)
    receipts = _analyze(root, discovered)
    assert len(opened) == 1 and not calls
    assert _analyze(root, discovered) == receipts and len(opened) == 1
    result = _publish(root, discovered, receipts)
    assert result["n_not_indexed"] == 1

    # The same unchanged bytes can become readable with another decoder. A new
    # generation must re-attempt the negative record, not reuse its description.
    monkeypatch.setattr(builder, "_open_image_with_exif", original_open)
    discovered = _discover(root, "retry")
    result = _publish(root, discovered, _analyze(root, discovered, "retry"), "retry")
    assert (result["n_indexed"], result["n_not_indexed"]) == (1, 0)
    assert len(calls) == 1 and _entries(result)[0]["indexing_status"] == "indexed"


def test_negative_record_does_not_invoke_any_model(corpus, monkeypatch):
    root, calls = corpus
    (root / "bad.jpg").write_bytes(b"not an image")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("negative records must not use models")

    monkeypatch.setattr("face_embedding.get_face_engine", forbidden)
    monkeypatch.setattr("virt.get_embedder", forbidden)
    result = _publish(root, (discovered := _discover(root)), _analyze(root, discovered))
    assert result["n_not_indexed"] == 1 and not calls


@pytest.mark.parametrize("code", ["source_changed", "image_description_unavailable", "face_model_unavailable"])
def test_non_decoder_errors_are_not_suppressed(corpus, monkeypatch, code):
    root, _calls = corpus
    _photo(root)
    monkeypatch.setattr(builder, "_open_image_with_exif", lambda _path: (_ for _ in ()).throw(
        storage.ImageIndexBuildError(code)))
    discovered = _discover(root)
    group = discovered["entries"][0]
    result = _invoke(root, "build-1", "analyze", entries=[{
        "part": group["part"], "folder_contexts": {label: "Photos" for label in group["folder_labels"]},
    }])
    assert result["ok"] is False and result["error_code"] == code
    assert not (builder._index_dir(root) / "meta.json").exists()


@pytest.mark.parametrize(("observation", "code", "error_class"), [
    ({"_vlm_error": "output_truncated"}, "image_description_truncated", "executor_transient"),
    ({"_vlm_error": "no_json_found"}, "image_description_invalid", "executor_transient"),
    ({"_vlm_error": "response_schema_mismatch"}, "image_description_invalid", "executor_transient"),
    ({"description": "  "}, "image_description_empty", "executor_transient"),
    ({"_vlm_error": "http_failed: private diagnostic"}, "image_description_unavailable", "executor_transient"),
    ({"_vlm_error": "response_schema_invalid"}, "image_description_schema_invalid", "capability_unavailable"),
])
def test_description_failures_keep_a_closed_cause_without_failing_the_archive_permanently(
    corpus, monkeypatch, observation, code, error_class,
):
    root, _calls = corpus
    _photo(root)
    monkeypatch.setattr(builder, "_call_vlm", lambda *_args, **_kwargs: observation)
    discovered = _discover(root)
    group = discovered["entries"][0]
    result = _invoke(root, "build-1", "analyze", entries=[{
        "part": group["part"], "folder_contexts": {label: "Photos" for label in group["folder_labels"]},
    }])
    assert result["ok"] is False
    assert result["error_code"] == code and result["error_class"] == error_class
    assert "private diagnostic" not in json.dumps(result)
    assert "domain_outcome" not in result  # Not an accepted negative file outcome.
    assert not (builder._index_dir(root) / "meta.json").exists()


@pytest.mark.parametrize("change", ["code", "description", "vectors", "models", "embedding"])
def test_negative_leaf_cannot_hide_invalid_metadata_or_invented_vectors(corpus, change):
    root, _calls = corpus
    (root / "bad.jpg").write_bytes(b"not an image")
    discovered = _discover(root)
    receipts = _analyze(root, discovered)
    store = storage.ImageIndexBuild(root, "build-1")
    merged = store._part(receipts[0])
    leaf = store._part({"part": merged["children"][0]})
    if change == "code":
        leaf["entry"]["indexing_error_code"] = "made_up"
    elif change == "description":
        leaf["entry"]["description"] = "A successfully indexed photograph"
    elif change == "vectors":
        leaf["vectors"]["text"] = [[1.0, 0.0]]
    elif change == "models":
        leaf["models"] = {"model_text": "invented"}
    else:
        leaf["entry"]["embedding_image_idx"] = 0
    with pytest.raises(storage.ImageIndexBuildError, match="invalid_indexing_failure"):
        storage._validate_analysis(leaf, str(root))


def test_marker_is_not_localized_and_only_reason_uses_i18n(monkeypatch):
    seen = []
    monkeypatch.setattr("messages.get", lambda key: seen.append(key) or "Reason in selected language.")
    for code in ("image_decode_failed", "image_format_unreadable"):
        assert failure_description(code) == f"IMAGE_NOT_INDEXED:{code} Reason in selected language."
    assert seen == ["MSG_IMAGE_INDEX_DECODE_FAILED", "MSG_IMAGE_INDEX_FORMAT_UNREADABLE"]
    seed = Path(__file__).resolve().parents[3] / "install/data/i18n_seed.sqlite"
    with sqlite3.connect(f"file:{seed}?mode=ro", uri=True) as db:
        assert not db.execute("SELECT 1 FROM i18n WHERE key=? OR instr(text, ?) > 0",
                              (FAILURE_MARKER, FAILURE_MARKER)).fetchone()
        for key in seen:
            assert {row[0] for row in db.execute("SELECT lang FROM i18n WHERE key=?", (key,))} >= {"it", "en"}
