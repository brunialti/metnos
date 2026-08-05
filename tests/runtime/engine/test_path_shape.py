"""Test path_shape.py — fingerprint deterministico delle pipeline (ADR 0122)."""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from path_shape import (
    extract_path_shape,
    is_shape_terminal,
    path_shape_hash,
    steps_to_tools,
    turn_total_ms,
)


def test_path_shape_hash_stable_across_calls():
    steps = [
        {"chosen_tool": "find_urls"},
        {"chosen_tool": "read_urls_html"},
        {"chosen_tool": "describe_entries"},  # excluded
        {"chosen_tool": "final_answer"},      # excluded
    ]
    h1 = path_shape_hash(steps)
    h2 = path_shape_hash(steps)
    assert h1 == h2
    assert len(h1) == 16


def test_path_shape_excludes_describe_and_final():
    a = path_shape_hash([{"chosen_tool": "find_urls"}, {"chosen_tool": "read_urls_html"}])
    b = path_shape_hash([
        {"chosen_tool": "find_urls"},
        {"chosen_tool": "read_urls_html"},
        {"chosen_tool": "describe_entries"},
        {"chosen_tool": "final_answer"},
    ])
    assert a == b


def test_path_shape_skips_errored_steps():
    a = path_shape_hash([{"chosen_tool": "find_urls"}, {"chosen_tool": "read_urls_html"}])
    b = path_shape_hash([
        {"chosen_tool": "find_urls"},
        {"chosen_tool": "find_urls", "error": "rate_limited"},
        {"chosen_tool": "read_urls_html"},
    ])
    assert a == b


def test_path_shape_distinguishes_order():
    a = path_shape_hash([{"chosen_tool": "find_urls"}, {"chosen_tool": "read_urls_html"}])
    b = path_shape_hash([{"chosen_tool": "read_urls_html"}, {"chosen_tool": "find_urls"}])
    assert a != b


def test_path_shape_empty_returns_empty_string():
    assert path_shape_hash([]) == ""
    assert path_shape_hash([{"chosen_tool": "final_answer"}]) == ""


def test_extract_path_shape_returns_tuple():
    rec = {"steps": [{"chosen_tool": "find_files"}, {"chosen_tool": "filter_entries"}]}
    h, n = extract_path_shape(rec)
    assert n == 2
    assert len(h) == 16


def test_turn_total_ms_uses_timestamps():
    rec = {"ts_start": 100.0, "ts_end": 102.5}
    assert turn_total_ms(rec) == 2500


def test_turn_total_ms_falls_back_to_per_step_sum():
    rec = {
        "steps": [
            {"exec_ms": 100, "llm_latency_ms": 200},
            {"exec_ms": 50},
        ]
    }
    # ts_start/ts_end mancanti → fallback somma
    total = turn_total_ms(rec)
    assert total == 350


def test_turn_total_ms_returns_none_if_no_timing():
    rec = {"steps": [{"chosen_tool": "find_urls"}]}
    assert turn_total_ms(rec) is None


def test_is_shape_terminal_true_when_last_is_final_answer():
    steps = [{"chosen_tool": "find_urls"}, {"chosen_tool": "final_answer"}]
    assert is_shape_terminal(steps) is True


def test_is_shape_terminal_false_when_loop_break():
    steps = [{"chosen_tool": "find_urls"}, {"chosen_tool": "find_urls"}]
    assert is_shape_terminal(steps) is False


def test_steps_to_tools_supports_dataclass_like():
    """Verifica che StepLog (con attributi, non dict) sia supportato."""
    class _Step:
        def __init__(self, tool, error=None):
            self.chosen_tool = tool
            self.error = error
    steps = [_Step("find_urls"), _Step("read_urls_html"), _Step("final_answer")]
    tools = steps_to_tools(steps)
    assert tools == ["find_urls", "read_urls_html"]


def test_steps_to_tools_excludes_meta_tools():
    steps = [
        {"chosen_tool": "@uploaded"},  # excluded (upload meta)
        {"chosen_tool": "request_new_executor"},  # excluded (synth meta)
        {"chosen_tool": "scratchpad_read"},  # excluded
        {"chosen_tool": "find_urls"},
    ]
    assert steps_to_tools(steps) == ["find_urls"]
