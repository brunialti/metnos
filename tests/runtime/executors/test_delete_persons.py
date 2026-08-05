#!/usr/bin/env python3
"""Tests for executors/delete_persons (PR2 persons registry)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
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
    monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path))
    monkeypatch.setattr(persons_registry, "DEFAULT_DB_PATH", db)
    yield db


def _enroll(name, seed=1, n=1):
    reg = persons_registry.PersonsRegistry()
    try:
        for i in range(n):
            reg.enroll(
                name=name, image_path=f"/p/{seed+i}.jpg",
                face_box=(i, i, 10, 10),
                embedding=_emb(seed + i),
                sha256=f"{seed+i:064d}",
            )
    finally:
        reg.close()


# --- validation -----------------------------------------------------------

def test_delete_persons_empty_name(isolated_db):
    out = dp.invoke({"name": ""})
    assert out["ok"] is False
    assert "non-empty" in out["error"]


def test_delete_persons_missing_name(isolated_db):
    out = dp.invoke({})
    assert out["ok"] is False


def test_delete_persons_non_string_name(isolated_db):
    out = dp.invoke({"name": 42})
    assert out["ok"] is False


# --- vectorial inputs (§2.1) ----------------------------------------------

def test_delete_persons_names_plural(isolated_db):
    _enroll("Matteo", seed=1, n=2)
    _enroll("Anna", seed=10, n=1)
    _enroll("Bob", seed=20, n=3)
    out = dp.invoke({"names": ["Matteo", "Anna", "Bob"]})
    assert out["ok"] is True
    assert out["n_removed"] == 3
    assert out["removed_examples_total"] == 6
    slugs = sorted(r["slug"] for r in out["results"])
    assert slugs == ["anna", "bob", "matteo"]
    reg = persons_registry.PersonsRegistry()
    try:
        assert reg.get("matteo") is None
        assert reg.get("anna") is None
        assert reg.get("bob") is None
    finally:
        reg.close()


def test_delete_persons_entries_from_step(isolated_db):
    _enroll("Matteo", seed=1, n=1)
    _enroll("Anna", seed=10, n=1)
    entries = [
        {"slug": "matteo", "name": "Matteo", "n_examples": 1},
        {"slug": "anna", "name": "Anna", "n_examples": 1},
    ]
    out = dp.invoke({"entries": entries})
    assert out["ok"] is True
    assert out["n_removed"] == 2


def test_delete_persons_all_purges_registry(isolated_db):
    _enroll("Matteo", seed=1, n=2)
    _enroll("Anna", seed=10, n=1)
    _enroll("Ospite Alfa", seed=20, n=3)
    out = dp.invoke({"all": True})
    assert out["ok"] is True
    assert out["n_removed"] == 3
    assert out["removed_examples_total"] == 6
    reg = persons_registry.PersonsRegistry()
    try:
        assert reg.list_all() == []
    finally:
        reg.close()


def test_delete_persons_all_on_empty_registry(isolated_db):
    out = dp.invoke({"all": True})
    assert out["ok"] is True
    assert out["n_removed"] == 0
    assert out["results"] == []


def test_delete_persons_mix_known_and_unknown(isolated_db):
    _enroll("Matteo", seed=1, n=1)
    out = dp.invoke({"names": ["Matteo", "Ghost"]})
    assert out["ok"] is True
    assert out["n_removed"] == 1
    assert out["unknown_names"] == ["Ghost"]


def test_delete_persons_only_unknown_returns_error(isolated_db):
    out = dp.invoke({"names": ["Ghost", "Phantom"]})
    assert out["ok"] is False
    assert out["error"] == "unknown_name"
    assert sorted(out["unknown_names"]) == ["Ghost", "Phantom"]


def test_delete_persons_names_invalid_type(isolated_db):
    out = dp.invoke({"names": "not-a-list"})
    assert out["ok"] is False


def test_delete_persons_names_with_empty_string(isolated_db):
    out = dp.invoke({"names": ["Matteo", ""]})
    assert out["ok"] is False


def test_delete_persons_names_empty_list(isolated_db):
    out = dp.invoke({"names": []})
    assert out["ok"] is False


def test_delete_persons_entries_no_usable_names(isolated_db):
    out = dp.invoke({"entries": [{"unrelated": "x"}]})
    assert out["ok"] is False


def test_delete_persons_chosen_slugs_batch(isolated_db):
    _enroll("Ospite Alfa", seed=1, n=2)
    _enroll("Ospite Beta", seed=10, n=1)
    out = dp.invoke({"chosen_slugs": ["ospite_alfa", "ospite_beta"]})
    assert out["ok"] is True
    assert out["n_removed"] == 2
    assert out["removed_examples_total"] == 3


def test_delete_persons_resume_multistep_vars(isolated_db):
    _enroll("Ospite Alfa", seed=1, n=2)
    _enroll("Anna Bianchi", seed=10, n=1)
    out = dp.invoke({
        "chosen_slug__Ospite": "ospite_alfa",
        "chosen_slug__Anna": "anna_bianchi",
    })
    assert out["ok"] is True
    assert out["n_removed"] == 2
    slugs = sorted(r["slug"] for r in out["results"])
    assert slugs == ["anna_bianchi", "ospite_alfa"]


def test_delete_persons_ambiguous_in_batch_returns_dialog(isolated_db):
    _enroll("Matteo", seed=1, n=1)
    _enroll("Ospite Alfa", seed=10, n=1)
    _enroll("Ospite Beta", seed=20, n=1)
    out = dp.invoke({"names": ["Matteo", "Ospite"]})
    # Matteo non ambiguo → cancellato.
    # Ospite ambiguo → dialog.
    assert out["ok"] is True
    assert out["decision"] == "needs_inputs"
    assert out["n_removed"] == 1  # solo matteo
    assert any(r["slug"] == "matteo" for r in out["results"])
    assert len(out["needs_inputs"]["dialog"]) == 1
    reg = persons_registry.PersonsRegistry()
    try:
        assert reg.get("matteo") is None
        assert reg.get("ospite_alfa") is not None
        assert reg.get("ospite_beta") is not None
    finally:
        reg.close()


def test_delete_persons_dry_run_all(isolated_db):
    _enroll("Matteo", seed=1, n=2)
    _enroll("Anna", seed=10, n=1)
    out = dp.invoke({"all": True, "dry_run": True})
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["n_candidates"] == 2
    # Niente cancellato
    reg = persons_registry.PersonsRegistry()
    try:
        assert reg.get("matteo") is not None
        assert reg.get("anna") is not None
    finally:
        reg.close()


# --- happy path -----------------------------------------------------------

def test_delete_persons_exact(isolated_db):
    _enroll("Matteo", seed=1, n=3)
    out = dp.invoke({"name": "Matteo"})
    assert out["ok"] is True
    assert len(out["results"]) == 1
    assert out["results"][0]["slug"] == "matteo"
    assert out["results"][0]["removed_examples"] == 3
    # Verify deleted
    reg = persons_registry.PersonsRegistry()
    try:
        assert reg.get("matteo") is None
    finally:
        reg.close()


def test_delete_persons_unknown(isolated_db):
    _enroll("Anna", seed=1, n=1)
    out = dp.invoke({"name": "Bruno"})
    assert out["ok"] is False
    assert out["error"] == "unknown_name"
    assert "final_message_hint" in out
    # Anna intatta
    reg = persons_registry.PersonsRegistry()
    try:
        assert reg.get("anna") is not None
    finally:
        reg.close()


def test_delete_persons_token_match(isolated_db):
    _enroll("Ospite Alfa", seed=1, n=2)
    out = dp.invoke({"name": "Ospite"})
    assert out["ok"] is True
    assert out["results"][0]["slug"] == "ospite_alfa"
    assert out["results"][0]["removed_examples"] == 2


def test_delete_persons_ambiguous_returns_needs_inputs(isolated_db):
    _enroll("Ospite Alfa", seed=1, n=1)
    _enroll("Ospite Beta", seed=10, n=1)
    out = dp.invoke({"name": "Ospite"})
    assert out["ok"] is True
    assert out["decision"] == "needs_inputs"
    assert out["ambiguous"] is True
    payload = out["needs_inputs"]
    assert len(payload["dialog"]) == 1
    # PR5: kind passato a choice_with_preview, options come dict ricco.
    assert payload["dialog"][0]["schema"]["kind"] == "choice_with_preview"
    options = payload["dialog"][0]["schema"]["options"]
    assert len(options) == 2
    assert all("preview_image_path" in o for o in options)
    assert all("value" in o and "label" in o for o in options)
    # Niente delete avvenuto
    reg = persons_registry.PersonsRegistry()
    try:
        assert reg.get("ospite_alfa") is not None
        assert reg.get("ospite_beta") is not None
    finally:
        reg.close()


def test_delete_persons_chosen_slug_bypass(isolated_db):
    _enroll("Ospite Alfa", seed=1, n=2)
    _enroll("Ospite Beta", seed=10, n=1)
    out = dp.invoke({"name": "Ospite", "chosen_slug": "ospite_alfa"})
    assert out["ok"] is True
    assert out["results"][0]["slug"] == "ospite_alfa"
    assert out["results"][0]["removed_examples"] == 2
    # Rossi intatto
    reg = persons_registry.PersonsRegistry()
    try:
        assert reg.get("ospite_alfa") is None
        assert reg.get("ospite_beta") is not None
    finally:
        reg.close()


def test_delete_persons_chosen_slug_unknown(isolated_db):
    _enroll("Ospite Alfa", seed=1, n=1)
    out = dp.invoke({"name": "anything", "chosen_slug": "ghost_slug"})
    assert out["ok"] is False
    assert "non trovato" in out["error"]


def test_delete_persons_dialog_options_listed(isolated_db):
    _enroll("Ospite Alfa", seed=1, n=2)
    _enroll("Ospite Beta", seed=10, n=1)
    out = dp.invoke({"name": "Ospite"})
    options = out["needs_inputs"]["dialog"][0]["schema"]["options"]
    slugs = sorted(o["value"] for o in options)
    assert slugs == ["ospite_alfa", "ospite_beta"]


def test_delete_with_backup_purges_crops_and_reverse_restores(tmp_path, monkeypatch):
    """Leave-no-trace + undo completo: `_delete_with_backup` copia le crop nel
    blob, cancella riga E file crop dal disco; `reverse()` ripristina riga,
    embedding e crop. Chiama le funzioni dirette (niente dialog di invoke)."""
    monkeypatch.setattr(persons_registry, "DEFAULT_DB_PATH",
                        tmp_path / "persons.sqlite")
    crops_root = tmp_path / "persons_examples"
    monkeypatch.setattr(persons_registry, "PERSISTENT_EXAMPLES_DIR", crops_root)
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "_hist"))
    monkeypatch.setenv("METNOS_TURN_ID", "t_crop")
    monkeypatch.delenv("METNOS_USER_DATA", raising=False)  # → DEFAULT_DB_PATH

    src = tmp_path / "src.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0JFIF dummy face crop")
    reg = persons_registry.PersonsRegistry()
    reg.enroll(name="Carol Test", image_path=str(src), face_box=(0, 0, 10, 10),
               embedding=_emb(7), sha256="a" * 64)
    slug = persons_registry.slugify("Carol Test")
    crop_dir = crops_root / slug
    assert list(crop_dir.glob("*")), "crop persistita pre-delete"

    row = dp._delete_with_backup(reg, slug, "Carol Test")
    reg.close()
    assert row["removed_example_files"] >= 1
    assert row.get("backup_path")
    assert not crop_dir.exists(), "leave-no-trace: crop rimosse dal disco"
    blob_crops = Path(row["backup_path"]).parent / f"{slug}_crops"
    assert list(blob_crops.glob("*")), "crop nel blob per l'undo"

    rev = dp.reverse({}, {"results": [row]})
    assert rev["ok"] and rev["ok_count"] == 1
    assert list(crop_dir.glob("*")), "l'undo ripristina le crop sul disco"
    reg2 = persons_registry.PersonsRegistry()
    try:
        assert reg2.get("Carol Test") is not None, "persona ripristinata"
    finally:
        reg2.close()


def test_delete_persons_round_trip_through_undo_executor(
        isolated_db, tmp_path, monkeypatch):
    """Il pattern legacy del manifest deve raggiungere module.reverse()."""
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "turn-persons")
    monkeypatch.delenv("METNOS_USER_DATA", raising=False)
    _enroll("Persona Test", seed=31, n=1)

    deleted = dp.invoke({"all": True})
    assert deleted["ok"] is True and deleted["n_removed"] == 1

    from undo import UndoLog
    log_path = tmp_path / "undo.jsonl"
    log = UndoLog(log_path)
    log.append_pending(
        "op-persons", "turn-persons", "delete_persons", {"all": True},
        plan={}, actor="host",
    )
    log.append_done("op-persons", deleted)
    sys.path.insert(0, str(_RUNTIME.parent / "executors" / "undo_last_turn"))
    try:
        import undo_last_turn as ult
        undone = ult.invoke({"log_path": str(log_path), "_actor": "host"})
    finally:
        sys.path.pop(0)

    assert undone["ok"] is True, undone
    assert undone["undone_count"] == 1
    reg = persons_registry.PersonsRegistry()
    try:
        assert reg.get("Persona Test") is not None
    finally:
        reg.close()
