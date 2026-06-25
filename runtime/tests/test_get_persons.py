#!/usr/bin/env python3
"""Tests for executors/get_persons (PR2 persons registry)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "get_persons"))

import persons_registry  # noqa: E402
import get_persons as gp  # noqa: E402


def _emb(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(persons_registry.EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "persons.sqlite"
    monkeypatch.setattr(persons_registry, "DEFAULT_DB_PATH", db)
    yield db


def _enroll(reg, name, seed=1):
    reg.enroll(
        name=name, image_path=f"/p/{seed}.jpg",
        face_box=(0, 0, 1, 1),
        embedding=_emb(seed), sha256=f"{seed:064d}",
    )


# --- list (no name) -------------------------------------------------------

def test_get_persons_empty_registry(isolated_db):
    out = gp.invoke({})
    assert out["ok"] is True
    assert out["entries"] == []
    assert out["n_entries"] == 0


def test_get_persons_lists_all(isolated_db):
    reg = persons_registry.PersonsRegistry()
    try:
        _enroll(reg, "Charlie", 1)
        _enroll(reg, "Alice", 2)
        _enroll(reg, "Bob", 3)
    finally:
        reg.close()
    out = gp.invoke({})
    assert out["ok"] is True
    assert out["n_entries"] == 3
    names = [e["name"] for e in out["entries"]]
    assert names == ["Alice", "Bob", "Charlie"]


def test_get_persons_name_null_lists_all(isolated_db):
    reg = persons_registry.PersonsRegistry()
    try:
        _enroll(reg, "Anna", 1)
    finally:
        reg.close()
    out = gp.invoke({"name": None})
    assert out["ok"] is True
    assert out["n_entries"] == 1


# --- by-name lookup -------------------------------------------------------

def test_get_persons_exact_match(isolated_db):
    reg = persons_registry.PersonsRegistry()
    try:
        _enroll(reg, "Matteo", 1)
        _enroll(reg, "Anna", 2)
    finally:
        reg.close()
    out = gp.invoke({"name": "Matteo"})
    assert out["ok"] is True
    assert out["n_entries"] == 1
    assert out["entries"][0]["name"] == "Matteo"
    # `get` ritorna anche examples
    assert "examples" in out["entries"][0]


def test_get_persons_token_match(isolated_db):
    reg = persons_registry.PersonsRegistry()
    try:
        _enroll(reg, "Silvia Buffa", 1)
    finally:
        reg.close()
    out = gp.invoke({"name": "Silvia"})
    assert out["ok"] is True
    assert out["n_entries"] == 1
    assert out["entries"][0]["slug"] == "silvia_buffa"


def test_get_persons_unknown_name(isolated_db):
    reg = persons_registry.PersonsRegistry()
    try:
        _enroll(reg, "Anna", 1)
    finally:
        reg.close()
    out = gp.invoke({"name": "Bruno"})
    assert out["ok"] is True
    assert out["entries"] == []
    assert out["status"] == "unknown_name"
    assert "final_message_hint" in out


def test_get_persons_ambiguous(isolated_db):
    reg = persons_registry.PersonsRegistry()
    try:
        _enroll(reg, "Silvia Buffa", 1)
        _enroll(reg, "Silvia Rossi", 2)
    finally:
        reg.close()
    out = gp.invoke({"name": "Silvia"})
    assert out["ok"] is True
    assert out["ambiguous"] is True
    assert out["n_entries"] == 2
    slugs = sorted([e["slug"] for e in out["entries"]])
    assert slugs == ["silvia_buffa", "silvia_rossi"]


def test_get_persons_empty_string_lists_all(isolated_db):
    """name='' equivale a omesso (lista intera). Coerente col manifest:
    ERRORE: passare empty string e' tollerato come lista intera."""
    reg = persons_registry.PersonsRegistry()
    try:
        _enroll(reg, "Anna", 1)
    finally:
        reg.close()
    out = gp.invoke({"name": ""})
    assert out["ok"] is True
    assert out["n_entries"] == 1


def test_get_persons_non_string_name(isolated_db):
    out = gp.invoke({"name": 42})
    assert out["ok"] is False
    assert "string" in out["error"]


def test_get_persons_examples_are_full_entries(isolated_db):
    reg = persons_registry.PersonsRegistry()
    try:
        _enroll(reg, "Matteo", 1)
        _enroll(reg, "Matteo", 2)
    finally:
        reg.close()
    out = gp.invoke({"name": "Matteo"})
    assert len(out["entries"][0]["examples"]) == 2
