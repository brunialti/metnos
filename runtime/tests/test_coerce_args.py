"""FASE 3.1 provenienza args — backstop `coerce_args_to_schema` (7/7/2026).

Contratto (spec_args_provenance_architecture.md §3.1):
- chiave fuori-schema → DROP (il manifest è la verità §2.5);
- chiave `runtime_resolved` → DROP (leak: proprietario = runtime/guard a valle);
- enum: valido intatto; case-insensitive UNICO → normalizza (§2.4); fuori
  dominio → DROP (mai snap "al più vicino");
- whitelist SEMPRE: from_step/entries (piping §4.1) + chiavi `_*` (runtime);
- tool senza schema tipizzato → no-op; idempotente; mai bloccare.

Proprietà PROV.3 per costruzione: tocca solo provenienza runtime (marcati) e
clause (enum) — mai un semantic in-schema. NIENTE LLM (§7.9).
"""
from __future__ import annotations

import sys
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

from engine.coerce_args import coerce_step_args, coerce_framework_to_schema  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
        "style": {"type": "string", "enum": ["compact", "by_importance"]},
        "client": {"type": "string", "enum": ["local"],
                   "runtime_resolved": True},
        "entries": {"type": "array", "runtime_resolved": True},
        "max_results": {"type": "integer"},
    },
}


def test_unknown_key_dropped_semantic_untouched():
    out, changed = coerce_step_args(
        {"pattern": "*.pdf", "sortby": "recent"}, SCHEMA)
    assert changed
    assert out == {"pattern": "*.pdf"}   # semantic in-schema intatto


def test_runtime_resolved_leak_dropped():
    out, changed = coerce_step_args({"client": "local", "pattern": "x"}, SCHEMA)
    assert changed
    assert "client" not in out and out["pattern"] == "x"


def test_universal_keys_survive_even_if_marked():
    # `entries` è marcato nello schema MA è piping §4.1 → resta.
    out, changed = coerce_step_args(
        {"entries": [{"a": 1}], "from_step": 2, "_actor": "host"}, SCHEMA)
    assert not changed
    assert out == {"entries": [{"a": 1}], "from_step": 2, "_actor": "host"}


def test_enum_valid_kept_case_normalized_invalid_dropped():
    ok, ch = coerce_step_args({"style": "compact"}, SCHEMA)
    assert not ch and ok["style"] == "compact"
    norm, ch = coerce_step_args({"style": "COMPACT"}, SCHEMA)
    assert ch and norm["style"] == "compact"          # §2.4 case-insensitive
    inv, ch = coerce_step_args({"style": "recent"}, SCHEMA)
    assert ch and "style" not in inv                  # drop, MAI snap


def test_enum_non_string_value_left_alone():
    out, changed = coerce_step_args({"style": ["compact"]}, SCHEMA)
    assert not changed and out["style"] == ["compact"]


def test_no_schema_or_empty_props_noop():
    for schema in (None, {}, {"properties": {}}, "boh"):
        out, changed = coerce_step_args({"x": 1}, schema)
        assert not changed and out == {"x": 1}


def test_idempotent():
    once, _ = coerce_step_args(
        {"pattern": "x", "style": "COMPACT", "junk": 1}, SCHEMA)
    twice, changed = coerce_step_args(once, SCHEMA)
    assert not changed and twice == once


def test_guard_owned_args_never_touched():
    """Gli arg dichiarati nei `writes` dei guard a valle (registro PROV.1,
    es. `client`) sono ESENTI anche se marcati/fuori-schema: dominio dei
    guard — l'idempotenza della catena dipende da questa esenzione."""
    owned = frozenset({"client"})
    # marcato MA guard-owned → resta (align/scope_sink lo arbitrano dopo)
    out, changed = coerce_step_args({"client": "local"}, SCHEMA,
                                    guard_owned=owned)
    assert not changed and out == {"client": "local"}
    # perfino fuori-schema (es. list_dirs senza client dichiarato): resta
    no_client_schema = {"properties": {"path": {"type": "string"}}}
    out, changed = coerce_step_args({"client": "google_workspace"},
                                    no_client_schema, guard_owned=owned)
    assert not changed and out == {"client": "google_workspace"}


def test_chain_idempotence_with_guard_rewrite():
    """Simula il ciclo coerce→guard-scrive→coerce: il valore scritto dal
    guard sopravvive alla seconda applicazione (niente oscillazione)."""
    owned = frozenset({"client"})
    first, _ = coerce_step_args({"pattern": "x", "junk": 1}, SCHEMA,
                                guard_owned=owned)
    first["client"] = "local"          # scrittura del guard a valle
    second, changed = coerce_step_args(first, SCHEMA, guard_owned=owned)
    assert not changed and second == first


def test_framework_level_unknown_tool_untouched():
    from engine.types import Framework, StepSpec

    class _Ex:
        name = "find_files"
        args_schema = SCHEMA

    fw = Framework(steps=[
        StepSpec(tool="find_files", args={"pattern": "x", "junk": 1}),
        StepSpec(tool="final_answer", args={"text": "ciao"}),
    ])
    out = coerce_framework_to_schema(fw, [_Ex()])
    assert out.steps[0].args == {"pattern": "x"}
    assert out.steps[1].args == {"text": "ciao"}   # tool ignoto → intatto
