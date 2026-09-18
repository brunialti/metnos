"""HTTP/admin expose semantic outcome instead of equating text with success."""
from __future__ import annotations

import time

import http_routes_admin
from http_routes_agent import _semantic_turn_outcome


def _failed_answer_record() -> dict:
    now = time.time()
    return {
        "turn_id": "functional-failure",
        "ts_start": now - 1,
        "ts_end": now,
        "final_kind": "answer",
        "effect_counts": {
            "items": 0, "mutations": 0, "failures": 1,
        },
        "steps": [{
            "chosen_tool": "find_urls",
            "result": {
                "ok": False,
                "error_class": "search_no_results",
                "entries": [],
            },
        }],
    }


def test_http_outcome_marks_functional_failure_despite_answer_kind():
    record = _failed_answer_record()
    assert record["final_kind"] == "answer"
    assert _semantic_turn_outcome(record) == "failed"


def test_admin_error_counter_uses_semantic_outcome(monkeypatch):
    failed = _failed_answer_record()
    completed = {
        **_failed_answer_record(),
        "turn_id": "completed",
        "effect_counts": {"items": 1, "mutations": 0, "failures": 0},
        "steps": [{
            "chosen_tool": "find_urls",
            "result": {"ok": True, "entries": [{"url": "https://example.test"}]},
        }],
    }
    monkeypatch.setattr(
        http_routes_admin, "_load_recent_turns",
        lambda limit=200: [failed, completed],
    )
    summary = http_routes_admin._summary_turns()
    assert summary["total"] == 2
    assert summary["errors"] == 1
