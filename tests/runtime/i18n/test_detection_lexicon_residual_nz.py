"""Prove mirate per i lessici eseguibili residui N-Z di RM-0005."""
from __future__ import annotations

from pathlib import Path

import pytest

import detection_lexicon as dl
import detection_lexicon_seed_residual_nz as seed


@pytest.fixture
def fresh_lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(dl, "_cache_data_version", None)
    monkeypatch.setattr(seed, "_registered_target", None)
    dl._cache.clear()
    dl._regex_cache.clear()
    dl.ensure_seeded()
    seed.register_all()
    return dl._open()


def _translate(concept: str, payload) -> None:
    dl.mark_for_translation(concept, "zz", source_lang="en")
    dl.set_translated(concept, "zz", payload)


def test_seed_census_preserves_legacy_routes_mappings_and_review_policy(
    monkeypatch,
):
    captured = {}

    def capture(concept, kind, **kwargs):
        captured[concept] = {"kind": kind, **kwargs}
        return True

    monkeypatch.setattr(seed._dl, "register", capture)
    seed.register_all()

    expected_routes = {
        "immagini": ["Pictures", "Immagini", "Foto", "Images", "images"],
        "foto": ["Pictures", "Foto", "Immagini", "images"],
        "documenti": ["Documents", "Documenti", "Docs"],
        "musica": ["Music", "Musica"],
        "video": ["Videos", "Video", "Movies"],
        "scaricati": ["Downloads", "Scaricati", "Download"],
        "scrivania": ["Desktop", "Scrivania"],
        "modelli": ["Templates", "Modelli"],
        "pubblici": ["Public", "Pubblici"],
        "pictures": ["Pictures", "Immagini", "Foto"],
        "documents": ["Documents", "Documenti"],
        "music": ["Music", "Musica"],
        "videos": ["Videos", "Video", "Movies"],
        "movies": ["Movies", "Videos", "Video"],
        "downloads": ["Downloads", "Scaricati"],
        "desktop": ["Desktop", "Scrivania"],
        "templates": ["Templates", "Modelli"],
        "public": ["Public", "Pubblici"],
        "images": ["Pictures", "Immagini", "images"],
    }
    actual_routes = {
        row[0]: list(row[1:])
        for concept in seed.PATH_ALIAS_CONCEPTS
        for row in [captured[concept]["it"]]
    }
    assert actual_routes == expected_routes
    assert all(
        captured[concept]["it"] == captured[concept]["en"]
        and captured[concept]["review_policy"] == "manual"
        for concept in seed.PATH_ALIAS_CONCEPTS
    )

    expected_tabular = {
        "path": {"path", "paths", "percorso", "percorsi", "filepath",
                 "filepaths", "file_path", "file_paths", "image_path",
                 "local_path"},
        "directory": {"directory", "directories", "folder", "folders",
                      "cartella", "cartelle", "directorio", "directorios",
                      "carpeta", "carpetas", "ordner"},
        "name": {"name", "names", "nome", "nomi", "filename", "filenames",
                 "file_name", "file_names", "basename", "title", "titles",
                 "titolo", "titoli"},
        "description": {"description", "descriptions", "descrizione",
                        "descrizioni", "desc", "caption", "captions"},
        "size": {"size", "sizes", "size_bytes", "bytes", "dimensione",
                 "dimensioni"},
        "hash": {"hash", "hashes", "sha", "sha256", "checksum", "checksums",
                 "digest", "digests", "impronta", "impronte"},
        "score": {"score", "scores", "punteggio", "punteggi", "relevance",
                  "rilevanza", "confidence", "confidenza"},
        "keywords": {"keywords", "keyword", "parole_chiave", "tags", "tag"},
        "date": {"date", "dates", "data", "datetime", "timestamp",
                 "created_at", "updated_at", "modified_at", "mtime"},
        "domain": {"domain", "domains", "dominio", "domini"},
        "origin": {"origin", "origins", "original", "originals", "origine",
                   "origini", "originale", "originali", "source", "sources",
                   "sorgente", "sorgenti"},
        "duplicate": {"duplicate", "duplicates", "duplicato", "duplicati",
                      "copia", "copie", "copy", "copies", "duplicado",
                      "duplicados", "duplikat", "duplikate"},
        "url": {"url", "urls", "link", "links", "web_url", "web_view_url"},
        "count": {"count", "counts", "conteggio", "numero", "total", "totale"},
    }
    actual_tabular = {
        key: set(forms)
        for key, forms in captured[seed.TABULAR_CONCEPT_ALIASES]["it"].items()
    }
    assert actual_tabular == expected_tabular
    assert captured[seed.TABULAR_CONCEPT_ALIASES]["it"] == captured[
        seed.TABULAR_CONCEPT_ALIASES
    ]["en"]
    assert captured[seed.TABULAR_ORDINALS]["it"] == {
        "1": ["first", "one", "primo", "prima"],
        "2": ["second", "two", "secondo", "seconda"],
        "3": ["third", "three", "terzo", "terza"],
        "4": ["fourth", "four", "quarto", "quarta"],
        "5": ["fifth", "five", "quinto", "quinta"],
    }
    assert captured[seed.IMPLICIT_SEND_BIGRAMS]["it"] == [
        "email me", "mail me", "message me", "text me", "tell me",
        "let me know", "ping me", "shoot me", "mandami una email",
        "mandami una mail", "mandami un messaggio", "mandami un'email",
        "mandami un'e-mail", "fammi sapere", "tienimi al corrente",
        "tienimi informato",
    ]
    assert captured[seed.TABULAR_CONCEPT_ALIASES]["review_policy"] == "manual"
    assert captured[seed.TABULAR_ORDINALS]["review_policy"] == "manual"
    assert captured[seed.IMPLICIT_SEND_BIGRAMS]["review_policy"] == "manual"
    assert set(captured) == seed.CONCEPTS


def test_ready_third_language_drives_all_consumers(
    fresh_lexicon, tmp_path, monkeypatch,
):
    for index, concept in enumerate(seed.PATH_ALIAS_CONCEPTS):
        target = "ZPictures" if index == 0 else f"ZTarget{index}"
        _translate(concept, [f"zzalias{index}", target])
    _translate(seed.TABULAR_CONCEPT_ALIASES, {
        canonical: [f"zz{canonical}"]
        for canonical in seed.TABULAR_CONCEPT_PAYLOAD
    })
    _translate(seed.TABULAR_ORDINALS, {
        canonical: [f"zzordinal{canonical}"]
        for canonical in seed.TABULAR_ORDINAL_PAYLOAD
    })
    _translate(seed.IMPLICIT_SEND_BIGRAMS, ["zznotify"])
    _translate(seed.PROMPTS_LINT_HEDGES, ["zzhedge"])
    _translate(seed.SYNT_HINT_STOP_WORDS, ["zzstop"])
    _translate(seed.TELOS_OVERLAP_STOP_WORDS, ["zzstop"])
    _translate(seed.TOOL_SCHEMA_BOUNDARIES, ["ZZBOUND:"])
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._cache.clear()

    import path_alias
    root = tmp_path / "root"
    root.mkdir()
    (root / "ZPictures").mkdir()
    monkeypatch.setattr(path_alias, "workspace_default", lambda: root)
    monkeypatch.setattr(path_alias, "candidate_roots", lambda: [root])
    resolved, note = path_alias.resolve_path_with_alias("zzalias0")
    assert resolved == root / "ZPictures"
    assert note is not None

    from tabular_projection import project_entries
    rows = project_entries(
        [{"left": "/a", "right": "/b"}],
        ["zzordinal2 zzpath"],
        field_roles=[
            {"field": "left", "roles": ["path"]},
            {"field": "right", "roles": ["path"]},
        ],
    )
    assert rows == [["zzordinal2 zzpath"], ["/b"]]

    from vocab import detect_implicit_actions
    implicit = detect_implicit_actions(
        # This fixture exercises only the residual mutating-bigram resource.
        # Canonical object identities avoid making the assertion depend on the
        # separate, manually reviewed routing.object_synonym family.
        "events messages zznotify",
        explicit_verbs=[],
    )
    assert [item["verb"] for item in implicit] == ["create_events"]

    from prompts_lint import _check_l2_hedge_blacklist
    prompt = tmp_path / "zz.j2"
    content = (
        "{# ---\nrole: x\ntier: middle\nlang: zz\nstyle: prescriptive\n"
        "version: 1\nowner: test\nupdated: 2026-08-30\nsha_prev: x\n--- #}\n"
        "MUST: zzhedge.\n"
    )
    assert [issue.code for issue in _check_l2_hedge_blacklist(
        prompt, content,
    )] == ["L2_HEDGE"]

    from synt import keywords_from_proto_name
    assert keywords_from_proto_name("archive_zzstop_news") == [
        "archive_zzstop_news", "archive", "news",
    ]

    from telos_proposals_store import _semantic_overlap_query
    assert _semantic_overlap_query("zzstop alpha", "zzstop beta") == 0

    from tool_schema_slim import slim_description
    assert "ZZBOUND" in slim_description("Intro. ZZBOUND: retain this clause.")


def test_partial_or_pending_manual_materialization_fails_closed(
    fresh_lexicon, tmp_path, monkeypatch,
):
    for concept in seed.PATH_ALIAS_CONCEPTS:
        dl.mark_for_translation(concept, "zz", source_lang="en")
    _translate(seed.PATH_ALIAS_CONCEPTS[0], ["zzalias", "ZPictures"])
    dl.mark_for_translation(
        seed.TABULAR_CONCEPT_ALIASES, "zz", source_lang="en",
    )
    dl.mark_for_translation(seed.TABULAR_ORDINALS, "zz", source_lang="en")
    dl.mark_for_translation(seed.IMPLICIT_SEND_BIGRAMS, "zz", source_lang="en")
    dl.mark_for_translation(seed.PROMPTS_LINT_HEDGES, "zz", source_lang="en")
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._cache.clear()

    import path_alias
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(path_alias, "workspace_default", lambda: root)
    monkeypatch.setattr(path_alias, "candidate_roots", lambda: [root])
    error = path_alias.check_mutating_path_ambiguity(
        str(root / "zzalias"), target_must_exist=True,
    )
    assert error is not None and error["error_code"] == "ERR_ARG_INVALID"
    unresolved, note = path_alias.resolve_path_with_alias("zzalias")
    assert unresolved == root / "zzalias"
    assert note is None

    from tabular_projection import TabularProjectionError, project_entries
    with pytest.raises(TabularProjectionError):
        project_entries(
            [{"payload": "/a"}], ["zzpath"],
            field_roles=[{"field": "payload", "roles": ["path"]}],
        )

    assert seed.implicit_send_request("zznotify") is False

    from prompts_lint import _check_l2_hedge_blacklist
    prompt = tmp_path / "pending.j2"
    content = (
        "{# ---\nrole: x\ntier: middle\nlang: zz\nstyle: prescriptive\n"
        "version: 1\nowner: test\nupdated: 2026-08-30\nsha_prev: x\n--- #}\n"
        "zzhedge\n"
    )
    issues = _check_l2_hedge_blacklist(prompt, content)
    assert [issue.code for issue in issues] == ["L2_LEXICON_UNAVAILABLE"]


def test_existing_path_is_not_blocked_when_manual_lexicon_is_unavailable(
    fresh_lexicon, tmp_path, monkeypatch,
):
    for concept in seed.PATH_ALIAS_CONCEPTS:
        dl.mark_for_translation(concept, "zz", source_lang="en")
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    existing = tmp_path / "existing"
    existing.mkdir()

    import path_alias
    assert path_alias.check_mutating_path_ambiguity(
        str(existing), target_must_exist=True,
    ) is None
