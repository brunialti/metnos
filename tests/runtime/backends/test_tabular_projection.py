from __future__ import annotations

import sys
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
