"""runtime/tool_grammar.py — generatore GBNF per constrained tool_call.

ADR 0133 (14/5/2026): forza il PLANNER LLM a emettere SOLO JSON tool_call
valido fra i tool ammessi del pool. Bypassa il `tools/tool_choice`
nativo di llama-server (che ammette grammar custom ma non insieme a
tools) generando direttamente la grammatica GBNF che riproduce il
tool_call protocol.

Strategia 1 (struttura outer):
    root ::= "{" "\"name\":\"" name "\"," "\"arguments\":" args "}"
    name ::= "get_now" | "find_files" | ...      ← enum tool ammessi

Strategia 2 (oneOf/enum disjunction):
    args dipende da `name` → grammar per-tool con tipi a partire da
    `args_schema`. Per-tool object ha required-first + optional keys
    in qualsiasi ordine; enum properties limitate ai valori dichiarati.

Strategia 3 (free-form fallback per schemi complessi): per tool con
complessita' alta (oneOf/anyOf/array di oggetti annidati), `args` cade
sul JSON generico (string|number|bool|null|object|array). La sintassi
resta valida; la semantica e' validata post-decoding (vedi
`tool_grammar.validate_tool_call`).

Determinismo §7.9. Nessun LLM. Genera la grammar da `args_schema` JSON
Schema gia' presente in ogni `Executor.args_schema`.

API:
    generate_tool_grammar(tools) -> str   # grammar GBNF complete
    validate_tool_call(tool_call, tools) -> tuple[bool, str]
    args_complexity(schema) -> int        # 0..N (cap 100)
    is_complex(schema, threshold=8) -> bool
"""
from __future__ import annotations

import json
import re
from typing import Any, Sequence

# Soglia di complessita' oltre la quale gli args cadono su JSON generico.
# Bilanciato da test empirici 14/5/2026 su catalogo Metnos: separa tool
# semplici (get_now/find_files/create_events) da complessi (send_messages
# con array di object polymorphico, get_inputs con dialog schema variant).
COMPLEXITY_THRESHOLD = 5

# Set di tipi atomici JSON Schema gestiti come grammar-typed.
_ATOMIC_TYPES = ("string", "integer", "number", "boolean", "null")


# --------------------------------------------------------------------------
# JSON Schema → complexity score
# --------------------------------------------------------------------------

def args_complexity(schema: dict | None) -> int:
    """Punteggio di complessita' deterministico (§7.9).

    Conta:
      +1 per ogni `oneOf|anyOf|allOf` (polymorphism)
      +1 per ogni `array` con `items.type=object` (lista di dict)
      +1 per ogni `object` annidato (depth >= 2)
      +1 per ogni `$ref` (recursive)
      +1 per ogni proprieta' senza `type` (free-form)
      +0.5 per ogni proprieta' oltre la decima

    Capped a 100. 0 = schema puro atomico.
    """
    if not isinstance(schema, dict):
        return 0
    score = 0.0

    def _walk(node: Any, depth: int = 0) -> None:
        nonlocal score
        if not isinstance(node, dict):
            return
        for k in ("oneOf", "anyOf", "allOf"):
            if k in node:
                score += 1
                for sub in node[k] or []:
                    _walk(sub, depth + 1)
        if "$ref" in node:
            score += 1
        t = node.get("type")
        if t == "array":
            items = node.get("items") or {}
            it_type = items.get("type") if isinstance(items, dict) else None
            if it_type == "object":
                score += 1
            _walk(items, depth + 1)
        if t == "object" or "properties" in node:
            if depth >= 1:
                score += 1
            props = node.get("properties") or {}
            if len(props) > 10:
                score += 0.5 * (len(props) - 10)
            for pname, pschema in props.items():
                # property senza type esplicito (`description` only)
                if isinstance(pschema, dict) and "type" not in pschema and not any(
                    k in pschema for k in ("oneOf", "anyOf", "allOf", "$ref", "enum")
                ):
                    score += 1
                _walk(pschema, depth + 1)

    _walk(schema)
    return min(int(score), 100)


def is_complex(schema: dict | None, *, threshold: int = COMPLEXITY_THRESHOLD) -> bool:
    return args_complexity(schema) >= threshold


# --------------------------------------------------------------------------
# GBNF emitters (mini-DSL)
# --------------------------------------------------------------------------
#
# La grammatica GBNF di llama.cpp ha sintassi simile a EBNF:
#   rule ::= alternative1 | alternative2
#   "literal" (token literal)
#   [a-z] (character class)
#   x* | x+ | x? (modifier)
#   (group)
#
# Useremo nomi snake_case per le regole. La grammar finale e' una stringa
# unica composta da N regole `name ::= rhs`.

# Regole condivise (JSON primitives + generic).
# Bug discovered 14/5/2026: llama-server fail su grammar con regole UNUSED
# (es. json_char def. ma root non lo raggiunge). Emit solo le primitives
# referenziate via dependency tracking nel generator.
# BUG llama-server (b540-5755a100c, scoperto 14/5/2026 sera): rule
# names con UNDERSCORE causano parsing failure SILENZIOSO (grammar
# ignorata, free generation). Workaround: camelCase ovunque.
_PRIMITIVE_DEFS: dict[str, str] = {
    # core structural (3-char no underscore — sicuri).
    "ws":    r"ws ::= [ \t\n]*",
    "sep":   r'sep ::= ws "," ws',
    "colon": r'colon ::= ws ":" ws',
    # string (camelCase)
    "jsonChar": r'jsonChar ::= [^"\\] | "\\" ["\\/bfnrt] | "\\u" hex hex hex hex',
    "jsonStr":  r'jsonStr ::= "\"" jsonChar* "\""',
    "hex":      r"hex ::= [0-9a-fA-F]",
    # number
    "jsonNum":  r'jsonNum ::= "-"? jsonInt jsonFrac? jsonExp?',
    "jsonInt":  r'jsonInt ::= "0" | [1-9] [0-9]*',
    "jsonFrac": r'jsonFrac ::= "." [0-9]+',
    "jsonExp":  r'jsonExp ::= [eE] [+-]? [0-9]+',
    # bool/null
    "jsonBool": r'jsonBool ::= "true" | "false"',
    "jsonNull": r'jsonNull ::= "null"',
    # generic value / collections
    "jsonValue":  r"jsonValue ::= jsonStr | jsonNum | jsonBool | jsonNull | jsonArray | jsonObject",
    "jsonArray":  r'jsonArray ::= "[" ws (jsonValue (sep jsonValue)*)? ws "]"',
    "jsonObject": r'jsonObject ::= "{" ws (jsonKv (sep jsonKv)*)? ws "}"',
    "jsonKv":     r"jsonKv ::= jsonStr colon jsonValue",
}

_PRIMITIVE_DEPS: dict[str, set[str]] = {
    "ws":    set(),
    "sep":   {"ws"},
    "colon": {"ws"},
    "jsonChar":   {"hex"},
    "jsonStr":    {"jsonChar"},
    "hex":        set(),
    "jsonNum":    {"jsonInt", "jsonFrac", "jsonExp"},
    "jsonInt":    set(),
    "jsonFrac":   set(),
    "jsonExp":    set(),
    "jsonBool":   set(),
    "jsonNull":   set(),
    "jsonValue":  {"jsonStr", "jsonNum", "jsonBool", "jsonNull",
                    "jsonArray", "jsonObject"},
    "jsonArray":  {"ws", "sep", "jsonValue"},
    "jsonObject": {"ws", "sep", "jsonKv"},
    "jsonKv":     {"jsonStr", "colon", "jsonValue"},
}


def _expand_deps(used: set[str]) -> set[str]:
    """Closure delle dipendenze: aggiunge tutte le primitives transitivamente
    referenziate."""
    out = set(used)
    while True:
        added = False
        for k in list(out):
            for dep in _PRIMITIVE_DEPS.get(k, set()):
                if dep not in out:
                    out.add(dep); added = True
        if not added:
            break
    return out


def _emit_primitives(used: set[str]) -> list[str]:
    """Emit solo le definizioni delle primitives referenziate (+ dependencies
    transitive). Bug llama-server: regole unused rompono grammar."""
    closure = _expand_deps(used)
    return [_PRIMITIVE_DEFS[k] for k in _PRIMITIVE_DEFS if k in closure]


def _sanitize_rule_name(name: str) -> str:
    """Converte tool name in identificatore GBNF camelCase.
    Bug llama-server: underscore nei rule names → grammar IGNORED."""
    parts = re.findall(r"[a-zA-Z0-9]+", name)
    if not parts:
        return "tool"
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def _emit_string_value(schema: dict) -> str:
    """Emette regola per un valore di tipo string. Supporta `enum`."""
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        # Tutti gli enum di tipo string vincolati.
        alts = []
        for v in enum_vals:
            if isinstance(v, str):
                alts.append(f"\"\\\"{v}\\\"\"")
        if alts:
            return " | ".join(alts)
    return "json_str"


def _emit_string_literal_alt(values: list) -> str:
    """Build alternation di literal stringa: \"a\" | \"b\" | ..."""
    alts = []
    for v in values:
        if isinstance(v, str):
            alts.append(f"\"\\\"{v}\\\"\"")
    return " | ".join(alts) if alts else "jsonStr"


def _emit_value(schema: dict, used: set[str]) -> str:
    """Emette regola GBNF per un valore secondo `schema`. Aggiunge a
    `used` le primitives referenziate (dependency tracking).
    Naming camelCase per bug llama-server underscore."""
    if not isinstance(schema, dict):
        used.add("jsonValue")
        return "jsonValue"
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        rhs = _emit_string_literal_alt(enum_vals)
        if rhs == "jsonStr":
            used.add("jsonStr")
        return rhs
    t = schema.get("type")
    if t == "string":
        used.add("jsonStr")
        return "jsonStr"
    if t in ("integer", "number"):
        used.add("jsonNum"); return "jsonNum"
    if t == "boolean":
        used.add("jsonBool"); return "jsonBool"
    if t == "null":
        used.add("jsonNull"); return "jsonNull"
    if t == "array":
        items = schema.get("items") or {}
        if isinstance(items, dict) and items.get("type") == "string" and "enum" not in items:
            used.update({"ws", "sep", "jsonStr"})
            return "(\"[\" ws (jsonStr (sep jsonStr)*)? ws \"]\")"
        used.add("jsonArray"); return "jsonArray"
    if t == "object":
        used.add("jsonObject"); return "jsonObject"
    used.add("jsonValue"); return "jsonValue"


def _emit_tool_args(tool_name: str, schema: dict | None
                     ) -> tuple[str, list[str], set[str]]:
    """Emette regole GBNF per gli args di UN tool.
    Naming camelCase per bug llama-server underscore."""
    base = _sanitize_rule_name(tool_name)
    cap_base = base[0].upper() + base[1:] if base else "Tool"
    rule_name = f"args{cap_base}"
    used: set[str] = set()

    if schema is None or not isinstance(schema, dict):
        used.add("jsonObject")
        return rule_name, [f"{rule_name} ::= jsonObject"], used

    if is_complex(schema):
        used.add("jsonObject")
        return rule_name, [f"{rule_name} ::= jsonObject"], used

    if any(k in schema for k in ("oneOf", "anyOf", "allOf")):
        used.add("jsonObject")
        return rule_name, [f"{rule_name} ::= jsonObject"], used

    props = schema.get("properties") or {}
    if not isinstance(props, dict) or not props:
        used.add("jsonObject")
        return rule_name, [f"{rule_name} ::= jsonObject"], used

    required = schema.get("required") or []
    if not isinstance(required, list):
        required = []
    keys_required = [k for k in required if k in props]
    keys_optional = [k for k in props.keys() if k not in keys_required]

    lines: list[str] = []
    kv_required_rules: list[str] = []
    kv_optional_rules: list[str] = []
    for k in keys_required:
        key_cap = _sanitize_rule_name(k)
        key_cap = key_cap[0].upper() + key_cap[1:] if key_cap else "X"
        rule = f"prop{cap_base}{key_cap}"
        val_expr = _emit_value(props[k], used)
        used.add("colon")
        lines.append(f"{rule} ::= \"\\\"{k}\\\"\" colon ({val_expr})")
        kv_required_rules.append(rule)
    for k in keys_optional:
        key_cap = _sanitize_rule_name(k)
        key_cap = key_cap[0].upper() + key_cap[1:] if key_cap else "X"
        rule = f"prop{cap_base}{key_cap}"
        val_expr = _emit_value(props[k], used)
        used.add("colon")
        lines.append(f"{rule} ::= \"\\\"{k}\\\"\" colon ({val_expr})")
        kv_optional_rules.append(rule)

    # Body: required keys ordinati, poi optional in qualsiasi ordine.
    used.update({"ws", "sep"})
    if kv_required_rules:
        req_seq = " sep ".join(kv_required_rules) if len(kv_required_rules) > 1 else kv_required_rules[0]
        if kv_optional_rules:
            opt_alt = " | ".join(kv_optional_rules)
            lines.append(
                f"{rule_name} ::= \"{{\" ws {req_seq} (sep ({opt_alt}))* ws \"}}\""
            )
        else:
            lines.append(
                f"{rule_name} ::= \"{{\" ws {req_seq} ws \"}}\""
            )
    else:
        if kv_optional_rules:
            opt_alt = " | ".join(kv_optional_rules)
            lines.append(
                f"{rule_name} ::= \"{{}}\" | \"{{\" ws ({opt_alt}) (sep ({opt_alt}))* ws \"}}\""
            )
        else:
            lines.append(f"{rule_name} ::= \"{{}}\"")
    return rule_name, lines, used


# --------------------------------------------------------------------------
# Public API: generate_tool_grammar
# --------------------------------------------------------------------------

def generate_tool_grammar(tools: Sequence[Any]) -> str:
    """Genera grammar GBNF per il pool `tools`. Emit ONLY primitives
    effettivamente referenziate (dependency tracking). Workaround bug
    llama-server 14/5/2026: regole UNUSED interferiscono col matching.

    Output:
        <primitives subset>
        root ::= "{" ws "\"name\":" colon name sep "\"arguments\":" colon args ws "}"
        name ::= "tool_a" | "tool_b" | ...
        args ::= args_tool_a | args_tool_b | ...
        args_tool_a ::= ...
        ...

    Determinismo §7.9.
    """
    if not tools:
        # Empty pool: grammar permissive (qualsiasi JSON object)
        used = {"json_object"}
        prims = _emit_primitives(used)
        return "\n".join(prims + ["root ::= json_object"])

    tool_names: list[str] = []
    schema_lines: list[str] = []
    args_rule_for: dict[str, str] = {}
    used_primitives: set[str] = set()
    for t in tools:
        name = _extract_name(t)
        schema = _extract_schema(t)
        if not name or name in args_rule_for:
            continue
        rule_name, lines, used = _emit_tool_args(name, schema)
        tool_names.append(name)
        schema_lines.extend(lines)
        args_rule_for[name] = rule_name
        used_primitives.update(used)

    if not tool_names:
        used = {"json_object"}
        prims = _emit_primitives(used)
        return "\n".join(prims + ["root ::= json_object"])

    # Root usa sempre `ws`, `sep`, `colon` per struttura. Marcate qui.
    used_primitives.update({"ws", "sep", "colon"})
    prims = _emit_primitives(used_primitives)

    name_alts = " | ".join(f"\"\\\"{n}\\\"\"" for n in tool_names)
    args_alts = " | ".join(args_rule_for.values())

    grammar = list(prims) + [""]
    grammar.append(
        "root ::= \"{\" ws \"\\\"name\\\"\" colon name sep "
        "\"\\\"arguments\\\"\" colon args ws \"}\""
    )
    grammar.append(f"name ::= {name_alts}")
    grammar.append(f"args ::= {args_alts}")
    grammar.extend(schema_lines)
    return "\n".join(grammar)


# --------------------------------------------------------------------------
# Validation (post-decode, Strategia 3)
# --------------------------------------------------------------------------

def validate_tool_call(tool_call: dict, tools: Sequence[Any]
                        ) -> tuple[bool, str]:
    """Valida tool_call sulla SOLA correttezza top-level (required keys
    presenti + tipo dict). Non valida nested schemas: l'executor stesso
    e' responsabile della deep-validation con messaggi specifici.

    Strategia 3 ADR 0133: blocco grossolani errori del LLM senza
    sovrapporsi alla validation built-in dell'executor.

    Returns:
      (ok, error_message). `error_message` e' user-facing, da iniettare
      nel prossimo prompt LLM se retry e' attivo.
    """
    if not isinstance(tool_call, dict):
        return False, "tool_call non e' dict"
    name = tool_call.get("name")
    if not name or not isinstance(name, str):
        return False, "tool_call manca 'name' valido"
    args = tool_call.get("arguments")
    if args is None:
        return False, "tool_call manca 'arguments'"
    target_schema = None
    for t in tools:
        if _extract_name(t) == name:
            target_schema = _extract_schema(t)
            break
    if target_schema is None:
        return False, f"tool '{name}' non e' nel pool"
    if not isinstance(args, dict):
        return False, "arguments deve essere object"
    # Top-level required-only check (no nested validation: l'executor
    # ha messaggi piu' specifici sul deep schema mismatch).
    req = target_schema.get("required") or []
    if isinstance(req, list):
        missing = [k for k in req if k not in args]
        if missing:
            return False, (f"missing required args {missing} for tool "
                           f"'{name}'")
    return True, ""


# --------------------------------------------------------------------------
# Adapters: Executor object vs dict-like
# --------------------------------------------------------------------------

def _extract_name(t: Any) -> str:
    """Accetta 3 shapes:
      - Executor object (attr `name`)
      - dict semplice {"name": ..., "args_schema": ...}
      - dict OpenAI tool: {"type":"function","function":{"name":...,"parameters":...}}
    """
    if isinstance(t, dict):
        if isinstance(t.get("function"), dict):
            return t["function"].get("name") or ""
        return t.get("name") or ""
    return getattr(t, "name", "") or ""


def _extract_schema(t: Any) -> dict | None:
    if isinstance(t, dict):
        if isinstance(t.get("function"), dict):
            sch = t["function"].get("parameters")
            return sch if isinstance(sch, dict) else None
        sch = t.get("args_schema") or t.get("parameters")
        return sch if isinstance(sch, dict) else None
    sch = getattr(t, "args_schema", None)
    return sch if isinstance(sch, dict) else None
