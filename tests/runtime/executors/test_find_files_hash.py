from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "executors" / "find_files_hash" / "find_files_hash.py"
SPEC = importlib.util.spec_from_file_location("find_files_hash_executor", MODULE_PATH)
assert SPEC and SPEC.loader
find_files_hash = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = find_files_hash
SPEC.loader.exec_module(find_files_hash)


def _disable_persistent_cache(monkeypatch):
    monkeypatch.setattr(find_files_hash, "_open_cache", lambda _scope: None)


def test_complete_scan_finds_duplicate_after_old_thousand_file_cap(
        tmp_path, monkeypatch):
    _disable_persistent_cache(monkeypatch)
    for index in range(1005):
        (tmp_path / f"u{index:04d}.bin").write_bytes(bytes(index + 1))
    (tmp_path / "z-original.bin").write_bytes(b"duplicate-after-cap")
    (tmp_path / "z-copy.bin").write_bytes(b"duplicate-after-cap")

    out = find_files_hash.invoke({
        "base_path": str(tmp_path),
        "patterns": ["*.bin"],
        "max_results": 1,
    })

    assert out["ok"] is True
    assert out["source_complete"] is True
    assert out["scanned_files"] == 1007
    assert out["duplicate_groups_count"] == 1
    assert out["redundant_files_count"] == 1
    assert out["entries"][0]["path"].endswith("z-original.bin") or \
        out["entries"][0]["path"].endswith("z-copy.bin")
    presentation = out["authoritative_presentation"]
    assert presentation["scope"] == "always"
    assert presentation["covers_truncation"] is True
    assert "tutti i 1\u202f007 file" in presentation["text"]
    assert "duplicati esatti" in presentation["text"]
    assert "stima di somiglianza" in presentation["text"]


def test_same_size_different_content_is_not_a_duplicate(tmp_path, monkeypatch):
    _disable_persistent_cache(monkeypatch)
    (tmp_path / "a.dat").write_bytes(b"abc")
    (tmp_path / "b.dat").write_bytes(b"xyz")

    out = find_files_hash.invoke({"base_path": str(tmp_path)})

    assert out["ok"] is True
    assert out["same_size_candidates"] == 2
    assert out["sampled_files"] == 2
    assert out["full_hash_candidates"] == 0
    assert out["hashed_files"] == 0
    assert out["duplicate_groups_count"] == 0
    assert out["entries"] == []
    assert "non ho trovato duplicati esatti" in \
        out["authoritative_presentation"]["text"]


def test_equal_samples_still_require_complete_hash(tmp_path, monkeypatch):
    _disable_persistent_cache(monkeypatch)
    chunk = find_files_hash._SAMPLE_CHUNK_BYTES
    left = bytearray(b"x" * (chunk * 4))
    right = bytearray(left)
    right[chunk * 2] = ord("y")
    (tmp_path / "a.dat").write_bytes(left)
    (tmp_path / "b.dat").write_bytes(right)

    monkeypatch.setattr(find_files_hash, "_SAMPLE_CHUNK_BYTES", 8)
    monkeypatch.setattr(
        find_files_hash,
        "_sample_digest_stable",
        lambda _path, _size: ("same-filter-fingerprint", None),
    )
    out = find_files_hash.invoke({"base_path": str(tmp_path)})

    assert out["full_hash_candidates"] == 2
    assert out["hashed_files"] == 2
    assert out["duplicate_groups_count"] == 0
    assert out["entries"] == []


def test_result_cap_is_applied_after_complete_scan(tmp_path, monkeypatch):
    _disable_persistent_cache(monkeypatch)
    for stem, payload in (("a", b"first"), ("b", b"second")):
        (tmp_path / f"{stem}1.jpg").write_bytes(payload)
        (tmp_path / f"{stem}2.jpg").write_bytes(payload)

    out = find_files_hash.invoke({
        "base_path": str(tmp_path),
        "patterns": ["*.jpg"],
        "max_results": 1,
    })

    assert out["scanned_files"] == 4
    assert out["duplicate_groups_count"] == 2
    assert out["redundant_files_count"] == 2
    assert len(out["entries"]) == 1
    assert out["truncated"] is True
    assert out["cap_field"] == "max_results"
    assert out["available_total"] == 2
    text = out["authoritative_presentation"]["text"]
    assert "L’elenco mostra 1 delle 2 copie ridondanti" in text
    assert "scansione completa" in text


def test_explicit_source_cap_is_honest(tmp_path, monkeypatch):
    _disable_persistent_cache(monkeypatch)
    for index in range(4):
        (tmp_path / f"{index}.txt").write_text("same", encoding="utf-8")

    out = find_files_hash.invoke({
        "base_path": str(tmp_path),
        "max_files": 2,
        "max_results": 0,
    })

    assert out["source_complete"] is False
    assert out["scan_truncated"] is True
    assert out["truncated"] is True
    assert out["cap_field"] == "max_files"
    assert out["used"] == 2
    assert "authoritative_presentation" not in out


def test_hash_failure_is_not_silently_discarded(tmp_path, monkeypatch):
    _disable_persistent_cache(monkeypatch)
    (tmp_path / "a.bin").write_bytes(b"same")
    (tmp_path / "b.bin").write_bytes(b"same")
    original = find_files_hash._sha256_stable

    def fail_one(path, expected_size):
        if path.name == "b.bin":
            return None, "injected_read_failure"
        return original(path, expected_size)

    monkeypatch.setattr(find_files_hash, "_sha256_stable", fail_one)
    out = find_files_hash.invoke({"base_path": str(tmp_path)})

    assert out["ok"] is False
    assert out["fail_count"] == 1
    assert out["failed"][0]["path"].endswith("b.bin")
    assert out["duplicate_groups_count"] == 0


def test_cache_reuses_hash_only_for_unchanged_file(tmp_path, monkeypatch):
    cache_path = tmp_path / "cache" / "file_hashes.sqlite"
    monkeypatch.setattr(
        find_files_hash, "_cache_path", lambda _scope: cache_path)
    (tmp_path / "a.bin").write_bytes(b"same")
    (tmp_path / "b.bin").write_bytes(b"same")

    query = {"base_path": str(tmp_path), "patterns": ["*.bin"]}
    first = find_files_hash.invoke(query)
    second = find_files_hash.invoke(query)

    assert first["hash_cache_hits"] == 0
    assert first["hashed_bytes"] == 8
    assert second["sample_cache_hits"] == 2
    assert second["hash_cache_hits"] == 2
    assert second["hashed_bytes"] == 0
    assert second["hash_cache_bytes"] == 8
    assert second["duplicate_groups_count"] == 1

    # Una modifica che conserva la dimensione invalida la firma del path.
    (tmp_path / "b.bin").write_bytes(b"else")
    changed = find_files_hash.invoke(query)
    assert changed["sample_cache_hits"] < 2
    assert changed["duplicate_groups_count"] == 0
    with sqlite3.connect(cache_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM file_digests").fetchone()[0] >= 2


def test_stream_parallelism_uses_full_central_budget(monkeypatch):
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "32")
    monkeypatch.delenv("METNOS_FIND_HASH_STREAM_WORKERS", raising=False)
    # File size is not a host/storage profile. The signed central allowance is
    # authoritative unless deployment explicitly lowers this executor.
    assert find_files_hash._stream_worker_count(
        [(Path("large"), 2 * 1024 * 1024)] * 20) == 20
    assert find_files_hash._stream_worker_count(
        [(Path("small"), 1024)] * 20) == 20


def test_hash_batches_are_bounded_and_cover_every_item():
    batches = list(find_files_hash._batches(list(range(130)), workers=1))
    assert [len(batch) for batch in batches] == [64, 64, 2]
    assert [value for batch in batches for value in batch] == list(range(130))


def test_cache_is_scoped_by_user_without_persisting_identity(
        tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.bin").write_bytes(b"same")
    (corpus / "b.bin").write_bytes(b"same")

    base = {"base_path": str(corpus), "_actor_email": "alice@example.test"}
    first_alice = find_files_hash.invoke(base)
    second_alice = find_files_hash.invoke(base)
    first_bob = find_files_hash.invoke({
        "base_path": str(corpus), "_actor_email": "bob@example.test",
    })

    assert first_alice["hash_cache_hits"] == 0
    assert second_alice["hash_cache_hits"] == 2
    assert first_bob["hash_cache_hits"] == 0
    cache_names = sorted(
        path.name for path in (
            tmp_path / "cache" / "metnos" / "file_hashes"
        ).glob("*.sqlite"))
    assert len(cache_names) == 2
    assert all("alice" not in name and "bob" not in name for name in cache_names)


def _catalog_entry(name, affinity):
    return SimpleNamespace(
        name=name,
        affinity=affinity,
        description=" ".join(affinity),
        dormant=False,
    )


def test_semantic_routing_exposes_hash_search_for_image_duplicates():
    from prefilter import rank_with_intent

    catalog = [
        _catalog_entry("find_files", ["trova", "file", "pattern"]),
        _catalog_entry("get_files", ["metadati", "exif"]),
        _catalog_entry("find_images_indices", ["immagini", "indice"]),
        _catalog_entry("find_files_hash", [
            "file duplicati", "immagini duplicate", "duplicati", "duplicate",
        ]),
    ]
    intent = {"verb": "find", "object": "images"}

    for query in (
        "Trova i file di immagini duplicati nella cartella Foto",
        "Individua le copie identiche delle fotografie in Foto",
        "Find duplicate image files under Photos",
    ):
        names = [entry.name for entry in
                 rank_with_intent(query, catalog, intent, k=8)]
        assert "find_files_hash" in names
