"""Private HTTP settings preserve precedence without implicit opt-in."""
from __future__ import annotations

import pytest

import runtime_settings as settings


@pytest.fixture
def runtime_config(monkeypatch, tmp_path):
    path = tmp_path / "runtime.toml"
    monkeypatch.setattr(settings, "_TOML_PATH", path)
    monkeypatch.setattr(settings, "_CACHE", {})
    monkeypatch.setattr(settings, "_CACHE_MTIME", 0.0)
    monkeypatch.delenv("METNOS_DEFAULT_MAIL_ACCOUNT", raising=False)
    monkeypatch.delenv("METNOS_TELOS_NIGHTLY", raising=False)
    return path


def test_missing_settings_keep_defaults_and_nightly_disabled(runtime_config):
    assert settings.mail_default_account() == "metnos_system"
    assert settings.telos_nightly_enabled() is False


def test_other_existing_settings_do_not_change_new_defaults(runtime_config):
    runtime_config.write_text('[feedback]\nerror_demote_threshold = 5\n', encoding="utf-8")
    assert settings.mail_default_account() == "metnos_system"
    assert settings.telos_nightly_enabled() is False
    assert settings.feedback_error_demote_threshold() == 5


def test_private_toml_settings(runtime_config):
    runtime_config.write_text(
        '[mail]\ndefault_account = "work"\n'
        '[telos]\nnightly_enabled = true\n', encoding="utf-8",
    )
    assert settings.mail_default_account() == "work"
    assert settings.telos_nightly_enabled() is True


def test_environment_overrides_toml(runtime_config, monkeypatch):
    runtime_config.write_text(
        '[mail]\ndefault_account = "work"\n'
        '[telos]\nnightly_enabled = true\n', encoding="utf-8",
    )
    monkeypatch.setenv("METNOS_DEFAULT_MAIL_ACCOUNT", "secondary")
    monkeypatch.setenv("METNOS_TELOS_NIGHTLY", "0")
    assert settings.mail_default_account() == "secondary"
    assert settings.telos_nightly_enabled() is False


@pytest.mark.parametrize("source", ["toml", "environment"])
def test_mail_account_strips_surrounding_whitespace(runtime_config, monkeypatch, source):
    if source == "toml":
        runtime_config.write_text('[mail]\ndefault_account = " work "\n', encoding="utf-8")
    else:
        monkeypatch.setenv("METNOS_DEFAULT_MAIL_ACCOUNT", " work ")
    assert settings.mail_default_account() == "work"


@pytest.mark.parametrize("value", ["", "0", "false", "true", "yes", "on", " 1 "])
def test_nightly_env_preserves_exact_opt_in(runtime_config, monkeypatch, value):
    runtime_config.write_text('[telos]\nnightly_enabled = true\n', encoding="utf-8")
    monkeypatch.setenv("METNOS_TELOS_NIGHTLY", value)
    assert settings.telos_nightly_enabled() is False


def test_nightly_env_one_overrides_persistent_false(runtime_config, monkeypatch):
    runtime_config.write_text('[telos]\nnightly_enabled = false\n', encoding="utf-8")
    monkeypatch.setenv("METNOS_TELOS_NIGHTLY", "1")
    assert settings.telos_nightly_enabled() is True


def test_persistent_nightly_false(runtime_config):
    runtime_config.write_text('[telos]\nnightly_enabled = false\n', encoding="utf-8")
    assert settings.telos_nightly_enabled() is False


@pytest.mark.parametrize("value", ['""', '"  "', "false", "7", "[]", '["work"]', "{}"])
def test_explicit_invalid_mail_setting_never_selects_fallback(runtime_config, value):
    runtime_config.write_text(f'[mail]\ndefault_account = {value}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="mail.default_account must be a nonempty string"):
        settings.mail_default_account()


@pytest.mark.parametrize("value", ["", "  "])
def test_invalid_mail_environment_never_falls_back_to_toml(runtime_config, monkeypatch, value):
    runtime_config.write_text('[mail]\ndefault_account = "work"\n', encoding="utf-8")
    monkeypatch.setenv("METNOS_DEFAULT_MAIL_ACCOUNT", value)
    with pytest.raises(ValueError, match="mail.default_account must be a nonempty string"):
        settings.mail_default_account()


@pytest.mark.parametrize("value", ['"true"', "1", "0", "[]", "[false]", "{}"])
def test_persistent_nightly_requires_boolean(runtime_config, value):
    runtime_config.write_text(f'[telos]\nnightly_enabled = {value}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="telos.nightly_enabled must be a boolean"):
        settings.telos_nightly_enabled()


@pytest.mark.parametrize("getter", [settings.mail_default_account, settings.telos_nightly_enabled])
def test_malformed_toml_is_not_treated_as_absent(runtime_config, getter):
    runtime_config.write_text('[mail]\ndefault_account = "unterminated\n', encoding="utf-8")
    with pytest.raises(ValueError, match="runtime configuration invalid"):
        getter()


def test_explicit_environment_does_not_need_toml(runtime_config, monkeypatch):
    runtime_config.write_text('not valid TOML', encoding="utf-8")
    monkeypatch.setenv("METNOS_DEFAULT_MAIL_ACCOUNT", "work")
    monkeypatch.setenv("METNOS_TELOS_NIGHTLY", "1")
    assert settings.mail_default_account() == "work"
    assert settings.telos_nightly_enabled() is True


@pytest.mark.parametrize("getter", [settings.mail_default_account, settings.telos_nightly_enabled])
def test_existing_config_requires_available_parser(runtime_config, monkeypatch, getter):
    runtime_config.write_text('[mail]\ndefault_account = "work"\n', encoding="utf-8")
    monkeypatch.setattr(settings, "tomllib", None)
    with pytest.raises(ValueError, match="runtime configuration parser unavailable"):
        getter()


@pytest.mark.parametrize("getter", [settings.mail_default_account, settings.telos_nightly_enabled])
def test_directory_is_not_an_absent_config(runtime_config, getter):
    runtime_config.mkdir()
    with pytest.raises(ValueError, match="runtime configuration must be a file"):
        getter()


@pytest.mark.parametrize("getter", [settings.mail_default_account, settings.telos_nightly_enabled])
def test_dangling_config_link_is_not_treated_as_absent(runtime_config, getter):
    runtime_config.symlink_to(runtime_config.parent / "missing.toml")
    with pytest.raises(ValueError, match="runtime configuration must be a file"):
        getter()


def test_dangling_config_link_still_fails_when_parser_unavailable(runtime_config, monkeypatch):
    runtime_config.symlink_to(runtime_config.parent / "missing.toml")
    monkeypatch.setattr(settings, "tomllib", None)
    with pytest.raises(ValueError, match="runtime configuration parser unavailable"):
        settings.mail_default_account()


def test_legacy_getters_keep_existing_parse_failure_behavior(runtime_config):
    runtime_config.write_text('not valid TOML', encoding="utf-8")
    assert settings.feedback_error_demote_threshold() == 3
    with pytest.raises(ValueError, match="runtime configuration invalid"):
        settings.mail_default_account()
