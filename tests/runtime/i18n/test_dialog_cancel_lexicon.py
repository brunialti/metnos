"""RM-0005: one native-ready exact authority for dialog cancellation."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import detection_lexicon as dl
import detection_lexicon_seed_dialog as dialog_lexicon
import dialog_pending


_OWNER = "dialog-cancel-owner"


@pytest.fixture
def isolated_dialog_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(dl, "_cache_data_version", None)
    monkeypatch.setattr(dialog_lexicon, "_registered_target", None)
    monkeypatch.setattr(dialog_pending, "DIALOG_DIR", tmp_path / "dialogs")
    dl._cache.clear()
    dl._regex_cache.clear()
    dialog_lexicon.register_all()
    yield
    if dl._conn is not None:
        dl._conn.close()


def _set_language(monkeypatch, language: str) -> None:
    monkeypatch.setattr(dl, "current_lang", lambda: language)
    dl._invalidate()


def _materialize_french() -> None:
    dl.mark_for_translation(
        dialog_lexicon.DIALOG_CANCEL, "fr", source_lang="en",
    )
    dl.set_translated(dialog_lexicon.DIALOG_CANCEL, "fr", ["annuler"])


def _save_pending(sender: str, dialog_id: str = "dlg-cancel") -> None:
    dialog_pending.save_pending(sender, dialog_id, {
        "dialog_id": dialog_id,
        "executor": "mutating_test_executor",
        "title": "Test",
        "dialog": [{
            "var": "value",
            "prompt": "Value?",
            "schema": {"kind": "text"},
        }],
        "on_complete": {"kind": "mutating_test_callback"},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "owner_user_id": _OWNER,
        "sender_id": sender,
        "values_collected": {},
        "step_index": 0,
        "completed": False,
        "cancelled": False,
    })


@pytest.mark.parametrize("language,text", [
    ("it", "annulla"),
    ("it", "ripristina"),
    ("en", "cancel"),
    ("en", "rollback"),
])
def test_reviewed_it_en_forms_match_exactly(
        isolated_dialog_cancel, monkeypatch, language, text) -> None:
    _set_language(monkeypatch, language)
    assert dialog_lexicon.exact_match(text) is True
    assert dialog_lexicon.exact_match(f"please {text}") is None
    assert dialog_lexicon.exact_match("ordinary value") is False


def test_ready_third_language_is_native_and_pending_is_unavailable(
        isolated_dialog_cancel, monkeypatch) -> None:
    _materialize_french()
    _set_language(monkeypatch, "fr")
    assert dialog_lexicon.exact_match("annuler") is True
    # Reviewed baselines remain additive only after the native gate.
    assert dialog_lexicon.exact_match("cancel") is True

    dl._open().execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='fr'",
        (dialog_lexicon.DIALOG_CANCEL,),
    )
    dl._open().commit()
    dl._invalidate()
    assert dialog_lexicon.exact_match("annuler") is None
    assert dialog_lexicon.exact_match("ordinary value") is None


@pytest.mark.parametrize("consumer", ["http", "telegram"])
def test_pending_grammar_cancels_safely_without_resuming_callback(
        isolated_dialog_cancel, monkeypatch, consumer) -> None:
    _materialize_french()
    _set_language(monkeypatch, "fr")
    dl._open().execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='fr'",
        (dialog_lexicon.DIALOG_CANCEL,),
    )
    dl._open().commit()
    dl._invalidate()

    callback = MagicMock(side_effect=AssertionError("callback resumed"))
    import orchestration
    monkeypatch.setattr(orchestration, "process_completion_callback", callback)

    if consumer == "http":
        sender = "http:host:cancel-test"
        _save_pending(sender)
        import http_routes_agent
        reply = http_routes_agent._apply_dialog_pending(
            sender, "ordinary value", owner_user_id=_OWNER,
        )
    else:
        sender = "telegram:host"
        _save_pending(sender)
        from channels.daemon import ChannelDaemon
        daemon = ChannelDaemon.__new__(ChannelDaemon)
        daemon.channel = MagicMock(name="telegram-channel")
        daemon.channel.name = "telegram"
        reply, completed, summary = daemon._consume_get_inputs_response(
            {
                "kind": "get_inputs_response",
                "dialog_id": "dlg-cancel",
                "sender_for_state": sender,
            },
            "ordinary value",
            actor="host",
            sender_id="42",
            owner_user_id=_OWNER,
        )
        assert completed is False
        assert summary is None

    assert reply
    assert dialog_pending.list_pending(
        sender, owner_user_id=_OWNER,
    ) == []
    callback.assert_not_called()


def test_http_cancel_intercept_uses_ready_third_language(
        isolated_dialog_cancel, monkeypatch) -> None:
    _materialize_french()
    _set_language(monkeypatch, "fr")
    sender = "http:host:french-cancel"
    _save_pending(sender)
    import http_routes_agent
    reply = http_routes_agent._apply_dialog_cancel(
        sender, "annuler", owner_user_id=_OWNER,
    )
    assert reply
    assert dialog_pending.list_pending(
        sender, owner_user_id=_OWNER,
    ) == []


def test_http_cancel_intercept_pending_grammar_never_passes_value_through(
        isolated_dialog_cancel, monkeypatch) -> None:
    _materialize_french()
    _set_language(monkeypatch, "fr")
    dl._open().execute(
        "UPDATE detection_lexicon SET needs_translation=1 "
        "WHERE concept=? AND lang='fr'",
        (dialog_lexicon.DIALOG_CANCEL,),
    )
    dl._open().commit()
    dl._invalidate()
    sender = "http:host:pending-cancel"
    _save_pending(sender)
    import http_routes_agent
    reply = http_routes_agent._apply_dialog_cancel(
        sender, "ordinary value", owner_user_id=_OWNER,
    )
    assert reply
    assert dialog_pending.list_pending(
        sender, owner_user_id=_OWNER,
    ) == []


def test_ambiguous_cancel_text_is_not_consumed_as_a_value(
        isolated_dialog_cancel, monkeypatch) -> None:
    _set_language(monkeypatch, "en")
    sender = "http:host:ambiguous-cancel"
    _save_pending(sender)
    import http_routes_agent
    reply = http_routes_agent._apply_dialog_pending(
        sender, "please cancel this", owner_user_id=_OWNER,
    )
    assert reply
    assert dialog_pending.list_pending(
        sender, owner_user_id=_OWNER,
    ) == []
