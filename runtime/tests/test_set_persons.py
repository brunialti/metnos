#!/usr/bin/env python3
"""Tests for executors/set_persons (PR2 persons registry).

Mock face_embedding.get_face_engine to avoid loading ArcFace ONNX models
(deterministic §7.9, no GPU/disk dep in tests).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "set_persons"))

import persons_registry  # noqa: E402
import set_persons as sp  # noqa: E402
from messages import get as _msg  # noqa: E402  # §11 i18n: assert language-independent


# --- helpers --------------------------------------------------------------

def _emb(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(persons_registry.EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


class _FakeEngine:
    """Mock di FaceEngine: ritorna un set predeterminato di volti per path.

    Configurazione via class attribute `behaviour: dict[path -> list[face]]`.
    Default: 1 volto per ogni path.
    """
    available = True
    behaviour: dict = {}

    def detect_faces(self, path):
        ps = str(path)
        if ps in self.behaviour:
            return list(self.behaviour[ps])
        # Default: 1 volto sintetico
        return [{
            "bbox": (10, 10, 100, 100),
            "score": 0.99,
            "landmarks": [],
            "embedding": _emb(hash(ps) % 1000),
        }]


@pytest.fixture
def fake_engine():
    eng = _FakeEngine()
    eng.behaviour = {}
    yield eng


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Patch DEFAULT_DB_PATH + PERSISTENT_EXAMPLES_DIR: DB e storage immagini
    isolati in tmp_path. Entrambi sono costanti import-time da config.PATH_USER_DATA
    (non override-abili via env), quindi monkeypatch del modulo. Senza la seconda,
    `_persist_example_image` (§7.3) scriveva nello storage reale ~/.local/share."""
    db = tmp_path / "persons.sqlite"
    monkeypatch.setattr(persons_registry, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(persons_registry, "PERSISTENT_EXAMPLES_DIR",
                        tmp_path / "persons_examples")
    yield db


def _photo(tmp_path, name="a.jpg") -> Path:
    p = tmp_path / name
    p.write_bytes(b"fake jpg bytes " + name.encode())
    return p


# --- validation -----------------------------------------------------------

def test_set_persons_empty_name(isolated_db):
    out = sp.invoke({"name": "", "paths": ["/tmp/x.jpg"]})
    assert out["ok"] is False
    assert out["error"] == _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="name")


def test_set_persons_empty_paths(isolated_db):
    out = sp.invoke({"name": "Matteo", "paths": []})
    assert out["ok"] is False
    assert out["error"] == _msg("ERR_ARG_NOT_LIST", arg="paths")


def test_set_persons_bad_mode(isolated_db):
    out = sp.invoke({"name": "Matteo", "paths": ["/tmp/x.jpg"], "mode": "wipe"})
    assert out["ok"] is False
    assert "mode" in out["error"]


def test_set_persons_unslugifiable_name(isolated_db):
    out = sp.invoke({"name": "@@@", "paths": ["/tmp/x.jpg"]})
    assert out["ok"] is False
    assert "slug" in out["error"].lower()


# --- happy path -----------------------------------------------------------

def test_set_persons_single_face_enrolls(tmp_path, isolated_db, fake_engine):
    p = _photo(tmp_path)
    fake_engine.behaviour = {str(p): [{
        "bbox": (10, 20, 50, 60),
        "score": 0.95,
        "landmarks": [],
        "embedding": _emb(7),
    }]}
    with patch.object(sp, "_detect_faces_for_path",
                      lambda pp: (fake_engine.detect_faces(pp), None)):
        out = sp.invoke({"name": "Matteo", "paths": [str(p)]})
    assert out["ok"] is True
    assert out["n_examples_after"] == 1
    assert len(out["results"]) == 1
    assert out["results"][0]["slug"] == "matteo"
    assert out["results"][0]["face_box"] == [10, 20, 50, 60]
    assert out["results"][0]["added"] is True
    assert out.get("errors") == []


def test_set_persons_no_face_records_error(tmp_path, isolated_db, fake_engine):
    p = _photo(tmp_path, "blank.jpg")
    fake_engine.behaviour = {str(p): []}
    with patch.object(sp, "_detect_faces_for_path",
                      lambda pp: (fake_engine.detect_faces(pp), None)):
        out = sp.invoke({"name": "Matteo", "paths": [str(p)]})
    assert out["ok"] is True
    assert out["n_examples_after"] == 0
    assert len(out["errors"]) == 1
    assert out["errors"][0]["error"] == "no_face_detected"


def test_set_persons_multi_face_returns_needs_inputs(
    tmp_path, isolated_db, fake_engine,
):
    p = _photo(tmp_path)
    fake_engine.behaviour = {str(p): [
        {"bbox": (10, 10, 50, 50), "score": 0.9, "landmarks": [],
         "embedding": _emb(1)},
        {"bbox": (200, 100, 50, 50), "score": 0.85, "landmarks": [],
         "embedding": _emb(2)},
    ]}
    with patch.object(sp, "_detect_faces_for_path",
                      lambda pp: (fake_engine.detect_faces(pp), None)):
        out = sp.invoke({"name": "Matteo", "paths": [str(p)]})
    assert out["ok"] is True
    assert out["decision"] == "needs_inputs"
    payload = out["needs_inputs"]
    assert "dialog" in payload
    assert len(payload["dialog"]) == 1
    # PR5: kind passato a choice_with_preview, options come dict ricco
    # con preview_image_path = path#bbox=x,y,w,h per crop al volo.
    assert payload["dialog"][0]["schema"]["kind"] == "choice_with_preview"
    options = payload["dialog"][0]["schema"]["options"]
    assert len(options) == 2
    for o in options:
        assert "preview_image_path" in o
        assert "#bbox=" in o["preview_image_path"]
        assert "value" in o and "label" in o
        # value e' l'idx del volto detected (0-based).
        assert isinstance(o["value"], int)
    # on_complete callback dichiarato
    oc = payload["on_complete"]
    assert oc["type"] == "resume_executor_with_values"
    assert oc["executor"] == "set_persons"
    assert oc["merge_into"] == "face_choices"


def test_set_persons_multi_face_with_choice_provided(
    tmp_path, isolated_db, fake_engine,
):
    """face_choices passato esplicitamente bypassa il dialog."""
    p = _photo(tmp_path)
    fake_engine.behaviour = {str(p): [
        {"bbox": (10, 10, 50, 50), "score": 0.9, "landmarks": [],
         "embedding": _emb(1)},
        {"bbox": (200, 100, 50, 50), "score": 0.85, "landmarks": [],
         "embedding": _emb(2)},
    ]}
    with patch.object(sp, "_detect_faces_for_path",
                      lambda pp: (fake_engine.detect_faces(pp), None)):
        out = sp.invoke({
            "name": "Matteo",
            "paths": [str(p)],
            "face_choices": {str(p): 1},  # scegli secondo volto
        })
    assert out["ok"] is True
    assert "decision" not in out
    assert out["n_examples_after"] == 1
    assert out["results"][0]["face_box"] == [200, 100, 50, 50]


def test_set_persons_mode_add_accumulates(tmp_path, isolated_db, fake_engine):
    p1 = _photo(tmp_path, "a.jpg")
    p2 = _photo(tmp_path, "b.jpg")
    fake_engine.behaviour = {
        str(p1): [{"bbox": (0, 0, 10, 10), "score": 0.9, "landmarks": [],
                    "embedding": _emb(1)}],
        str(p2): [{"bbox": (0, 0, 10, 10), "score": 0.9, "landmarks": [],
                    "embedding": _emb(2)}],
    }
    with patch.object(sp, "_detect_faces_for_path",
                      lambda pp: (fake_engine.detect_faces(pp), None)):
        out1 = sp.invoke({"name": "Matteo", "paths": [str(p1)]})
        assert out1["n_examples_after"] == 1
        out2 = sp.invoke({"name": "Matteo", "paths": [str(p2)]})
    assert out2["n_examples_after"] == 2


def test_set_persons_mode_replace_wipes(tmp_path, isolated_db, fake_engine):
    p1 = _photo(tmp_path, "a.jpg")
    p2 = _photo(tmp_path, "b.jpg")
    fake_engine.behaviour = {
        str(p1): [{"bbox": (0, 0, 10, 10), "score": 0.9, "landmarks": [],
                    "embedding": _emb(1)}],
        str(p2): [{"bbox": (5, 5, 10, 10), "score": 0.9, "landmarks": [],
                    "embedding": _emb(2)}],
    }
    with patch.object(sp, "_detect_faces_for_path",
                      lambda pp: (fake_engine.detect_faces(pp), None)):
        sp.invoke({"name": "Matteo", "paths": [str(p1)]})
        out = sp.invoke({"name": "Matteo", "paths": [str(p2)], "mode": "replace"})
    assert out["n_examples_after"] == 1
    # solo p2 esiste ora
    reg = persons_registry.PersonsRegistry()
    try:
        entry = reg.get("matteo")
        assert entry is not None
        assert entry["n_examples"] == 1
        # replace ha tenuto p2 (non p1): l'esempio persistito (§7.3 copia in
        # PERSISTENT_EXAMPLES_DIR) ha il contenuto di p2. Confronto per bytes:
        # robusto allo schema di naming <sha256>.<ext>.
        assert Path(entry["examples"][0]["image_path"]).read_bytes() == p2.read_bytes()
    finally:
        reg.close()


def test_set_persons_path_not_found(isolated_db, fake_engine):
    out = sp.invoke({"name": "Matteo", "paths": ["/tmp/nonexistent_xyz_42.jpg"]})
    assert out["ok"] is True
    assert out["n_examples_after"] == 0
    assert len(out["errors"]) == 1
    assert out["errors"][0]["error"] == _msg(
        "ERR_PATH_NOT_FOUND", path="/tmp/nonexistent_xyz_42.jpg")


def test_set_persons_warn_examples_limit(tmp_path, isolated_db, fake_engine):
    """≥50 esempi → warn nel result."""
    p = _photo(tmp_path)
    # Pre-popola 49 esempi via registry diretto
    reg = persons_registry.PersonsRegistry()
    try:
        for i in range(49):
            reg.enroll(
                name="Matteo", image_path=f"/p/{i}.jpg",
                face_box=(0, 0, 1, 1),
                embedding=_emb(100 + i), sha256=f"{i:064d}",
            )
    finally:
        reg.close()
    fake_engine.behaviour = {str(p): [{
        "bbox": (0, 0, 10, 10), "score": 0.9, "landmarks": [],
        "embedding": _emb(999),
    }]}
    with patch.object(sp, "_detect_faces_for_path",
                      lambda pp: (fake_engine.detect_faces(pp), None)):
        out = sp.invoke({"name": "Matteo", "paths": [str(p)]})
    assert out["n_examples_after"] == 50
    assert out.get("warn") == "examples_>=50"
    assert "final_message_hint" in out


def test_set_persons_face_engine_unavailable(tmp_path, isolated_db):
    """Engine non disponibile → error per ogni path, niente crash."""
    p = _photo(tmp_path)
    with patch.object(sp, "_detect_faces_for_path",
                      lambda pp: (None, "FaceEngine model pack non installato")):
        out = sp.invoke({"name": "Matteo", "paths": [str(p)]})
    assert out["ok"] is True
    assert out["n_examples_after"] == 0
    assert len(out["errors"]) == 1
    assert "model pack" in out["errors"][0]["error"]
