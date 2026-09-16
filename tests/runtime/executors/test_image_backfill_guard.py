"""The old manual writer must not touch immutable or negative generations."""
import json
from types import SimpleNamespace

import pytest

from jobs import index_image_embed_backfill as backfill
from jobs import reembed_path_context as reembed


@pytest.mark.parametrize("operation", ["image", "text", "text_dry"])
@pytest.mark.parametrize("metadata,entry", [
    ({"active_generation": "generation-one"}, {"path": "/photo.jpg"}),
    ({"active_generation": None}, {"path": "/photo.jpg"}),
    ({"generation_files": {}}, {"path": "/photo.jpg"}),
    ({"generation_files": None}, {"path": "/photo.jpg"}),
    ({"n_not_indexed": 1}, {"path": "/photo.jpg"}),
    ({}, {"path": "/photo.jpg", "indexing_status": "not_indexed"}),
    ({"n_not_indexed": 0}, {"path": "/photo.jpg", "indexing_status": "not_indexed",
                           "indexing_error_code": "unknown_future_code"}),
])
def test_protected_index_rejected_before_any_model_or_write(tmp_path, monkeypatch, metadata, entry, operation):
    directory = tmp_path / "index" / "image" / "corpus" / "unified"
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(json.dumps(metadata))
    (directory / "entries.jsonl").write_text(json.dumps(entry) + "\n")
    (directory / "embeddings_image.npy").write_bytes(b"must-not-be-loaded-or-written")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    def forbidden(*_args, **_kwargs):
        pytest.fail("protected index must not invoke models or load/write vectors")

    monkeypatch.setattr(backfill, "get_embedder", forbidden)
    monkeypatch.setattr(reembed, "get_embedder", forbidden)
    monkeypatch.setattr(reembed.C, "folder_path_context", forbidden)
    monkeypatch.setattr(reembed, "_load_cache", forbidden)
    monkeypatch.setattr(reembed, "_save_cache", forbidden)
    monkeypatch.setattr(backfill.np, "load", forbidden)
    monkeypatch.setattr(backfill.np, "save", forbidden)
    with pytest.raises(ValueError, match="^image_backfill_requires_create_images_indices$"):
        if operation == "image":
            backfill._process_index(directory)
        else:
            reembed.reembed(directory, "en", operation == "text_dry")
    monkeypatch.setattr(backfill, "_C", SimpleNamespace(PATH_USER_DATA=tmp_path))
    assert backfill._list_target_indices() == []
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_generation_path_is_protected_even_without_its_metadata_flags(tmp_path, monkeypatch):
    directory = tmp_path / ".generations" / "generation-one"
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    monkeypatch.setattr(backfill, "get_embedder", lambda *_: pytest.fail("must not reach models"))
    with pytest.raises(ValueError, match="image_backfill_requires_create_images_indices"):
        backfill._process_index(directory)


def test_ordinary_legacy_entries_still_pass_read_only_preflight(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"n_entries": 1}))
    (tmp_path / "entries.jsonl").write_text(json.dumps({"path": "/old/photo.jpg"}) + "\n")
    assert backfill._legacy_paths(tmp_path) == ["/old/photo.jpg"]
