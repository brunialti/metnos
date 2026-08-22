"""Regressione turn 39d11458: Luxor era estratto ma il filtro ``name_regex``
lo eliminava perché il campo dinamico si chiamava ``destinazione``."""
from __future__ import annotations

import sys
from pathlib import Path

import filter_field_resolver as resolver
from engine.executor import resolve_query_canonical_args

_FILTER_DIR = Path(__file__).resolve().parents[3] / "executors" / "filter_entries"
sys.path.insert(0, str(_FILTER_DIR))
import filter_entries  # noqa: E402


def _booking_args():
    return {
        "name_regex": "luxor",
        "entries": [{
            "destinazione": "Luxor and Cairo",
            "data_inizio": "2024-12-21",
            "data_fine": "2024-12-26",
            "numero_prenotazioni": "4",
            "_source_title": "Booking.com",
        }],
    }


def test_live_shape_selects_the_only_matching_public_field():
    out = resolver.resolve_filter_field(
        "filter_entries", _booking_args(), "trova prenotazioni Luxor")
    assert "name_regex" not in out
    assert out["where_field"] == "destinazione"
    assert out["where_regex"] == "luxor"
    result = filter_entries.invoke(out)
    assert result["ok"] is True
    assert result["metadata"]["count_out"] == 1
    assert result["entries"][0]["numero_prenotazioni"] == "4"


def test_registered_pipeline_repairs_materialized_entries():
    out = resolve_query_canonical_args(
        "filter_entries", _booking_args(), "trova prenotazioni Luxor")
    assert out["where_field"] == "destinazione"
    assert filter_entries.invoke(out)["metadata"]["count_out"] == 1


def test_selected_collection_scope_is_not_reapplied_to_child_fields():
    entries = [
        {"citta": "Luxor", "stato": "Completata",
         "_source_scope_label": "Luxor and Cairo"},
        {"citta": "Il Cairo", "stato": "Completata",
         "_source_scope_label": "Luxor and Cairo"},
        {"citta": "Cairo International Airport", "stato": "Cancellata",
         "_source_scope_label": "Luxor and Cairo"},
    ]
    args = {"name_regex": "luxor", "entries": entries}

    out = resolver.resolve_filter_field(
        "filter_entries", args, "trova prenotazioni Luxor")

    assert "name_regex" not in out
    assert "where_field" not in out
    result = filter_entries.invoke(out)
    assert result["metadata"]["count_out"] == 3


def test_source_scope_must_cover_every_child_before_filter_is_consumed():
    args = {"name_regex": "luxor", "entries": [
        {"citta": "Luxor", "_source_scope_label": "Luxor and Cairo"},
        {"citta": "Rome"},
    ]}

    out = resolver.resolve_filter_field("filter_entries", args, "")

    assert out["where_field"] == "citta"
    assert out["where_regex"] == "luxor"


def test_existing_name_contract_is_never_reinterpreted():
    args = {"name_regex": "luxor", "entries": [
        {"name": "Rome", "destinazione": "Luxor"},
    ]}
    assert resolver.resolve_filter_field("filter_entries", args, "") == args


def test_explicit_collection_field_repairs_name_regex_and_inherent_kind():
    args = {
        "name_regex": ".*@.*",
        "kind": "contact",
        "entries": [
            {"name": "Ada", "emails": ["ada@example.test"]},
            {"name": "Bruno", "emails": []},
        ],
    }
    out = resolver.resolve_filter_field(
        "filter_entries", args,
        "Find all contacts with an email address")
    assert "name_regex" not in out
    assert "kind" not in out
    assert out["where_field"] == "emails"
    assert out["where_regex"] == ".*@.*"
    assert filter_entries.invoke(out)["metadata"]["count_out"] == 1


def test_semantic_type_maps_to_unique_collection_presence():
    """Exact C3 failure shape: type=email is shorthand for emails non-empty."""
    args = {
        "kind": "contact",
        "type": "email",
        "entries": [
            {"id": "ada", "name": "Ada", "emails": ["ada@example.test"]},
            {"id": "bruno", "name": "Bruno", "emails": []},
            {"id": "cora", "name": "Cora", "emails": ["cora@example.test"]},
        ],
    }
    out = resolver.resolve_filter_field(
        "filter_entries", args, "Find all contacts with an email address")
    assert "type" not in out
    assert "kind" not in out
    assert out["where_field"] == "emails"
    assert out["where_present"] is True
    result = filter_entries.invoke(out)
    assert [entry["id"] for entry in result["entries"]] == ["ada", "cora"]


def test_semantic_type_presence_fails_closed_when_field_is_ambiguous():
    args = {
        "type": "tag",
        "entries": [{"tags": ["x"], "tag": "x"}],
    }
    assert resolver.resolve_filter_field(
        "filter_entries", args, "entries with a tag") == args


def test_ambiguous_matching_fields_fail_closed():
    args = {"name_regex": "luxor", "entries": [
        {"destinazione": "Luxor", "descrizione": "Hotel Luxor"},
    ]}
    assert resolver.resolve_filter_field("filter_entries", args, "") == args


def test_private_metadata_is_not_a_candidate():
    args = {"name_regex": "luxor", "entries": [
        {"destinazione": "Cairo", "_source_url": "https://x.test/luxor"},
    ]}
    assert resolver.resolve_filter_field("filter_entries", args, "") == args


def test_invalid_regex_remains_for_executor_error():
    args = {"name_regex": "[", "entries": [{"destinazione": "Luxor"}]}
    assert resolver.resolve_filter_field("filter_entries", args, "") == args
    result = filter_entries.invoke(args)
    assert result["ok"] is False
    assert result["error_code"] == "invalid_name_regex"


def test_presence_filter_resolves_direct_singular_plural_field():
    entries = [
        {"id": "ada", "emails": ["ada@example.test"]},
        {"id": "bruno", "emails": []},
        {"id": "cora", "emails": ["cora@example.test"]},
    ]
    result = filter_entries.invoke({
        "entries": entries,
        "where_field": "email",
        "where_present": True,
    })
    assert result["ok"] is True
    assert result["metadata"]["criteria"]["where_field"] == "emails"
    assert [entry["id"] for entry in result["entries"]] == ["ada", "cora"]


def test_legacy_not_empty_value_maps_to_typed_presence_filter():
    result = filter_entries.invoke({
        "entries": [{"id": "a", "tags": ["x"]}, {"id": "b", "tags": []}],
        "where_field": "tags",
        "where_value": "not_empty",
    })
    assert result["ok"] is True
    assert result["metadata"]["criteria"]["where_present"] is True
    assert [entry["id"] for entry in result["entries"]] == ["a"]
