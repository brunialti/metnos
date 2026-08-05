"""Test loop_detect.py — Strategia E ADR 0133 safety net."""
from __future__ import annotations

import sys
from pathlib import Path


from loop_detect import (  # noqa: E402
    _step_signature, is_repeated_failure, repeated_failure_hint,
)


def _step(tool: str, ok: bool, error_class: str = "",
          error: str = "") -> dict:
    """Helper: costruisce uno step dict con shape compatible con Step."""
    return {
        "chosen_tool": tool,
        "result": {"ok": ok, "error_class": error_class} if error_class
                  else {"ok": ok},
        "error": error,
    }


# --------------------------------------------------------------------------
# _step_signature
# --------------------------------------------------------------------------

def test_signature_ok_step_returns_none():
    s = _step("get_now", ok=True)
    assert _step_signature(s) is None


def test_signature_fail_with_error_class():
    s = _step("get_inputs", ok=False, error_class="invalid_args")
    assert _step_signature(s) == ("get_inputs", "invalid_args")


def test_signature_fail_with_error_field_only():
    s = _step("create_dirs", ok=False, error="malformed_reference")
    assert _step_signature(s) == ("create_dirs", "malformed_reference")


def test_signature_fail_with_error_normalized():
    # step.error con prefisso "<classe>: <dettaglio>" → prende solo la classe
    s = _step("get_inputs", ok=False,
              error="grammar_post_validate: missing title")
    assert _step_signature(s) == ("get_inputs", "grammar_post_validate")


def test_signature_no_tool_returns_none():
    s = {"chosen_tool": "", "result": {"ok": False}}
    assert _step_signature(s) is None


def test_signature_none_step():
    assert _step_signature(None) is None


# --------------------------------------------------------------------------
# is_repeated_failure
# --------------------------------------------------------------------------

def test_repeated_failure_two_identical_fails():
    steps = [
        _step("read_files", ok=True),
        _step("get_inputs", ok=False, error_class="invalid_args"),
        _step("get_inputs", ok=False, error_class="invalid_args"),
    ]
    assert is_repeated_failure(steps, threshold=2)


def test_repeated_failure_three_identical():
    steps = [
        _step("get_inputs", ok=False, error_class="invalid_args"),
        _step("get_inputs", ok=False, error_class="invalid_args"),
        _step("get_inputs", ok=False, error_class="invalid_args"),
    ]
    assert is_repeated_failure(steps, threshold=3)
    # Default soglia 2 cattura comunque (ultimi 2 identici).
    assert is_repeated_failure(steps, threshold=2)


def test_repeated_failure_different_tool_no_match():
    steps = [
        _step("get_inputs", ok=False, error_class="invalid_args"),
        _step("send_messages", ok=False, error_class="invalid_args"),
    ]
    assert not is_repeated_failure(steps, threshold=2)


def test_repeated_failure_same_tool_different_error_class():
    steps = [
        _step("get_inputs", ok=False, error_class="invalid_args"),
        _step("get_inputs", ok=False, error_class="not_found"),
    ]
    assert not is_repeated_failure(steps, threshold=2)


def test_repeated_failure_ok_step_breaks_chain():
    steps = [
        _step("get_inputs", ok=False, error_class="invalid_args"),
        _step("describe_entries", ok=True),
        _step("get_inputs", ok=False, error_class="invalid_args"),
    ]
    # Ultimo + penultimo: penultimo e' OK → no loop.
    assert not is_repeated_failure(steps, threshold=2)


def test_repeated_failure_below_threshold():
    steps = [
        _step("get_inputs", ok=False, error_class="invalid_args"),
    ]
    assert not is_repeated_failure(steps, threshold=2)


def test_repeated_failure_empty_steps():
    assert not is_repeated_failure([], threshold=2)


def test_repeated_failure_threshold_one_invalid():
    # Threshold < 2 e' insensato: ritorna sempre False.
    steps = [_step("x", ok=False, error_class="e")]
    assert not is_repeated_failure(steps, threshold=1)


def test_repeated_failure_default_threshold_two():
    steps = [
        _step("get_inputs", ok=False, error_class="invalid_args"),
        _step("get_inputs", ok=False, error_class="invalid_args"),
    ]
    assert is_repeated_failure(steps)  # default threshold=2


# --------------------------------------------------------------------------
# repeated_failure_hint
# --------------------------------------------------------------------------

def test_hint_includes_tool_and_error_class():
    steps = [
        _step("get_inputs", ok=False, error_class="invalid_args"),
        _step("get_inputs", ok=False, error_class="invalid_args"),
    ]
    h = repeated_failure_hint(steps)
    assert "get_inputs" in h
    assert "invalid_args" in h


def test_hint_empty_when_last_step_ok():
    steps = [_step("x", ok=True)]
    assert repeated_failure_hint(steps) == ""


def test_hint_empty_when_no_steps():
    assert repeated_failure_hint([]) == ""
