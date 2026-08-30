"""RM-0005: native coverage means valid and current, not merely present."""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

import detection_lexicon as dl


def test_failed_seed_is_retried_instead_of_freezing_partial_state(
    monkeypatch,
) -> None:
    import detection_lexicon_seed as seed

    calls = []

    def flaky_register_all():
        calls.append("seed")
        if len(calls) == 1:
            raise RuntimeError("temporary store failure")

    checks = []
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(seed, "register_all", flaky_register_all)
    monkeypatch.setattr(dl, "_startup_coverage_check", lambda: checks.append(1))

    dl.ensure_seeded()
    assert dl._seeded is False
    assert checks == []

    dl.ensure_seeded()
    assert dl._seeded is True
    assert calls == ["seed", "seed"]
    assert checks == [1]


@pytest.fixture
def fresh_lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(dl, "_cache_data_version", None)
    dl._cache.clear()
    dl._regex_cache.clear()
    dl.ensure_seeded()
    return dl._open()


def test_external_commit_invalidates_cached_manual_surface(
    fresh_lexicon, monkeypatch,
) -> None:
    concept = "test.external.cache"
    dl.register(
        concept, "phrases", match_mode="word", review_policy="manual",
        en=["allow"], it=["consenti"],
    )
    monkeypatch.setattr(dl, "current_lang", lambda: "en")
    dl._invalidate(concept)
    assert "allow" in dl.forms(concept)

    external = sqlite3.connect(str(dl.DB_PATH))
    try:
        external.execute(
            "UPDATE detection_lexicon SET payload='[\"deny\"]',"
            "needs_translation=1 WHERE concept=? AND lang='en'",
            (concept,),
        )
        external.commit()
    finally:
        external.close()

    assert "allow" not in dl.forms(concept)
    assert "deny" not in dl.forms(concept)


def test_writer_cannot_publish_between_resolution_and_cache_store(
    fresh_lexicon, monkeypatch,
) -> None:
    concept = "test.local.cache.race"
    dl.register(
        concept, "phrases", match_mode="word",
        en=["old"], it=["vecchio"],
    )
    monkeypatch.setattr(dl, "current_lang", lambda: "en")
    dl._invalidate(concept)
    entered = threading.Event()
    release = threading.Event()
    writer_started = threading.Event()
    writer_done = threading.Event()
    result: list[list[str]] = []
    original_native = dl._native

    def blocked_native(selected: str, language: str):
        if selected == concept and language == "en" and not entered.is_set():
            entered.set()
            assert release.wait(2)
        return original_native(selected, language)

    monkeypatch.setattr(dl, "_native", blocked_native)
    reader = threading.Thread(target=lambda: result.append(dl.forms(concept)))
    reader.start()
    assert entered.wait(2)

    def write() -> None:
        writer_started.set()
        dl.register(
            concept, "phrases", match_mode="word",
            en=["new"], it=["nuovo"],
        )
        writer_done.set()

    writer = threading.Thread(target=write)
    writer.start()
    assert writer_started.wait(2)
    assert not writer_done.wait(0.05)
    release.set()
    reader.join(2)
    writer.join(2)

    assert result and "old" in result[0]
    assert writer_done.is_set()
    assert "new" in dl.forms(concept)
    assert "old" not in dl.forms(concept)


@pytest.mark.parametrize(
    ("kind", "source", "translated", "reader", "empty"),
    (
        (
            "phrases", ["allow"], ["autoriser"],
            lambda concept: dl.native_ready_forms(
                concept, require_manual=True,
                include_reviewed_baselines=True,
            ),
            [],
        ),
        (
            "regex", [r"\ballow\b"], [r"\bautoriser\b"],
            lambda concept: dl.native_ready_patterns(
                concept, require_manual=True,
                include_reviewed_baselines=True,
            ),
            [],
        ),
        (
            "mapping", {"yes": ["allow"]}, {"yes": ["autoriser"]},
            lambda concept: dl.native_ready_mapping(
                concept, require_manual=True,
                include_reviewed_baselines=True,
            ),
            {},
        ),
    ),
)
def test_native_ready_snapshot_fails_closed_on_concurrent_external_commit(
    fresh_lexicon, monkeypatch, kind, source, translated, reader, empty,
) -> None:
    concept = f"test.native.race.{kind}"
    dl.register(
        concept, kind, match_mode="word", review_policy="manual",
        en=source, it=source,
    )
    dl.mark_for_translation(concept, "fr", source_lang="en")
    dl.set_translated(concept, "fr", translated)
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")
    original_resource = dl.resource_for_language
    committed = False

    def commit_after_native_read(selected, language, **kwargs):
        nonlocal committed
        resource = original_resource(selected, language, **kwargs)
        if selected == concept and language == "fr" and not committed:
            external = sqlite3.connect(str(dl.DB_PATH))
            try:
                external.execute(
                    "UPDATE detection_lexicon SET needs_translation=1 "
                    "WHERE concept=? AND lang='fr'",
                    (concept,),
                )
                external.commit()
            finally:
                external.close()
            committed = True
        return resource

    monkeypatch.setattr(dl, "resource_for_language", commit_after_native_read)

    assert reader(concept) == empty
    assert committed


def test_native_ready_family_fails_closed_on_intermediate_external_commit(
    fresh_lexicon, monkeypatch,
) -> None:
    concepts = {
        "test.family.first": "phrases",
        "test.family.second": "mapping",
    }
    dl.register(
        "test.family.first", "phrases", match_mode="word",
        review_policy="manual", en=["first"], it=["primo"],
    )
    dl.register(
        "test.family.second", "mapping", match_mode="word",
        review_policy="manual", en={"value": ["second"]},
        it={"value": ["secondo"]},
    )
    dl.mark_for_translation("test.family.first", "fr", source_lang="en")
    dl.set_translated("test.family.first", "fr", ["premier"])
    dl.mark_for_translation("test.family.second", "fr", source_lang="en")
    dl.set_translated("test.family.second", "fr", {"value": ["deuxieme"]})
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")

    original_resource = dl.resource_for_language
    committed = False

    def commit_after_first_read(selected, language, **kwargs):
        nonlocal committed
        resource = original_resource(selected, language, **kwargs)
        if selected == "test.family.first" and language == "fr" and not committed:
            external = sqlite3.connect(str(dl.DB_PATH))
            try:
                external.execute(
                    "UPDATE detection_lexicon SET needs_translation=1 "
                    "WHERE concept=? AND lang='fr'",
                    ("test.family.first",),
                )
                external.commit()
            finally:
                external.close()
            committed = True
        return resource

    monkeypatch.setattr(dl, "resource_for_language", commit_after_first_read)

    assert dl.native_ready_family_resources(
        concepts, require_manual=True, include_reviewed_baselines=True,
    ) is None
    assert committed


def test_sites_action_resolver_fails_closed_without_ready_lexicon(
    fresh_lexicon, monkeypatch,
) -> None:
    from playwright_sidecar import action_resolver as resolver

    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl.mark_for_translation("sites.action_verb", "zz", source_lang="en")
    dl._invalidate("sites.action_verb")

    assert resolver._verbs() == {}
    assert resolver.is_goal_navigation_request("click account") is False
    assert resolver.parse_action("click account") == {
        "ok": False, "error_class": "lexicon_unavailable",
    }


def test_sites_action_resolver_has_no_private_fallback(monkeypatch) -> None:
    from playwright_sidecar import action_resolver as resolver

    monkeypatch.setattr(resolver, "_detlex", None)

    assert resolver._verbs() == {}
    assert resolver._target_noise() == ()
    assert resolver.overlay_dismiss_forms() == ()


def _translated_mapping(conn, concept: str, lang: str = "zz") -> dict:
    source = json.loads(conn.execute(
        "SELECT payload FROM detection_lexicon "
        "WHERE concept=? AND lang='en'",
        (concept,),
    ).fetchone()[0])
    dl.mark_for_translation(concept, lang, source_lang="en")
    candidate = {
        key: [f"zz_{index}_{key}"] for index, key in enumerate(source)
    }
    dl.set_translated(concept, lang, candidate)
    return candidate


def test_pending_row_with_stale_payload_is_not_native(
    fresh_lexicon, monkeypatch,
):
    conn = fresh_lexicon
    concept = "notify.request"
    dl.mark_for_translation(concept, "zz", source_lang="en")
    conn.execute(
        "UPDATE detection_lexicon SET payload='[\"stale\"]' "
        "WHERE concept=? AND lang='zz'",
        (concept,),
    )
    conn.commit()

    status = dl.native_resource_status(concept, "zz")

    assert not status["ok"]
    assert "native row is pending" in status["errors"]
    assert concept in dl.verify_coverage("zz")["missing"]

    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate(concept)
    assert "stale" not in dl.forms(concept)
    assert "notify me" in dl.forms(concept)


def test_partial_or_ambiguous_mapping_is_not_native(fresh_lexicon):
    conn = fresh_lexicon
    concept = "notify.channel"
    candidate = _translated_mapping(conn, concept)
    assert dl.has_native(concept, "zz")

    first = next(iter(candidate))
    partial = {first: candidate[first]}
    raw = json.dumps(partial, ensure_ascii=False, sort_keys=True)
    conn.execute(
        "UPDATE detection_lexicon SET payload=?,version_hash=? "
        "WHERE concept=? AND lang='zz'",
        (raw, dl._sha256(raw), concept),
    )
    conn.commit()
    dl._invalidate(concept)
    assert not dl.has_native(concept, "zz")

    source = json.loads(conn.execute(
        "SELECT payload FROM detection_lexicon "
        "WHERE concept=? AND lang='en'",
        (concept,),
    ).fetchone()[0])
    collision = {key: ["same"] for key in source}
    raw = json.dumps(collision, ensure_ascii=False, sort_keys=True)
    conn.execute(
        "UPDATE detection_lexicon SET payload=?,version_hash=? "
        "WHERE concept=? AND lang='zz'",
        (raw, dl._sha256(raw), concept),
    )
    conn.commit()
    dl._invalidate(concept)
    assert not dl.has_native(concept, "zz")


def test_set_translated_rejects_invalid_payload_before_visibility(fresh_lexicon):
    concept = "notify.channel"
    dl.mark_for_translation(concept, "zz", source_lang="en")

    with pytest.raises(ValueError, match="invalid translated payload"):
        dl.set_translated(concept, "zz", {"email": ["courrier"]})

    assert not dl.has_native(concept, "zz")
    assert concept in dl.verify_coverage("zz")["missing"]


def test_source_realign_invalidates_translated_row(fresh_lexicon):
    conn = fresh_lexicon
    concept = "test.mapping.realign"
    dl.register(
        concept, "mapping", match_mode="word",
        en={"one": ["one"], "two": ["two"]},
        it={"one": ["uno"], "two": ["due"]},
    )
    dl.mark_for_translation(concept, "zz", source_lang="en")
    dl.set_translated(concept, "zz", {"one": ["un"], "two": ["deux"]})
    assert dl.has_native(concept, "zz")

    dl.register(
        concept, "mapping", match_mode="word",
        en={"one": ["single"], "two": ["two"]},
        it={"one": ["uno"], "two": ["due"]},
    )

    row = conn.execute(
        "SELECT needs_translation FROM detection_lexicon "
        "WHERE concept=? AND lang='zz'",
        (concept,),
    ).fetchone()
    assert row == (1,)
    assert not dl.has_native(concept, "zz")


def test_identical_seed_repairs_legacy_baseline_provenance(fresh_lexicon):
    conn = fresh_lexicon
    concept = "test.legacy.baseline"
    payloads = {"en": ["legacy"], "it": ["storico"]}
    dl.register(concept, "phrases", match_mode="word", translations=payloads)
    dl.mark_for_translation(concept, "zz", source_lang="en")
    dl.set_translated(concept, "zz", ["zz_ready"])
    conn.execute(
        "UPDATE detection_lexicon SET source_lang=NULL,version_hash='bad',"
        "source_text_hash='bad',needs_translation=1 "
        "WHERE concept=? AND lang IN ('en','it')",
        (concept,),
    )
    conn.commit()

    corrupted = dl.native_resource_status(concept, "en")
    assert not corrupted["ok"]
    assert "baseline source language is invalid" in corrupted["errors"]
    assert "baseline source hash must be empty" in corrupted["errors"]

    dl.register(concept, "phrases", match_mode="word", translations=payloads)

    rows = conn.execute(
        "SELECT lang,source_lang,needs_translation,source_text_hash,version_hash "
        "FROM detection_lexicon WHERE concept=? ORDER BY lang",
        (concept,),
    ).fetchall()
    baselines = [row for row in rows if row[0] in {"en", "it"}]
    assert all(row[0] == row[1] and row[2] == 0 and row[3] is None
               and row[4].startswith("sha256:") for row in baselines)
    # A metadata-only repair cannot stale a still-current translation.
    assert conn.execute(
        "SELECT needs_translation FROM detection_lexicon "
        "WHERE concept=? AND lang='zz'",
        (concept,),
    ).fetchone() == (0,)
    assert set(dl.baseline_languages(concept)) == {"en", "it"}


@pytest.mark.parametrize("source_lang", [None, "missing"])
def test_translated_row_requires_its_exact_declared_source(
    fresh_lexicon, source_lang,
):
    conn = fresh_lexicon
    concept = "notify.channel"
    _translated_mapping(conn, concept)
    conn.execute(
        "UPDATE detection_lexicon SET source_lang=? "
        "WHERE concept=? AND lang='zz'",
        (source_lang, concept),
    )
    conn.commit()

    status = dl.native_resource_status(concept, "zz")

    assert not status["ok"]
    assert (
        "source language is missing" in status["errors"]
        if source_lang is None
        else "source payload is missing" in status["errors"]
    )


def test_runtime_resolver_does_not_serve_nonpending_row_with_bad_hash(
    fresh_lexicon, monkeypatch,
):
    conn = fresh_lexicon
    concept = "notify.request"
    dl.mark_for_translation(concept, "zz", source_lang="en")
    dl.set_translated(concept, "zz", ["zz_ready"])
    conn.execute(
        "UPDATE detection_lexicon SET version_hash='sha256:tampered' "
        "WHERE concept=? AND lang='zz'",
        (concept,),
    )
    conn.commit()
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate(concept)

    assert not dl.has_native(concept, "zz")
    assert "zz_ready" not in dl.forms(concept)
    assert dl.resource_for_language(
        concept, "zz", fallback=False, ready_only=True,
    ) is None


def test_removed_editorial_language_becomes_pending_not_fallback(
    fresh_lexicon, monkeypatch,
):
    conn = fresh_lexicon
    concept = "test.removed.baseline"
    dl.register(
        concept, "phrases", match_mode="word", review_policy="manual",
        translations={"en": ["allow"], "it": ["consenti"]},
    )
    assert set(dl.baseline_languages(concept)) == {"en", "it"}

    dl.register(
        concept, "phrases", match_mode="word", review_policy="manual",
        translations={"en": ["allow"]},
    )
    monkeypatch.setattr(dl, "current_lang", lambda: "en")
    dl._invalidate(concept)

    assert dl.baseline_languages(concept) == ["en"]
    assert dl.forms(concept) == ["allow"]
    assert not dl.has_native(concept, "it")
    assert conn.execute(
        "SELECT needs_translation,source_lang FROM detection_lexicon "
        "WHERE concept=? AND lang='it'",
        (concept,),
    ).fetchone() == (1, "en")

    # Even an immutable legacy store which could not persist that repair must
    # not revive the removed self-sourced row.
    conn.execute(
        "UPDATE detection_lexicon SET needs_translation=0,source_lang='it' "
        "WHERE concept=? AND lang='it'",
        (concept,),
    )
    conn.commit()
    status = dl.native_resource_status(concept, "it")
    assert not status["ok"]
    assert "self-sourced language is not a declared baseline" in status["errors"]


def test_manual_to_automatic_policy_change_converges_for_target(
    fresh_lexicon,
):
    conn = fresh_lexicon
    concept = "test.policy.evolution"
    payloads = {"en": ["allow"], "it": ["consenti"]}
    dl.register(
        concept, "phrases", match_mode="word", review_policy="manual",
        translations=payloads,
    )
    dl.mark_for_translation(concept, "fr", source_lang="en")
    dl.set_translated(concept, "fr", ["autoriser"])
    assert concept in dl.manual_review_concepts()

    dl.register(
        concept, "phrases", match_mode="word", review_policy="automatic",
        translations=payloads,
    )

    assert conn.execute(
        "SELECT review_policy,needs_translation FROM detection_lexicon "
        "WHERE concept=? AND lang='fr'",
        (concept,),
    ).fetchone() == ("automatic", 1)
    assert concept not in dl.manual_review_concepts()
    dl.set_translated(concept, "fr", ["autoriser"])
    assert dl.has_native(concept, "fr")


def test_manual_policy_declarations_do_not_leak_across_store_swap(
    tmp_path, monkeypatch,
):
    first = tmp_path / "first.sqlite"
    second = tmp_path / "second.sqlite"
    monkeypatch.setattr(dl, "_seeded", True)
    monkeypatch.setattr(dl, "_declared_review_policies", {})
    monkeypatch.setattr(dl, "_declared_baseline_languages", {})
    monkeypatch.setattr(dl, "DB_PATH", first)
    monkeypatch.setattr(dl, "_conn", None)

    dl.register(
        "test.first.manual", "phrases", review_policy="manual",
        en=["allow"], it=["consenti"],
    )
    assert dl.manual_review_concepts() == frozenset({"test.first.manual"})
    dl._conn.close()

    monkeypatch.setattr(dl, "DB_PATH", second)
    monkeypatch.setattr(dl, "_conn", None)
    dl.register(
        "test.second.manual", "phrases", review_policy="manual",
        en=["approve"], it=["approva"],
    )

    assert dl.manual_review_concepts() == frozenset({"test.second.manual"})


def test_reviewed_baseline_union_rejects_cross_language_mapping_collision(
    fresh_lexicon, monkeypatch,
):
    concept = "test.security.mapping.union"
    dl.register(
        concept, "mapping", match_mode="word", review_policy="manual",
        translations={
            "en": {"username": ["user"], "password": ["password"]},
            "it": {"username": ["utente"], "password": ["password"]},
        },
    )
    dl.mark_for_translation(concept, "fr", source_lang="en")
    dl.set_translated(
        concept, "fr",
        {"username": ["password"], "password": ["motdepasse"]},
    )
    monkeypatch.setattr(dl, "current_lang", lambda: "fr")

    assert dl.has_native(concept, "fr")
    assert dl.native_ready_mapping(
        concept, require_manual=True, include_reviewed_baselines=True,
    ) == {}


def test_removed_or_pending_source_cannot_authorize_a_target(
    fresh_lexicon,
):
    conn = fresh_lexicon
    concept = "test.stale.source"
    dl.register(
        concept, "phrases", match_mode="word",
        translations={"en": ["allow"], "it": ["consenti"]},
    )
    dl.register(
        concept, "phrases", match_mode="word",
        translations={"en": ["allow"]},
    )

    with pytest.raises(ValueError, match="not an editorial baseline"):
        dl.set_payload(
            concept, "fr", ["autoriser"], kind="phrases",
            match_mode="word", source_lang="it",
        )

    source_raw = conn.execute(
        "SELECT payload FROM detection_lexicon WHERE concept=? AND lang='it'",
        (concept,),
    ).fetchone()[0]
    target_raw = json.dumps(["autoriser"], ensure_ascii=False, sort_keys=True)
    conn.execute(
        "INSERT OR REPLACE INTO detection_lexicon("
        "concept,lang,kind,match_mode,payload,needs_translation,source_lang,"
        "review_policy,version_hash,source_text_hash,updated_at) "
        "VALUES (?,?,?,?,?,0,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (concept, "fr", "phrases", "word", target_raw, "it", "automatic",
         dl._sha256(target_raw), dl._sha256(source_raw)),
    )
    conn.commit()

    status = dl.native_resource_status(concept, "fr")
    assert not status["ok"]
    assert "source language is not an editorial baseline" in status["errors"]
