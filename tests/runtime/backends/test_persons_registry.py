#!/usr/bin/env python3
"""Tests for runtime/persons_registry.py (PR1: storage + helper layer)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


import persons_registry  # noqa: E402  # per monkeypatch.setattr del modulo
from persons_registry import (  # noqa: E402
    EMBEDDING_DIM,
    PersonsRegistry,
    slugify,
)


# --- helpers -------------------------------------------------------------

def _rand_emb(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _close_emb(base: np.ndarray, seed: int, eps: float = 0.05) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    n /= np.linalg.norm(n)
    v = base + eps * n
    return (v / np.linalg.norm(v)).astype(np.float32)


def _orthogonal_emb(base: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    v -= np.dot(v, base) * base
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture
def reg(tmp_path, monkeypatch):
    db = tmp_path / "persons.sqlite"
    # Isola anche lo storage immagini (§7.3 _persist_example_image): costante
    # import-time da config, altrimenti scrive in ~/.local/share reale.
    monkeypatch.setattr(persons_registry, "PERSISTENT_EXAMPLES_DIR",
                        tmp_path / "persons_examples")
    r = PersonsRegistry(db_path=db)
    yield r
    r.close()


# --- slugify -------------------------------------------------------------

def test_slugify_case_insensitive():
    assert slugify("Matteo") == "matteo"
    assert slugify("matteo") == "matteo"
    assert slugify("MATTEO") == "matteo"


def test_slugify_accents():
    assert slugify("Matteò") == "matteo"
    assert slugify("Maté") == "mate"
    assert slugify("Renée") == "renee"


def test_slugify_spaces():
    assert slugify("Maria Chiara") == "maria_chiara"
    assert slugify("Maria  Chiara") == "maria_chiara"


def test_slugify_punctuation():
    assert slugify("O'Connor") == "oconnor"
    assert slugify("  Matteo  ") == "matteo"
    # Hyphen e slash trattati come separatore di parola (PR1 decisione)
    assert slugify("Jean-Luc") == "jean_luc"
    assert slugify("Maria-Chiara") == "maria_chiara"
    assert slugify("Anna/Marie") == "anna_marie"


def test_slugify_empty_raises():
    with pytest.raises(ValueError):
        slugify("")
    with pytest.raises(ValueError):
        slugify("   ")
    with pytest.raises(ValueError):
        slugify("@@@")
    with pytest.raises(ValueError):
        slugify("___")


# --- enroll --------------------------------------------------------------

def test_enroll_creates_person_row(reg):
    emb = _rand_emb(1)
    out = reg.enroll(
        name="Matteo", image_path="/p/a.jpg", face_box=(10, 10, 100, 100),
        embedding=emb, sha256="aa" * 32,
    )
    assert out == {"slug": "matteo", "name": "Matteo", "n_examples": 1, "added": True}
    p = reg.get("matteo")
    assert p is not None
    assert p["n_examples"] == 1
    assert len(p["examples"]) == 1
    # §7.3: enroll persiste l'immagine in PERSISTENT_EXAMPLES_DIR/<slug>/<sha256><ext>
    assert p["examples"][0]["image_path"].endswith("aa" * 32 + ".jpg")
    assert p["examples"][0]["face_box"] == [10, 10, 100, 100]


def test_enroll_preserves_first_display_name(reg):
    emb1 = _rand_emb(1)
    emb2 = _rand_emb(2)
    reg.enroll(
        name="MATTEO", image_path="/p/a.jpg", face_box=(0, 0, 1, 1),
        embedding=emb1, sha256="a" * 64,
    )
    out = reg.enroll(
        name="matteo", image_path="/p/b.jpg", face_box=(0, 0, 2, 2),
        embedding=emb2, sha256="b" * 64,
    )
    assert out["name"] == "MATTEO"  # first wins
    assert reg.get("matteo")["name"] == "MATTEO"


def test_enroll_mode_add_accumulates(reg):
    for i in range(3):
        reg.enroll(
            name="Anna", image_path=f"/p/{i}.jpg", face_box=(i, i, 50, 50),
            embedding=_rand_emb(10 + i), sha256=f"{i:064d}",
        )
    assert reg.get("anna")["n_examples"] == 3
    assert len(reg.get("anna")["examples"]) == 3


def test_enroll_mode_replace_wipes(reg):
    for i in range(3):
        reg.enroll(
            name="Anna", image_path=f"/p/{i}.jpg", face_box=(i, i, 50, 50),
            embedding=_rand_emb(20 + i), sha256=f"{i:064d}",
        )
    assert reg.get("anna")["n_examples"] == 3
    out = reg.enroll(
        name="Anna", image_path="/p/new.jpg", face_box=(9, 9, 9, 9),
        embedding=_rand_emb(99), sha256="f" * 64, mode="replace",
    )
    assert out["n_examples"] == 1
    assert reg.get("anna")["n_examples"] == 1
    # replace ha tenuto solo il nuovo esempio (sha "f"*64), persistito §7.3
    assert reg.get("anna")["examples"][0]["image_path"].endswith("f" * 64 + ".jpg")


def test_enroll_idempotent_on_dup(reg):
    emb = _rand_emb(7)
    a = reg.enroll(
        name="Luca", image_path="/p/x.jpg", face_box=(1, 2, 3, 4),
        embedding=emb, sha256="c" * 64,
    )
    b = reg.enroll(
        name="Luca", image_path="/p/x.jpg", face_box=(1, 2, 3, 4),
        embedding=emb, sha256="c" * 64,
    )
    assert a["added"] is True
    assert b["added"] is False
    assert reg.get("luca")["n_examples"] == 1


def test_enroll_skips_near_duplicate_embedding(reg):
    base = _rand_emb(11)
    a = reg.enroll(
        name="Anna", image_path="/p/a.jpg", face_box=(1, 1, 2, 2),
        embedding=base, sha256="a" * 64,
    )
    near = _close_emb(base, seed=12, eps=0.02)
    b = reg.enroll(
        name="Anna", image_path="/p/b.jpg", face_box=(3, 3, 4, 4),
        embedding=near, sha256="b" * 64,
    )
    assert a["added"] is True
    assert b["added"] is False
    assert b["reason"] == "near_duplicate_embedding"
    assert b["max_cosine"] >= 0.95
    assert reg.get("anna")["n_examples"] == 1


def test_enroll_keeps_distinct_pose(reg):
    base = _rand_emb(13)
    a = reg.enroll(
        name="Bob", image_path="/p/a.jpg", face_box=(1, 1, 2, 2),
        embedding=base, sha256="a" * 64,
    )
    far = _orthogonal_emb(base, seed=14)
    b = reg.enroll(
        name="Bob", image_path="/p/b.jpg", face_box=(3, 3, 4, 4),
        embedding=far, sha256="b" * 64,
    )
    assert a["added"] is True
    assert b["added"] is True
    assert reg.get("bob")["n_examples"] == 2


def test_enroll_threshold_one_disables_dedup(reg):
    base = _rand_emb(15)
    reg.enroll(
        name="Carol", image_path="/p/a.jpg", face_box=(1, 1, 2, 2),
        embedding=base, sha256="a" * 64,
    )
    near = _close_emb(base, seed=16, eps=0.001)
    out = reg.enroll(
        name="Carol", image_path="/p/b.jpg", face_box=(3, 3, 4, 4),
        embedding=near, sha256="b" * 64,
        dedupe_cosine_threshold=1.0,
    )
    assert out["added"] is True
    assert reg.get("carol")["n_examples"] == 2


def test_enroll_bad_embedding_dim_raises(reg):
    bad = np.zeros(256, dtype=np.float32)
    bad[0] = 1.0
    with pytest.raises(ValueError):
        reg.enroll(
            name="X", image_path="/p", face_box=(0, 0, 1, 1),
            embedding=bad, sha256="0" * 64,
        )


def test_enroll_empty_name_raises(reg):
    emb = _rand_emb(1)
    with pytest.raises(ValueError):
        reg.enroll(
            name="", image_path="/p", face_box=(0, 0, 1, 1),
            embedding=emb, sha256="0" * 64,
        )
    with pytest.raises(ValueError):
        reg.enroll(
            name="   ", image_path="/p", face_box=(0, 0, 1, 1),
            embedding=emb, sha256="0" * 64,
        )


# --- get / list / delete -------------------------------------------------

def test_get_returns_examples(reg):
    reg.enroll(name="Z", image_path="/p/1.jpg", face_box=(0, 0, 10, 10),
               embedding=_rand_emb(1), sha256="1" * 64)
    reg.enroll(name="Z", image_path="/p/2.jpg", face_box=(5, 5, 10, 10),
               embedding=_rand_emb(2), sha256="2" * 64)
    p = reg.get("Z")
    assert p["n_examples"] == 2
    # path persistiti §7.3: basename = <sha256>.jpg
    assert {Path(e["image_path"]).name for e in p["examples"]} == {
        "1" * 64 + ".jpg", "2" * 64 + ".jpg"}
    assert p["examples"][0]["face_box"] == [0, 0, 10, 10]


def test_get_unknown_returns_none(reg):
    assert reg.get("nobody") is None


def test_list_all_sorted_by_name(reg):
    for nm in ["Charlie", "Alice", "Bob"]:
        reg.enroll(name=nm, image_path="/p", face_box=(0, 0, 1, 1),
                   embedding=_rand_emb(hash(nm) % 1000),
                   sha256=nm.encode().hex().ljust(64, "0"))
    names = [r["name"] for r in reg.list_all()]
    assert names == ["Alice", "Bob", "Charlie"]


def test_delete_cascades_examples(reg):
    for i in range(3):
        reg.enroll(name="K", image_path=f"/p/{i}", face_box=(i, i, 1, 1),
                   embedding=_rand_emb(30 + i), sha256=f"{i:064d}")
    out = reg.delete("k")
    assert out == {"slug": "k", "deleted": True, "removed_examples": 3}
    assert reg.get("k") is None
    rows = reg._conn.execute(
        "SELECT COUNT(*) AS c FROM person_examples WHERE person_slug='k'"
    ).fetchone()
    assert rows["c"] == 0


def test_delete_unknown_returns_false(reg):
    out = reg.delete("ghost")
    assert out == {"slug": "ghost", "deleted": False, "removed_examples": 0}


# --- top_k_match ---------------------------------------------------------

def test_top_k_match_above_threshold(reg):
    base = _rand_emb(42)
    reg.enroll(name="Tom", image_path="/p", face_box=(0, 0, 1, 1),
               embedding=base, sha256="d" * 64)
    q = _close_emb(base, seed=99, eps=0.02)
    out = reg.top_k_match(q, name="Tom", threshold=0.55)
    assert len(out) == 1
    assert out[0]["slug"] == "tom"
    assert out[0]["name"] == "Tom"
    assert out[0]["best_score"] > 0.9
    assert out[0]["matched_example_idx"] == 0


def test_top_k_match_below_threshold(reg):
    base = _rand_emb(43)
    reg.enroll(name="Tim", image_path="/p", face_box=(0, 0, 1, 1),
               embedding=base, sha256="e" * 64)
    q = _orthogonal_emb(base, seed=11)
    out = reg.top_k_match(q, name="Tim", threshold=0.55)
    assert out == []


def test_top_k_match_picks_best_example(reg):
    e0 = _rand_emb(100)
    e1 = _rand_emb(200)
    e2 = _rand_emb(300)
    for i, e in enumerate([e0, e1, e2]):
        reg.enroll(name="Multi", image_path=f"/p/{i}", face_box=(i, 0, 1, 1),
                   embedding=e, sha256=f"{i:064d}")
    # Query closest to example #1
    q = _close_emb(e1, seed=7, eps=0.01)
    out = reg.top_k_match(q, name="Multi", threshold=0.55)
    assert len(out) == 1
    assert out[0]["matched_example_idx"] == 1


def test_top_k_match_unknown_name_returns_empty(reg):
    q = _rand_emb(5)
    assert reg.top_k_match(q, name="ghost", threshold=0.55) == []


def test_top_k_match_no_name_scans_all(reg):
    eA = _rand_emb(1000)
    eB = _rand_emb(2000)
    eC = _rand_emb(3000)
    reg.enroll(name="A", image_path="/p", face_box=(0, 0, 1, 1),
               embedding=eA, sha256="aa" * 32)
    reg.enroll(name="B", image_path="/p", face_box=(0, 0, 1, 1),
               embedding=eB, sha256="bb" * 32)
    reg.enroll(name="C", image_path="/p", face_box=(0, 0, 1, 1),
               embedding=eC, sha256="cc" * 32)
    q = _close_emb(eB, seed=3, eps=0.01)
    out = reg.top_k_match(q, threshold=0.55)
    # B must match; others may or may not (random embeddings near-orthogonal),
    # but B must be first if multiple match.
    assert len(out) >= 1
    assert out[0]["slug"] == "b"


# --- persistence / concurrency ------------------------------------------

def test_persist_across_close_open(tmp_path):
    db = tmp_path / "p.sqlite"
    r1 = PersonsRegistry(db_path=db)
    emb = _rand_emb(1)
    r1.enroll(name="Persist", image_path="/p", face_box=(0, 0, 1, 1),
              embedding=emb, sha256="9" * 64)
    r1.close()

    r2 = PersonsRegistry(db_path=db)
    p = r2.get("persist")
    assert p is not None
    assert p["n_examples"] == 1
    embs = r2.lookup_embeddings("persist")
    assert len(embs) == 1
    assert embs[0].shape == (EMBEDDING_DIM,)
    np.testing.assert_allclose(embs[0], emb, rtol=0, atol=0)
    r2.close()


def test_two_instances_same_db(tmp_path):
    db = tmp_path / "shared.sqlite"
    r1 = PersonsRegistry(db_path=db)
    r2 = PersonsRegistry(db_path=db)
    try:
        r1.enroll(name="One", image_path="/p", face_box=(0, 0, 1, 1),
                  embedding=_rand_emb(1), sha256="11" * 32)
        # r2 must see the row written by r1 (WAL committed reads).
        assert r2.get("one") is not None
        r2.enroll(name="Two", image_path="/p", face_box=(0, 0, 1, 1),
                  embedding=_rand_emb(2), sha256="22" * 32)
        assert r1.get("two") is not None
    finally:
        r1.close()
        r2.close()


# --- lookup_embeddings ---------------------------------------------------

def test_lookup_embeddings_unknown(reg):
    assert reg.lookup_embeddings("nope") == []


# --- resolve_name (PR2) --------------------------------------------------

def _enroll_minimal(reg, name, seed=1):
    reg.enroll(
        name=name, image_path=f"/p/{seed}.jpg",
        face_box=(0, 0, 1, 1),
        embedding=_rand_emb(seed), sha256=f"{seed:064d}",
    )


def test_resolve_exact_full_name(reg):
    _enroll_minimal(reg, "Ospite Alfa", seed=1)
    assert reg.resolve_name("Ospite Alfa") == ["ospite_alfa"]


def test_resolve_first_token(reg):
    _enroll_minimal(reg, "Ospite Alfa", seed=1)
    assert reg.resolve_name("Ospite") == ["ospite_alfa"]


def test_resolve_last_token(reg):
    _enroll_minimal(reg, "Buffa Ospite", seed=1)
    assert reg.resolve_name("Ospite") == ["buffa_ospite"]


def test_resolve_middle_token(reg):
    _enroll_minimal(reg, "Ospite Alfa Beta", seed=1)
    assert reg.resolve_name("Alfa") == ["ospite_alfa_beta"]


def test_resolve_ambiguous_returns_multiple(reg):
    _enroll_minimal(reg, "Ospite Alfa", seed=1)
    _enroll_minimal(reg, "Ospite Beta", seed=2)
    assert reg.resolve_name("Ospite") == ["ospite_alfa", "ospite_beta"]


def test_resolve_unknown_returns_empty(reg):
    _enroll_minimal(reg, "Anna", seed=1)
    assert reg.resolve_name("Bruno") == []


def test_resolve_case_insensitive(reg):
    _enroll_minimal(reg, "Ospite Alfa", seed=1)
    assert reg.resolve_name("OSPITE") == ["ospite_alfa"]
    assert reg.resolve_name("ospite") == ["ospite_alfa"]


def test_resolve_empty_input_returns_empty(reg):
    _enroll_minimal(reg, "Anyone", seed=1)
    assert reg.resolve_name("") == []
    assert reg.resolve_name("@@@") == []
    assert reg.resolve_name("   ") == []


def test_resolve_no_partial_substring(reg):
    """Token-anywhere richiede match di token intero, non substring.

    Garantisce: query "Sil" NON deve matchare "Ospite" (eviterebbe
    falsi positivi tipo "Lia"→"Lialaki"). Solo tokens completi.
    """
    _enroll_minimal(reg, "Ospite Alfa", seed=1)
    assert reg.resolve_name("Sil") == []


def test_resolve_hyphenated_name(reg):
    """Hyphen e' separator (PR1): "Jean-Luc" → token "jean", "luc"."""
    _enroll_minimal(reg, "Jean-Luc Picard", seed=1)
    assert reg.resolve_name("Jean") == ["jean_luc_picard"]
    assert reg.resolve_name("Luc") == ["jean_luc_picard"]
    assert reg.resolve_name("Picard") == ["jean_luc_picard"]


def test_lookup_embeddings_roundtrip(reg):
    emb = _rand_emb(123)
    reg.enroll(name="RT", image_path="/p", face_box=(0, 0, 1, 1),
               embedding=emb, sha256="ab" * 32)
    out = reg.lookup_embeddings("rt")
    assert len(out) == 1
    np.testing.assert_allclose(out[0], emb, rtol=0, atol=0)


# --- leave-no-trace: purge crop files su delete (bug 13/6) ----------------

def test_examples_dir_and_purge_example_files(reg, tmp_path):
    """`purge_example_files` rimuove le crop persistite (orfane dopo delete) e
    la dir; idempotente. `examples_dir` punta a PERSISTENT_EXAMPLES_DIR/<slug>."""
    src = tmp_path / "src.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0JFIF crop bytes")
    reg.enroll(name="Carol Verde", image_path=str(src),
               face_box=(0, 0, 10, 10), embedding=_rand_emb(11),
               sha256="c" * 64)
    d = reg.examples_dir("Carol Verde")
    assert d == (persons_registry.PERSISTENT_EXAMPLES_DIR / "carol_verde")
    assert d.is_dir() and list(d.iterdir()), "crop persistita pre-purge"
    n = reg.purge_example_files("carol_verde")  # accetta anche lo slug
    assert n >= 1
    assert not d.exists(), "dir crop rimossa (leave-no-trace)"
    assert reg.purge_example_files("Carol Verde") == 0  # idempotente
