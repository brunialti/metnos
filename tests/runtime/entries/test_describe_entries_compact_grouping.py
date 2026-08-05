from __future__ import annotations

import sys
from pathlib import Path

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import describe_entries


def test_compact_structured_report_groups_in_existing_sorted_order() -> None:
    entries = [
        {"entità": "A", "scadenza": "2026-07-01", "conflitto": "x ↔ y"},
        {"entità": "B", "scadenza": "2026-07-01"},
        {"entità": "C", "scadenza": "2026-08-01"},
        {"entità": "D", "scadenza": ""},
    ]

    result = describe_entries.handle_describe_entries({
        "entries": entries,
        "style": "compact",
        "data_kind": "entries",
        "format": "markdown",
        "group_by": "scadenza",
    })

    assert result["ok"] is True
    summary = result["summary"]
    assert summary.count("## scadenza: 2026-07-01") == 1
    assert summary.index("## scadenza: 2026-07-01") < summary.index(
        "## scadenza: 2026-08-01") < summary.index("## scadenza: ?")
    assert "**conflitto**: x ↔ y" in summary
    assert result["item_count"] == 4


def test_compact_structured_report_omits_meaningless_all_unknown_group() -> None:
    result = describe_entries.handle_describe_entries({
        "entries": [
            {"entità": "A", "organizzazione": ""},
            {"entità": "B"},
        ],
        "style": "compact",
        "data_kind": "entries",
        "format": "markdown",
        "group_by": "organizzazione",
    })

    assert result["ok"] is True
    assert "## organizzazione: ?" not in result["summary"]
    assert "**entità**: A" in result["summary"]
    assert "**entità**: B" in result["summary"]
