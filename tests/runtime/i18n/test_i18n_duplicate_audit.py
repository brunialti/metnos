from __future__ import annotations

from tests.tools.i18n_duplicate_audit import (
    _cross_language_identical,
    _groups,
    _placeholders,
)


def test_placeholder_extraction_is_stable_for_supported_forms():
    assert _placeholders("{name} ${RUNTIME:actor} {{ count }} %(total)d") == (
        "RUNTIME:actor", "count", "name", "total"
    )


def test_exact_duplicates_are_grouped_by_language_and_key():
    rows = [
        {"key": "A", "lang": "it", "text": "Stesso testo"},
        {"key": "B", "lang": "it", "text": "  stesso  testo "},
        {"key": "A", "lang": "en", "text": "Same"},
    ]
    assert _groups(rows) == [{
        "lang": "it", "normalized": "stesso testo", "keys": ["A", "B"]
    }]


def test_cross_language_identical_is_review_only():
    rows = [
        {"key": "A", "lang": "it", "text": "OK"},
        {"key": "A", "lang": "en", "text": "ok"},
    ]
    assert _cross_language_identical(rows)[0]["entries"] == [
        {"lang": "en", "key": "A"}, {"lang": "it", "key": "A"}
    ]
