from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest


_RUNTIME = Path(__file__).resolve().parents[3] / "runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from tabular_projection import (  # noqa: E402
    TabularProjectionError,
    project_entries,
)


def _duplicate_entries():
    return [{
        "path": "/srv/Pictures/copia/foto.jpg",
        "duplicate_of": "/srv/Pictures/originali/foto.jpg",
        "size": 1234,
        "sha256": "a" * 64,
        "group_count": 2,
    }]


def test_natural_pair_headers_map_by_meaning_and_derive_names():
    rows = project_entries(
        _duplicate_entries(),
        ["Path File 1", "Nome File 1", "Path File 2", "Nome File 2", "Hash"],
    )
    assert rows == [[
        "Path File 1", "Nome File 1", "Path File 2", "Nome File 2", "Hash",
    ], [
        "/srv/Pictures/copia/foto.jpg", "foto.jpg",
        "/srv/Pictures/originali/foto.jpg", "foto.jpg", "a" * 64,
    ]]


def test_explicit_specs_are_independent_of_display_language():
    rows = project_entries(_duplicate_entries(), column_specs=[
        {"header": "ファイル名", "source": "path", "transform": "basename"},
        {"header": "チェックサム", "source": "sha256"},
    ])
    assert rows == [
        ["ファイル名", "チェックサム"],
        ["foto.jpg", "a" * 64],
    ]


def test_empty_exact_alias_does_not_mask_populated_canonical_field():
    rows = project_entries([{
        "dominio": "files; email",
        "origine": "file:a.pdf; email:m1",
        "domini": "",
        "origini": "",
    }], ["domini", "origini"])
    assert rows == [
        ["domini", "origini"],
        ["files; email", "file:a.pdf; email:m1"],
    ]


def test_ambiguous_headers_fail_closed_instead_of_using_dict_position():
    with pytest.raises(TabularProjectionError) as raised:
        project_entries([{"alpha": 1, "beta": 2}], ["Prima", "Seconda"])
    assert raised.value.unresolved == ("Prima", "Seconda")


def test_explicit_specs_support_windows_basename_on_linux():
    rows = project_entries(
        [{"path": r"C:\Users\Ada\Pictures\foto.png"}],
        column_specs=[{
            "header": "Nome", "source": "path", "transform": "basename",
        }],
    )
    assert rows[1] == ["foto.png"]


def test_producer_roles_map_localized_directory_headers_without_key_order():
    """Presentation labels use roles, not producer dict position or key names."""
    entries = [{
        "copy_location": "/srv/Pictures/copies/foto.jpg",
        "source_location": "/srv/Pictures/originals/foto.jpg",
        "unrelated": 42,
    }]
    roles = [
        {"field": "copy_location", "roles": ["path", "duplicate"]},
        {"field": "source_location", "roles": ["path", "origin"]},
    ]

    rows = project_entries(
        entries, ["Cartella Originale", "Cartella Duplicato"],
        field_roles=roles)

    assert rows == [["Cartella Originale", "Cartella Duplicato"], [
        "/srv/Pictures/originals", "/srv/Pictures/copies",
    ]]


def test_directory_role_without_origin_or_duplicate_remains_ambiguous():
    roles = [
        {"field": "left", "roles": ["path"]},
        {"field": "right", "roles": ["path"]},
    ]
    with pytest.raises(TabularProjectionError):
        project_entries(
            [{"left": "/a/x", "right": "/b/y"}], ["Cartella"],
            field_roles=roles)


def test_duplicate_finder_to_spreadsheet_pipeline_uses_signed_contract():
    """Regression for the live Original-folder/Duplicate-folder request."""
    from backends.files import local
    from engine.executor import _resolve_from_step
    from engine.types import StepRun

    root = Path(__file__).resolve().parents[3]
    manifest = tomllib.loads((
        root / "executors" / "create_files_spreadsheet" / "manifest.toml"
    ).read_text(encoding="utf-8"))
    roles = [
        {"field": "path", "roles": ["path", "duplicate"]},
        {"field": "duplicate_of", "roles": ["path", "origin"]},
    ]
    history = [StepRun(
        step_idx=1, tool="find_files_hash", args={}, ok=True, latency_ms=0,
        result={
            "ok": True,
            "entries": _duplicate_entries(),
            "entry_field_roles": roles,
        })]

    args = _resolve_from_step({
        "from_step": 1,
        "columns": ["Cartella Originale", "Cartella Duplicato"],
    }, history, manifest["args"])
    rows = local._resolve_values(args)

    assert args["field_roles"] == roles
    assert rows == [["Cartella Originale", "Cartella Duplicato"], [
        "/srv/Pictures/originali", "/srv/Pictures/copia",
    ]]


def test_unique_rows_deduplicates_after_directory_projection():
    """Distinct files may collapse to one directory-pair row."""
    from backends.files import local

    entries = _duplicate_entries() + [{
        "path": "/srv/Pictures/copia/altra.jpg",
        "duplicate_of": "/srv/Pictures/originali/altra.jpg",
        "size": 4321,
        "sha256": "b" * 64,
        "group_count": 2,
    }]
    roles = [
        {"field": "path", "roles": ["path", "duplicate"]},
        {"field": "duplicate_of", "roles": ["path", "origin"]},
    ]

    rows = local._resolve_values({
        "entries": entries,
        "columns": ["Cartella Originale", "Cartella Duplicato"],
        "field_roles": roles,
        "unique_rows": True,
    })

    assert rows == [["Cartella Originale", "Cartella Duplicato"], [
        "/srv/Pictures/originali", "/srv/Pictures/copia",
    ]]
    assert local._data_row_count({"entries": entries, "unique_rows": True}, rows) == 1


def test_unique_rows_is_opt_in():
    from backends.files import local

    rows = local._resolve_values({
        "values": [["a"], [1], [1]],
        "unique_rows": False,
    })

    assert rows == [["a"], [1], [1]]
