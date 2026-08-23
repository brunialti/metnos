#!/usr/bin/env python3
"""Gate RM-0005 per superfici e confini del vocabolario azioni."""
from __future__ import annotations

import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

import detection_lexicon as dl
import i18n
import prefilter
import vocab


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clear_detection_caches_after_test(monkeypatch):
    yield
    dl._invalidate()


def _fresh_catalogs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(i18n, "DB_PATH", tmp_path / "i18n.sqlite")
    monkeypatch.setattr(
        i18n, "_SEED_DB_PATH", ROOT / "install/data/i18n_seed.sqlite",
    )
    monkeypatch.setattr(i18n, "_conn", None)
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    dl._invalidate()
    dl.ensure_seeded()
    i18n._open()


def _materialize_synthetic_language(lang: str = "fr") -> None:
    dl.enqueue_language(lang)
    surfaces = {
        action: (["démarrer"] if action == "run" else [f"forme{index}"])
        for index, action in enumerate(vocab.ACTIONS)
    }
    dl.set_translated(vocab.ACTION_SURFACES_CONCEPT, lang, surfaces)
    for action in vocab.ACTIONS:
        text = (
            "Démarre un programme ou processus; ce n'est pas ouvrir une page."
            if action == "run"
            else f"Frontière sémantique de {action}."
        )
        i18n.set_catalog_translations(
            vocab.action_boundary_key(action), {lang: text}, source_lang=lang,
        )


def test_seed_editoriale_completa_e_senza_boundary_legacy():
    assert set(vocab.ACTION_MAPPING) == set(vocab.ACTIONS)
    assert vocab.action_seed_languages()
    for action in vocab.ACTIONS:
        spec = vocab.ACTION_MAPPING[action]
        assert isinstance(spec["boundary"], dict), action
        for lang in vocab.action_seed_languages():
            assert spec[lang], (action, lang)
            assert spec["boundary"][lang].strip(), (action, lang)


def test_seed_sqlite_contiene_tutti_i_confini_versionati():
    path = ROOT / "install/data/i18n_seed.sqlite"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT key, lang, text, needs_translation, version_hash "
            "FROM i18n WHERE key LIKE ?",
            (f"{vocab.ACTION_BOUNDARY_KEY_PREFIX}%_BOUNDARY",),
        ).fetchall()
    finally:
        conn.close()
    expected = len(vocab.ACTIONS) * len(vocab.action_seed_languages())
    assert len(rows) == expected
    assert all(text and not pending and version_hash for _, _, text, pending, version_hash in rows)


def test_terza_lingua_guida_detection_rendering_e_coverage(tmp_path, monkeypatch):
    _fresh_catalogs(tmp_path, monkeypatch)
    _materialize_synthetic_language("fr")
    monkeypatch.setattr(i18n._C, "INSTANCE_LANG", "fr")

    coverage = vocab.action_vocabulary_coverage("fr")
    assert coverage["ok"], coverage
    assert prefilter.detect_canonical_verb(prefilter.tokenize("démarrer")) == "run"
    rendered = vocab.render_action_mapping_block()
    boundaries = vocab.render_boundaries()
    assert "démarrer" in rendered
    assert "Démarre un programme" in rendered
    assert "Démarre un programme" in boundaries
    assert " IT:" not in rendered and " EN:" not in rendered


def test_seed_languages_passano_lo_stesso_gate(tmp_path, monkeypatch):
    _fresh_catalogs(tmp_path, monkeypatch)
    for lang in vocab.action_seed_languages():
        report = vocab.action_vocabulary_coverage(lang)
        assert report["ok"], report


def test_add_lang_accoda_catalogo_e_lessico_nella_stessa_operazione(
        tmp_path, monkeypatch, capsys):
    _fresh_catalogs(tmp_path, monkeypatch)
    from admin import i18n_cli

    i18n_cli.cmd_add_lang(Namespace(code="fr", source_lang="en"))
    output = capsys.readouterr().out
    assert "output=" in output and "input=" in output
    assert "fr" in i18n.available_languages()
    row = dl._open().execute(
        "SELECT payload, needs_translation FROM detection_lexicon "
        "WHERE concept=? AND lang='fr'",
        (vocab.ACTION_SURFACES_CONCEPT,),
    ).fetchone()
    assert row == (None, 1)


def test_gap_nativo_e_esplicito_ma_il_runtime_fa_fallback(tmp_path, monkeypatch):
    _fresh_catalogs(tmp_path, monkeypatch)
    _materialize_synthetic_language("fr")
    key = vocab.action_boundary_key("run")
    conn = i18n._open()
    conn.execute("DELETE FROM i18n WHERE key=? AND lang='fr'", (key,))
    conn.commit()

    coverage = vocab.action_vocabulary_coverage("fr")
    assert not coverage["ok"]
    assert coverage["missing_boundaries"] == ["run"]
    assert vocab.action_boundary("run", "fr") == vocab.ACTION_MAPPING["run"]["boundary"]["en"]


def test_tokenizer_accetta_parole_unicode():
    assert prefilter.tokenize("Démarrer l’action") == {"démarrer", "l", "action"}
