"""A publication retry must not certify damaged already-written files."""

from pathlib import Path
import json
import os

import pytest
import image_index_build as storage

from test_image_index_build_phases import corpus, _analyze, _discover, _invoke, _photo, _publish


@pytest.mark.parametrize("filename", ["entries.jsonl", "embeddings_image.npy", "lookup.sqlite"])
@pytest.mark.parametrize("same_size", [False, True])
def test_publication_retry_rejects_corrupted_completed_generation(corpus, filename, same_size):
    root, _calls = corpus
    _photo(root)
    discovery = _discover(root)
    receipts = _analyze(root, discovery)
    published = _publish(root, discovery, receipts)
    target = Path(published["index_path"]) / filename
    previous = target.read_bytes()
    target.write_bytes(previous[:-1] + bytes([previous[-1] ^ 1]) if same_size else b"corrupted")
    repeated = _invoke(root, "build-1", "publish", entries=receipts, expected_count=1)
    assert repeated["ok"] is False
    assert repeated["error_code"] == "generation_incomplete"


def test_incomplete_post_rename_retry_never_overwrites_the_previous_active_index(corpus, monkeypatch):
    root, _calls = corpus
    _photo(root)
    first = _discover(root)
    _publish(root, first, _analyze(root, first))
    build = storage.ImageIndexBuild(root, "interrupted-publication")
    active = build.root / "meta.json"
    previous = active.read_bytes()
    discovered = _discover(root, build.generation)
    receipts = _analyze(root, discovered, build.generation)
    writer = storage._write_bytes

    def interrupt_activation(path, *args, **kwargs):
        if path == active:
            raise OSError("synthetic activation interruption")
        return writer(path, *args, **kwargs)

    monkeypatch.setattr(storage, "_write_bytes", interrupt_activation)
    assert not _invoke(root, build.generation, "publish", entries=receipts, expected_count=1)["ok"]
    monkeypatch.setattr(storage, "_write_bytes", writer)
    (build.root / ".generations" / build.generation / "entries.jsonl").write_bytes(b"damaged")
    retried = _invoke(root, build.generation, "publish", entries=receipts, expected_count=1)
    assert not retried["ok"] and retried["error_code"] == "generation_incomplete"
    assert active.read_bytes() == previous


@pytest.mark.parametrize("manifest", [None, {}, {"unexpected": {}}, {"entries.jsonl": {}}])
def test_missing_or_non_closed_generation_manifest_is_not_a_successful_retry(corpus, manifest):
    root, _calls = corpus
    _photo(root)
    discovery = _discover(root)
    receipts = _analyze(root, discovery)
    published = _publish(root, discovery, receipts)
    target_meta = Path(published["index_path"]) / "meta.json"
    metadata = json.loads(target_meta.read_bytes())
    metadata["generation_files"] = manifest
    target_meta.write_text(json.dumps(metadata))
    repeated = _invoke(root, "build-1", "publish", entries=receipts, expected_count=1)
    assert not repeated["ok"] and repeated["error_code"] == "generation_incomplete"


def test_fingerprint_refuses_fifo_without_waiting(tmp_path, monkeypatch):
    path = tmp_path / "pipe"
    os.mkfifo(path)
    real_open = os.open

    def checked_open(target, flags, *args, **kwargs):
        if Path(target) == path:
            assert flags & os.O_NONBLOCK
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", checked_open)
    with pytest.raises(storage.ImageIndexBuildError, match="generation_incomplete"):
        storage._generation_file_fact(path)


def test_fingerprint_refuses_a_path_replaced_during_read(tmp_path, monkeypatch):
    path = tmp_path / "entry"
    path.write_bytes(b"original")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"original")
    read = os.read

    def replaced_read(fd, size):
        data = read(fd, size)
        if replacement.exists():
            os.replace(replacement, path)
        return data

    monkeypatch.setattr(os, "read", replaced_read)
    with pytest.raises(storage.ImageIndexBuildError, match="generation_incomplete"):
        storage._generation_file_fact(path)
