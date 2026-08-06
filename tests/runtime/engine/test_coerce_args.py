"""FASE 3.1 provenienza args — backstop `coerce_args_to_schema` (7/7/2026).

Contratto (spec_args_provenance_architecture.md §3.1):
- chiave fuori-schema → DROP (il manifest è la verità §2.5);
- chiave `runtime_resolved` → DROP (leak: proprietario = runtime/guard a valle);
- enum: valido intatto; case-insensitive UNICO → normalizza (§2.4); fuori
  dominio → DROP (mai snap "al più vicino");
- whitelist SEMPRE: from_step/entries (piping §4.1); le chiavi `_*` del
  proposer vengono sempre rimosse e reiniettate piu' tardi dal runtime;
- tool senza schema tipizzato → no-op; idempotente; mai bloccare.

Proprietà PROV.3 per costruzione: tocca solo provenienza runtime (marcati) e
clause (enum) — mai un semantic in-schema. NIENTE LLM (§7.9).
"""
from __future__ import annotations

import sys
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")

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
    assert changed
    assert out == {"entries": [{"a": 1}], "from_step": 2}


def test_model_internal_authority_keys_are_always_dropped():
    attempted = {
        "pattern": "x", "_actor": "host", "_confirmed": True,
        "_pre_approved": True, "_turn_id": "foreign",
    }
    out, changed = coerce_step_args(attempted, SCHEMA)
    assert changed
    assert out == {"pattern": "x"}


def test_internal_keys_are_dropped_even_without_typed_schema():
    out, changed = coerce_step_args(
        {"value": 1, "_confirmed": True}, {"properties": {}})
    assert changed
    assert out == {"value": 1}


def test_set_credentials_confirmation_fields_are_runtime_owned():
    import tomllib

    manifest = tomllib.loads((
        Path(__file__).resolve().parents[3]
        / "executors/set_credentials/manifest.toml"
    ).read_text(encoding="utf-8"))
    out, changed = coerce_step_args({
        "binding": "service",
        "replace": True,
        "overwrite_confirmed": True,
    }, manifest["args"])

    assert out == {"binding": "service"}
    assert changed is True


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


def test_guard_owned_arg_survives_sul_tool_che_lo_dichiara():
    """Gli arg dichiarati nei `writes` dei guard a valle (registro PROV.1,
    es. `client`) restano anche se MARCATI `runtime_resolved`: sono dominio dei
    guard, e l'idempotenza della catena dipende da questa esenzione."""
    owned = frozenset({"client"})
    out, changed = coerce_step_args({"client": "local"}, SCHEMA,
                                    guard_owned=owned)
    assert not changed and out == {"client": "local"}


def test_guard_owned_resta_tollerato_all_ingresso_perche_e_una_prova():
    """L'esenzione all'INGRESSO deve restare larga: un arg fuori-schema può
    essere la PROVA che una guardia a valle legge. `include_health` non
    appartiene al contratto di `get_files`, ed è esattamente per questo che
    dimostra un instradamento sbagliato. Toglierlo qui accieca la guardia —
    misurato, rompe il ripristino del produttore health."""
    owned = frozenset({"include_health"})
    no_health_schema = {"properties": {"paths": {"type": "array"}}}
    out, changed = coerce_step_args({"include_health": True}, no_health_schema,
                                    guard_owned=owned)
    assert not changed and out == {"include_health": True}


def test_uscita_toglie_cio_che_il_tool_non_dichiara():
    """Il CRICCHETTO, chiuso il 6/8 in USCITA. L'esenzione `guard_owned` è per
    NOME NUDO: una guardia che dichiara `args.X` rende `X` accettabile su OGNI
    tool, per sempre — erano 21 nomi, fra cui `path`, `paths`, `mode`,
    `exist_ok`, `dst_folder`, cioè dove scrivere e che cosa cancellare. Non si
    può chiudere all'ingresso senza accecare chi legge quegli arg come prova;
    si chiude qui, dove nessuno deve più leggerli.

    Misurato sul corpus reale: 132 piani su 2423 cambiano, e ogni differenza è
    una caduta — nessun tool cambia, nessun arg appare. Verificato uno per uno
    che nessun executor legga l'arg che gli cade."""
    from engine.coerce_args import strip_unknown_args
    from engine.types import Framework, StepSpec

    class _Ex:
        def __init__(self, name, props):
            self.name = name
            self.args_schema = {"type": "object", "properties": props}

    cat = [_Ex("list_dirs", {"path": {"type": "string"}}),
           _Ex("senza_schema", {})]
    fw = Framework(steps=[
        StepSpec(tool="list_dirs", args={"path": "/tmp", "client": "google",
                                         "from_step": 1, "_actor": "roberto"}),
        StepSpec(tool="senza_schema", args={"qualunque": 1}),
        StepSpec(tool="tool_ignoto", args={"qualunque": 1})])
    out = strip_unknown_args(fw, cat)
    # fuori schema via; piping universale e metadati runtime restano
    assert out.steps[0].args == {"path": "/tmp", "from_step": 1,
                                 "_actor": "roberto"}
    # schema non tipizzato o tool ignoto → no-op, mai bloccare
    assert out.steps[1].args == {"qualunque": 1}
    assert out.steps[2].args == {"qualunque": 1}


def test_uscita_e_idempotente():
    from engine.coerce_args import strip_unknown_args
    from engine.types import Framework, StepSpec

    class _Ex:
        name = "list_dirs"
        args_schema = {"type": "object",
                       "properties": {"path": {"type": "string"}}}

    fw = Framework(steps=[StepSpec(tool="list_dirs",
                                   args={"path": "/tmp", "client": "google"})])
    once = strip_unknown_args(fw, [_Ex()])
    snapshot = dict(once.steps[0].args)
    twice = strip_unknown_args(once, [_Ex()])
    assert twice.steps[0].args == snapshot == {"path": "/tmp"}


def test_guard_owned_resta_idempotente_dopo_la_stretta():
    """La stretta non riapre l'oscillazione che l'esenzione preveniva: un arg
    guard-owned e DICHIARATO dal tool sopravvive a quante applicazioni si
    vuole."""
    owned = frozenset({"client"})
    once, _ = coerce_step_args({"client": "local", "pattern": "x"}, SCHEMA,
                               guard_owned=owned)
    twice, changed = coerce_step_args(once, SCHEMA, guard_owned=owned)
    assert not changed and twice == once == {"client": "local", "pattern": "x"}


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
