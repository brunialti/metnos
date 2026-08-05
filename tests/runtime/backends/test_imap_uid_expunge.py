from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from backends.messages import email_metnos
from backends.messages.email_metnos import _uid_expunge


class _Connection:
    def __init__(self, capabilities, status="OK"):
        self.capabilities = capabilities
        self.status = status
        self.calls = []

    def uid(self, *args):
        self.calls.append(args)
        return self.status, []

    def expunge(self):
        raise AssertionError("folder-wide EXPUNGE must never be called")


def test_uidplus_targets_only_selected_messages():
    conn = _Connection((b"IMAP4rev1", b"UIDPLUS"))
    assert _uid_expunge(conn, ["10", "11"]) is None
    assert conn.calls == [("EXPUNGE", "10,11")]


def test_missing_uidplus_fails_closed_without_folder_expunge():
    conn = _Connection((b"IMAP4rev1",))
    error = _uid_expunge(conn, ["10"])
    assert "UIDPLUS or IMAP4rev2 required" in error
    assert conn.calls == []


def test_imap4rev2_includes_targeted_uid_expunge():
    conn = _Connection((b"IMAP4REV2",))
    assert _uid_expunge(conn, ["10"]) is None
    assert conn.calls == [("EXPUNGE", "10")]


def test_empty_uid_set_needs_no_server_command():
    conn = _Connection((b"UIDPLUS",))
    assert _uid_expunge(conn, []) is None
    assert conn.calls == []


class _MoveConnection:
    def __init__(self, capabilities):
        self.capabilities = capabilities
        self.calls = []
        self.unselected = False
        self.logged_out = False

    def list(self):
        return "OK", [b'(\\Trash) "/" "Trash"']

    def select(self, folder):
        self.calls.append(("SELECT", folder))
        return "OK", []

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"10 11"]
        if command == "FETCH":
            uid = str(args[0])
            return "OK", [(
                b"header",
                f"Message-ID: <message-{uid}@example.test>\r\n".encode(),
            )]
        return "OK", []

    def unselect(self):
        self.unselected = True
        self.calls.append(("UNSELECT",))
        return "OK", []

    def close(self):
        raise AssertionError("CLOSE would expunge unrelated messages")

    def logout(self):
        self.logged_out = True
        return "BYE", []


def test_rev2_move_is_atomic_and_never_closes_selected_mailbox(monkeypatch):
    import mail_client

    conn = _MoveConnection((b"IMAP4REV2",))
    monkeypatch.setattr(mail_client, "open_imap", lambda _account: conn)

    out = email_metnos.move({
        "account": "metnos_system",
        "src_folder": "INBOX",
        "dst_folder": "Trash",
        "uids": ["10", "11"],
    })

    assert out["ok"] is True
    assert out["ok_count"] == 2
    assert [(call[0], call[1]) for call in conn.calls
            if call[0] == "MOVE"] == [("MOVE", "10"), ("MOVE", "11")]
    assert not any(call[0] in {"COPY", "STORE", "EXPUNGE"}
                   for call in conn.calls)
    assert conn.unselected is True
    assert conn.logged_out is True


def test_legacy_server_without_safe_move_fails_before_mutation(monkeypatch):
    import mail_client

    conn = _MoveConnection((b"IMAP4REV1",))
    monkeypatch.setattr(mail_client, "open_imap", lambda _account: conn)

    out = email_metnos.move({
        "src_folder": "INBOX", "dst_folder": "Trash", "uids": ["10"],
    })

    assert out["ok"] is False
    assert out["error_code"] == "ERR_IMAP_SAFE_MOVE_UNSUPPORTED"
    assert not any(call[0] in {"MOVE", "COPY", "STORE", "EXPUNGE"}
                   for call in conn.calls)
    assert conn.unselected is True
