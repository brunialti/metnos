from __future__ import annotations

import json
from datetime import timedelta

import pytest

import args_extractor as ae
import detection_lexicon as dl
from time_window_parser import temporal_now


@pytest.fixture
def lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    dl._cache.clear()
    dl._regex_cache.clear()
    dl.ensure_seeded()
    return dl._open()


def test_seed_preserves_date_window_and_home_behavior(lexicon) -> None:
    assert ae._extract_date_keyword("eventi dopodomani") == (
        temporal_now() + timedelta(days=2)
    ).strftime("%Y-%m-%d")
    assert ae._extract_time_window("mail delle ultime 24 ore") == "last-24h"
    assert ae._extract_time_window("files from last 30 days") == "last-30d"
    assert ae._extract_paths("trova in home/Documenti") == ["~/Documenti"]


def test_third_language_uses_materialized_argument_lexicon(
    lexicon, monkeypatch,
) -> None:
    candidates = {
        "args.date_offset": {
            "0": ["zoday"], "-1": ["zesterday"], "1": ["zorrow"],
            "2": ["zafter"], "-2": ["zbefore"],
        },
        "args.time_window": {
            "this-week": ["z week"], "last-week": ["z old week"],
            "next-week": ["z next week"], "this-month": ["z month"],
            "last-7d": ["z seven"], "last-24h": ["z hours"],
        },
        "args.relative_window_unit": {"d": ["zdays"], "h": ["zhours"]},
    }
    for concept, payload in candidates.items():
        dl.mark_for_translation(concept, "zz", source_lang="en")
        dl.set_translated(concept, "zz", payload)
    for concept, payload in {
        "args.relative_window_prefix": ["zlast"],
        "args.home_marker": ["zhome"],
        "args.flag_description_noise": ["znoise"],
    }.items():
        dl.mark_for_translation(concept, "zz", source_lang="en")
        dl.set_translated(concept, "zz", payload)
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate()

    assert ae._extract_date_keyword("evento zorrow") == (
        temporal_now() + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    assert ae._extract_time_window("items z next week") == "next-week"
    assert ae._extract_time_window("items zlast 12 zhours") == "last-12h"
    assert ae._extract_paths("find zhome/Photos") == ["~/Photos"]


def test_pending_home_payload_is_invisible_and_baseline_still_works(
    lexicon, monkeypatch,
) -> None:
    concept = "args.home_marker"
    dl.mark_for_translation(concept, "zz", source_lang="en")
    raw = json.dumps(["zhome"], ensure_ascii=False, sort_keys=True)
    lexicon.execute(
        "UPDATE detection_lexicon SET payload=?,version_hash=? "
        "WHERE concept=? AND lang='zz'",
        (raw, dl._sha256(raw), concept),
    )
    lexicon.commit()
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate()

    assert ae._extract_paths("find zhome/Photos") == []
    assert ae._extract_paths("find home/Photos") == ["~/Photos"]


def test_file_glob_inference_requires_reviewed_native_grammar(
    lexicon, monkeypatch,
) -> None:
    dl.mark_for_translation("args.file_noun", "zz", source_lang="en")
    dl.set_translated("args.file_noun", "zz", ["zzfile"])
    dl.mark_for_translation(
        "args.file_extension_clause", "zz", source_lang="en",
    )
    dl.set_translated(
        "args.file_extension_clause", "zz",
        [r"\bzzfile\s+([A-Za-z0-9]{2,5})\b"],
    )
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate()

    assert ae._extract_file_ext_glob("zzfile PDF") == "*.pdf"
    assert ae._extract_file_ext_glob("zzfile javascript") == "*.js"

    lexicon.execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept='args.file_extension_clause' AND lang='zz'",
    )
    lexicon.commit()
    dl._invalidate("args.file_extension_clause")
    assert ae._extract_file_ext_glob("zzfile PDF") is None

    lexicon.execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept='args.file_noun' AND lang='zz'",
    )
    lexicon.commit()
    dl._invalidate("args.file_noun")
    assert ae._extract_file_ext_glob("zzfile javascript") is None
