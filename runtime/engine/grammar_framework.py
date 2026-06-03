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
