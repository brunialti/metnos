"""RM-0005 parser families are data-driven and fail safe by family."""
from __future__ import annotations

import copy
import json

import pytest

import compound_decomposer
import detection_lexicon as dl
import detection_lexicon_seed_parsers as parser_seed
import ordering_clause
import recurring_tasks
import time_window_parser
import time_window_resolver


@pytest.fixture
def isolated_parser_lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(parser_seed, "_registered_target", None)
    dl._cache.clear()
    dl._regex_cache.clear()
    dl.ensure_seeded()
    parser_seed.register_all()
    yield
    if dl._conn is not None:
        dl._conn.close()


def _materialize_family(family: str, lang: str, overrides: dict) -> None:
    for concept in parser_seed.FAMILY_CONCEPTS[family]:
        if (concept not in overrides and dl.resource_for_language(
                concept, lang, fallback=False, ready_only=True) is not None):
            continue
        source = dl.resource_for_language(
            concept, "en", fallback=False, ready_only=True,
        )
        assert source is not None
        payload = copy.deepcopy(source["payload"])
        override = overrides.get(concept)
        if isinstance(payload, dict) and override is None:
            payload = {
                canonical: [f"{lang}_{index}_{canonical}"]
                for index, canonical in enumerate(payload)
            }
        elif isinstance(payload, dict) and isinstance(override, dict):
            payload.update(copy.deepcopy(override))
        elif override is not None:
            payload = copy.deepcopy(override)
        dl.mark_for_translation(concept, lang, source_lang="en")
        dl.set_translated(concept, lang, payload)


def test_complete_third_language_drives_each_parser_family(
        isolated_parser_lexicon, monkeypatch):
    _materialize_family("ordering", "fr", {
        "parser.ordering.mode_verb": {
            "group": ["grouper"], "sort": ["trier"],
        },
        "parser.ordering.group_connector": ["par-groupe"],
        "parser.ordering.sort_connector": ["selon"],
        "parser.ordering.field_alias": {"size": ["taille"]},
    })
    _materialize_family("time_resolver", "fr", {
        "parser.time.past_determiner": ["derniers"],
        "parser.time.unit": {
            "h": ["heures"], "d": ["jours"], "w": ["semaines"],
            "m": ["mois"], "y": ["ans"],
        },
        "parser.time.singular_unit": {
            "h": ["heure"], "d": ["jour"], "m": ["mois"], "y": ["an"],
        },
    })
    _materialize_family("time_parser", "fr", {
        "parser.time.range_connector": {"from": ["du"], "to": ["au"]},
    })
    _materialize_family("recurrence", "fr", {
        "parser.recurrence.interrogative": ["combien"],
        "parser.recurrence.quantifier": ["chaque"],
        "parser.recurrence.unit": {
            "half_hour": ["demi heure"], "minute": ["minute"],
            "hour": ["heure"], "day": ["jour"],
        },
        "parser.recurrence.at": ["à"],
    })
    _materialize_family("compound", "fr", {
        "parser.compound.tabular_noun": ["tableau"],
        "parser.compound.with_connector": ["avec"],
        "parser.compound.list_connector": ["et"],
    })
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")
    dl._cache.clear()

    assert ordering_clause.detect("trier les fichiers selon taille") == {
        "mode": "sort", "key_text": "taille", "desc": False,
    }
    assert ordering_clause.resolve_field(
        "taille", [{"size": 7}]) == "size"
    assert time_window_resolver.parse_query_time_window(
        "messages des derniers 3 jours") == "last-3d"
    start, end = time_window_parser.parse_time_window("du 1/2/2026 au 3/2/2026")
    assert start.startswith("2026-02-01T00:00:00")
    assert end.startswith("2026-02-03T23:59:59")
    assert recurring_tasks.parse_recurrence_query(
        "chaque 15 minute vérifier les alertes") == {
            "when": "every_15m",
            "query": "vérifier les alertes",
            "label": "vérifier les alertes",
        }
    assert compound_decomposer.derive_sink_fields(
        "tableau avec nom, montant et ville") == ["nom", "montant", "ville"]


@pytest.mark.parametrize("family,partial_concept,probe", [
    ("ordering", "parser.ordering.mode_verb",
     lambda: ordering_clause.detect("trier selon taille")),
    ("time_resolver", "parser.time.past_determiner",
     lambda: time_window_resolver.parse_query_time_window("derniers 3 jours")),
    ("time_parser", "parser.time.range_connector",
     lambda: time_window_parser.parse_time_window("du 1/2 au 3/2")),
    ("recurrence", "parser.recurrence.quantifier",
     lambda: recurring_tasks.parse_recurrence_query(
         "chaque 5 minutes verifier")),
    ("compound", "parser.compound.tabular_noun",
     lambda: compound_decomposer.derive_sink_fields(
         "tableau avec nom et ville")),
])
def test_partial_third_language_family_is_never_assembled(
        isolated_parser_lexicon, monkeypatch, family, partial_concept, probe):
    source = dl.resource_for_language(
        partial_concept, "en", fallback=False, ready_only=True,
    )
    assert source is not None
    dl.mark_for_translation(partial_concept, "fr", source_lang="en")
    dl.set_translated(partial_concept, "fr", copy.deepcopy(source["payload"]))
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")
    dl._cache.clear()

    assert parser_seed.load_family(family) is None
    if family == "time_parser":
        with pytest.raises(ValueError, match="unknown time_window"):
            probe()
        # Canonical, language-independent grammar remains available.
        assert time_window_parser.parse_time_window("last-1d")
    elif family == "compound":
        assert probe() == []
    else:
        assert probe() is None


def test_registered_forms_are_literal_not_regex(
        isolated_parser_lexicon, monkeypatch):
    _materialize_family("ordering", "fr", {
        "parser.ordering.mode_verb": {
            "group": ["grouper"], "sort": ["tri(er"],
        },
        "parser.ordering.group_connector": ["par-groupe"],
        "parser.ordering.sort_connector": ["selon+"],
    })
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")
    dl._cache.clear()

    assert ordering_clause.detect("tri(er selon+ taille")["key_text"] == "taille"
    assert ordering_clause.detect("trier selon taille") is None


def test_partial_mapping_payload_revokes_the_complete_family(
        isolated_parser_lexicon, monkeypatch):
    _materialize_family("ordering", "fr", {
        "parser.ordering.mode_verb": {
            "group": ["grouper"], "sort": ["trier"],
        },
        "parser.ordering.group_connector": ["par-groupe"],
        "parser.ordering.sort_connector": ["selon"],
    })
    concept = "parser.ordering.field_alias"
    raw = json.dumps({"size": ["taille"]}, ensure_ascii=False, sort_keys=True)
    dl._open().execute(
        "UPDATE detection_lexicon SET payload=?,version_hash=? "
        "WHERE concept=? AND lang='fr'",
        (raw, dl._sha256(raw), concept),
    )
    dl._open().commit()
    dl._invalidate(concept)
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")
    dl._cache.clear()

    assert not dl.native_resource_status(concept, "fr")["ok"]
    assert parser_seed.load_family("ordering") is None
    assert ordering_clause.detect("trier selon taille") is None
