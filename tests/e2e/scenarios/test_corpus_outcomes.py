"""Regression tests for evidence-based corpus outcome classification."""
from __future__ import annotations

import importlib.util
from pathlib import Path


_EXTRACT_PATH = Path(__file__).resolve().parents[1] / "corpus" / "extract.py"
_SPEC = importlib.util.spec_from_file_location("e2e_corpus_extract", _EXTRACT_PATH)
assert _SPEC and _SPEC.loader
_EXTRACT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EXTRACT)


def _turn(*, result=None, final_kind="answer"):
    steps = [] if result is None else [{
        "chosen_tool": "read_files",
        "result": result,
    }]
    return {"turn_id": "turn-1", "final_kind": final_kind, "steps": steps}


def test_text_answer_without_executor_is_not_inferred_success():
    assert _EXTRACT._infer_success(_turn(), {}) is None


def test_successful_executor_result_is_inferred_success():
    assert _EXTRACT._infer_success(_turn(result={"ok": True}), {}) == 1


def test_failed_executor_result_is_inferred_failure():
    assert _EXTRACT._infer_success(
        _turn(result={"ok": False, "error_class": "not_found"}), {},
    ) == 0


def test_interactive_handoff_is_not_inferred_success():
    assert _EXTRACT._infer_success(
        _turn(result={"ok": True, "decision": "needs_inputs"}), {},
    ) is None


def test_explicit_feedback_has_precedence():
    assert _EXTRACT._infer_success(
        _turn(result={"ok": False}), {"turn-1": "ok"},
    ) == 1
