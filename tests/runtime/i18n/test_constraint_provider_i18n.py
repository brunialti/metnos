from __future__ import annotations

import json

import pytest

import detection_lexicon as dl
from prefilter_strategies import constraint


@pytest.fixture
def lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    dl._cache.clear()
    dl._regex_cache.clear()
    dl.ensure_seeded()
    return dl._open()


def test_constraint_provider_reuses_ready_central_it_en_markers(lexicon) -> None:
    assert constraint._provider_from_query("cerca su Gmail") == "google_workspace"
    assert constraint._provider_from_query("search GitHub issues") == "github"
    assert constraint._provider_from_query("find on Google Photos") == "google_photos"
    assert constraint._provider_from_query("telegram") is None
    assert constraint._extract_constraints(
        "search Gmail", prefer_intent=False,
    )["provider"] == "google_workspace"


def test_constraint_provider_third_language_requires_native_readiness(
    lexicon, monkeypatch,
) -> None:
    concept = "provider.markers"
    source = json.loads(lexicon.execute(
        "SELECT payload FROM detection_lexicon "
        "WHERE concept=? AND lang='en'", (concept,),
    ).fetchone()[0])
    candidate = {
        canonical: [f"zz{index}"]
        for index, canonical in enumerate(source)
    }
    workspace_surface = candidate["_google_workspace"][0]
    dl.mark_for_translation(concept, "zz", source_lang="en")
    dl.set_translated(concept, "zz", candidate)
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate(concept)

    assert constraint._provider_from_query(workspace_surface) == "google_workspace"
    assert constraint._provider_from_query("gmail") == "google_workspace"

    lexicon.execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='zz'", (concept,),
    )
    lexicon.commit()
    dl._invalidate(concept)
    assert constraint._provider_from_query(workspace_surface) is None
    assert constraint._provider_from_query("gmail") is None
    assert constraint._extract_constraints(
        "search Gmail", prefer_intent=False,
    )["provider"] is None

    lexicon.execute(
        "DELETE FROM detection_lexicon WHERE concept=? AND lang='zz'",
        (concept,),
    )
    lexicon.commit()
    dl._invalidate(concept)
    assert constraint._provider_from_query("gmail") is None
