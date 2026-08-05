"""Canonical encrypted mail-account discovery and transport boundaries."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import mail_client


def test_encrypted_smtp_bindings_are_discovered_for_account_all(monkeypatch):
    monkeypatch.setitem(sys.modules, "credentials", SimpleNamespace(
        list_domains=lambda: [
            "smtp_work", "telegram_bot_token", "smtp_personal", "smtp_",
        ],
    ))

    def account_creds(name):
        if name in {"work", "personal"}:
            return {"user": f"{name}@example.test", "password": "secret"}
        return {"user": "", "password": ""}

    monkeypatch.setattr(mail_client, "_account_creds", account_creds)
    # Avoid depending on host legacy files: only the encrypted namespace is
    # relevant to this assertion.
    monkeypatch.setattr(mail_client._C, "PATH_USER_CONFIG", _AbsentPath())
    assert mail_client.list_known_accounts() == ["personal", "work"]


class _AbsentPath:
    def __truediv__(self, _other):
        return self

    def exists(self):
        return False


def test_read_only_encrypted_account_has_no_invented_smtp_host(monkeypatch):
    monkeypatch.setitem(sys.modules, "credentials", SimpleNamespace(
        load=lambda _domain: {
            "user": "reader@example.test",
            "password": "secret",
            "imap_host": "imap.example.test",
        },
    ))
    account = mail_client._load_from_credentials_store("reader")
    assert account is not None
    assert account["imap_host"] == "imap.example.test"
    assert account["smtp_host"] == ""


def test_smtp_fails_before_network_for_read_only_account(monkeypatch):
    monkeypatch.setattr(mail_client, "_account_creds", lambda _account: {
        "user": "reader@example.test", "password": "secret",
        "imap_host": "imap.example.test", "imap_port": 993,
        "smtp_host": "", "smtp_port": 465, "verify_tls": True,
    })
    with pytest.raises(RuntimeError, match="not configured for SMTP"):
        mail_client.open_smtp("reader")


def test_imap_capabilities_are_refreshed_after_login(monkeypatch):
    """Servers may hide MOVE/UIDPLUS until the session is authenticated."""
    observed = {}

    class AuthenticatedCapabilities:
        def __init__(self, *_args, **_kwargs):
            self.capabilities = ("IMAP4REV1",)
            self.authenticated = False
            observed["connection"] = self

        def login(self, user, password):
            assert user == "reader@example.test"
            assert password == "secret"
            self.authenticated = True
            return "OK", [b""]

        def _get_capabilities(self):
            assert self.authenticated is True
            self.capabilities = ("IMAP4REV1", "MOVE", "UIDPLUS")

    monkeypatch.setattr(mail_client.imaplib, "IMAP4_SSL",
                        AuthenticatedCapabilities)
    monkeypatch.setattr(mail_client, "_account_creds", lambda _account: {
        "user": "reader@example.test", "password": "secret",
        "imap_host": "imap.example.test", "imap_port": 993,
        "smtp_host": "", "smtp_port": 465, "verify_tls": True,
    })

    connection = mail_client.open_imap("reader", attempts=1)

    assert connection is observed["connection"]
    assert connection.capabilities == ("IMAP4REV1", "MOVE", "UIDPLUS")
