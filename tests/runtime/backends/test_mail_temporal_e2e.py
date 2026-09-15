"""Real mail parsing/filtering with a simulated, strictly read-only IMAP peer.

No mail credentials or external servers are used. UID SEARCH follows IMAP's
server-local day buckets; FETCH returns real RFC822 bytes and INTERNALDATE.
"""
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from zoneinfo import ZoneInfo

import pytest

from backends.messages import email_metnos as backend
from time_window_parser import resolve_time_bounds

NOW = datetime(2026, 9, 15, 12, 30, 5, tzinfo=ZoneInfo("Europe/Rome"))


class ReadOnlyMailbox:
    def __init__(self, messages):
        self.messages = {str(index).encode(): row for index, row in enumerate(messages, 1)}
        self.searches = []
        self.fetched = []
        self.closed = self.logged_out = False

    def select(self, folder, readonly):
        assert folder == "INBOX" and readonly is True
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        if command == "SEARCH":
            self.searches.append(args)
            lower = upper = None
            for index, token in enumerate(args):
                if token in {"SINCE", "BEFORE"}:
                    day = resolve_time_bounds(args[index + 1], NOW)[0].date()
                    if token == "SINCE":
                        lower = day
                    else:
                        upper = day
            selected = [uid for uid, row in self.messages.items()
                        if (lower is None or row["received"].date() >= lower)
                        and (upper is None or row["received"].date() < upper)]
            return "OK", [b" ".join(selected)]
        assert command == "FETCH", "a read must never mutate message flags"
        uid, selector = args
        assert "BODY.PEEK[]" in selector and "INTERNALDATE" in selector
        self.fetched.append(uid)
        row = self.messages[uid]
        message = EmailMessage()
        message["Subject"] = row["subject"]
        message["From"] = "fixture@example.invalid"
        message["Date"] = format_datetime(row.get("header_date", row["received"]))
        message["Message-ID"] = f"<{uid.decode()}@fixture.invalid>"
        message.set_content("This is a synthetic mail body, not user data.")
        body = message.as_bytes()
        received = row["received"]
        stamp = backend._imap_date(received.date()) + received.strftime(" %H:%M:%S %z")
        header = f'{uid.decode()} (INTERNALDATE "{stamp}" RFC822.SIZE {len(body)} BODY[] {{{len(body)}}}'.encode()
        return "OK", [(header, body), b")"]

    def close(self):
        self.closed = True

    def logout(self):
        self.logged_out = True


@pytest.fixture
def mailbox(monkeypatch):
    import mail_client

    opened = []
    monkeypatch.setattr(backend, "_window_now", lambda: NOW)
    monkeypatch.setattr(mail_client, "list_known_accounts", lambda: ["fixture"])
    monkeypatch.setattr(mail_client, "resolve_account", lambda name: name)
    def install(rows):
        peer = ReadOnlyMailbox(rows)
        def connect(account):
            assert account == "fixture"
            opened.append(peer)
            return peer
        monkeypatch.setattr(mail_client, "open_imap", connect)
        return peer, opened
    return install


def row(subject, received, **kwargs):
    return {"subject": subject, "received": received, **kwargs}


@pytest.mark.parametrize("expression,duration", [
    ("last-48h", timedelta(hours=48)),
    ("last-2h", timedelta(hours=2)),
    ("last-1h30min", timedelta(minutes=90)),
    ("last-15min", timedelta(minutes=15)),
])
def test_real_rfc822_and_receipt_date_have_exact_rolling_bounds(mailbox, expression, duration):
    start = NOW - duration
    peer, _opened = mailbox([
        row("outside", start - timedelta(seconds=1)),
        row("start included", start, header_date=NOW - timedelta(days=365)),
        row("end included", NOW),
        row("future excluded", NOW + timedelta(seconds=1)),
    ])
    result = backend.read({"account": "fixture", "time_window": expression})
    assert result["ok"] and not result["failed"]
    assert [entry["subject"] for entry in result["entries"]] == ["end included", "start included"]
    assert result["time_bounds"]["start"] == start.isoformat()
    assert result["entries"][1]["received_at"] == start.isoformat()
    assert peer.closed and peer.logged_out


@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Etc/GMT+12", "UTC"])
def test_server_timezone_cannot_hide_a_matching_receipt(mailbox, zone):
    stamp = NOW.replace(hour=0, minute=0, second=1).astimezone(ZoneInfo(zone))
    peer, _opened = mailbox([row("inside local day", stamp)])
    result = backend.read({"account": "fixture", "time_window": "today"})
    assert [entry["subject"] for entry in result["entries"]] == ["inside local day"]
    assert peer.fetched == [b"1"]


def test_explicit_before_scans_past_nonmatching_newer_mail_before_limiting(mailbox):
    end = NOW.replace(hour=10, minute=0, second=0)
    peer, _opened = mailbox([
        row("older match", end - timedelta(minutes=30)),
        row("newest match", end - timedelta(seconds=1)),
        row("exclusive endpoint", end),
        row("too new", NOW),
    ])
    result = backend.read({"account": "fixture", "since": "today", "before": "today@10:00",
                           "max_results": 1, "max_total": 1, "page_size": 1})
    assert [entry["subject"] for entry in result["entries"]] == ["newest match"]
    assert result["time_bounds"]["end_exclusive"] is True
    assert peer.fetched == [b"4", b"3", b"2"]
    assert result["truncated"] is True


def test_scan_limit_remains_bounded_and_partial_is_visible(mailbox, monkeypatch):
    monkeypatch.setattr(backend, "_MAX_RESULTS_CAP", 4)
    peer, _opened = mailbox([
        row("unreached matching mail", NOW.replace(hour=8)),
        *(row(f"outside-{index}", NOW + timedelta(seconds=index)) for index in range(5)),
    ])
    result = backend.read({"account": "fixture", "before": "today@10:00", "max_results": 1})
    assert result["entries"] == []
    assert len(peer.fetched) == 4
    assert result["truncated"] is True and result["available_total"] == 2


@pytest.mark.parametrize("arguments", [
    {"since": 'today) OR ALL'}, {"before": "2026-02-30"},
    {"since": "tomorrow", "before": "yesterday"}, {"time_window": "last--2h"},
])
def test_invalid_bounds_never_connect_to_imap(mailbox, arguments):
    _peer, opened = mailbox([])
    result = backend.read({"account": "fixture", **arguments})
    assert result["ok"] is False and result["error_code"] == "ERR_TIME_WINDOW_INVALID"
    assert opened == []


def test_calendar_day_includes_its_final_fractional_second():
    bounds = backend._read_time_bounds("today", None, None, now=NOW)
    assert backend._outside_time_bounds(
        {"received_at": NOW.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()}, bounds) is False
    assert backend._outside_time_bounds(
        {"received_at": (NOW + timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat()}, bounds) is True


def test_explicit_unbounded_period_keeps_all_dates_within_result_limits(mailbox):
    peer, _opened = mailbox([row("old", NOW - timedelta(days=400)), row("recent", NOW)])
    result = backend.read({"account": "fixture", "time_window": "all"})
    assert result["ok"] is True and len(result["entries"]) == 2
    assert backend._read_time_bounds("all", None, None, now=NOW) == (None, None, False)
    assert peer.closed and peer.logged_out
