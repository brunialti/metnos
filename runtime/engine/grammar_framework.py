"""engine/grammar_framework.py — GBNF grammar per Framework JSON (engine v2).

GBNF che vincola l'output del Proposer alla forma Framework:
`{"steps": [...], "fillers": {...}?, "final_message": "..."?}`.

Quando attivo (env METNOS_PROPOSER_GRAMMAR=1) garantisce parse rate 100%
forzando think=False (ADR 0133: grammar + think collidono su max_tokens).

Relocato da _legacy/praxis_propose.py durante la bonifica del flusso
decisionale duplicato (Engine v2 unico vivo). §7.11: niente path assoluti
hardcoded verso _legacy.
"""
from __future__ import annotations

import re as _re

# `__TOOLNAME_RULE__` e' un placeholder: `build_framework_grammar(pool)` lo
# sostituisce con `toolName ::= "\"a\"" | "\"b\"" | ...` (alternation dei nomi
# del pool) e cambia lo `step` per usarlo. Senza pool (fallback) `tool` resta
# uno `string` libero (back-compat).
# IMPORTANTE: ogni regola su UNA SOLA RIGA. llama.cpp tratta il newline come
# terminatore di regola: una regola multi-riga (es. il vecchio `root`/`fillerVal`
# spezzati) viene parsata come regole spurie → grammar INVALIDA → llama-server
# la scarta SILENZIOSAMENTE e genera senza vincolo (bug 2/6/2026: il proposer
# allucinava tool inesistenti perche' la grammar non era mai applicata davvero).
_GRAMMAR_FRAMEWORK_TMPL = r"""root ::= "{" ws "\"steps\":" ws stepList ("," ws "\"fillers\":" ws fillerObj)? ("," ws "\"final_message\":" ws string)? ws "}"
stepList ::= "[" ws step ("," ws step)* ws "]"
step ::= "{" ws "\"tool\":" ws __TOOL_VALUE__ "," ws "\"args\":" ws args ws "}"
args ::= "{" ws "}" | "{" ws kv ("," ws kv)* ws "}"
kv ::= string ":" ws value
value ::= string | number | "true" | "false" | "null" | args | arrayVal
arrayVal ::= "[" ws "]" | "[" ws value ("," ws value)* ws "]"
fillerObj ::= "{" ws "}" | "{" ws fk ("," ws fk)* ws "}"
fk ::= string ":" ws fillerVal
fillerVal ::= "{" ws "\"prompt\":" ws string ("," ws "\"default\":" ws string)? ("," ws "\"tier\":" ws string)? ws "}"
string ::= "\"" charSeq "\""
charSeq ::= ([^"\\] | "\\" anyEscape)*
anyEscape ::= ["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
number ::= "-"? ("0" | [1-9] [0-9]*) ("." [0-9]+)? ([eE] [+-]? [0-9]+)?
ws ::= [ \t\n\r]*
__TOOLNAME_RULE__"""

# Default (fallback): `tool` = string libero. Usato quando il pool non e'
# disponibile. Il path moderno passa per build_framework_grammar(pool).
GRAMMAR_FRAMEWORK = _GRAMMAR_FRAMEWORK_TMPL.replace(
    "__TOOL_VALUE__", "string").replace("__TOOLNAME_RULE__", "")

# Nomi tool: snake_case + eventuale suffisso provider. Validazione difensiva
# prima di iniettarli come literal GBNF (no metacaratteri).
_SAFE_TOOL_NAME = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def build_framework_grammar(pool_names) -> str:
    """GBNF Framework con `tool` VINCOLATO ai nomi del pool (no allucinazioni).

    Senza questo, `step.tool` e' uno `string` libero → il Proposer poteva
    emettere nomi inesistenti (es. `get_issues`) o sub-ottimali ignorando il
    tool giusto in pool (bug 2/6/2026: `find_issues_github` in pool #1 ma il
    LLM sceglieva `find_urls`/`get_issues`). Vincolando `tool` a una
    alternation dei nomi reali, l'LLM PUO' scegliere solo dal pool.

    `pool_names`: iterable di nomi tool. `final_answer` aggiunto sempre
    (terminale valido). Se vuoto/invalid → fallback a GRAMMAR_FRAMEWORK
    (string libero), back-compat."""
    names: list[str] = []
    seen = set()
    for n in list(pool_names or []) + ["final_answer"]:
        if isinstance(n, str) and _SAFE_TOOL_NAME.match(n) and n not in seen:
            seen.add(n)
            names.append(n)
    if not names:
        return GRAMMAR_FRAMEWORK
    alt = " | ".join('"\\"%s\\""' % n for n in names)
    return _GRAMMAR_FRAMEWORK_TMPL.replace(
        "__TOOL_VALUE__", "toolName").replace(
        "__TOOLNAME_RULE__", "toolName ::= " + alt + "\n")


# ── CP5 grammar-on-args (ADR 0177 T2/M4, 6/7/2026) ─────────────────────────
# Vincola ANCHE gli args di ogni step allo SCHEMA del tool (dominio-chiuso
# §2.4: enum→alternation; testo libero→jsonStr). Il `step` diventa una UNION
# DISCRIMINATA per-tool che lega `"tool":"name"` alla regola args di QUEL tool
# — così l'LLM non può emettere `sort:"recent"` fuori dominio, e i guard args
# (dispatch: _promote/_demote_count, _fill_clause_args sugli enum,
# _decontaminate_reader_qualifier…) diventano no-op sugli enum. Riusa la
# macchina schema→GBNF di `tool_grammar` (ADR 0133, 55 test). Gli args
# `runtime_resolved` (client/account/provider) sono ESCLUSI: il runtime li
# inietta, l'LLM non deve emetterli (Lesson A3/B1). Tool senza schema →
# `args` libero come oggi (fallback, mai bloccare).

# Struttura Framework SENZA la vecchia regola `args`/`kv`/`value`/`arrayVal`
# (rimpiazzate dalle args tipizzate per-tool) ma CON string/number/ws/fillers.
_GRAMMAR_FRAMEWORK_TYPED_TMPL = r"""root ::= "{" ws "\"steps\":" ws stepList ("," ws "\"fillers\":" ws fillerObj)? ("," ws "\"final_message\":" ws string)? ws "}"
stepList ::= "[" ws step ("," ws step)* ws "]"
step ::= __STEP_UNION__
fillerObj ::= "{" ws "}" | "{" ws fk ("," ws fk)* ws "}"
fk ::= string ":" ws fillerVal
fillerVal ::= "{" ws "\"prompt\":" ws string ("," ws "\"default\":" ws string)? ("," ws "\"tier\":" ws string)? ws "}"
string ::= "\"" charSeq "\""
charSeq ::= ([^"\\] | "\\" anyEscape)*
anyEscape ::= ["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
number ::= "-"? ("0" | [1-9] [0-9]*) ("." [0-9]+)? ([eE] [+-]? [0-9]+)?
ws ::= [ \t\n\r]*"""

# args liberi per i tool senza schema (fallback) + final_answer.
_FREE_ARGS_RULES = (
    "argsFree ::= \"{\" ws \"}\" | \"{\" ws kvFree (\",\" ws kvFree)* ws \"}\"\n"
    "kvFree ::= string \":\" ws valueFree\n"
    "valueFree ::= string | number | \"true\" | \"false\" | \"null\" | argsFree | arrayFree\n"
    "arrayFree ::= \"[\" ws \"]\" | \"[\" ws valueFree (\",\" ws valueFree)* ws \"]\""
)


def _strip_runtime_resolved(schema):
    """Ritorna una COPIA dello schema senza gli args `runtime_resolved`
    (proprietà + required): l'LLM non deve emetterli, li inietta il runtime
    (proposer._render_tool_pool li nasconde per la stessa ragione). None se
    lo schema non è un dict tipizzato utile."""
    if not isinstance(schema, dict):
        return None
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    keep = {k: v for k, v in props.items()
            if not (isinstance(v, dict) and v.get("runtime_resolved"))}
    if not keep:
        # tutti gli args sono runtime_resolved → nessun arg-intento: args liberi
        return None
    req = [r for r in (schema.get("required") or []) if r in keep]
    out = {"type": "object", "properties": keep}
    if req:
        out["required"] = req
    # requires_one_of non è JSON-schema standard: ignorato per la grammatica
    # (resta enforce a validazione). oneOf/anyOf/allOf: _emit_tool_args li
    # fa cadere su jsonObject (fallback) da solo.
    return out


def build_framework_grammar_typed(pool_names, catalog) -> str:
    """CP5: GBNF Framework con `tool` E `args` vincolati allo schema per-tool.

    `pool_names`: iterable di nomi tool. `catalog`: lista Executor (per lo
    schema). Fallback a `build_framework_grammar(pool_names)` se il catalog
    manca o nessun tool ha uno schema tipizzabile (back-compat totale).

    Ogni step è una regola per-tool `stepX ::= "{" "\"tool\":\"x\","
    "\"args\":" argsX "}"` con `argsX` dallo schema (enum→alternation). Il
    tool senza schema utile usa `argsFree`. `final_answer` sempre presente
    con args liberi (terminale). L'union `step ::= stepA | stepB | …` lega
    tool↔args (no cross-contamination, come la discriminated union di
    tool_grammar)."""
    try:
        import tool_grammar as _tg
    except Exception:
        return build_framework_grammar(pool_names)
    if not catalog:
        return build_framework_grammar(pool_names)

    by_name = {}
    for e in catalog:
        n = getattr(e, "name", None)
        if isinstance(n, str):
            by_name[n] = e

    names: list[str] = []
    seen = set()
    for n in list(pool_names or []) + ["final_answer"]:
        if isinstance(n, str) and _SAFE_TOOL_NAME.match(n) and n not in seen:
            seen.add(n)
            names.append(n)
    if not names:
        return build_framework_grammar(pool_names)

    step_union: list[str] = []
    args_rule_lines: list[str] = []
    used_primitives: set[str] = set()
    typed_count = 0

    for n in names:
        cap = _tg._sanitize_rule_name(n)
        cap = cap[0].upper() + cap[1:] if cap else "Tool"
        step_rule = f"step{cap}"
        if n == "final_answer":
            args_expr = "argsFree"
        else:
            ex = by_name.get(n)
            schema = _strip_runtime_resolved(
                getattr(ex, "args_schema", None) if ex else None)
            if schema is None:
                args_expr = "argsFree"
            else:
                arg_rule_name, lines, used = _tg._emit_tool_args(n, schema)
                args_rule_lines.extend(lines)
                used_primitives.update(used)
                args_expr = arg_rule_name
                typed_count += 1
        step_union.append(step_rule)
        args_rule_lines.append(
            f"{step_rule} ::= \"{{\" ws \"\\\"tool\\\":\" ws "
            f"\"\\\"{n}\\\"\" \",\" ws \"\\\"args\\\":\" ws {args_expr} ws \"}}\"")

    if typed_count == 0:
        # nessun tool aveva uno schema tipizzabile → niente da guadagnare
        return build_framework_grammar(pool_names)

    # primitive referenziate dalle regole args (jsonStr/jsonNum/…, colon, sep)
    used_primitives.update({"ws", "sep", "colon", "jsonObject"})
    prims = _tg._emit_primitives(used_primitives)

    step_union_rule = "step ::= " + " | ".join(step_union)
    body = _GRAMMAR_FRAMEWORK_TYPED_TMPL.replace(
        "step ::= __STEP_UNION__", step_union_rule)

    parts = [body, _FREE_ARGS_RULES] + prims + args_rule_lines
    # DEDUP per nome-regola: il template definisce già `ws` (e string/number),
    # le primitive di tool_grammar ridefiniscono `ws` → una regola definita
    # DUE volte rende la GBNF invalida e llama-server la scarta SILENZIOSAMENTE
    # (bug 2/6/2026). Prima-vince: il template (parts[0]) precede le primitive.
    seen_rules: set[str] = set()
    out_lines: list[str] = []
    for chunk in parts:
        for line in chunk.split("\n"):
            if "::=" not in line:
                out_lines.append(line)
                continue
            lhs = line.split("::=", 1)[0].strip()
            if lhs in seen_rules:
                continue  # duplicato: già definito prima (template vince)
            seen_rules.add(lhs)
            out_lines.append(line)
    return "\n".join(out_lines)
