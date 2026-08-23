"""Round-trip contracts for provider-neutral undo receipts."""
from __future__ import annotations


def test_membership_receipt_is_canonical_and_invertible() -> None:
    from state_receipts import inverse_membership_delta, membership_delta

    receipt = membership_delta(["a", "b", "b"], ["b", "c"])

    assert receipt == {
        "members_before": ["a", "b"],
        "members_after": ["b", "c"],
        "members_added": ["c"],
        "members_removed": ["a"],
    }
    assert inverse_membership_delta(receipt) == (["a"], ["c"])


def test_set_messages_records_and_reverses_only_effective_delta(
        monkeypatch) -> None:
    from backends.messages import gmail_google_workspace as gmail

    calls: list[list[str]] = []

    def fake_run(argv, *, executor, args_base):
        calls.append(list(argv))
        if argv[:2] == ["gmail", "get"]:
            return {"id": "m1", "labels": ["INBOX", "OLD"]}, None
        if "--add-labels" in argv and "NEW" in argv:
            return {"id": "m1", "labels": ["INBOX", "NEW"]}, None
        return {"id": "m1", "labels": ["INBOX", "OLD"]}, None

    monkeypatch.setattr(gmail, "_run_gmail", fake_run)

    forward = gmail.labels({
        "message_id": "m1", "add": ["NEW"], "remove": ["OLD"],
    })
    reverse = gmail.reverse_labels(forward)

    assert forward["ok"] is True
    assert forward["results"][0]["membership_receipt"] == {
        "members_before": ["INBOX", "OLD"],
        "members_after": ["INBOX", "NEW"],
        "members_added": ["NEW"],
        "members_removed": ["OLD"],
    }
    assert reverse["ok"] is True and reverse["ok_count"] == 1
    assert calls[-1] == [
        "gmail", "modify", "m1",
        "--add-labels", "OLD", "--remove-labels", "NEW",
    ]
