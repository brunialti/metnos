"""Test parser tollerante per tool_call (ADR 0133 grammar-mode +
recovery JSON truncated 15/5/2026)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm_provider import _parse_tool_call_tolerant  # noqa: E402


def test_json_complete_ok():
    out = _parse_tool_call_tolerant(
        '{"name": "get_now", "arguments": {}}'
    )
    assert out == {"name": "get_now", "arguments": {}}


def test_json_complete_with_args():
    out = _parse_tool_call_tolerant(
        '{"name": "read_events", "arguments": {"time_window": "today"}}'
    )
    assert out == {"name": "read_events", "arguments": {"time_window": "today"}}


def test_json_pretty_printed_ok():
    out = _parse_tool_call_tolerant(
        '{\n  "name": "get_now",\n  "arguments": {}\n}'
    )
    assert out == {"name": "get_now", "arguments": {}}


def test_json_truncated_name_only_recovers_with_empty_args():
    """Bug live 15/5/2026: llama.cpp grammar termina prematuramente
    lasciando solo `{"name": "list_tasks"` senza chiusura."""
    out = _parse_tool_call_tolerant('{\n  "name": "list_tasks"')
    assert out == {"name": "list_tasks", "arguments": {}}


def test_json_truncated_with_partial_args():
    out = _parse_tool_call_tolerant(
        '{\n  "name": "read_events"\n  ,\n  "arguments": {}\n}'
    )
    # Anche con virgola mal posizionata (post-key), recovery estrae name+args.
    assert out is not None
    assert out["name"] == "read_events"


def test_json_truncated_in_middle_of_args():
    """Tronco a meta' degli args: il recovery estrae solo name."""
    out = _parse_tool_call_tolerant(
        '{"name": "list_tasks", "arguments": {"sin'
    )
    # Recovery: name presente, args parziale → args={} default
    assert out is not None
    assert out["name"] == "list_tasks"
    assert out["arguments"] == {}


def test_json_truncated_with_valid_args():
    """Args validi presenti seguiti da tronco: estrae entrambi."""
    out = _parse_tool_call_tolerant(
        '{"name": "read_messages", "arguments": {"time_window": "today"}'
    )
    assert out is not None
    assert out["name"] == "read_messages"
    assert out["arguments"].get("time_window") == "today"


def test_empty_text_returns_none():
    assert _parse_tool_call_tolerant("") is None
    assert _parse_tool_call_tolerant("   ") is None


def test_non_json_prose_returns_none():
    """Prosa senza JSON e senza pattern Gemma → None."""
    assert _parse_tool_call_tolerant("Ho cercato i task ma non ho trovato nulla.") is None


def test_invalid_name_format_returns_none():
    """Pattern senza `name` valido (literal misspelled) → None."""
    assert _parse_tool_call_tolerant('{"not_name": "list_tasks"}') is None


def test_gemma_template_still_works():
    out = _parse_tool_call_tolerant(
        '<|tool_call>call:get_now()<tool_call|>'
    )
    assert out is not None
    assert out["name"] == "get_now"
    assert out["arguments"] == {}


def test_gemma_template_with_args():
    out = _parse_tool_call_tolerant(
        '<|tool_call>call:read_events(time_window="tomorrow")<tool_call|>'
    )
    assert out is not None
    assert out["name"] == "read_events"
    assert out["arguments"].get("time_window") == "tomorrow"
