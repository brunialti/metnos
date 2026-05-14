"""Test ADR 0133 — tool_grammar generator GBNF deterministico."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tool_grammar import (  # noqa: E402
    args_complexity, is_complex, generate_tool_grammar, validate_tool_call,
    COMPLEXITY_THRESHOLD,
)


# --------------------------------------------------------------------------
# args_complexity
# --------------------------------------------------------------------------

def test_complexity_empty():
    assert args_complexity({}) == 0


def test_complexity_simple():
    s = {"type": "object", "required": ["x"],
         "properties": {"x": {"type": "string"}}}
    assert args_complexity(s) == 0


def test_complexity_oneof():
    s = {"oneOf": [
        {"type": "object", "properties": {"a": {"type": "string"}}},
        {"type": "object", "properties": {"b": {"type": "integer"}}},
    ]}
    assert args_complexity(s) >= 1


def test_complexity_nested_array_of_objects():
    s = {"type": "object", "properties": {
        "messages": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "body": {"type": "string"},
            }}}}}
    # +1 nested object, +1 array di object
    assert args_complexity(s) >= 2


def test_complexity_capped():
    big = {"type": "object", "properties": {
        f"p{i}": {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        for i in range(50)
    }}
    assert args_complexity(big) <= 100


def test_is_complex_simple_returns_false():
    assert not is_complex({"type": "object", "properties": {
        "x": {"type": "string"}}})


def test_is_complex_polymorphic_returns_true():
    # send_messages reale: array di object con 8+ properties incluse
    # free-form (cc/bcc senza type) + array di object nested.
    s = {"type": "object", "properties": {
        "messages": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "to_user": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "body_html": {"type": "string"},
                "cc": {},
                "bcc": {},
                "attachments": {"type": "array",
                                "items": {"type": "object"}},
            }}}}}
    # COMPLEXITY_THRESHOLD=5; questo schema ha:
    # +1 array di object messages, +1 nested object (depth=2),
    # +1 nested object items, +2 free-form (cc, bcc),
    # +1 array di object attachments. Score >= 5.
    assert is_complex(s)


# --------------------------------------------------------------------------
# generate_tool_grammar - struttura outer
# --------------------------------------------------------------------------

def test_generate_empty_tools():
    g = generate_tool_grammar([])
    assert "root ::= " in g
    assert "json_object" in g


def test_generate_single_tool_get_now():
    tools = [{"name": "get_now",
              "args_schema": {"type": "object", "properties": {
                  "timezone": {"type": "string", "default": "UTC"}}}}]
    g = generate_tool_grammar(tools)
    assert "root ::=" in g
    assert '"\\"get_now\\""' in g  # name enum (literal tool name)
    # Rule names camelCase per bug llama-server underscore (vedi tool_grammar.py).
    assert "argsGetNow ::=" in g
    assert "propGetNowTimezone ::=" in g


def test_generate_multi_tools_have_alternation():
    tools = [
        {"name": "tool_a",
         "args_schema": {"type": "object", "properties": {}}},
        {"name": "tool_b",
         "args_schema": {"type": "object", "required": ["x"],
                          "properties": {"x": {"type": "string"}}}},
    ]
    g = generate_tool_grammar(tools)
    # name enum: literal tool name (con underscore — solo come stringa
    # tra virgolette, non come rule name GBNF).
    assert '"\\"tool_a\\""' in g and '"\\"tool_b\\""' in g
    # rule names camelCase: argsToolA / argsToolB.
    assert "argsToolA" in g and "argsToolB" in g


def test_generate_enum_string_property():
    tools = [{"name": "send_messages",
              "args_schema": {"type": "object",
                               "required": ["messages"],
                               "properties": {
                                   "via_channel": {
                                       "type": "string",
                                       "enum": ["auto", "email", "telegram"]
                                   },
                                   "messages": {"type": "array"}
                               }}}]
    g = generate_tool_grammar(tools)
    # enum vincolato
    assert '"\\"auto\\""' in g
    assert '"\\"email\\""' in g
    assert '"\\"telegram\\""' in g


def test_generate_complex_schema_falls_back_generic():
    # Schema con oneOf top-level: anche se sotto soglia, polymorphism
    # rende grammar puntuale inaffidabile → fallback su jsonObject.
    tools = [{"name": "complex_tool",
              "args_schema": {"oneOf": [
                  {"type": "object", "properties": {"a": {"type": "string"}}},
                  {"type": "object", "properties": {"b": {"type": "integer"}}},
              ]}}]
    g = generate_tool_grammar(tools)
    assert "argsComplexTool ::= jsonObject" in g


def test_generate_array_of_string():
    tools = [{"name": "find_urls",
              "args_schema": {"type": "object",
                               "required": ["urls"],
                               "properties": {"urls": {
                                   "type": "array",
                                   "items": {"type": "string"}}}}}]
    g = generate_tool_grammar(tools)
    # array di string tipizzato — referenzia jsonStr (camelCase)
    assert "jsonStr" in g


def test_grammar_is_deterministic():
    tools = [{"name": "x",
              "args_schema": {"type": "object",
                               "required": ["a"],
                               "properties": {
                                   "a": {"type": "string"},
                                   "b": {"type": "integer"}}}}]
    g1 = generate_tool_grammar(tools)
    g2 = generate_tool_grammar(tools)
    assert g1 == g2


# --------------------------------------------------------------------------
# validate_tool_call (Strategia 3 post-decode)
# --------------------------------------------------------------------------

def _t(name, schema):
    return {"name": name, "args_schema": schema}


def test_validate_missing_name():
    ok, err = validate_tool_call({"arguments": {}}, [_t("x", {})])
    assert not ok
    assert "name" in err.lower()


def test_validate_missing_arguments():
    ok, err = validate_tool_call({"name": "x"}, [_t("x", {})])
    assert not ok
    assert "arguments" in err.lower()


def test_validate_unknown_tool():
    ok, err = validate_tool_call(
        {"name": "zzz", "arguments": {}},
        [_t("x", {"type": "object"})])
    assert not ok
    assert "zzz" in err


def test_validate_required_missing():
    ok, err = validate_tool_call(
        {"name": "x", "arguments": {}},
        [_t("x", {"type": "object", "required": ["a"],
                  "properties": {"a": {"type": "string"}}})])
    assert not ok
    assert "a" in err or "required" in err.lower()


def test_validate_happy_path():
    ok, err = validate_tool_call(
        {"name": "x", "arguments": {"a": "v"}},
        [_t("x", {"type": "object", "required": ["a"],
                  "properties": {"a": {"type": "string"}}})])
    assert ok, err
    assert err == ""


def test_validate_enum_top_level_ok():
    # Post-deep-validation refactor: validate_tool_call NON entra nei
    # nested schemas. Enum violation viene catturato dall'executor
    # built-in validation. Qui: required satisfied → ok.
    ok, err = validate_tool_call(
        {"name": "x", "arguments": {"a": "zzz"}},
        [_t("x", {"type": "object", "properties": {
            "a": {"type": "string", "enum": ["yes", "no"]}}})])
    assert ok, err


# --------------------------------------------------------------------------
# Integration smoke: catalog reale
# --------------------------------------------------------------------------

def test_real_catalog_grammar_generates(tmp_path, monkeypatch):
    """Genera grammar per un sample di 5 tool reali del catalogo."""
    from loader import load_catalog
    cat = load_catalog(verify=True, include_synth=False)
    names = ["get_now", "find_files", "read_files", "create_events",
             "send_messages"]
    tools = [cat.executors[n] for n in names if n in cat.executors]
    assert tools, "catalog mancante per il test"
    g = generate_tool_grammar(tools)
    # struttura outer presente
    assert "root ::=" in g
    assert "args ::=" in g
    # name enum contiene ogni tool
    for t in tools:
        assert f'"\\"{t.name}\\""' in g
