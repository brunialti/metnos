"""HTTP preserves private SMTP settings without exposing the config to children."""
from __future__ import annotations

import os
import subprocess
import sys
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
        # A real, harmless child observes exactly the existing env contract.
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c",
             'import os; print(os.environ["METNOS_DEFAULT_MAIL_ACCOUNT"])'],
            capture_output=True, text=True, check=True, timeout=10,
        )
        events.append(("child", result.stdout.strip()))

    monkeypatch.setattr(server, "_serve", serve)
    return server, birth, path, events


@pytest.mark.parametrize("explicit,expected", [(None, "work"), ("secondary", "secondary")])
def test_birth_then_private_setting_then_workers_and_child(startup, monkeypatch, explicit, expected):
    server, _birth, _path, events = startup
    if explicit is not None:
        monkeypatch.setenv("METNOS_DEFAULT_MAIL_ACCOUNT", explicit)
    server.run_standalone()
    assert events == ["birth", "lock", ("child", expected), "unlock"]
    assert os.environ["METNOS_DEFAULT_MAIL_ACCOUNT"] == expected


def test_invalid_setting_stops_before_workers_without_fallback(startup):
    server, _birth, path, events = startup
    path.write_text('[mail]\ndefault_account = false\n', encoding="utf-8")
    with pytest.raises(ValueError, match="mail.default_account"):
        server.run_standalone()
    assert events == ["birth"]
    assert "METNOS_DEFAULT_MAIL_ACCOUNT" not in os.environ


def test_birth_refusal_prevents_setting_and_workers(startup, monkeypatch):
    server, birth, _path, events = startup

    def refuse():
        raise RuntimeError("birth refused")

    monkeypatch.setattr(birth, "require_birth_runtime_before_workers", refuse)
    with pytest.raises(RuntimeError, match="birth refused"):
        server.run_standalone()
    assert events == []
    assert "METNOS_DEFAULT_MAIL_ACCOUNT" not in os.environ
