from __future__ import annotations

import json
from pathlib import Path

from reliability import build_snapshot, classify_turn, read_turn_records


def _turn(*, kind="answer", tool="read_files", result=None, **extra):
    return {
        "ts_start": 10.0, "ts_end": 10.25, "final_kind": kind,
        "steps": [{"chosen_tool": tool, "result": result or {"ok": True}}],
        "effect_counts": {"items": 1, "failures": 0},
        "metnos_version": "1.2.3", **extra,
    }


def test_classification_uses_structural_outcome_and_canonical_domain():
    assert classify_turn(_turn())["outcome"] == "completed"
    assert classify_turn(_turn())["domain"] == "files"
    partial = _turn(result={"ok": False, "partial": True})
    assert classify_turn(partial)["outcome"] == "partial"
    assert classify_turn(partial)["failure_origin"] == "executor"


def test_false_success_is_a_failure_even_after_user_message_was_corrected():
    item = classify_turn(_turn(false_success_detected=True))
    assert item["outcome"] == "failed"
    assert item["false_success"] is True


def test_answer_with_failed_executor_and_zero_effects_is_failed():
    record = _turn(
        result={"ok": False, "error_class": "search_no_results"},
        effect_counts={"items": 0, "mutations": 0, "failures": 1},
    )
    assert record["final_kind"] == "answer"
    assert classify_turn(record)["outcome"] == "failed"


def test_final_answer_step_does_not_turn_failure_into_partial_success():
    record = _turn(
        result={"ok": False, "error_class": "search_no_results"},
        effect_counts=None,
    )
    record["steps"].append({
        "chosen_tool": "final_answer", "result": {"ok": True},
    })
    assert classify_turn(record)["outcome"] == "failed"


def test_snapshot_contains_no_turn_text_or_identifiers():
    record = _turn(user_query="private", final_message="secret", turn_id="abc")
    payload = build_snapshot([record])
    encoded = json.dumps(payload)
    assert payload["rates"]["completion"] == 1.0
    assert payload["latency_p95_ms_by_domain"]["files"] == 250
    assert "private" not in encoded and "secret" not in encoded and "abc" not in encoded


def test_reader_skips_malformed_and_honours_window(tmp_path: Path):
    path = tmp_path / "2026-08-24.jsonl"
    path.write_text(
        json.dumps(_turn()) + "\nnot-json\n" +
        json.dumps({**_turn(), "ts_start": 30.0}) + "\n",
        encoding="utf-8",
    )
    records, malformed = read_turn_records(tmp_path, since=20.0)
    assert len(records) == 1
    assert malformed == 1
