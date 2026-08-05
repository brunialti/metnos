"""Authority and behavioural gates for the standardized mail reader."""
from __future__ import annotations

import json
import sys
import threading
import time
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from backends.messages import email_metnos  # noqa: E402


MANIFEST = ROOT / "executors" / "read_messages" / "manifest.toml"
MOVE_MANIFEST = ROOT / "executors" / "move_messages" / "manifest.toml"


def test_manifest_uses_semantic_mail_authority_without_raw_secret_paths() -> None:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["executor_standard"] == "metnos.executor/1.0"
    capabilities = manifest["capabilities"]
    assert any(cap.get("name") == "mail:read" for cap in capabilities)
    assert any(cap.get("name") == "provider:access" for cap in capabilities)
    assert not any(
        cap.get("name") == "fs:read"
        and any("mail" in str(hint) for hint in cap.get("hint", []))
        for cap in capabilities
    )


def test_move_manifest_declares_pipeline_context_projection() -> None:
    manifest = tomllib.loads(MOVE_MANIFEST.read_text(encoding="utf-8"))
    properties = manifest["args"]["properties"]
    assert properties["message_ids"]["from_entries_key"] == "uid"
    assert properties["account"]["from_entries_key"] == "account"
    assert properties["src_folder"]["from_entries_key"] == "folder"
    expected_requirement = {"arg": "client", "values": ["metnos"]}
    assert properties["account"]["from_entries_required"] == expected_requirement
    assert properties["src_folder"]["from_entries_required"] == expected_requirement
    assert "default" not in properties["account"]
    assert "default" not in properties["src_folder"]
    assert "obbligatorio" in properties["account"]["description"]["it"]
    assert "required" in properties["account"]["description"]["en"]
    assert any(
        capability.get("name") == "mail:write"
        for capability in manifest["capabilities"]
    )


def test_unknown_account_fails_before_any_imap_connection(monkeypatch) -> None:
    import mail_client

    monkeypatch.setattr(mail_client, "list_known_accounts", lambda: [])
    monkeypatch.setattr(mail_client, "resolve_account", lambda _name: None)
    monkeypatch.setattr(
        mail_client,
        "open_imap",
        lambda _account: (_ for _ in ()).throw(
            AssertionError("unknown account reached the network")
        ),
    )

    result = email_metnos.read({"account": "ghost"})

    assert result["ok"] is False
    assert result["error_code"] == "ERR_ACCOUNT"


def test_empty_mailbox_is_honest_and_never_returns_or_logs_password(
        monkeypatch, caplog) -> None:
    import imaplib
    import mail_client

    sentinel = "mail-secret-MUST-NOT-LEAK"
    login = {}

    class EmptyMailbox:
        def __init__(self, host, port, **_kwargs):
            login["host"] = host
            login["port"] = port

        def login(self, user, password):
            login["user"] = user
            login["password"] = password
            return "OK", [b""]

        def select(self, _folder, readonly=True):
            assert readonly is True
            return "OK", [b"0"]

        def uid(self, command, *_args):
            assert command == "SEARCH"
            return "OK", [b""]

        def close(self):
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    monkeypatch.setattr(imaplib, "IMAP4_SSL", EmptyMailbox)
    monkeypatch.setattr(mail_client, "list_known_accounts",
                        lambda: ["metnos_system"])
    monkeypatch.setattr(mail_client, "resolve_account", lambda name: name)
    monkeypatch.setattr(mail_client, "_account_creds", lambda _account: {
        "imap_host": "imap.test.invalid",
        "imap_port": 993,
        "smtp_host": "smtp.test.invalid",
        "smtp_port": 465,
        "user": "reader@test.invalid",
        "password": sentinel,
        "verify_tls": True,
    })

    result = email_metnos.read({"account": "metnos_system", "max_results": 1})

    assert result == {
        "ok": True,
        "ok_count": 0,
        "fail_count": 0,
        "entries": [],
        "failed": [],
        "accounts": ["metnos_system"],
    }
    assert login["password"] == sentinel
    assert sentinel not in json.dumps(result, ensure_ascii=False)
    assert sentinel not in caplog.text


def test_multi_account_reads_are_parallel_and_globally_capped(
        monkeypatch) -> None:
    import mail_client

    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "2")
    monkeypatch.setattr(mail_client, "list_known_accounts", lambda: ["a", "b"])
    monkeypatch.setattr(mail_client, "resolve_account", lambda name: name)
    monkeypatch.setattr(mail_client, "open_imap", lambda _account: object())
    monkeypatch.setattr(mail_client, "parse_envelope", lambda *_args: {})
    lock = threading.Lock()
    active = 0
    maximum = 0
    seen_caps = []

    def fake_read(account, _folder, _max_results, _unseen_only,
                  _since, _before, per_account_cap, _page_size,
                  entries, _failed, _time_window, _from_contains,
                  _subject_contains, _body_contains, _open_imap,
                  _parse_envelope):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            seen_caps.append(per_account_cap)
        time.sleep(0.03 if account == "a" else 0.01)
        rows = {
            "a": [
                {"account": "a", "date": "Mon, 01 Jun 2026 10:00:00 +0000"},
                {"account": "a", "date": "Mon, 01 May 2026 10:00:00 +0000"},
            ],
            "b": [
                {"account": "b", "date": "Mon, 01 Jul 2026 10:00:00 +0000"},
                {"account": "b", "date": "Mon, 01 Apr 2026 10:00:00 +0000"},
            ],
        }
        entries.extend(rows[account])
        with lock:
            active -= 1
        return len(rows[account])

    monkeypatch.setattr(email_metnos, "_read_one_account", fake_read)

    result = email_metnos.read({"account": "all", "max_total": 2})

    assert maximum == 2
    assert seen_caps == [2, 2]
    assert [entry["account"] for entry in result["entries"]] == ["b", "a"]
    assert result["ok_count"] == 2
    assert result["available_total"] == 4
    assert result["truncated"] is True


def test_last_three_days_excludes_calendar_day_overhang(monkeypatch) -> None:
    import datetime
    import mail_client

    now = datetime.datetime(2026, 8, 3, 15, 44,
                            tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(email_metnos, "_window_now", lambda: now)
    monkeypatch.setattr(mail_client, "list_known_accounts", lambda: ["a"])
    monkeypatch.setattr(mail_client, "resolve_account", lambda name: name)

    def fake_read(_account, _folder, _max_results, _unseen_only,
                  _since, _before, _per_account_cap, _page_size,
                  entries, _failed, _time_window, _from_contains,
                  _subject_contains, _body_contains, _open_imap,
                  _parse_envelope):
        entries.extend([
            {"subject": "inside", "date": "Fri, 31 Jul 2026 15:45:00 +0000"},
            {"subject": "overhang", "date": "Fri, 31 Jul 2026 00:04:00 +0000"},
            # Preserve an unparsable header instead of silently dropping mail.
            {"subject": "unknown", "date": "not-a-date"},
        ])
        return 3

    monkeypatch.setattr(email_metnos, "_read_one_account", fake_read)
    result = email_metnos.read({"account": "a", "time_window": "last-3d"})

    assert result["ok"] is True
    assert [entry["subject"] for entry in result["entries"]] == [
        "inside", "unknown",
    ]
    assert result["ok_count"] == 2
    assert result.get("available_total", 2) == 2
