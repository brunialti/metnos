"""Similarity di SCENA (SigLIP image-to-image, 6/7): reference SENZA volto →
cosine sul corpus `embeddings_image` invece di errore «solo volti»."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = (Path(__file__).resolve().parents[3] / "runtime").parent
sys.path.insert(0, str(_REPO / "executors" / "find_images_indices"))

import find_images_indices as fii  # noqa: E402


class _FakeClip:
    def embed_images(self, paths, normalize=True):
        # reference = vettore [1,0,0,...] normalizzato
        v = np.zeros((1, 4), dtype=np.float32)
        v[0, 0] = 1.0
        return v


def _entries():
    return [
        {"path": "/a.jpg", "embedding_image_idx": 0},   # identica → 1.0
        {"path": "/b.jpg", "embedding_image_idx": 1},   # simile → ~0.7
        {"path": "/c.jpg", "embedding_image_idx": 2},   # lontana → 0.1
    ]


def _emb_image():
    m = np.zeros((3, 4), dtype=np.float32)
    m[0] = [1, 0, 0, 0]
    m[1] = [0.7, 0.7, 0, 0]
    m[2] = [0.1, 0.99, 0, 0]
    return m


def test_scene_similarity_keeps_above_floor(monkeypatch):
    monkeypatch.setattr(fii, "get_clip_engine", lambda: _FakeClip(),
                        raising=False)
    monkeypatch.setattr("clip_embedding.get_clip_engine",
                        lambda: _FakeClip(), raising=False)
    monkeypatch.setattr("os.path.exists", lambda p: True)
    meta = {"_emb_image": _emb_image()}
    res = fii._apply_scene_similarity(_entries(), ["/ref.jpg"], meta,
                                      {"max_results": 10})
    assert res is not None
    kept = res["entries"]
    paths = [e["path"] for e in kept]
    assert "/a.jpg" in paths and "/b.jpg" in paths   # >= floor 0.35
    assert "/c.jpg" not in paths                      # 0.1 < floor
    assert kept[0]["path"] == "/a.jpg"                # ordinato per score
    assert res["match_mode"] == "scene_similarity"


def test_scene_none_without_emb_image(monkeypatch):
    res = fii._apply_scene_similarity(_entries(), ["/ref.jpg"],
                                      {"_emb_image": None}, {})
    assert res is None   # → il chiamante degrada onesto (§2.8)
