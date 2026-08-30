from __future__ import annotations

import sqlite3

import pytest

import credential_intake as ci
import detection_lexicon as dl


@pytest.fixture
def lexicon(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    dl._cache.clear()
    dl._regex_cache.clear()
    dl.ensure_seeded()
    return dl._open()


def test_pending_credential_grammar_denies_storage_but_still_redacts(
    lexicon, monkeypatch,
) -> None:
    for concept in (
        "credentials.field_label", "credentials.pair_connector",
        "credentials.redaction_label", "credentials.intake_prefix",
    ):
        dl.mark_for_translation(concept, "zz", source_lang="en")
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate()
    text = "anything=alice something=Secret123"

    assert ci.credential_pair_matches(text, for_storage=True) == ()
    scrubbed, count = ci.scrub_sensitive_text(text)
    assert count >= 2
    assert "alice" not in scrubbed and "Secret123" not in scrubbed


def test_manually_ready_third_language_credential_grammar(
    lexicon, monkeypatch,
) -> None:
    candidates = {
        "credentials.field_label": {
            "username": ["zuser"], "password": ["zsecret"],
        },
        "credentials.pair_connector": ["zand"],
        "credentials.redaction_label": ["zuser", "zsecret"],
        "credentials.intake_prefix": ["zcredentials"],
    }
    for concept, payload in candidates.items():
        dl.mark_for_translation(concept, "zz", source_lang="en")
        dl.set_translated(concept, "zz", payload)
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate()
    text = "zuser=alice zand zsecret=Secret123"

    matches = ci.credential_pair_matches(text, for_storage=True)
    assert len(matches) == 1
    assert ci.is_password_label("zsecret")


def test_field_labels_revoked_mid_snapshot_never_admit_structural_pair(
    lexicon, monkeypatch,
) -> None:
    candidates = {
        "credentials.field_label": {
            "username": ["anything"], "password": ["something"],
        },
        "credentials.pair_connector": ["plus"],
        "credentials.redaction_label": ["anything", "something"],
        "credentials.intake_prefix": ["secrets"],
    }
    for concept, payload in candidates.items():
        dl.mark_for_translation(concept, "zz", source_lang="en")
        dl.set_translated(concept, "zz", payload)
    monkeypatch.setattr(dl, "current_lang", lambda: "zz")
    dl._invalidate()

    original_resource = dl.resource_for_language
    revoked = False

    def revoke_after_field_read(concept, language, **kwargs):
        nonlocal revoked
        resource = original_resource(concept, language, **kwargs)
        if (
            concept == "credentials.field_label"
            and language == "zz"
            and not revoked
        ):
            external = sqlite3.connect(str(dl.DB_PATH))
            try:
                external.execute(
                    "UPDATE detection_lexicon SET needs_translation=1 "
                    "WHERE concept=? AND lang=?",
                    (concept, language),
                )
                external.commit()
            finally:
                external.close()
            revoked = True
        return resource

    monkeypatch.setattr(dl, "resource_for_language", revoke_after_field_read)

    from agent_runtime import extract_credentials

    assert extract_credentials(
        "anything=alice something=Secret123",
    ) == []
    assert revoked
