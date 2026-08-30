"""Prove RM-0005 mirate per codegen e builtin store_entries."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import detection_lexicon_seed_codegen as seed


def test_codegen_seed_census_preserves_canonical_keys_and_legacy_order(
    monkeypatch,
):
    concepts = {}
    messages = {}

    def capture(concept, kind, **kwargs):
        concepts[concept] = {"kind": kind, **kwargs}
        return True

    monkeypatch.setattr(seed._dl, "register", capture)
    monkeypatch.setattr(
        seed._i18n, "register_key_if_missing",
        lambda key, it, en=None, **_kw: messages.setdefault(key, (it, en)),
    )

    seed.register_all()

    from vocab import ACTIONS, OBJECTS

    assert set(concepts) == {
        "codegen.affinity.object",
        "codegen.affinity.action",
        "codegen.store_entries_affinity",
    }
    objects = concepts["codegen.affinity.object"]
    actions = concepts["codegen.affinity.action"]
    stores = concepts["codegen.store_entries_affinity"]
    assert set(objects["it"]) == set(OBJECTS)
    assert set(actions["it"]) == set(ACTIONS)
    assert objects["it"] == objects["en"] == seed._OBJECT_AFFINITY
    assert actions["it"] == actions["en"] == seed._ACTION_AFFINITY
    assert stores["it"] == stores["en"] == seed._STORE_AFFINITY
    assert objects["it"]["events"] == [
        "appuntamento", "appuntamenti", "agenda", "evento", "eventi",
        "calendario", "calendar", "riunione", "riunioni", "meeting",
        "scadenza", "deadline", "promemoria", "events", "schedule",
    ]
    assert actions["it"]["find"] == ["cerca", "find", "search"]
    assert {
        "MSG_CODEGEN_DESCRIPTION_PROVIDER",
        "MSG_CODEGEN_OAUTH_PROMPT",
        "ERR_STORE_REQUIRED_WRITE",
        "MSG_STORE_WRITE_DESCRIPTION",
    } <= set(messages)


def test_detection_only_registration_does_not_depend_on_output_catalog(
    monkeypatch,
):
    concepts = []
    monkeypatch.setattr(
        seed._dl, "register",
        lambda concept, _kind, **_kwargs: concepts.append(concept),
    )
    monkeypatch.setattr(
        seed._i18n, "register_key_if_missing",
        lambda *_args, **_kwargs: pytest.fail("output catalog was accessed"),
    )

    seed.register_detection()

    assert concepts == [
        "codegen.affinity.object",
        "codegen.affinity.action",
        "codegen.store_entries_affinity",
    ]


def test_default_affinity_keeps_cap_order_and_accepts_a_third_language(
    monkeypatch,
):
    import skill_codegen as codegen

    monkeypatch.setattr(codegen._codegen_seed, "ensure_registered", lambda: None)
    plan = SimpleNamespace(obj="events", verb="find")

    monkeypatch.setattr(
        codegen._detlex, "mapping",
        lambda concept: (
            seed._OBJECT_AFFINITY
            if concept == "codegen.affinity.object"
            else seed._ACTION_AFFINITY
        ),
    )
    assert codegen._default_affinity(plan) == [
        f"cerca {surface}" for surface in seed._OBJECT_AFFINITY["events"]
    ]

    monkeypatch.setattr(
        codegen._detlex, "mapping",
        lambda concept: (
            {"events": ["zz-evento"]}
            if concept == "codegen.affinity.object"
            else {"find": ["zz-cerca"]}
        ),
    )
    assert codegen._default_affinity(plan) == ["zz-cerca zz-evento"]


def test_codegen_runtime_ensure_is_detection_only(
    monkeypatch,
):
    queued = []
    monkeypatch.setattr(seed, "_registered", False)
    monkeypatch.setattr(
        seed, "register_detection", lambda: queued.append(("registered", None)),
    )
    monkeypatch.setattr(
        seed, "_register_output_messages",
        lambda: pytest.fail("output catalog was accessed"),
    )
    monkeypatch.setattr(seed._dl, "current_lang", lambda: "zz")
    monkeypatch.setattr(
        seed._dl, "enqueue_language",
        lambda lang: queued.append(("input", lang)),
    )

    seed.ensure_registered()

    assert queued == [("registered", None), ("input", "zz")]
    assert seed._registered is True


def test_missing_output_translation_is_queued_once(monkeypatch):
    queued = []
    monkeypatch.setattr(
        seed, "_OUTPUT_MESSAGES", {"MSG_TEST": ("sorgente", "source")},
    )
    monkeypatch.setattr(seed._i18n, "normalize_language", lambda lang: lang)
    monkeypatch.setattr(
        seed._i18n, "language_chain", lambda _lang: ("zz", "it"),
    )
    monkeypatch.setattr(
        seed._i18n, "resource_for_language",
        lambda _key, lang, **_kw: {"text": "sorgente"} if lang == "it" else None,
    )
    monkeypatch.setattr(
        seed._i18n, "mark_for_translation",
        lambda key, target, source: queued.append((key, target, source)),
    )

    assert seed._enqueue_output_language("zz") == 1
    assert queued == [("MSG_TEST", "zz", "it")]


def test_codegen_and_store_outputs_resolve_through_i18n(monkeypatch):
    import skill_codegen as codegen
    import store_entries

    monkeypatch.setattr(codegen._codegen_seed, "ensure_registered", lambda: None)

    def translated(key, lang, **kwargs):
        if key.startswith("MSG_CODEGEN_DOMAIN_"):
            return f"{lang}:{key.rsplit('_', 1)[-1].lower()}"
        if key.startswith("MSG_CODEGEN_ACTION_"):
            return f"{lang}:verb"
        return f"{lang}:{key}:{kwargs['verb']}:{kwargs['noun']}"

    monkeypatch.setattr(codegen._i18n, "get_for_language", translated)
    plan = SimpleNamespace(
        verb="read", output_kind="entries", args=[], name="read_events_demo",
        skill_domain="calendar", obj="events",
    )
    it, en = codegen._description_boilerplate(plan)
    assert it.startswith("it:MSG_CODEGEN_DESCRIPTION_PROVIDER:it:verb:it:noun")
    assert en.startswith("en:MSG_CODEGEN_DESCRIPTION_PROVIDER:en:verb:en:noun")

    monkeypatch.setattr(
        store_entries._codegen_seed, "ensure_registered", lambda: None,
    )
    monkeypatch.setattr(
        store_entries._i18n, "get",
        lambda key, **_kwargs: f"zz:{key}",
    )
    result = store_entries.handle_write_entries({"entries": []})
    assert result == {
        "ok": False,
        "error_class": "invalid_args",
        "error": "zz:ERR_STORE_REQUIRED_WRITE",
        "results": [],
    }


def test_technical_status_fields_and_store_affinity_api_stay_stable(monkeypatch):
    import skill_codegen as codegen
    import store_entries

    schema = codegen._output_schema_inline(SimpleNamespace(
        output_kind="results", verb="create",
    ))
    assert "n_created: int" in schema
    assert codegen._STATUS_WORD_BY_VERB["create"] == "created"

    monkeypatch.setattr(
        store_entries._codegen_seed, "ensure_registered", lambda: None,
    )
    monkeypatch.setattr(
        store_entries._detlex, "forms",
        lambda concept: ["zz-archivio"]
        if concept == "codegen.store_entries_affinity" else [],
    )
    assert store_entries._store_affinity() == ["zz-archivio"]
