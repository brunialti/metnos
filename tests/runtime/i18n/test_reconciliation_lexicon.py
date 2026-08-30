"""RM-0005 reconciliation fields are native-ready boundary data."""
from __future__ import annotations

import copy

import pytest

import detection_lexicon as dl
import detection_lexicon_seed_reconciliation as reconciliation


@pytest.fixture
def isolated_reconciliation_lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(dl, "_cache_data_version", None)
    monkeypatch.setattr(reconciliation, "_registered_target", None)
    dl._cache.clear()
    dl._regex_cache.clear()
    reconciliation.register_all()
    yield
    if dl._conn is not None:
        dl._conn.close()


def _materialize(language: str) -> None:
    overrides = {
        reconciliation.FIELD_CONCEPT: {
            "entity": ["entite"],
            "domain": ["domaine", "domaines"],
            "origin": ["origine-fr", "origines-fr"],
            "organization": ["organisation"],
            "person": ["personne"],
            "project": ["projet"],
            "type": ["type-fr"],
            "email": ["courriel"],
            "phone": ["telephone-fr"],
            "deadline": ["echeance"],
        },
        reconciliation.TEMPORAL_CONCEPT: {
            "modified_time": ["modifie"],
        },
        reconciliation.SOURCE_CONCEPT: {
            "sender": ["expediteur"],
        },
        reconciliation.AUDIT_CONCEPT: {
            "supplier": ["fournisseur"],
            "status": ["etat"],
        },
        reconciliation.RELEVANCE_GENERIC_CONCEPT: [
            "documents-fr", "projet-generique",
        ],
        reconciliation.RELEVANCE_PERSON_TYPE_CONCEPT: [
            "personne", "contact-fr",
        ],
        reconciliation.RELEVANCE_ORGANIZATION_TYPE_CONCEPT: [
            "organisation", "entreprise",
        ],
    }
    for concept in reconciliation.CONCEPTS:
        source = dl.resource_for_language(
            concept, "en", fallback=False, ready_only=True,
        )
        assert source is not None
        if source["kind"] == "mapping":
            payload = {
                canonical: [f"{language}_{index}_{canonical}"]
                for index, canonical in enumerate(source["payload"])
            }
            payload.update(copy.deepcopy(overrides.get(concept, {})))
        else:
            payload = copy.deepcopy(overrides.get(concept)) or [
                f"{language}_{index}_{concept.replace('.', '_')}"
                for index, _value in enumerate(source["payload"])
            ]
        dl.mark_for_translation(concept, language, source_lang="en")
        dl.set_translated(concept, language, payload)


def test_historical_it_en_bindings_are_equivalent(
        isolated_reconciliation_lexicon, monkeypatch) -> None:
    monkeypatch.setattr(dl, "current_lang", lambda: "it")
    italian = reconciliation.load()
    assert italian is not None
    assert italian.record_field("domini") == "dominio"
    assert italian.record_field("origins") == "origine"
    assert italian.source_keys("mittente") == ("from",)
    assert italian.audit_forms("vendor") == (
        "fornitore", "supplier", "vendor",
    )
    assert {"file", "files"}.issubset(italian.relevance_generic_tokens)
    assert italian.canonical_field("stato") == "status"
    assert italian.canonical_audit("stato") == "status"

    monkeypatch.setattr(dl, "current_lang", lambda: "en")
    dl._invalidate()
    english = reconciliation.load()
    assert english is not None
    assert english.record_field("domini") == "domain"
    assert english.record_field("origins") == "origin"
    assert english.source_keys("mittente") == ("from",)
    assert "data_modifica" in reconciliation.temporal_aliases()


def test_complete_third_language_drives_field_and_evidence_boundaries(
        isolated_reconciliation_lexicon, monkeypatch) -> None:
    _materialize("fr")
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")
    dl._invalidate()

    lexicon = reconciliation.load()
    assert lexicon is not None
    assert lexicon.surface("entity") == "entite"
    assert lexicon.record_field("domaines") == "domaine"
    assert lexicon.source_keys("expediteur") == ("from",)
    assert lexicon.audit_forms("fournisseur")[0] == "fournisseur"

    import extract_entries
    assert extract_entries._source_field_value(
        {"from": "sender@example.test"}, "expediteur",
    ) == "sender@example.test"
    assert extract_entries._extract_labeled_audit_values(
        "Fournisseur: Vega", ["fournisseur"],
    ) == {"fournisseur": "Vega"}

    from engine import dispatch
    from engine.types import Framework, Intent, StepSpec
    from types import SimpleNamespace

    original = Framework(steps=[
        StepSpec("read_messages", {"account": "all"}),
        StepSpec("read_events", {"top_k": 10}),
    ])
    catalog = [SimpleNamespace(name=name) for name in {
        "read_messages", "read_events", "extract_entries", "group_entries",
        "sort_entries", "describe_entries", "create_dirs", "write_files",
        "create_files_spreadsheet",
    }]
    out = dispatch._normalize_message_event_report_pipeline(
        original,
        Intent(verb="read", object="messages"),
        "Analizza email ed eventi e crea un foglio con i risultati",
        catalog,
    )
    assert out is not original
    assert "entite" in out.steps[2].args["fields"]
    assert out.steps[-2].args["path"].endswith("/dati_estratti.xlsx")
    assert "dati_estratti.xlsx" in out.final_message


def test_pending_binding_cannot_finalize_a_reconciliation_artifact(
        isolated_reconciliation_lexicon, monkeypatch) -> None:
    _materialize("fr")
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")
    dl._open().execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='fr'",
        (reconciliation.SOURCE_CONCEPT,),
    )
    dl._open().commit()
    dl._invalidate()
    assert reconciliation.load() is None

    import extract_entries
    assert extract_entries._source_field_value(
        {"from": "sender@example.test"}, "expediteur",
    ) is None
    assert extract_entries._extract_labeled_audit_values(
        "Fournisseur: Vega", ["fournisseur"],
    ) == {}

    from engine import dispatch
    from engine.types import Framework, Intent, StepSpec
    from types import SimpleNamespace

    original = Framework(steps=[
        StepSpec("read_messages", {"account": "all"}),
        StepSpec("read_events", {"top_k": 10}),
    ])
    catalog = [SimpleNamespace(name=name) for name in {
        "read_messages", "read_events", "extract_entries", "group_entries",
        "sort_entries", "describe_entries", "create_dirs", "write_files",
        "create_files_spreadsheet",
    }]
    query = "Analizza email ed eventi e crea un foglio con i risultati"
    out = dispatch._normalize_message_event_report_pipeline(
        original, Intent(verb="read", object="messages"), query, catalog,
    )

    assert out is original
    assert not any(step.tool in {
        "create_dirs", "write_files", "create_files_spreadsheet",
        "compress_files",
    } for step in out.steps)


def test_non_latin_ready_field_surfaces_remain_distinct(
        isolated_reconciliation_lexicon, monkeypatch) -> None:
    _materialize("zh")
    source = dl.resource_for_language(
        reconciliation.FIELD_CONCEPT, "en", fallback=False, ready_only=True,
    )
    assert source is not None
    payload = {
        canonical: [f"字段{index}"]
        for index, canonical in enumerate(source["payload"])
    }
    payload["entity"] = ["实体"]
    payload["origin"] = ["来源"]
    dl.set_translated(reconciliation.FIELD_CONCEPT, "zh", payload)
    monkeypatch.setattr(dl, "current_lang", lambda: "zh")
    dl._invalidate()

    lexicon = reconciliation.load()
    assert lexicon is not None
    assert lexicon.canonical_field("实体") == "entity"
    assert lexicon.canonical_field("来源") == "origin"
    assert lexicon.canonical_field("实体") != lexicon.canonical_field("来源")


def test_extract_consumers_use_ready_third_language_only(
        isolated_reconciliation_lexicon, monkeypatch) -> None:
    _materialize("fr")
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")
    dl._invalidate()

    import extract_entries

    lexicon = reconciliation.load()
    assert lexicon is not None
    assert extract_entries._field_temporal_kind(
        "echeance", lexicon=lexicon,
    ) == "date_only"
    assert extract_entries._is_runtime_owned_field(
        "origine-fr", lexicon=lexicon,
    )
    assert extract_entries._relevance_terms(
        [], [{"type-fr": "personne", "personne": "Marie Curie"}], [],
    ) == ["marie curie", "curie"]


def test_extract_artifact_fails_closed_before_state_detection_when_unavailable(
        monkeypatch) -> None:
    import extract_entries

    monkeypatch.setattr(
        extract_entries._reconciliation_lex, "load", lambda: None,
    )
    out = extract_entries.handle_extract_entries({
        "entries": [{"status": "", "text": "Booking cancelled"}],
        "fields": ["status"],
        "state_markers": {"cancelled": ["Booking cancelled"]},
    })

    assert out["ok"] is False
    assert out["error_class"] == "dependency_unavailable"
    assert out["entries"] == []


def test_extract_handler_uses_one_reconciliation_snapshot(
        isolated_reconciliation_lexicon, monkeypatch) -> None:
    _materialize("fr")
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")
    dl._invalidate()

    import extract_entries

    frozen = reconciliation.load()
    assert frozen is not None
    calls = 0

    def load_once():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("reconciliation snapshot reloaded")
        return frozen

    monkeypatch.setattr(extract_entries._reconciliation_lex, "load", load_once)
    monkeypatch.setattr(
        extract_entries, "call_llm",
        lambda *_args, **_kwargs: (
            '[{"entite": "Budget Atlas"}]',
            {"in_tokens": 1, "out_tokens": 1, "latency_ms": 1},
        ),
    )
    out = extract_entries.handle_extract_entries({
        "entries": ["Fournisseur: Vega"],
        "fields": ["entite"],
        "audit_fields": ["fournisseur"],
    })

    assert out["ok"] is True
    assert out["entries"][0]["fournisseur"] == "Vega"
    assert calls == 1
