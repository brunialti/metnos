"""RM-0005: native-ready authority for the expensive deep-crawl mode."""
from __future__ import annotations

import pytest

import detection_lexicon as dl
import detection_lexicon_seed_runtime_safety as safety_lexicon
import i18n
from engine.executor import Executor
from engine.types import Framework, StepSpec


@pytest.fixture(autouse=True)
def isolated_detection_store(monkeypatch, tmp_path):
    old_conn = dl._conn
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(dl, "_cache", {})
    monkeypatch.setattr(dl, "_regex_cache", {})
    monkeypatch.setattr(dl, "_cache_data_version", None)
    monkeypatch.setattr(dl, "_coverage_gaps_logged", set())
    monkeypatch.setattr(dl, "_declared_review_policies", {})
    monkeypatch.setattr(dl, "_declared_baseline_languages", {})
    monkeypatch.setattr(safety_lexicon, "_registered_target", None)
    monkeypatch.setattr(i18n, "current_lang", lambda: "it")
    safety_lexicon.register_all()
    yield
    new_conn = dl._conn
    if new_conn is not None and new_conn is not old_conn:
        new_conn.close()


def _observed_modes(query: str) -> tuple[str, str]:
    """Exercise both the parallel preparation and ordinary serial consumer."""
    step = StepSpec(tool="find_urls", args={"query": "x", "mode": "research"})
    executor = Executor(invoke_executor=lambda _tool, _args: {"ok": True})
    prepared = executor._prepare_static_read_args(
        step, query=query, runtime_ctx={},
    )

    invoked: list[dict] = []

    def invoke(_tool: str, args: dict) -> dict:
        invoked.append(dict(args))
        return {"ok": True}

    Executor(invoke_executor=invoke).run(
        Framework(steps=[step]), query=query,
    )
    assert len(invoked) == 1
    return prepared["mode"], invoked[0]["mode"]


def test_deep_crawl_requires_one_ready_native_manual_grammar(monkeypatch) -> None:
    for language, query in (
        ("it", "esplora ricorsivamente tutto il sito"),
        ("en", "explore the entire site recursively"),
    ):
        monkeypatch.setattr(i18n, "current_lang", lambda lang=language: lang)
        dl._invalidate()
        assert _observed_modes(query) == ("research", "research")
        assert _observed_modes("ordinary informational query") == (
            "default", "default",
        )

    concept = safety_lexicon.DEEP_CRAWL_INTENT
    # A missing native row cannot inherit an IT/EN baseline and open the mode.
    monkeypatch.setattr(i18n, "current_lang", lambda: "zz")
    dl._invalidate(concept)
    assert _observed_modes("zzdeep explore the entire site") == (
        "default", "default",
    )

    dl.mark_for_translation(concept, "zz", source_lang="en")
    dl.set_translated(concept, "zz", [r"\bzzdeep\b"])
    dl._invalidate(concept)

    assert _observed_modes("please zzdeep") == ("research", "research")
    # Reviewed baselines remain additive, but only after the native row is ready.
    assert _observed_modes("explore the entire site") == (
        "research", "research",
    )

    dl._open().execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='zz'",
        (concept,),
    )
    dl._open().commit()
    dl._invalidate(concept)
    assert _observed_modes("zzdeep explore the entire site") == (
        "default", "default",
    )
