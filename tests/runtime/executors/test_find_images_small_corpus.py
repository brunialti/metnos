"""Small-corpus relevance regressions; optional real local BGE CPU checks.

Set METNOS_TEST_BGE_MODEL_DIR to an already installed model to run real
embeddings. Tests never download a model or call an LLM/VLM service.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "executors/find_images_indices"))
import find_images_indices as search

_COMPUTER = ("Roberto with a computer laptop on a desk", ["computer", "laptop", "desk"])
_MOUNTAIN = ("Roberto on a mountain trail", ["mountain", "trail"])
_SYNONYM = ("Roberto working on a laptop at a table", ["laptop", "table"])
_SECOND_COMPUTER = ("Roberto typing on a notebook computer", ["computer", "notebook"])
_ITALIAN = ("Roberto ripara un portatile in ufficio", ["portatile", "ufficio"])
_REAL_COMPUTER = (
    "L'immagine mostra un laptop con schermo che visualizza testo su uno sfondo blu, "
    "posizionato su una superficie marrone. Il design è semplice e stilizzato, "
    "con un'illustrazione di un computer in uso.",
    ["laptop", "schermo", "testo", "computer", "stilizzazione", "ambiente", "computer in uso"],
)
_REAL_MOUNTAIN = (
    "Illustrazione di una scenografia montuosa con due montagne grigie e bianche, "
    "un cielo azzurro, un sole giallo e un sentiero che conduce verso le montagne. "
    "Sul terreno verde sono presenti quattro alberi di conifera. "
    "L'immagine è in stile grafico piatto.",
    ["montagna", "sole", "cielo", "sentiero", "alberi", "verde", "grigio", "bianco", "giorno"],
)
_LARGER_CORPUS = [
    _COMPUTER, _MOUNTAIN, _SECOND_COMPUTER, _SYNONYM, _ITALIAN,
    ("Roberto utilizza un computer alla scrivania", ["computer", "scrivania"]),
    ("Roberto working on a MacBook", ["macbook"]),
    ("Roberto sitting at his desktop PC", ["desktop", "pc"]),
    ("Roberto programming on a laptop", ["programming", "laptop"]),
    ("Roberto sitting in an office meeting", ["office", "meeting"]),
    ("Roberto reading a newspaper at home", ["newspaper", "home"]),
    ("Roberto holding a telephone", ["telephone"]),
    ("Roberto standing by a television", ["television"]),
    ("Roberto writing on paper at a desk", ["paper", "desk"]),
    ("Roberto at the beach by the sea", ["beach", "sea"]),
    ("Roberto skiing on a snowy slope", ["skiing", "snow"]),
    ("Roberto walking through the forest", ["forest"]),
    ("Roberto cycling along a country road", ["cycling", "road"]),
    ("Roberto walking a dog in a park", ["dog", "park"]),
    ("Roberto playing with a cat at home", ["cat", "home"]),
    ("Roberto eating dinner with friends", ["dinner", "friends"]),
    ("Roberto cutting a birthday cake", ["birthday", "cake"]),
    ("Roberto dancing at a wedding", ["dancing", "wedding"]),
    ("Roberto holding a baby", ["baby"]),
    ("Roberto standing outside a church", ["church"]),
    ("Roberto visiting an art museum", ["museum"]),
    ("Roberto fishing from a boat", ["fishing", "boat"]),
    ("Roberto swimming in a pool", ["swimming", "pool"]),
    ("Roberto drinking coffee in a cafe", ["coffee", "cafe"]),
    ("Roberto driving a car", ["driving", "car"]),
    ("Roberto cooking in a kitchen", ["cooking", "kitchen"]),
    ("Roberto watching a football match", ["football"]),
]


def _filter(tmp_path, monkeypatch, documents, vectors, *, query="computer",
            path_tokens=None, **args):
    entries = [{
        "path": str(tmp_path / f"photo-{index}.jpg"), "name": f"photo-{index}.jpg",
        "description": description, "keywords": keywords,
        "path_tokens": list(path_tokens or []),
        "embedding_text_idx": index, "faces": [{"embedding_face_idx": index}],
    } for index, (description, keywords) in enumerate(documents)]
    (tmp_path / "entries.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    face = np.array([1.0, 0.0], dtype="float32")
    monkeypatch.setattr("persons_registry.resolve_face_embeddings_for_name", lambda _name: [face])
    result = search._filter_unified(
        copy.deepcopy(entries), vectors, np.stack([face] * len(entries)), {},
        {"query_text": query, "name": "Roberto", **args}, idx_dir=tmp_path,
    )
    return [int(Path(entry["path"]).stem.split("-")[-1]) for entry in result["entries"]]


@pytest.fixture
def synthetic_model(monkeypatch):
    model = SimpleNamespace(
        embed_texts=lambda texts: np.asarray([[1, 0] for _text in texts], dtype="float32"),
        embed_query=lambda _text: np.asarray([1, 0], dtype="float32"),
    )
    monkeypatch.setattr("virt.get_local_embedder", lambda _role: model)
    monkeypatch.setattr(search, "_expand_query_via_corpus", lambda *_args: ["computer", "laptop"])
    return model


def _cosines(values):
    return np.asarray([[value, (1 - value**2)**0.5] for value in values], dtype="float32")


def test_two_photo_identity_set_does_not_admit_unrelated_scene(tmp_path, monkeypatch, synthetic_model):
    assert _filter(tmp_path, monkeypatch, [_COMPUTER, _MOUNTAIN], _cosines([.755185, .599582])) == [0]


def test_small_corpus_is_not_forced_to_top_one(tmp_path, monkeypatch, synthetic_model):
    assert set(_filter(tmp_path, monkeypatch, [_COMPUTER, _SYNONYM], _cosines([.755185, .733298]))) == {0, 1}


def test_explicit_result_count_retains_its_existing_floor_policy(tmp_path, monkeypatch, synthetic_model):
    assert set(_filter(tmp_path, monkeypatch, [_COMPUTER, _MOUNTAIN],
                       _cosines([.755185, .599582]), max_results=2)) == {0, 1}


def test_mostly_relevant_set_does_not_reopen_all_identity_matches(tmp_path, monkeypatch, synthetic_model):
    documents = [_COMPUTER] * 5 + [_MOUNTAIN] * 3
    assert set(_filter(tmp_path, monkeypatch, documents, _cosines([.75] * 5 + [.60] * 3))) == set(range(5))


def test_constant_dense_background_does_not_leak_on_threshold_equality(tmp_path, monkeypatch, synthetic_model):
    documents = [_COMPUTER] * 2 + [_MOUNTAIN] * 30
    assert set(_filter(tmp_path, monkeypatch, documents, _cosines([.75] * 2 + [.60] * 30))) == {0, 1}


@pytest.mark.parametrize("caption_contains_path_term", [False, True])
def test_expansion_ignores_shared_path_without_losing_descriptive_evidence(
        tmp_path, monkeypatch, synthetic_model, caption_contains_path_term):
    seen = []

    def expand(query, tokens, _embeddings):
        seen.append(tokens)
        return [query] + [term for term in tokens if term in {"laptop", "data"}]

    monkeypatch.setattr(search, "_expand_query_via_corpus", expand)
    computer = (_COMPUTER[0] + (" displaying data" if caption_contains_path_term else ""),
                _COMPUTER[1])
    documents = [computer, _MOUNTAIN]
    for root_terms in (["data", "archive"], ["collection", "pictures"]):
        assert _filter(tmp_path, monkeypatch, documents, _cosines([.75, .60]),
                       path_tokens=root_terms) == [0]
    assert ("data" in seen[0]) is caption_contains_path_term


def test_literal_common_path_query_is_preserved(tmp_path, monkeypatch, synthetic_model):
    monkeypatch.setattr(search, "_expand_query_via_corpus", lambda query, *_args: [query])
    assert set(_filter(tmp_path, monkeypatch, [_COMPUTER, _MOUNTAIN],
                       _cosines([.75, .60]), query="archive", path_tokens=["archive"])) == {0, 1}


@pytest.fixture(scope="module")
def real_bge():
    path = os.environ.get("METNOS_TEST_BGE_MODEL_DIR")
    if not path:
        pytest.skip("explicit local BGE model path required; no downloads")
    from bge_embedding import BGEEmbeddingService
    return BGEEmbeddingService(path)


@pytest.mark.parametrize("documents,query,expected", [
    pytest.param([_COMPUTER, _MOUNTAIN], "computer", {0}, id="original-two-photo-oracle"),
    pytest.param([_COMPUTER, _SECOND_COMPUTER], "computer", {0, 1}, id="two-real-matches"),
    pytest.param([_COMPUTER, _SYNONYM], "computer", {0, 1}, id="synonym-laptop"),
    pytest.param([_COMPUTER, _ITALIAN], "computer portatile", {0, 1}, id="multilingual-synonym"),
    pytest.param([_COMPUTER, _MOUNTAIN], "photo", {0, 1}, id="broad-query"),
    pytest.param(_LARGER_CORPUS, "computer", {0, 2, 3, 4, 5, 6, 7, 8}, id="independent-larger-corpus"),
])
def test_real_bge_relevance(tmp_path, monkeypatch, real_bge, documents, query, expected):
    monkeypatch.setattr("virt.get_local_embedder", lambda _role: real_bge)
    vectors = real_bge.embed_texts([description for description, _keywords in documents])
    assert set(_filter(tmp_path, monkeypatch, documents, vectors, query=query)) == expected


@pytest.mark.parametrize("path_terms", [
    ["data", "fixture", "images"], ["collection", "pictures"], [],
])
def test_real_cold_start_captions_are_invariant_to_storage_root(
        tmp_path, monkeypatch, real_bge, path_terms):
    monkeypatch.setattr("virt.get_local_embedder", lambda _role: real_bge)
    documents = [_REAL_COMPUTER, _REAL_MOUNTAIN]
    vectors = real_bge.embed_texts([description for description, _keywords in documents])
    assert _filter(tmp_path, monkeypatch, documents, vectors, name=None,
                   path_tokens=path_terms) == [0]
