"""Optional SMTP settings cannot prevent the HTTP repair surface from starting."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


@pytest.fixture
def startup(monkeypatch, tmp_path):
    import executor_birth_bootstrap as birth
    import metnos_http_server as server
    import runtime_settings as settings

    path = tmp_path / "runtime.toml"
    path.write_text('[mail]\ndefault_account = "work"\n', encoding="utf-8")
    monkeypatch.setattr(settings, "_TOML_PATH", path)
    monkeypatch.setattr(settings, "_CACHE", {})
    monkeypatch.setattr(settings, "_CACHE_MTIME", 0.0)
    monkeypatch.delenv("METNOS_DEFAULT_MAIL_ACCOUNT", raising=False)
    events = []
    monkeypatch.setattr(birth, "require_birth_runtime_before_workers",
                        lambda: events.append("birth"))
    monkeypatch.setattr(server, "ProcessLock", lambda _path: SimpleNamespace(
        acquire=lambda: events.append("lock"),
        release=lambda: events.append("unlock"),
    ))

    async def serve(_host, _port):
        events.append("serve")

    monkeypatch.setattr(server, "_serve", serve)
    return server, birth, path, events


@pytest.mark.parametrize("explicit", [None, "secondary"])
def test_http_does_not_materialize_optional_mail_settings(startup, monkeypatch, explicit):
    server, _birth, _path, events = startup
    if explicit is not None:
        monkeypatch.setenv("METNOS_DEFAULT_MAIL_ACCOUNT", explicit)
    server.run_standalone()
    assert events == ["lock", "serve", "unlock"]
    assert os.environ.get("METNOS_DEFAULT_MAIL_ACCOUNT") == explicit


def test_invalid_mail_setting_does_not_stop_http_or_select_another_account(startup):
    server, _birth, path, events = startup
    path.write_text('[mail]\ndefault_account = false\n', encoding="utf-8")
    server.run_standalone()
    assert events == ["lock", "serve", "unlock"]
    assert "METNOS_DEFAULT_MAIL_ACCOUNT" not in os.environ


def test_runner_leaves_birth_check_to_http_application(startup, monkeypatch):
    server, birth, _path, events = startup

    def refuse():
        raise RuntimeError("birth refused")

    monkeypatch.setattr(birth, "require_birth_runtime_before_workers", refuse)
    server.run_standalone()
    assert events == ["lock", "serve", "unlock"]
    assert "METNOS_DEFAULT_MAIL_ACCOUNT" not in os.environ
