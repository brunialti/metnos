from __future__ import annotations

from types import SimpleNamespace

from admin import i18n_cli


def test_requested_target_is_noop_without_signed_request(monkeypatch):
    monkeypatch.setattr(
        i18n_cli._C, "read_localization_request", lambda: (None, "missing"),
    )
    assert i18n_cli._requested_target() is None
    assert i18n_cli._advance_requested(20) == {"status": "no_target"}


def test_requested_target_tracks_pending_and_active_nonbootstrap(monkeypatch):
    pending = SimpleNamespace(
        requested_lang="nl", instance_lang="en", state="bootstrap_english",
    )
    monkeypatch.setattr(
        i18n_cli._C, "read_localization_request", lambda: (pending, None),
    )
    assert i18n_cli._requested_target() == "nl"

    active = SimpleNamespace(
        requested_lang=None, instance_lang="pt-br", state="active",
    )
    monkeypatch.setattr(
        i18n_cli._C, "read_localization_request", lambda: (active, None),
    )
    assert i18n_cli._requested_target() == "pt-br"


def test_nightly_wrapper_uses_registry_pipeline_without_language_lists():
    script = (i18n_cli._C.PATH_ROOT / "deploy" / "run_prompts_translator.sh").read_text(
        encoding="utf-8",
    )
    assert "advance-requested" in script
    assert "METNOS_LOCALIZATION_CAP_PER_FIRE" in script
    assert "align-prompts" not in script
    assert "prompts/it" not in script
