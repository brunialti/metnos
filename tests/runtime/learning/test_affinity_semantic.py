"""Test affinity_semantic (semantic fallback BGE-M3 per prefilter)."""
from __future__ import annotations

import sys
import os
from pathlib import Path

import numpy as np
import pytest


import affinity_semantic as asm  # noqa: E402


class _MockExec:
    def __init__(self, name, affinity):
        self.name = name
        self.affinity = list(affinity)


class _MockEmbedder:
    """Embedder finto: dimensione 8, deterministico (hash → vec L2-norm)."""

    def __init__(self):
        self.dim = 8
        self.calls_query = 0
        self.calls_texts = 0

    def _vec(self, s):
        import hashlib
        h = hashlib.sha256(s.lower().encode("utf-8")).digest()
        v = np.frombuffer(h[: self.dim * 4], dtype=np.int32).astype(np.float32)
        n = np.linalg.norm(v)
        return (v / n) if n > 0 else v

    def embed_query(self, s):
        self.calls_query += 1
        return self._vec(s)

    def embed_texts(self, texts):
        self.calls_texts += 1
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._vec(t) for t in texts])


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    asm.invalidate_cache()
    monkeypatch.setattr(asm, "_EMB", _MockEmbedder())
    monkeypatch.setattr(asm, "CACHE_DIR", tmp_path / "cache")
    yield
    asm.invalidate_cache()


def test_cache_key_deterministic():
    a = [_MockExec("a", ["x", "y"]), _MockExec("b", ["z"])]
    k1 = asm._cache_key(a)
    k2 = asm._cache_key(a)
    assert k1 == k2 and k1.startswith("sem2-") and len(k1) == 21


def test_cache_key_changes_with_affinity():
    a1 = [_MockExec("a", ["x", "y"])]
    a2 = [_MockExec("a", ["x", "y", "z"])]
    assert asm._cache_key(a1) != asm._cache_key(a2)


def test_build_returns_none_for_empty_catalog():
    assert asm.build_or_load_cache([]) is None


def test_build_returns_none_when_all_affinities_empty():
    a = [_MockExec("a", []), _MockExec("b", [])]
    assert asm.build_or_load_cache(a) is None


def test_build_caches_in_memory_and_disk():
    a = [_MockExec("get_processes", ["situazione", "stato server"]),
         _MockExec("get_now", ["ora", "time"])]
    c1 = asm.build_or_load_cache(a)
    assert c1 is not None
    assert c1["matrix"].shape == (4, 8)
    # Second call: ritorna SAME object dalla cache memory
    c2 = asm.build_or_load_cache(a)
    assert c2 is c1


def test_cache_invalidated_when_catalog_changes():
    a1 = [_MockExec("a", ["x"])]
    c1 = asm.build_or_load_cache(a1)
    a2 = [_MockExec("a", ["x", "y"])]
    c2 = asm.build_or_load_cache(a2)
    assert c2 is not c1
    assert c2["matrix"].shape[0] == 2


def test_semantic_max_per_executor_finds_highest_tag_cosine():
    a = [_MockExec("get_processes",
                    ["situazione", "stato server", "salute sistema"]),
         _MockExec("get_now", ["ora", "time", "what time"])]
    cache = asm.build_or_load_cache(a)
    scores = asm.semantic_max_per_executor("situazione", cache)
    # La query "situazione" dovrebbe avere score=1.0 (perfect self-match) su
    # get_processes (esatto match con tag "situazione" via hash determinismo).
    assert "get_processes" in scores
    assert scores["get_processes"] == pytest.approx(1.0, abs=1e-5)
    assert scores["get_now"] < 1.0


def test_semantic_empty_query_returns_empty():
    a = [_MockExec("a", ["x"])]
    cache = asm.build_or_load_cache(a)
    assert asm.semantic_max_per_executor("", cache) == {}
    assert asm.semantic_max_per_executor("   ", cache) == {}


def test_semantic_handles_none_cache():
    assert asm.semantic_max_per_executor("query", None) == {}


def test_is_enabled_default_on():
    os.environ.pop("METNOS_SEMANTIC_MATCH", None)
    assert asm.is_enabled() is True


def test_is_enabled_opt_out():
    os.environ["METNOS_SEMANTIC_MATCH"] = "0"
    try:
        assert asm.is_enabled() is False
    finally:
        os.environ.pop("METNOS_SEMANTIC_MATCH", None)


def test_threshold_default_and_env_override():
    os.environ.pop("METNOS_SEMANTIC_THRESHOLD", None)
    assert asm.threshold() == asm.SEMANTIC_THRESHOLD_DEFAULT
    os.environ["METNOS_SEMANTIC_THRESHOLD"] = "10"
    try:
        assert asm.threshold() == 10
    finally:
        os.environ.pop("METNOS_SEMANTIC_THRESHOLD", None)


def test_alpha_default_and_env_override():
    os.environ.pop("METNOS_SEMANTIC_ALPHA", None)
    assert asm.alpha() == asm.SEMANTIC_ALPHA_DEFAULT
    os.environ["METNOS_SEMANTIC_ALPHA"] = "2.5"
    try:
        assert asm.alpha() == 2.5
    finally:
        os.environ.pop("METNOS_SEMANTIC_ALPHA", None)


def test_threshold_invalid_falls_back_to_default():
    os.environ["METNOS_SEMANTIC_THRESHOLD"] = "not-a-number"
    try:
        assert asm.threshold() == asm.SEMANTIC_THRESHOLD_DEFAULT
    finally:
        os.environ.pop("METNOS_SEMANTIC_THRESHOLD", None)


def test_persists_to_disk_and_reloads():
    a = [_MockExec("a", ["tag1", "tag2"])]
    c1 = asm.build_or_load_cache(a)
    # Reset cache memory; il file su disco deve essere riusato.
    asm.invalidate_cache()
    c2 = asm.build_or_load_cache(a)
    assert c2 is not None
    assert c2["matrix"].shape == c1["matrix"].shape
    # Reload-from-disk non chiama embed_texts: 1 build solo.
    assert asm._EMB.calls_texts == 1
