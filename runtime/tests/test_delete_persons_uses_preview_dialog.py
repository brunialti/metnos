"""Test: delete_persons ambiguous emette dialog choice_with_preview (PR5).

Verifica che ogni candidate abbia preview_image_path con bbox
costruito dal primo esempio del registry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "delete_persons"))

import persons_registry  # noqa: E402
import delete_persons as dp  # noqa: E402


def _emb(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(persons_registry.EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "persons.sqlite"
    monkeypatch.setattr(persons_registry, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(persons_registry, "PERSISTENT_EXAMPLES_DIR",
                        tmp_path / "persons_examples")
    yield db


def test_delete_persons_two_ospite_emits_choice_with_preview(isolated_db):
    """2 ospite diverse → kind=choice_with_preview con thumbnail per
    ciascun slug."""
    reg = persons_registry.PersonsRegistry()
    try:
        reg.enroll(
            name="Ospite Alfa",
            image_path="/photos/ospite_alfa_001.jpg",
            face_box=(100, 200, 150, 150),
            embedding=_emb(1), sha256="1" * 64,
        )
        reg.enroll(
            name="Ospite Beta",
            image_path="/photos/ospite_beta_001.jpg",
            face_box=(50, 80, 200, 200),
            embedding=_emb(2), sha256="2" * 64,
        )
    finally:
        reg.close()

    out = dp.invoke({"name": "Ospite"})
    assert out["ok"] is True
    assert out["decision"] == "needs_inputs"
    assert out["ambiguous"] is True

    payload = out["needs_inputs"]
    schema = payload["dialog"][0]["schema"]
    assert schema["kind"] == "choice_with_preview"
    options = schema["options"]
    assert len(options) == 2

    by_value = {o["value"]: o for o in options}
    assert "ospite_alfa" in by_value
    assert "ospite_beta" in by_value

    o1 = by_value["ospite_alfa"]
    # §7.3: preview = <PERSISTENT_EXAMPLES_DIR>/<slug>/<sha256>.jpg#bbox=...
    assert o1["preview_image_path"].endswith("1" * 64 + ".jpg#bbox=100,200,150,150")
    assert "Ospite Alfa" in o1["label"]
    assert "1 esempi" in o1["label"] or "(1 " in o1["label"]

    o2 = by_value["ospite_beta"]
    assert o2["preview_image_path"].endswith("2" * 64 + ".jpg#bbox=50,80,200,200")


def test_delete_persons_ambiguous_callback_value_is_slug(isolated_db):
    """Il `value` di ogni option e' lo slug, cosi' chosen_slug nel
    resume e' direttamente lo slug (no remap label→slug)."""
    reg = persons_registry.PersonsRegistry()
    try:
        reg.enroll(name="Matteo Bianchi", image_path="/p/mb.jpg",
                    face_box=(0, 0, 10, 10),
                    embedding=_emb(3), sha256="3" * 64)
        reg.enroll(name="Matteo Verdi", image_path="/p/mv.jpg",
                    face_box=(5, 5, 20, 20),
                    embedding=_emb(4), sha256="4" * 64)
    finally:
        reg.close()

    out = dp.invoke({"name": "Matteo"})
    payload = out["needs_inputs"]
    options = payload["dialog"][0]["schema"]["options"]
    values = sorted(o["value"] for o in options)
    assert values == ["matteo_bianchi", "matteo_verdi"]
