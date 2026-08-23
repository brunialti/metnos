"""Round-trip contracts for provider-neutral undo receipts."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


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


def test_delete_dirs_round_trip_preserves_tree(
        tmp_path: Path, monkeypatch) -> None:
    from backends.files import local
    from reverse_patterns import apply_patterns

    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    payload = nested / "payload.bin"
    payload.write_bytes(b"\x00metnos\xff")
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "turn-exact-dir")

    forward = local.delete_dirs({"paths": [str(source)], "force": True})

    assert forward["ok"] is True
    assert not source.exists()
    receipt = forward["results"][0]
    assert receipt["src"] == str(source)
    assert Path(receipt["dst"]).is_dir()

    reverse = apply_patterns("restore_archived_directory", {}, forward)

    assert reverse["ok"] is True and reverse["ok_count"] == 1
    assert payload.read_bytes() == b"\x00metnos\xff"
    assert not Path(receipt["dst"]).exists()


def test_directory_archive_refuses_history_nested_in_target_without_mutation(
        tmp_path: Path) -> None:
    from executor_helpers import archive_directory_for_undo

    source = tmp_path / "source"
    source.mkdir()
    history = source / "history"

    try:
        archive_directory_for_undo(
            source, history_dir=history, turn_id="turn-nested-history")
    except OSError as exc:
        assert "inside" in str(exc)
    else:
        raise AssertionError("nested undo store must be refused")

    assert source.is_dir()
    assert not history.exists()


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


def test_remote_swap_receipt_allows_exact_directory_move() -> None:
    from reverse_patterns import build_remote_reverse_calls

    result = build_remote_reverse_calls(
        "restore_archived_directory", {},
        {"results": [{"src": "C:/work/tree", "dst": "C:/undo/tree"}]},
    )

    assert result["unsupported"] == []
    assert result["calls"][0]["executor"] == "move_files"
    assert result["calls"][0]["args"]["allow_dirs"] is True


def test_signed_reverse_patterns_grant_only_their_history_leaves(
        tmp_path: Path, monkeypatch) -> None:
    import sandbox

    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    executor = SimpleNamespace(reverse_pattern=[
        "restore_archived_directory", "restore_blob_backup",
    ])

    paths = sandbox.undo_history_extras(executor, turn_id="turn-receipts")

    assert paths == [
        tmp_path / "history" / "turn-receipts" / "blob",
        tmp_path / "history" / "turn-receipts" / "dirs",
    ]
    assert all(path.is_dir() for path in paths)
