from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "executors" / "sort_entries"))


def test_date_sort_places_iso_dates_before_relative_text_and_missing() -> None:
    import sort_entries

    out = sort_entries.invoke({
        "entries": [
            {"id": "relative", "due": "18 mesi"},
            {"id": "late", "due": "2026-07-02"},
            {"id": "missing", "due": ""},
            {"id": "early", "due": "2026-06-12"},
            {"id": "relative-2", "due": "20 giorni"},
        ],
        "by": "due", "value_type": "date",
    })

    assert out["ok"] is True
    assert [entry["id"] for entry in out["entries"]] == [
        "early", "late", "relative", "relative-2", "missing"]
    assert out["unparsed_count"] == 2


def test_date_sort_desc_keeps_unparsed_and_missing_after_dates() -> None:
    import sort_entries

    out = sort_entries.invoke({
        "entries": [
            {"id": "text", "due": "fine mese"},
            {"id": "early", "due": "2026-06-12"},
            {"id": "late", "due": "2026-07-02"},
            {"id": "missing"},
        ],
        "by": "due", "value_type": "date", "desc": True,
    })

    assert [entry["id"] for entry in out["entries"]] == [
        "late", "early", "text", "missing"]
