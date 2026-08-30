from __future__ import annotations

import json

import pytest

import detection_lexicon as dl
import google_places_client as google_places
import photon_client as photon


@pytest.fixture
def lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    dl._cache.clear()
    dl._regex_cache.clear()
    dl.ensure_seeded()
    return dl._open()


def test_it_en_osm_category_behavior_is_preserved(lexicon) -> None:
    assert photon._autotag_for_query("farmacie vicine") == "amenity:pharmacy"
    assert photon._autotag_for_query("nearby pharmacies") == "amenity:pharmacy"
    assert photon._autotag_for_query("negozio musica vintage") is None


def test_google_places_reuses_ready_it_en_osm_categories(lexicon) -> None:
    assert google_places._autotype_for_query("farmacie vicine") == "pharmacy"
    assert google_places._autotype_for_query("nearby pharmacies") == "pharmacy"
    assert google_places._autotype_for_query("pizzeria vicina") == "pizza_restaurant"
    assert google_places._autotype_for_query("negozio musica vintage") is None


def test_google_places_third_language_requires_native_readiness(
    lexicon, monkeypatch,
) -> None:
    concept = "geo.osm_tag"
    source = json.loads(lexicon.execute(
        "SELECT payload FROM detection_lexicon "
        "WHERE concept=? AND lang='en'", (concept,),
    ).fetchone()[0])
    candidate = {
        tag: [f"zz{index}"] for index, tag in enumerate(source)
    }
    pharmacy_surface = candidate["amenity:pharmacy"][0]
    dl.mark_for_translation(concept, "zz", source_lang="en")
    dl.set_translated(concept, "zz", candidate)
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate(concept)

    assert google_places._autotype_for_query(pharmacy_surface) == "pharmacy"
    assert google_places._autotype_for_query("pharmacy") == "pharmacy"

    lexicon.execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='zz'", (concept,),
    )
    lexicon.commit()
    dl._invalidate(concept)
    assert google_places._autotype_for_query(pharmacy_surface) is None
    assert google_places._autotype_for_query("pharmacy") is None

    lexicon.execute(
        "DELETE FROM detection_lexicon WHERE concept=? AND lang='zz'",
        (concept,),
    )
    lexicon.commit()
    dl._invalidate(concept)
    assert google_places._autotype_for_query("pharmacy") is None


def test_third_language_category_and_pending_fallback(
    lexicon, monkeypatch,
) -> None:
    concept = "geo.osm_tag"
    source = json.loads(lexicon.execute(
        "SELECT payload FROM detection_lexicon "
        "WHERE concept=? AND lang='en'", (concept,),
    ).fetchone()[0])
    candidate = {
        tag: [f"zz{index}"] for index, tag in enumerate(source)
    }
    pharmacy_surface = candidate["amenity:pharmacy"][0]
    dl.mark_for_translation(concept, "zz", source_lang="en")
    dl.set_translated(concept, "zz", candidate)
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate(concept)
    assert photon._autotag_for_query(pharmacy_surface) == "amenity:pharmacy"

    lexicon.execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='zz'", (concept,),
    )
    lexicon.commit()
    dl._invalidate(concept)
    assert photon._autotag_for_query(pharmacy_surface) is None
    assert photon._autotag_for_query("pharmacy") == "amenity:pharmacy"
