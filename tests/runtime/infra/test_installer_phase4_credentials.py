"""Installer phase 4 writes the runtime's canonical encrypted schemas."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from install.phases import phase4_secrets


def test_store_credential_uses_dict_payload_and_never_plaintext_fallback(
        monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setitem(sys.modules, "credentials", SimpleNamespace(
        store=lambda domain, payload: observed.update(
            domain=domain, payload=payload),
    ))
    assert phase4_secrets._store_credential(
        "telegram_bot_token", {"value": "token"},
        description="Telegram token",
    )
    assert observed == {
        "domain": "telegram_bot_token",
        "payload": {"value": "token", "_description": "Telegram token"},
    }
    assert not (tmp_path / "credentials_pending").exists()


def test_mail_dialog_writes_smtp_binding_consumed_by_runtime(monkeypatch):
    answers = {
        "Account label": "work",
        "IMAP server hostname": "imap.example.test",
        "IMAP server port": "993",
        "IMAP username": "person@example.test",
        "IMAP password": "mail-secret",
        "SMTP server hostname": "smtp.example.test",
        "SMTP server port": "465",
    }
    confirmations = iter((True, True, False))
    stored = {}
    monkeypatch.setattr(phase4_secrets.ui, "confirm",
                        lambda *_a, **_k: next(confirmations))
    monkeypatch.setattr(
        phase4_secrets.ui, "ask",
        lambda question, **_kwargs: next(
            value for prefix, value in answers.items()
            if question.startswith(prefix)),
    )
    monkeypatch.setattr(
        phase4_secrets, "_store_credential",
        lambda domain, payload, **_kwargs: (
            stored.update(domain=domain, payload=payload) or True),
    )
    args = SimpleNamespace(yes=False)
    assert phase4_secrets._ask_imap(args) == 1
    assert stored["domain"] == "smtp_work"
    assert stored["payload"] == {
        "user": "person@example.test",
        "password": "mail-secret",
        "imap_host": "imap.example.test",
        "imap_port": 993,
        "smtp_host": "smtp.example.test",
        "smtp_port": 465,
        "verify_tls": True,
    }


def test_provider_key_domain_is_explicit_not_derived(monkeypatch):
    confirmations = iter((True,))
    observed = {}
    monkeypatch.setattr(phase4_secrets.ui, "confirm",
                        lambda *_a, **_k: next(confirmations))
    monkeypatch.setattr(phase4_secrets.ui, "ask",
                        lambda *_a, **_k: "github-token")
    monkeypatch.setattr(
        phase4_secrets, "_store_credential",
        lambda domain, payload, **_kwargs: (
            observed.update(domain=domain, payload=payload) or True),
    )
    assert phase4_secrets._ask_apikey(
        SimpleNamespace(yes=False), "GitHub", "GITHUB_PAT", "github")
    assert observed == {"domain": "github",
                        "payload": {"value": "github-token"}}


def test_noninteractive_install_enables_private_lan_ui(monkeypatch):
    monkeypatch.delenv("METNOS_HTTP_HOST", raising=False)
    assert phase4_secrets._ask_http_host(SimpleNamespace(yes=True)) == "0.0.0.0"


def test_http_host_accepts_only_loopback_or_private_ipv4(monkeypatch):
    monkeypatch.setenv("METNOS_HTTP_HOST", "192.168.50.8")
    assert phase4_secrets._ask_http_host(SimpleNamespace(yes=True)) == "192.168.50.8"

    monkeypatch.setenv("METNOS_HTTP_HOST", "127.4.3.2")
    assert phase4_secrets._ask_http_host(SimpleNamespace(yes=True)) == "127.0.0.1"

    warnings = []
    monkeypatch.setenv("METNOS_HTTP_HOST", "8.8.8.8")
    monkeypatch.setattr(phase4_secrets.ui, "warn", warnings.append)
    assert phase4_secrets._ask_http_host(SimpleNamespace(yes=True)) == "0.0.0.0"
    assert warnings


def test_interactive_install_can_choose_loopback_only(monkeypatch):
    monkeypatch.delenv("METNOS_HTTP_HOST", raising=False)
    monkeypatch.setattr(phase4_secrets.ui, "confirm", lambda *_a, **_k: False)
    monkeypatch.setattr(
        phase4_secrets.ui, "console",
        lambda: SimpleNamespace(print=lambda *_a, **_k: None),
    )
    assert phase4_secrets._ask_http_host(SimpleNamespace(yes=False)) == "127.0.0.1"
