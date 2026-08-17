"""Regressione turn f069f295: il planner ometteva ``unique_rows`` anche se
la query chiedeva esplicitamente una sola riga per coppia."""
from __future__ import annotations

import detection_lexicon_seed as _seed
import unique_rows_resolver as resolver
from engine.executor import resolve_query_canonical_args

_seed.register_all()

_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {"type": "array"},
        "unique_rows": {"type": "boolean", "default": False},
    },
}


def test_live_query_sets_unique_rows_when_planner_omits_it():
    query = (
        "Trova i file immagine duplicati e crea un foglio con una sola riga "
        "per coppia"
    )
    out = resolver.resolve_unique_rows(
        "create_files_spreadsheet", {"entries": []}, query,
        args_schema=_SCHEMA,
    )
    assert out["unique_rows"] is True


def test_query_constraint_overrides_planner_default_false():
    out = resolver.resolve_unique_rows(
        "any_schema_compatible_tool", {"unique_rows": False},
        "Create unique rows without duplicate rows", args_schema=_SCHEMA,
    )
    assert out["unique_rows"] is True


def test_duplicate_files_alone_does_not_mean_duplicate_rows():
    args = {"entries": []}
    out = resolver.resolve_unique_rows(
        "create_files_spreadsheet", args,
        "Trova tutti i file immagine duplicati", args_schema=_SCHEMA,
    )
    assert out == args


def test_schema_without_capability_is_untouched():
    args = {"entries": []}
    out = resolver.resolve_unique_rows(
        "other_tool", args, "una sola riga per coppia",
        args_schema={"type": "object", "properties": {}},
    )
    assert out == args


def test_registered_query_canonical_pipeline_applies_the_rule():
    out = resolve_query_canonical_args(
        "create_files_spreadsheet", {"entries": []},
        "Voglio una sola riga per coppia, senza righe duplicate",
        args_schema=_SCHEMA,
    )
    assert out["unique_rows"] is True


def test_resolver_is_idempotent():
    args = {"entries": [], "unique_rows": True}
    out = resolver.resolve_unique_rows(
        "create_files_spreadsheet", args, "one row per pair",
        args_schema=_SCHEMA,
    )
    assert out == args
