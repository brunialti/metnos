# Status (ADR 0163, 26/5/2026): NON deprecato.
#   - `filter_pool_for_grammar` USATO da Praxis (praxis_executor.py +
#     pronoia.py) per filtrare pool tool con provider qualifier markers.
#   - GBNF generator step-by-step USATO dal fallback PLANNER monolitico
#     in agent_runtime.run_turn (6% query Praxis-miss).
# Il marker DEPRECATED-PRAXIS originale era misleading: il modulo è
# riusato da Praxis stessa per pool filtering.
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
import os
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


# Cap della profondita' di emit grammar ricorsivo (B2).
# Oltre questo livello: fallback su jsonObject/jsonValue per safety.
# Schema reali Metnos hanno depth tipicamente <= 3 (dialog.items.schema).
_MAX_RECURSION_DEPTH = 4


def _emit_value(schema: dict, used: set[str], depth: int = 0,
                _extra_rules: list[str] | None = None,
                _tool_prefix: str = "") -> str:
    """Emette regola GBNF per un valore secondo `schema`. Aggiunge a
    `used` le primitives referenziate; per nested object/array of object
    aggiunge regole anonime in `_extra_rules` (Strategia B2 recursive).

    `_tool_prefix` prependa al nome delle sub-regole anonime per evitare
    collisioni cross-tool nel pool (es. tool A.dialog.items e tool B.x.items
    entrambi `objD1I0` → grammar invalid).

    Naming camelCase per bug llama-server underscore.
    """
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
        used.add("jsonStr"); return "jsonStr"
    if t in ("integer", "number"):
        used.add("jsonNum"); return "jsonNum"
    if t == "boolean":
        used.add("jsonBool"); return "jsonBool"
    if t == "null":
        used.add("jsonNull"); return "jsonNull"

    if t == "array":
        items = schema.get("items") or {}
        # Array di stringhe semplici (paths, urls, to_user list, ecc.)
        if isinstance(items, dict) and items.get("type") == "string" and "enum" not in items:
            used.update({"ws", "sep", "jsonStr"})
            return "(\"[\" ws (jsonStr (sep jsonStr)*)? ws \"]\")"
        # B2 RECURSIVE: array di object con properties tipizzate.
        if (isinstance(items, dict) and items.get("type") == "object"
                and isinstance(items.get("properties"), dict)
                and items["properties"]
                and depth < _MAX_RECURSION_DEPTH
                and _extra_rules is not None):
            item_expr = _emit_object_inline(items, used, depth + 1,
                                              _extra_rules, _tool_prefix)
            used.update({"ws", "sep"})
            return f"(\"[\" ws ({item_expr} (sep {item_expr})*)? ws \"]\")"
        used.add("jsonArray"); return "jsonArray"

    if t == "object":
        # B2 RECURSIVE: object con properties tipizzate.
        if (isinstance(schema.get("properties"), dict)
                and schema["properties"]
                and depth < _MAX_RECURSION_DEPTH
                and _extra_rules is not None):
            return _emit_object_inline(schema, used, depth + 1, _extra_rules,
                                         _tool_prefix)
        used.add("jsonObject"); return "jsonObject"

    used.add("jsonValue"); return "jsonValue"


def _emit_object_inline(schema: dict, used: set[str], depth: int,
                          extra_rules: list[str],
                          tool_prefix: str = "") -> str:
    """B2 recursive: emit GBNF inline per `type=object` con `required` +
    `properties` tipizzate. Aggiunge una regola anonima named (per evitare
    inline grammar troppo lunga) e ritorna il nome regola.

    Args:
      schema: il sub-schema object.
      used: set delle primitives referenziate (mutato).
      depth: profondita' ricorsione attuale (cap _MAX_RECURSION_DEPTH).
      extra_rules: lista mutabile dove appendere le sub-rule generate.
      tool_prefix: prefix per univocita' cross-tool (es. `GetInputs` →
          `GetInputsObjD1I0`). Evita collisioni nel pool.
    """
    if depth >= _MAX_RECURSION_DEPTH:
        used.add("jsonObject")
        return "jsonObject"
    props = schema.get("properties") or {}
    if not isinstance(props, dict) or not props:
        used.add("jsonObject")
        return "jsonObject"

    # Nome univoco per la sub-regola (tool_prefix + depth + counter).
    # Naming camelCase: niente underscore per bug llama-server.
    rule_idx = len(extra_rules)
    rule_name = f"{tool_prefix}ObjD{depth}I{rule_idx}"

    required = schema.get("required") or []
    if not isinstance(required, list):
        required = []
    keys_required = [k for k in required if k in props]
    keys_optional = [k for k in props.keys() if k not in keys_required]

    # Per ogni property: nome sub-rule + value expr (potenzialmente ricorsiva)
    kv_rules: dict[str, str] = {}  # key -> rule_name
    for k in list(keys_required) + list(keys_optional):
        prop_rule = f"prop{rule_name}{_sanitize_key_to_camel(k)}"
        val_expr = _emit_value(props[k], used, depth + 1, extra_rules,
                                _tool_prefix=tool_prefix)
        used.add("colon")
        extra_rules.append(
            f"{prop_rule} ::= \"\\\"{k}\\\"\" colon ({val_expr})"
        )
        kv_rules[k] = prop_rule

    used.update({"ws", "sep"})
    # Same fix repeat-loop: ordina optional alfabeticamente + (sep prop)?
    # invece di (sep (opt_alt))* (vedi commento _emit_tool_args).
    opt_rules_sorted = sorted(kv_rules[k] for k in keys_optional)
    if keys_required:
        req_seq = " sep ".join(kv_rules[k] for k in keys_required)
        if opt_rules_sorted:
            opt_seq = " ".join(f"(sep {r})?" for r in opt_rules_sorted)
            extra_rules.append(
                f"{rule_name} ::= \"{{\" ws {req_seq} {opt_seq} ws \"}}\""
            )
        else:
            extra_rules.append(
                f"{rule_name} ::= \"{{\" ws {req_seq} ws \"}}\""
            )
    else:
        if len(opt_rules_sorted) == 1:
            extra_rules.append(
                f"{rule_name} ::= \"{{}}\" | \"{{\" ws {opt_rules_sorted[0]} ws \"}}\""
            )
        else:
            first = opt_rules_sorted[0]
            rest_seq = " ".join(f"(sep {r})?" for r in opt_rules_sorted[1:])
            extra_rules.append(
                f"{rule_name} ::= \"{{}}\" | \"{{\" ws {first} {rest_seq} ws \"}}\""
            )
    return rule_name


def _sanitize_key_to_camel(k: str) -> str:
    """Property key → CamelCase (no underscore)."""
    parts = re.findall(r"[a-zA-Z0-9]+", k)
    if not parts:
        return "X"
    return "".join(p.capitalize() for p in parts)


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

    # B2 recursive (14/5/2026): is_complex non scatta piu' come fallback
    # ai tool con schema "ricco" (es. get_inputs, send_messages). Il
    # generator esplora ricorsivamente properties annidate. Fallback
    # jsonObject SOLO se schema mancante / oneOf top-level.
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
    extra_rules: list[str] = []  # B2 recursive sub-rules
    kv_required_rules: list[str] = []
    kv_optional_rules: list[str] = []
    for k in keys_required:
        key_cap = _sanitize_rule_name(k)
        key_cap = key_cap[0].upper() + key_cap[1:] if key_cap else "X"
        rule = f"prop{cap_base}{key_cap}"
        val_expr = _emit_value(props[k], used, depth=0,
                                _extra_rules=extra_rules,
                                _tool_prefix=cap_base)
        used.add("colon")
        lines.append(f"{rule} ::= \"\\\"{k}\\\"\" colon ({val_expr})")
        kv_required_rules.append(rule)
    for k in keys_optional:
        key_cap = _sanitize_rule_name(k)
        key_cap = key_cap[0].upper() + key_cap[1:] if key_cap else "X"
        rule = f"prop{cap_base}{key_cap}"
        val_expr = _emit_value(props[k], used, depth=0,
                                _extra_rules=extra_rules,
                                _tool_prefix=cap_base)
        used.add("colon")
        lines.append(f"{rule} ::= \"\\\"{k}\\\"\" colon ({val_expr})")
        kv_optional_rules.append(rule)
    # Le sub-rule ricorsive vanno PRIMA delle prop rule (ordine di
    # dipendenza: prop rule referenzia objD*I* sub-rule names).
    lines = extra_rules + lines

    # Body: required keys ordinati, poi optional in ORDINE FISSO con `?`.
    # Bug live 14/5/2026 sera: `(sep (opt_a|opt_b))*` ammette ripetizioni
    # infinite — il LLM emette `"timeout_s":3600` 300+ volte in repeat-loop.
    # GBNF non ha "unordered set"; soluzione: ordinare alfabeticamente gli
    # optional e dare a ognuno `(sep prop)?` esattamente una volta. Trade-off:
    # il LLM deve emettere optional in ordine fissato → grado di liberta'
    # ridotto ma niente repeat-loop possibili.
    used.update({"ws", "sep"})
    # Ordina optional rules per nome PROPRIETA' (deterministico, leggibile).
    # `kv_optional_rules` ha gia' nome `prop{cap_base}{key_cap}` → ordering
    # alfabetico sul nome rule = ordine alfabetico sui key originali (case
    # preserved).
    kv_optional_sorted = sorted(kv_optional_rules)
    if kv_required_rules:
        req_seq = " sep ".join(kv_required_rules) if len(kv_required_rules) > 1 else kv_required_rules[0]
        if kv_optional_sorted:
            opt_seq = " ".join(f"(sep {r})?" for r in kv_optional_sorted)
            lines.append(
                f"{rule_name} ::= \"{{\" ws {req_seq} {opt_seq} ws \"}}\""
            )
        else:
            lines.append(
                f"{rule_name} ::= \"{{\" ws {req_seq} ws \"}}\""
            )
    else:
        if kv_optional_sorted:
            # No required, solo optional: prima e' senza `sep` (vuoto ok),
            # seguenti con `(sep prop)?` ordinati.
            if len(kv_optional_sorted) == 1:
                lines.append(
                    f"{rule_name} ::= \"{{}}\" | \"{{\" ws {kv_optional_sorted[0]} ws \"}}\""
                )
            else:
                first = kv_optional_sorted[0]
                rest_seq = " ".join(f"(sep {r})?" for r in kv_optional_sorted[1:])
                lines.append(
                    f"{rule_name} ::= \"{{}}\" | \"{{\" ws {first} {rest_seq} ws \"}}\""
                )
        else:
            lines.append(f"{rule_name} ::= \"{{}}\"")
    return rule_name, lines, used


# --------------------------------------------------------------------------
# Public API: generate_tool_grammar
# --------------------------------------------------------------------------

FINAL_ANSWER_TOOL_NAME = "final_answer"
DISAMBIG_TOOL_NAME = "request_disambiguation_from_user"


def generate_tool_grammar(tools: Sequence[Any], *,
                            allow_final_answer: bool = False,
                            allow_disambiguation: bool = False,
                            include_canonical_query: bool = False) -> str:
    """Genera grammar GBNF per il pool `tools`. Emit ONLY primitives
    effettivamente referenziate (dependency tracking). Workaround bug
    llama-server 14/5/2026: regole UNUSED interferiscono col matching.

    Args:
      tools: pool di tools reali del catalogo.
      allow_final_answer: se True aggiunge una pair sintetica per
        `final_answer({message: str})` alla discriminated union. Il
        runtime intercetta tool_call.name == "final_answer" e chiude
        il turno con arguments.message. Estensione ADR 0133 (15/5/2026)
        per risolvere la regressione "final stupido post-grammar"
        (LLM forzato a tool_call non poteva piu' emettere text final
        naturale → describe_entries duplicato → auto_final_on_duplicate).
        Pattern: passare True per step >= 2 (non-iniziali), False per
        step 1 (forza esecuzione di un producer prima di rispondere).

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

    # DISCRIMINATED UNION (14/5/2026): legare name a args per tool.
    # Bug live: `name ::= A|B` + `args ::= argsA|argsB` ammetteva
    # `{"name":"get_inputs","arguments":argsFilterEntries}` (kind=choice).
    # Fix: per ogni tool emetto pairTool che vincola name + args insieme.
    pair_rules: list[str] = []
    pair_names: list[str] = []
    for n in tool_names:
        # Nome regola camelCase per coerenza naming (no underscore §B).
        cap = _sanitize_rule_name(n)
        cap = cap[0].upper() + cap[1:] if cap else "Tool"
        pair_name = f"pair{cap}"
        args_rule = args_rule_for[n]
        pair_rules.append(
            f"{pair_name} ::= \"\\\"{n}\\\"\" sep \"\\\"arguments\\\"\" colon ({args_rule})"
        )
        pair_names.append(pair_name)

    # Synthetic `final_answer({message: str})` (ADR 0133 ext, 15/5/2026).
    if allow_final_answer:
        pair_rules.append(
            f"pairFinalAnswer ::= \"\\\"{FINAL_ANSWER_TOOL_NAME}\\\"\" "
            f"sep \"\\\"arguments\\\"\" colon "
            f"(\"{{\" ws \"\\\"message\\\"\" colon jsonStr ws \"}}\")"
        )
        pair_names.append("pairFinalAnswer")
        used_primitives.add("jsonStr")

    # Synthetic `request_disambiguation_from_user({question, options})`
    # (Test 6 fix sistemico, 16/5/2026). Il PLANNER lo emette quando
    # rileva due interpretazioni plausibili invece di scegliere
    # arbitrariamente. Args:
    #   question: stringa, la domanda da porre all'utente
    #   options: array di 2+ stringhe (label leggibili)
    # Runtime intercetta -> get_inputs(kind=choice) -> resume con
    # scelta = nuova user_query.
    if allow_disambiguation:
        pair_rules.append(
            f"pairRequestDisambig ::= \"\\\"{DISAMBIG_TOOL_NAME}\\\"\" "
            f"sep \"\\\"arguments\\\"\" colon "
            f"(\"{{\" ws \"\\\"question\\\"\" colon jsonStr ws "
            f"\",\" ws \"\\\"options\\\"\" colon "
            f"\"[\" ws jsonStr ws (\",\" ws jsonStr ws)+ \"]\" ws \"}}\")"
        )
        pair_names.append("pairRequestDisambig")
        used_primitives.add("jsonStr")

    # ADR 0149: opt-in by-product `canonical_query` (top-level sibling of
    # name/arguments). LLM emits the lemma form of the user query alongside
    # the tool_call. Cost: ~50 ms output tokens. Consumed by mnestoma for
    # future fast-path promotion. Off by default for back-compat.
    if include_canonical_query:
        used_primitives.add("jsonStr")
        prims = _emit_primitives(used_primitives)

    grammar = list(prims) + [""]
    if include_canonical_query:
        grammar.append(
            "root ::= \"{\" ws \"\\\"name\\\"\" colon (" + " | ".join(pair_names) +
            ") sep \"\\\"canonical_query\\\"\" colon jsonStr ws \"}\""
        )
    else:
        grammar.append(
            "root ::= \"{\" ws \"\\\"name\\\"\" colon (" + " | ".join(pair_names) + ") ws \"}\""
        )
    grammar.extend(pair_rules)
    grammar.extend(schema_lines)
    return "\n".join(grammar)


# --------------------------------------------------------------------------
# Validation (post-decode, Strategia 3)
# --------------------------------------------------------------------------

def validate_tool_call(tool_call: dict, tools: Sequence[Any], *,
                         allow_final_answer: bool = False,
                         allow_disambiguation: bool = False
                         ) -> tuple[bool, str]:
    """Valida tool_call sulla SOLA correttezza top-level (required keys
    presenti + tipo dict). Non valida nested schemas: l'executor stesso
    e' responsabile della deep-validation con messaggi specifici.

    Strategia 3 ADR 0133: blocco grossolani errori del LLM senza
    sovrapporsi alla validation built-in dell'executor.

    Quando `allow_final_answer=True`, accetta il synthetic tool
    `final_answer({message: string})`. Coerente con `generate_tool_grammar`.

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
    if allow_final_answer and name == FINAL_ANSWER_TOOL_NAME:
        if not isinstance(args, dict):
            return False, "arguments deve essere object"
        msg_val = args.get("message")
        if not isinstance(msg_val, str):
            return False, "final_answer richiede 'message' (string)"
        return True, ""
    if allow_disambiguation and name == DISAMBIG_TOOL_NAME:
        if not isinstance(args, dict):
            return False, "arguments deve essere object"
        q_val = args.get("question")
        if not isinstance(q_val, str) or not q_val.strip():
            return False, (f"{DISAMBIG_TOOL_NAME} richiede 'question' "
                           "(string non vuota)")
        opts = args.get("options")
        if not isinstance(opts, list) or len(opts) < 2:
            return False, (f"{DISAMBIG_TOOL_NAME} richiede 'options' "
                           "(array di almeno 2 stringhe)")
        if not all(isinstance(o, str) and o.strip() for o in opts):
            return False, (f"{DISAMBIG_TOOL_NAME}: ogni option deve essere "
                           "stringa non vuota")
        return True, ""
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
# Pool filter (14/5/2026): escape-hatch + provider-specific exclusion.
# Funzione pura testabile: input = tools_for_step + user_query,
# output = subset filtrato. Determinismo §7.9.
# --------------------------------------------------------------------------

# Marker per ogni provider suffix (estensibile). Lookup table = single
# source of truth, niente if/elif per-provider sparsi nel codice.
_PROVIDER_SUFFIX_MARKERS: dict[str, tuple[str, ...]] = {
    "_google_workspace": (
        "google", "drive", "gmail", "gdrive",
        "workspace", "calendar google", "g suite",
    ),
    "_github": (
        "github", "pr", "issue", "issues",
        "repo", "repository", "commit", "branch",
        "workflow", "gist", "fork", "merge",
    ),
}

_UNDO_MARKERS: tuple[str, ...] = (
    "annulla", "annullare", "annullo", "annullala",
    "undo", "ripristina", "ripristino", "ripristinare",
    "torna indietro", "torna su", "rollback",
    "disfa", "disfare", "annulla l'ultimo",
)

# Markers semantici per `*_tasks` (scheduler v2). Se la query NON contiene
# nessun marker, escludi `create/list/delete/read/set_tasks` +
# `read_tasks_history` dal pool grammar. Senza, il PLANNER LLM li seleziona
# erroneamente su query mail/file/etc (es. "cerca mail bookings" → PLANNER
# scelse read_tasks_history per ambiguità nome).
_TASKS_MARKERS: tuple[str, ...] = (
    "task", "tasks", "schedule", "scheduled", "schedula", "schedulare",
    "ricorrente", "ricorrenti", "promemoria", "reminder", "timer",
    "ricordami", "ricordati", "ricorda", "remind",
    "daily", "weekly", "hourly",
    "storico", "history", "esecuzione", "esecuzioni",
    "cancella task", "elenca task", "lista task",
)

# "ogni"/"fra"/"every" da soli sono parole COMUNI (es. "leggi ogni messaggio",
# "differenza fra A e B") e baiterebbero i `*_tasks` nel pool. Ma sono marker
# di scheduling QUANDO adiacenti a un'unita' temporale ("ogni giorno", "ogni 30
# minuti", "fra 2 ore"). Regex deterministico (§7.9), complementare a
# _TASKS_MARKERS, preserva la rilevazione dei monitor schedulati.
_RE_SCHEDULE_PHRASE = re.compile(
    r"\b(?:ogni|every)\s+(?:\d+\s*)?"
    r"(?:second|minut|min\b|or[ae]\b|giorn|d[ìi]\b|settiman|mes[ei]\b|ann|"
    r"day|hour|week|month|year)"
    r"|\b(?:fra|tra)\s+(?:\d+|un[ao']?|mezz)",
    re.IGNORECASE,
)
_TASKS_NAMES: tuple[str, ...] = (
    "create_tasks", "list_tasks", "delete_tasks",
    "read_tasks", "set_tasks", "read_tasks_history",
)

# Skill-admin builtin (asse 2): `list_skills`/`set_skills` baiterebbero query
# generiche di lista/attivazione ("elenca i file", "attiva il monitor"). Nel
# pool grammar SOLO se la query nomina esplicitamente le SKILL/capacità.
_SKILLS_MARKERS: tuple[str, ...] = (
    "skill", "skills", "capacità", "capacita", "capability", "capabilities",
    "modulo", "moduli", "module", "modules",
)
_SKILLS_NAMES: tuple[str, ...] = ("list_skills", "set_skills")


# Token candidato a path filesystem: sequenza non-spazio con almeno uno '/'.
_RE_FS_PATH_TOKEN = re.compile(r"\S*/\S*")
# Estensione file alla fine di un segmento (`/issues.md`, `/foo.py`).
_RE_PATH_EXT = re.compile(r"/[^/]+\.[A-Za-z0-9]{1,5}$")


def _looks_like_fs_path(tok: str) -> bool:
    """True se `tok` e' CHIARAMENTE un path filesystem (non un compound di
    dominio come 'issue/PR' ne' 'e/o'). Criteri: URL escluso; anchor esplicito
    (`/`, `~`, `./`, `../`); oppure >=3 segmenti (a/b/c); oppure termina con
    `/file.ext`. Cosi' '/opt/metnos/issues' e 'github/issues.md' sono path, ma
    'issue/PR', 'e/o', 'and/or' NON lo sono (preserva i loro marker)."""
    if "://" in tok:
        return False
    if tok[:1] in "/~" or tok.startswith(("./", "../")):
        return True
    if tok.count("/") >= 2:
        return True
    return bool(_RE_PATH_EXT.search(tok))


def _strip_fs_paths(query_lc: str) -> str:
    """Rimuove SOLO i token che sono path-filesystem (preserva URL e compound
    di dominio). Usato prima del match dei marker: 'issues' in
    '/opt/metnos/issues' non deve innescare il provider github, ma 'issue/PR'
    SI'. Deterministico §7.9."""
    def _drop(m: "re.Match[str]") -> str:
        tok = m.group(0)
        return " " if _looks_like_fs_path(tok) else tok
    return _RE_FS_PATH_TOKEN.sub(_drop, query_lc)


def _has_word(query_lc: str, words: tuple[str, ...]) -> bool:
    """Match word-boundary (regex \\b) per evitare falsi positivi
    tipo `qua` ⊆ `qualcosa`, `vicino` ⊆ `vicinato`."""
    for w in words:
        pat = r"\b" + re.escape(w) + r"\b"
        if re.search(pat, query_lc):
            return True
    return False


# §7.3 verb-aware filtering: universal helpers che vanno SEMPRE inclusi
# anche quando filtriamo per verbo (servono a quasi tutti i framework).
_UNIVERSAL_HELPERS = frozenset({
    "describe_entries", "filter_entries", "sort_entries",
    "classify_entries", "extract_entries", "compute_entries", "get_inputs",
    "undo_last_turn",
})


def filter_pool_by_intent_verb(tools: Sequence[Any], intent_verb: str,
                                 *, include_universals: bool = True,
                                 always_include: Sequence[str] = ()
                                 ) -> tuple[list[Any], list[str]]:
    """§7.3 Task #40 — Verb-aware GBNF: restringe il pool ai tool che
    matchano il verbo dell'intent + universal helpers + always_include.

    Pattern:
      - intent_verb='find' → tool tipo find_files, find_messages, ...
      - intent_verb='read' → tool read_files, read_messages, ...
      - intent_verb='get'  → tool get_now, get_files, get_processes, ...

    Esclusi:
      - tool con first_segment != intent_verb (eccetto universal helpers)
      - SE intent_verb assente/vuoto: ritorna pool invariato (no filter)

    Safety: se filter azzera il pool, ritorna originale.

    Args:
      tools: pool corrente (sequence di Executor o dict-like)
      intent_verb: verbo canonico (lowercase) da intent_extractor
      include_universals: includi describe/filter/sort/... entries
      always_include: nomi tool sempre presenti (override filtro)

    Returns:
      (pool_filtrato, lista nomi esclusi)
    """
    if not intent_verb:
        return list(tools), []
    verb = intent_verb.lower().strip()
    if not verb:
        return list(tools), []
    excluded: list[str] = []
    keep: list = []
    always_set = set(always_include) | (_UNIVERSAL_HELPERS if include_universals else set())
    # §2.2 — i verbi-produttori (find/get/read/list) sono la SORGENTE di quasi
    # ogni pipeline e vanno SEMPRE tenuti nel pool, oltre al verbo dell'intent:
    #  - intent produttore (get/find/...): sono intercambiabili a livello di
    #    routing ("quanti file/foto" estrae verb=get ma l'enumeratore e' find_*);
    #  - intent transformer (sort/filter/classify/...): richiede un producer a
    #    monte (regola TRANSFORMER RICHIEDE PRODUCER) → senza producer il
    #    Proposer ALLUCINA un tool (es. list_processes per "che processi
    #    consumano memoria" con intent=sort) → malformata;
    #  - intent mutating (delete/move/...): serve un producer per individuare i
    #    target ("cancella i file vecchi" → find_files + delete_files).
    # Il prefilter inietta gia' i precursor: il verb-filter NON deve stripparli.
    try:
        from vocab import PRODUCER_VERBS as _PROD
    except Exception:
        _PROD = frozenset({"read", "find", "get", "list"})
    allowed_segs = set(_PROD) | {verb}
    for t in tools:
        name = _extract_name(t)
        if not name:
            continue
        if name in always_set:
            keep.append(t)
            continue
        first_seg = name.split("_", 1)[0]
        if first_seg in allowed_segs:
            keep.append(t)
        else:
            excluded.append(name)
    if not keep:
        return list(tools), []  # safety: filter vuoto → restore
    return keep, excluded


def filter_pool_for_grammar(tools: Sequence[Any], user_query: str,
                             proximity_markers: tuple[str, ...] = ()
                             ) -> tuple[list[Any], list[str]]:
    """Filtra pool per grammar-mode escludendo escape-hatch builtin senza
    marker semantico. Ritorna (pool_filtrato, lista nomi esclusi).

    Esclusi:
      - `request_new_executor` se >=3 canonical (sempre, escape globale).
      - `request_location_from_user` se manca marker prossimita'.
      - `undo_last_turn` se manca marker undo.
      - `<verb>_<obj>_<provider_suffix>` se manca marker provider.
      - `_SCHEDULING_CONFLICT_TOOLS` se la query ha marker scheduling
        (forza scelta su list_tasks/delete_tasks/read_tasks).

    Determinismo §7.9. Niente LLM, niente IO.
    """
    query_lc = (user_query or "").lower()
    # I marker di dominio si valutano sulla query SENZA i path filesystem:
    # 'issues' in '/opt/metnos/issues' non e' il provider github (URL intatti).
    query_markers = _strip_fs_paths(query_lc)
    excluded: list[str] = []
    # Canonical = tutto tranne escape-hatch globali
    canonical = [
        t for t in tools
        if _extract_name(t) not in (
            "request_new_executor",
            "request_location_from_user",
        )
    ]
    if len(canonical) >= 3:
        excluded.append("request_new_executor")
    if not _has_word(query_markers, proximity_markers):
        excluded.append("request_location_from_user")
    if not _has_word(query_markers, _UNDO_MARKERS):
        excluded.append("undo_last_turn")
    # Tasks builtin: escludi se query non ha marker scheduling (anti-bait
    # del PLANNER LLM su query mail/file ambigue).
    if not (_has_word(query_markers, _TASKS_MARKERS)
            or _RE_SCHEDULE_PHRASE.search(query_markers)):
        excluded.extend(_TASKS_NAMES)
    # Skill-admin builtin: escludi se la query non nomina skill/capacità.
    if not _has_word(query_markers, _SKILLS_MARKERS):
        excluded.extend(_SKILLS_NAMES)
    # Indice nomi presenti nel pool (per il check "esiste canonical?")
    _names_in_pool = {_extract_name(t) for t in tools}
    for suffix, markers in _PROVIDER_SUFFIX_MARKERS.items():
        if not _has_word(query_markers, markers):
            # Marker provider ASSENTE → escludi tool con suffix.
            # In produzione l'esclusione e' INCONDIZIONATA: un tool con
            # provider-suffix non deve mai entrare nel pool grammar senza il
            # suo marker (es. find_issues_github su "leggi i file in
            # /opt/metnos/issues" → deve restare find_files locale, anche se
            # non esiste un canonical 'find_issues').
            # Eccezione SOLO E2E: con METNOS_HIDE_EXECUTORS i canonical sono
            # nascosti di proposito, quindi se il canonical equivalente manca
            # si tiene il provider-suffixed come unica opzione semantica.
            _hide_mode = bool(os.environ.get("METNOS_HIDE_EXECUTORS"))
            for t in tools:
                name = _extract_name(t)
                if not name.endswith(suffix):
                    continue
                canonical_name = name[: -len(suffix)].rstrip("_")
                if canonical_name in _names_in_pool or not _hide_mode:
                    excluded.append(name)
        else:
            # Marker provider PRESENTE → escludi canonical (non-suffixed)
            # SE esiste provider equivalente nel pool. Forza scelta univoca
            # del backend coerente con la query (universale §7.3, no
            # marker-by-tool hardcoded).
            for t in tools:
                name = _extract_name(t)
                if name.endswith(suffix):
                    continue
                if name in excluded:
                    continue
                provider_eq = f"{name}{suffix}"
                if provider_eq in _names_in_pool:
                    excluded.append(name)
    filtered = [t for t in tools if _extract_name(t) not in excluded]
    # Safety: se filter ha azzerato il pool, ripristina originale.
    if not filtered:
        return list(tools), []
    return filtered, excluded


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
