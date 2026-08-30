from __future__ import annotations

import pytest

import detection_lexicon as dl
from playwright_sidecar import factor_resolvers as fr
from telos_lenses._base import paternalism_check


@pytest.fixture
def lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    dl._cache.clear()
    dl._regex_cache.clear()
    dl.ensure_seeded()
    return dl._open()


def test_factor_grammar_fails_closed_while_target_language_is_pending(
    lexicon, monkeypatch,
) -> None:
    for concept in (
        "sites.factor.email_page", "sites.factor.marker",
        "sites.factor.non_code", "sites.factor.code_pattern",
    ):
        dl.mark_for_translation(concept, "zz", source_lang="en")
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate()

    assert not fr.is_email_factor_page("sent to alice@example.test")
    assert fr._extract_codes("Your verification code is ZX9Q2A") == []


def test_manually_ready_third_language_factor_grammar_is_used(
    lexicon, monkeypatch,
) -> None:
    candidates = {
        "sites.factor.email_page": ["zmail"],
        "sites.factor.marker": ["zverify"],
        "sites.factor.non_code": ["zcode"],
        "sites.factor.code_pattern": [r"\bzcode:\s*([A-Z0-9]{4,12})\b"],
    }
    for concept, payload in candidates.items():
        dl.mark_for_translation(concept, "zz", source_lang="en")
        dl.set_translated(concept, "zz", payload)
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate()

    assert fr.is_email_factor_page("open zmail")
    assert fr._extract_codes("zcode: ZX9Q2A") == ["ZX9Q2A"]


def test_paternalism_guard_is_native_and_fails_closed(
    lexicon, monkeypatch,
) -> None:
    concept = "safety.paternalism_marker"
    assert paternalism_check("tell the user to stop")
    assert not paternalism_check("propose a shorter workflow")

    dl.mark_for_translation(concept, "zz", source_lang="en")
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate(concept)
    assert paternalism_check("ordinary proposal")

    dl.set_translated(concept, "zz", ["zjudge"])
    dl._invalidate(concept)
    assert paternalism_check("zjudge the person")
    assert not paternalism_check("ordinary proposal")
