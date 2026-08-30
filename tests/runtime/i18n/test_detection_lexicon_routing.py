"""RM-0005 routing lexicon: compatibility, additive locale and readiness."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import detection_lexicon as dl
import detection_lexicon_seed_routing as routing_lexicon
import i18n


@pytest.fixture(autouse=True)
def isolated_detection_store(monkeypatch, tmp_path):
    old_conn = dl._conn
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(dl, "_cache", {})
    monkeypatch.setattr(dl, "_regex_cache", {})
    monkeypatch.setattr(dl, "_coverage_gaps_logged", set())
    monkeypatch.setattr(dl, "_declared_review_policies", {})
    monkeypatch.setattr(dl, "_declared_baseline_languages", {})
    monkeypatch.setattr(routing_lexicon, "_registered_target", None)
    monkeypatch.setattr(i18n, "current_lang", lambda: "it")
    yield
    new_conn = dl._conn
    if new_conn is not None and new_conn is not old_conn:
        new_conn.close()


def test_historical_it_en_routing_behaviour_is_preserved():
    from adaptive_rerank import extract_keywords
    from engine.dispatch import (
        _clause_requests_count, _drive_search_term,
        _filter_operation_markers, _is_drive_phantom,
        _message_event_focus_terms,
    )
    from prefilter_rules import _detect_semantic_types, _same_class
    from prefilter_strategies.token_flat_v2 import _query_has_provider_marker
    from prefilter_strategies.trie_v2 import _detect_qualifiers

    assert _detect_qualifiers(
        "leggi la scansione pdf su Google Drive") == [
            "google_workspace", "ocr", "pdf"]
    assert _query_has_provider_marker(
        "cerca su google drive", "_google_workspace")
    assert _same_class("data", "mtime")
    assert _same_class("nome", "filename")
    assert _detect_semantic_types("domani di Roberto") >= {
        "time_window", "person_name"}
    assert extract_keywords({"x": "questo hostname and serveur"}) == [
        "hostname", "serveur"]
    assert not _clause_requests_count("ultimi 7 giorni")
    assert _clause_requests_count("primi dieci file")
    assert _clause_requests_count("at most 15 results")
    assert _drive_search_term(
        "cerca su google drive il documento KAKEBO") == "KAKEBO"
    assert _is_drive_phantom("/google-drive")
    assert "dedup" in _filter_operation_markers()
    assert _message_event_focus_terms(
        "impegni relativi a Roma, Milano e Torino.") == [
            "Roma", "Milano", "Torino"]


def test_query_boosts_read_surfaces_from_routing_lexicon():
    from prefilter import tokenize
    from prefilter_rules import compute_rule_boost

    send = SimpleNamespace(name="send_messages", description="", affinity=[])
    files = SimpleNamespace(name="find_files", description="", affinity=[])
    assert compute_rule_boost(
        "invia una mail", tokenize("invia una mail"), None, send) >= 8
    assert compute_rule_boost(
        "trova la cartella", tokenize("trova la cartella"), None, files) >= 4


def test_ready_third_language_is_additive_but_pending_row_is_invisible(
        monkeypatch):
    from prefilter_strategies.trie_v2 import _detect_qualifiers

    routing_lexicon.register_all()
    spanish = routing_lexicon.mapping("routing.trie.qualifier")
    spanish["image"] = ["imagen"]
    assert dl.validate_mapping_payload(
        routing_lexicon.mapping("routing.trie.qualifier"), spanish)["ok"]
    dl.set_payload(
        "routing.trie.qualifier", "es", spanish,
        kind="mapping", match_mode="word", source_lang="en",
    )
    monkeypatch.setattr(i18n, "current_lang", lambda: "es")
    assert _detect_qualifiers("procesa la imagen") == ["image"]
    # Partial materialization is discriminating and additive: the ready target
    # concept works while an untranslated qualifier still falls back to IT/EN.
    assert _detect_qualifiers("procesa imagen pdf") == ["pdf", "image"]

    dl._open().execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang=?",
        ("routing.trie.qualifier", "es"),
    )
    dl._open().commit()
    dl._invalidate("routing.trie.qualifier")
    assert _detect_qualifiers("procesa la imagen") == []
    assert _detect_qualifiers("procesa imagen pdf") == ["pdf"]


def test_action_and_tool_enums_remain_closed_technical_invariants():
    from engine.dispatch import _READ_INTENT_VERBS
    from prefilter_strategies.token_flat_v2 import _VERB_FAMILY

    assert _READ_INTENT_VERBS == ("read", "find", "get", "list")
    assert _VERB_FAMILY["read"] == {"read", "find", "get", "list"}


def test_object_synonym_routing_requires_complete_reviewed_native_mapping(
        monkeypatch):
    from vocab import canonical_object

    routing_lexicon.register_all()
    source = routing_lexicon.mapping("routing.object_synonym")
    translated = {
        canonical: [f"zz-{canonical}"] for canonical in source
    }
    dl.mark_for_translation(
        "routing.object_synonym", "zz", source_lang="en",
    )
    dl.set_translated("routing.object_synonym", "zz", translated)
    monkeypatch.setattr(i18n, "current_lang", lambda: "zz")

    assert canonical_object("zz-events") == "events"
    assert canonical_object("events") == "events"  # protocol identity

    dl._open().execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept='routing.object_synonym' AND lang='zz'",
    )
    dl._open().commit()
    dl._invalidate("routing.object_synonym")
    assert canonical_object("zz-events") is None
    assert canonical_object("events") == "events"


def test_store_target_uses_one_complete_reviewed_native_grammar(monkeypatch):
    routing_lexicon.register_all()

    assert routing_lexicon.capture_store_target(
        "salva nello store clienti_2026",
    ) == "clienti_2026"
    assert routing_lexicon.capture_store_target(
        "write into archive customers_2026",
    ) == "customers_2026"
    assert routing_lexicon.capture_store_target(
        "store first archive second",
    ) is None

    concept = "routing.store_target"
    dl.mark_for_translation(concept, "es", source_lang="en")
    dl.set_translated(
        concept, "es",
        [r"\balmac[eé]n\s+(?P<target>[A-Za-z0-9_]+)\b"],
    )
    monkeypatch.setattr(i18n, "current_lang", lambda: "es")
    dl._invalidate(concept)
    assert routing_lexicon.capture_store_target(
        "guardar en almacén clientes",
    ) == "clientes"

    dl._open().execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='es'",
        (concept,),
    )
    dl._open().commit()
    dl._invalidate(concept)
    assert routing_lexicon.capture_store_target(
        "guardar en almacén clientes",
    ) is None


def test_result_folder_exclusion_requires_reviewed_native_grammar(monkeypatch):
    concept = "routing.result_folder_exclusion"
    routing_lexicon.register_all()
    assert routing_lexicon.native_manual_matches(
        concept, "senza includere i risultati precedenti",
    )
    assert routing_lexicon.native_manual_matches(
        concept, "exclude prior results",
    )

    dl.mark_for_translation(concept, "es", source_lang="en")
    dl.set_translated(concept, "es", [r"\bexclu(?:ir|ye)\w*\b"])
    monkeypatch.setattr(i18n, "current_lang", lambda: "es")
    dl._invalidate(concept)
    assert routing_lexicon.native_manual_matches(
        concept, "excluir resultados anteriores",
    )

    dl._open().execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='es'",
        (concept,),
    )
    dl._open().commit()
    dl._invalidate(concept)
    assert not routing_lexicon.native_manual_matches(
        concept, "excluir resultados anteriores",
    )
