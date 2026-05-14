"""Test ADR 0133 — tool_grammar generator GBNF deterministico."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tool_grammar import (  # noqa: E402
    args_complexity, is_complex, generate_tool_grammar, validate_tool_call,
    filter_pool_for_grammar, COMPLEXITY_THRESHOLD,
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
# Discriminated union (14/5/2026): pairTool lega name+args per evitare
# `name=A + args=argsB` mix-match. Bug live: get_inputs cross-pollinated
# con argsFilterEntries (kind=choice top-level).
# --------------------------------------------------------------------------

def test_discriminated_union_pair_per_tool():
    tools = [
        {"name": "get_inputs",
         "args_schema": {"type": "object", "required": ["title", "dialog"],
                          "properties": {
                              "title": {"type": "string"},
                              "dialog": {"type": "array"}}}},
        {"name": "filter_entries",
         "args_schema": {"type": "object",
                          "properties": {
                              "kind": {"type": "string"},
                              "from_step": {"type": "integer"}}}},
    ]
    g = generate_tool_grammar(tools)
    # Ogni tool ha la sua pair rule che lega name+args.
    assert "pairGetInputs ::=" in g
    assert "pairFilterEntries ::=" in g
    # Root ha alternation di pair (non di name/args separate).
    assert "pairGetInputs | pairFilterEntries" in g
    # Nessun blocco `name ::=` o `args ::=` globale.
    assert "\nname ::=" not in g
    assert "\nargs ::=" not in g


def test_discriminated_prevents_cross_pollination():
    # Verifica che la grammar accetti SOLO la coppia tool+args coerente.
    # Property: la stringa `pairA ::= "\"a\"" sep "\"arguments\"" colon
    # (argsA)` lega A → argsA. La presenza di pairB e' a fianco non
    # in conflitto perche' pair* sono alternative al toplevel.
    tools = [
        {"name": "a",
         "args_schema": {"type": "object", "required": ["x"],
                          "properties": {"x": {"type": "string"}}}},
        {"name": "b",
         "args_schema": {"type": "object", "required": ["y"],
                          "properties": {"y": {"type": "integer"}}}},
    ]
    g = generate_tool_grammar(tools)
    # pair rules referenziano IL LORO args_rule, non l'altro.
    assert "pairA ::= \"\\\"a\\\"\" sep \"\\\"arguments\\\"\" colon (argsA)" in g
    assert "pairB ::= \"\\\"b\\\"\" sep \"\\\"arguments\\\"\" colon (argsB)" in g


# --------------------------------------------------------------------------
# B2 recursive (14/5/2026): emit nested object/array-of-object via
# sub-rule camelCase con prefix tool per univocita' cross-tool.
# --------------------------------------------------------------------------

def test_b2_recursive_array_of_object_with_properties():
    # Caso get_inputs.dialog: array di object con properties tipizzate.
    tools = [{"name": "get_inputs",
              "args_schema": {"type": "object",
                               "required": ["dialog"],
                               "properties": {
                                   "dialog": {
                                       "type": "array",
                                       "items": {
                                           "type": "object",
                                           "required": ["var", "prompt"],
                                           "properties": {
                                               "var": {"type": "string"},
                                               "prompt": {"type": "string"}}}}}}}]
    g = generate_tool_grammar(tools)
    # Sub-rule generata per items: GetInputsObjD*I*
    assert "GetInputsObjD" in g
    # Le property della sub-rule: propGetInputsObjD*I*Var/Prompt
    assert "Var ::=" in g and "Prompt ::=" in g
    # NON e' un fallback jsonArray generico.
    assert "propGetInputsDialog ::= \"\\\"dialog\\\"\" colon (jsonArray)" not in g


def test_b2_recursive_nested_object():
    # Schema con object di livello 2 (es. dialog.items.schema).
    tools = [{"name": "t",
              "args_schema": {"type": "object",
                               "required": ["cfg"],
                               "properties": {
                                   "cfg": {
                                       "type": "object",
                                       "required": ["kind"],
                                       "properties": {
                                           "kind": {"type": "string",
                                                     "enum": ["a", "b"]}}}}}}]
    g = generate_tool_grammar(tools)
    # Sub-rule nested object con prefix T.
    assert "TObjD" in g
    # Enum del kind nested viene emesso come literal alternation.
    assert '"\\"a\\""' in g and '"\\"b\\""' in g


def test_b2_max_recursion_depth_fallback():
    # Schema deliberatamente nested oltre _MAX_RECURSION_DEPTH (=4).
    deep = {"type": "object", "required": ["a"],
            "properties": {"a": {"type": "object", "required": ["b"],
                "properties": {"b": {"type": "object", "required": ["c"],
                    "properties": {"c": {"type": "object", "required": ["d"],
                        "properties": {"d": {"type": "object", "required": ["e"],
                            "properties": {"e": {"type": "object",
                                "properties": {"f": {"type": "string"}}}}}}}}}}}}}
    tools = [{"name": "deep_tool", "args_schema": deep}]
    g = generate_tool_grammar(tools)
    # Oltre il cap: fallback jsonObject (no esplosione regole).
    assert "jsonObject" in g


def test_b2_cross_tool_no_subrule_collision():
    # Due tool con stesso shape nested → sub-rule DEVONO essere diverse.
    nested = {"type": "object", "required": ["x"],
              "properties": {"x": {"type": "string"}}}
    tools = [
        {"name": "alpha",
         "args_schema": {"type": "object", "required": ["item"],
                          "properties": {"item": nested}}},
        {"name": "beta",
         "args_schema": {"type": "object", "required": ["item"],
                          "properties": {"item": nested}}},
    ]
    g = generate_tool_grammar(tools)
    # Sub-rule unique per tool (prefix Alpha/Beta).
    assert "AlphaObjD" in g
    assert "BetaObjD" in g
    # NIENTE rule duplicate (parser GBNF accetta solo una def per nome).
    import re
    rules = [m.group(1) for m in re.finditer(
        r"^([a-zA-Z][a-zA-Z0-9]*)\s*::=", g, flags=re.MULTILINE)]
    assert len(rules) == len(set(rules)), \
        f"duplicate rules: {[r for r in rules if rules.count(r) > 1]}"


def test_no_underscore_in_rule_names():
    """Bug llama-server b540-5755a100c: rule names con underscore vengono
    silenziosamente IGNORATI dal parser GBNF. Workaround: camelCase ovunque."""
    tools = [
        {"name": "with_under_score",
         "args_schema": {"type": "object", "required": ["from_step"],
                          "properties": {"from_step": {"type": "integer"},
                                          "some_field": {"type": "string"}}}},
    ]
    g = generate_tool_grammar(tools)
    import re
    rules = [m.group(1) for m in re.finditer(
        r"^([a-zA-Z][a-zA-Z0-9]*)\s*::=", g, flags=re.MULTILINE)]
    bad = [r for r in rules if "_" in r]
    assert not bad, f"underscore nei rule names (llama-server bug): {bad}"


def test_primitives_only_referenced_emitted():
    """Bug llama-server: rule non-referenziate possono interferire col
    matching. Generator emette solo le primitives effettivamente usate."""
    # Tool minimale: usa solo jsonStr (no array, no number, no bool).
    tools = [{"name": "t",
              "args_schema": {"type": "object", "required": ["x"],
                               "properties": {"x": {"type": "string"}}}}]
    g = generate_tool_grammar(tools)
    # jsonStr e' richiesto e dipendenza chiusura include jsonChar/hex.
    assert "jsonStr ::=" in g
    # jsonBool/jsonNull non referenziati → non emessi.
    assert "jsonBool ::=" not in g
    assert "jsonNull ::=" not in g


def test_validator_rejects_args_schema_mismatch():
    """Strategia 3: top-level required missing → reject pre-execute.
    Bug live 14/5: LLM emetteva get_inputs senza title/dialog → executor
    falliva con messaggio interno. Validator top-level cattura prima."""
    ok, err = validate_tool_call(
        {"name": "get_inputs",
         "arguments": {"display_template": "x", "kind": "choice"}},
        [_t("get_inputs", {"type": "object",
                            "required": ["title", "dialog"],
                            "properties": {
                                "title": {"type": "string"},
                                "dialog": {"type": "array"}}})])
    assert not ok
    assert "title" in err and "dialog" in err


# --------------------------------------------------------------------------
# Integration smoke: catalog reale
# --------------------------------------------------------------------------

def test_real_catalog_grammar_generates(tmp_path, monkeypatch):
    """Genera grammar per un sample di 5 tool reali del catalogo."""
    from loader import load_catalog, invalidate_catalog_cache
    invalidate_catalog_cache()  # evita cache sporca da altri test
    cat = load_catalog(verify=True, include_synth=False)
    names = ["get_now", "find_files", "read_files", "create_events",
             "send_messages"]
    tools = [cat.executors[n] for n in names if n in cat.executors]
    assert tools, "catalog mancante per il test"
    g = generate_tool_grammar(tools)
    # struttura outer presente (discriminated union pair* dopo refactor
    # 14/5/2026: niente `args ::=` o `name ::=` globale, ogni tool emette
    # pairTool che lega name+args).
    assert "root ::=" in g
    assert "pair" in g  # pairGetNow, pairFindFiles, ...
    # name letterale di ogni tool presente in pair rules
    for t in tools:
        assert f'"\\"{t.name}\\""' in g


# --------------------------------------------------------------------------
# filter_pool_for_grammar (14/5/2026): escape-hatch + provider exclusion.
# Convergenza bench v6 100% raggiunta dopo questo filter (era 80% senza).
# --------------------------------------------------------------------------

def _mk(name):
    return {"name": name, "args_schema": {"type": "object"}}


def test_filter_excludes_request_new_executor_with_3plus_canonical():
    tools = [_mk("get_now"), _mk("find_files"), _mk("read_files"),
             _mk("request_new_executor")]
    pool, excluded = filter_pool_for_grammar(tools, "che ora e'")
    assert "request_new_executor" in excluded
    assert all(_t["name"] != "request_new_executor" for _t in pool)


def test_filter_keeps_request_new_executor_with_few_canonical():
    # <3 canonical → request_new_executor NON e' escluso (escape utile).
    tools = [_mk("get_now"), _mk("request_new_executor")]
    pool, excluded = filter_pool_for_grammar(tools, "uno strano comando")
    assert "request_new_executor" not in excluded


def test_filter_excludes_location_without_proximity_marker():
    tools = [_mk("get_now"), _mk("find_files"), _mk("read_files"),
             _mk("request_location_from_user")]
    pool, excluded = filter_pool_for_grammar(
        tools, "crea la cartella /tmp/foo",
        proximity_markers=("vicino a me", "near me", "nearby"))
    assert "request_location_from_user" in excluded


def test_filter_keeps_location_with_proximity_marker():
    tools = [_mk("get_now"), _mk("find_files"), _mk("read_files"),
             _mk("request_location_from_user")]
    pool, excluded = filter_pool_for_grammar(
        tools, "ristorante vicino a me",
        proximity_markers=("vicino a me", "near me", "nearby"))
    assert "request_location_from_user" not in excluded


def test_filter_proximity_word_boundary_no_false_positive():
    # `qua` ⊆ `qualcosa` ma word-boundary regex non matcha.
    tools = [_mk("get_now"), _mk("find_files"), _mk("read_files"),
             _mk("request_location_from_user")]
    pool, excluded = filter_pool_for_grammar(
        tools, "fai qualcosa di interessante",
        proximity_markers=("qua", "qui", "vicino"))
    assert "request_location_from_user" in excluded, \
        "qua ⊆ qualcosa NON deve attivare proximity"


def test_filter_excludes_undo_without_undo_marker():
    tools = [_mk("get_now"), _mk("find_files"), _mk("read_files"),
             _mk("undo_last_turn")]
    pool, excluded = filter_pool_for_grammar(tools, "che ora e'")
    assert "undo_last_turn" in excluded


def test_filter_keeps_undo_with_undo_marker():
    tools = [_mk("get_now"), _mk("find_files"), _mk("read_files"),
             _mk("undo_last_turn")]
    pool, excluded = filter_pool_for_grammar(tools, "annulla l'ultima azione")
    assert "undo_last_turn" not in excluded


def test_filter_excludes_provider_specific_without_marker():
    tools = [_mk("create_dirs"), _mk("create_dirs_google_workspace"),
             _mk("find_files"), _mk("read_files")]
    pool, excluded = filter_pool_for_grammar(
        tools, "crea la cartella /tmp/foo")
    assert "create_dirs_google_workspace" in excluded
    assert "create_dirs" not in excluded  # canonical preservato


def test_filter_keeps_provider_specific_with_marker():
    tools = [_mk("create_dirs"), _mk("create_dirs_google_workspace")]
    pool, excluded = filter_pool_for_grammar(
        tools, "crea una cartella su google drive")
    assert "create_dirs_google_workspace" not in excluded


def test_filter_keeps_provider_multiple_markers():
    # "gmail" triggera google_workspace filter.
    tools = [_mk("send_messages"), _mk("send_messages_google_workspace")]
    pool, excluded = filter_pool_for_grammar(
        tools, "mandami una mail via gmail")
    assert "send_messages_google_workspace" not in excluded


def test_filter_safety_never_empties_pool():
    # Tutti tool sono escape-hatch + nessun marker → safety ripristina.
    tools = [_mk("request_new_executor"), _mk("request_new_executor")]
    pool, excluded = filter_pool_for_grammar(tools, "x")
    assert pool, "filter non deve mai azzerare il pool"


def test_filter_is_deterministic():
    tools = [_mk("get_now"), _mk("find_files"), _mk("read_files"),
             _mk("undo_last_turn"), _mk("create_dirs_google_workspace")]
    p1, e1 = filter_pool_for_grammar(tools, "che ora e'")
    p2, e2 = filter_pool_for_grammar(tools, "che ora e'")
    assert [t["name"] for t in p1] == [t["name"] for t in p2]
    assert e1 == e2


def test_real_catalog_get_inputs_dialog_emits_subrule():
    """Smoke per il pool tipico di propose+notify: get_inputs.dialog DEVE
    avere sub-rule recursive, non fallback jsonArray. Regression guard B2."""
    from loader import load_catalog, invalidate_catalog_cache
    invalidate_catalog_cache()
    cat = load_catalog(verify=True, include_synth=False)
    assert "get_inputs" in cat.executors
    g = generate_tool_grammar([cat.executors["get_inputs"]])
    # B2 attivo: la prop dialog ha sub-rule object (non jsonArray nudo).
    assert "GetInputsObjD" in g
    assert "propGetInputsDialog ::= \"\\\"dialog\\\"\" colon (jsonArray)" not in g
