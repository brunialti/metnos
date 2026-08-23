"""General per-execution undo outcome contract."""
from __future__ import annotations

from pathlib import Path


def _records(path: Path) -> list[dict]:
    import json

    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_per_execution_outcomes_close_pending_without_domain_switches(
        tmp_path: Path) -> None:
    from undo import UndoLog

    log = UndoLog(tmp_path / "undo.jsonl")
    for suffix, outcome in (
        ("r", "reversible"),
        ("n", "no_effect"),
        ("i", "irreversible"),
    ):
        log.append_pending(
            f"op-{suffix}", f"turn-{suffix}", "generic_executor", {}, {},
            outcome_contract="per_execution",
        )
        log.append_completion(
            f"op-{suffix}",
            {"ok": True, "results": [{}], "_undo": {"outcome": outcome}},
            outcome_contract="per_execution",
        )

    records = _records(log.path)
    assert [record["type"] for record in records] == [
        "pending", "done", "pending", "closed", "pending", "closed",
    ]
    assert log.find_crashed() == []
    assert log.latest_turn_done() == []


def test_closed_turn_blocks_undo_from_reaching_an_older_turn(
        tmp_path: Path) -> None:
    from undo import UndoLog

    log = UndoLog(tmp_path / "undo.jsonl")
    log.append_pending("old", "turn-old", "generic_executor", {}, {})
    log.append_done("old", {"ok": True, "results": [{}]})
    log.append_pending(
        "new", "turn-new", "generic_executor", {}, {},
        outcome_contract="per_execution",
    )
    log.append_completion(
        "new", {"ok": True, "_undo": {"outcome": "no_effect"}},
        outcome_contract="per_execution",
    )

    assert log.latest_turn_done() == []


def test_invalid_conditional_receipt_fails_closed(
        tmp_path: Path) -> None:
    from undo import UndoLog

    log = UndoLog(tmp_path / "undo.jsonl")
    log.append_pending(
        "bad", "turn-bad", "generic_executor", {}, {},
        outcome_contract="per_execution",
    )
    outcome = log.append_completion(
        "bad", {"ok": True, "results": [{}]},
        outcome_contract="per_execution",
    )

    assert outcome == "invalid"
    assert _records(log.path)[-1] == {
        "type": "closed",
        "op_id": "bad",
        "ts": _records(log.path)[-1]["ts"],
        "outcome": "irreversible",
        "reason": "invalid_execution_receipt",
    }
    assert log.find_crashed() == []
