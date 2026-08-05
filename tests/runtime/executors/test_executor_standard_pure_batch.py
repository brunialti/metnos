"""Behavioral and natural-language gates for the first legacy migration batch."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.filter_entries.filter_entries import invoke as filter_entries  # noqa: E402
from executors.group_entries.group_entries import invoke as group_entries  # noqa: E402
from executors.sort_entries.sort_entries import invoke as sort_entries  # noqa: E402
from executors.compute_entries.compute_entries import invoke as compute_entries  # noqa: E402
from executors.describe_numbers.describe_numbers import invoke as describe_numbers  # noqa: E402
from executors.filter_texts_lines.filter_texts_lines import invoke as filter_texts_lines  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


def test_sort_entries_empty_and_failure_are_distinct() -> None:
    empty = sort_entries({"entries": [], "by": "size"})
    invalid = sort_entries({"entries": "bad", "by": "size"})

    assert empty == {
        "ok": True, "entries": [], "count": 0, "total_input": 0,
        "sorted_by": "size", "desc": False,
    }
    assert invalid["ok"] is False
    assert invalid["error_class"] == "invalid_input"
    assert invalid["error_code"] == "entries_not_list"


def test_sort_entries_keeps_blank_deadlines_at_the_end() -> None:
    result = sort_entries({
        "entries": [
            {"name": "unreadable.pdf", "deadline": ""},
            {"name": "later.pdf", "deadline": "2026-09-30"},
            {"name": "earlier.pdf", "deadline": "2026-08-05"},
        ],
        "by": "deadline",
    })

    assert result["ok"] is True
    assert [entry["name"] for entry in result["entries"]] == [
        "earlier.pdf", "later.pdf", "unreadable.pdf"]


def test_group_entries_reports_mixed_outcome_without_dropping_it() -> None:
    result = group_entries({
        "entries_lists": [[{"url": "a"}], "bad", [{"url": "b"}]],
    })

    assert result["ok"] is True
    assert result["partial"] is True
    assert result["ok_count"] == 2
    assert result["fail_count"] == 1
    assert result["failed"][0]["error_code"] == "entry_list_not_list"


def test_filter_entries_empty_and_failure_are_distinct() -> None:
    empty = filter_entries({"entries": []})
    invalid = filter_entries({"entries": "bad"})

    assert empty["ok"] is True
    assert empty["metadata"]["count_out"] == 0
    assert invalid["ok"] is False
    assert invalid["error_class"] == "invalid_input"
    assert invalid["error_code"] == "entries_not_list"


def test_filter_entries_accepts_find_files_mtime_field() -> None:
    result = filter_entries({
        "entries": [
            {"path": "/tmp/recent.pdf", "mtime": 1_784_535_003.0},
            {"path": "/tmp/old.pdf", "mtime": 1_700_000_000.0},
        ],
        "mtime_after": "2026-05-21T00:00:00+00:00",
        "mtime_before": "2026-07-21T00:00:00+00:00",
    })

    assert result["ok"] is True
    assert [entry["path"] for entry in result["entries"]] == [
        "/tmp/recent.pdf"]
    assert result["metadata"]["dropped"] == 1


def test_filter_entries_still_accepts_list_dirs_mtime_epoch() -> None:
    result = filter_entries({
        "entries": [{"path": "/tmp/a", "mtime_epoch": 1_784_535_003.0}],
        "mtime_after": "2026-05-21T00:00:00+00:00",
    })

    assert result["ok"] is True
    assert len(result["entries"]) == 1


def test_compute_entries_empty_and_failure_are_distinct() -> None:
    empty = compute_entries({"entries": [], "op": "count"})
    invalid = compute_entries({"entries": "bad", "op": "count"})
    invalid_root = compute_entries([])

    assert empty["ok"] is True
    assert empty["value"] == 0
    assert invalid["ok"] is False
    assert invalid["error_class"] == "invalid_input"
    assert invalid["error_code"] == "entries_not_list"
    assert invalid_root["error_code"] == "args_not_object"


def test_describe_numbers_preserves_tolerant_piping_and_explicit_failure() -> None:
    empty = describe_numbers({"values": "not-a-number"})
    invalid_fields = describe_numbers({"values": [1, 2], "fields": ["unknown"]})
    invalid_root = describe_numbers([])

    assert empty["ok"] is True
    assert empty["n"] == 0
    assert invalid_fields["ok"] is False
    assert invalid_fields["error_class"] == "invalid_input"
    assert invalid_fields["error_code"] == "fields_unknown"
    assert invalid_root["error_code"] == "args_not_object"


def test_filter_text_lines_preserves_output_and_reports_invalid_input() -> None:
    result = filter_texts_lines({"content": "keep\ndrop", "substring": "keep"})
    invalid = filter_texts_lines({"content": "x"})
    invalid_root = filter_texts_lines([])

    assert result["ok"] is True
    assert result["lines"] == [{"line": "keep"}]
    assert "entries" not in result
    assert invalid["error_code"] == "filter_missing"
    assert invalid["error_class"] == "invalid_input"
    assert invalid_root["error_code"] == "args_not_object"


@pytest.fixture(scope="module")
def catalog(standard_catalog):
    return standard_catalog


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("voglio prima i tre elementi piu grandi", "sort_entries"),
        ("classifica questi elementi per dimensione", "sort_entries"),
        ("combina i risultati delle due ricerche", "group_entries"),
        ("unisci le due liste eliminando i duplicati", "group_entries"),
        ("tieni soltanto gli elementi con priorita alta", "filter_entries"),
        ("escludi gli elementi etichettati junk", "filter_entries"),
        ("calcola la somma dei valori trovati", "compute_entries"),
        ("quanti elementi distinti ci sono?", "compute_entries"),
        ("fammi un riepilogo statistico di questi numeri", "describe_numbers"),
        ("qual e la mediana e il percentile 95?", "describe_numbers"),
        ("tieni solo le righe che contengono warning", "filter_texts_lines"),
        ("seleziona dal testo le linee che iniziano con errore", "filter_texts_lines"),
    ],
)
def test_materially_different_natural_paraphrases_remain_routable(
    query: str, expected: str, catalog,
) -> None:
    names = [item.name for item in rank(query, catalog, k=8, min_score=1)]
    assert expected in names, (query, names)
