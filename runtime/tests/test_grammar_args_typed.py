"""CP5 grammar-on-args (ADR 0177 T2/M4, 6/7/2026).

`build_framework_grammar_typed` vincola gli args di ogni step allo schema del
tool: enum→alternation (dominio-chiuso §2.4), testo libero→jsonStr. Union
discriminata step↔args. Riusa la macchina di tool_grammar.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))
os.environ.setdefault("METNOS_ENGINE", "v3")


def _cat():
    from loader import load_catalog
    return load_catalog()


def _g(pool):
    from engine.grammar_framework import build_framework_grammar_typed
    return build_framework_grammar_typed(pool, _cat())


def test_enum_becomes_alternation():
    g = _g(["list_dirs", "compute_entries", "final_answer"])
    # list_dirs.sort ∈ {name,mtime,size} → i 3 literal presenti
    assert "mtime" in g and "size" in g
    # compute_entries.op ∈ {max,min,avg,sum,...}
    assert "sum" in g and "avg" in g


def test_free_text_stays_jsonstr():
    g = _g(["find_files", "final_answer"])
    # find_files.pattern è testo libero → nessun enum, usa jsonStr
    assert "jsonStr" in g


def test_step_is_discriminated_union():
    g = _g(["list_dirs", "find_files", "final_answer"])
    step_line = next(l for l in g.splitlines() if l.startswith("step ::="))
    # step ::= stepListDirs | stepFindFiles | stepFinalAnswer
    assert "|" in step_line
    assert "stepListDirs" in step_line and "stepFinalAnswer" in step_line


def test_no_duplicate_rules():
    g = _g(["list_dirs", "compute_entries", "write_files", "send_messages",
            "create_events", "find_files", "final_answer"])
    names = [l.split("::=")[0].strip() for l in g.splitlines() if "::=" in l]
    dup = [n for n in set(names) if names.count(n) > 1]
    assert not dup, f"regole duplicate (GBNF invalida): {dup}"


def test_llama_workarounds():
    g = _g(["list_dirs", "compute_entries", "final_answer"])
    # una regola per riga (no multi ::=)
    assert not [l for l in g.splitlines() if l.count("::=") > 1]
    # nomi-regola senza underscore (camelCase)
    names = [l.split("::=")[0].strip() for l in g.splitlines() if "::=" in l]
    assert not [n for n in names if "_" in n]


def test_no_dangling_refs():
    g = _g(["list_dirs", "compute_entries", "write_files", "send_messages",
            "find_files", "final_answer"])
    lines = [l for l in g.splitlines() if "::=" in l]
    defined = {l.split("::=")[0].strip() for l in lines}
    refs = set()
    for l in lines:
        rhs = l.split("::=", 1)[1]
        rhs = re.sub(r'"(?:\\.|[^"\\])*"', ' ', rhs)   # togli literal
        rhs = re.sub(r'\[[^\]]*\]', ' ', rhs)          # togli char-class
        refs.update(re.findall(r'[a-zA-Z][a-zA-Z0-9]*', rhs))
    dangling = [r for r in refs if r not in defined]
    assert not dangling, f"regole referenziate ma non definite: {dangling}"


def test_runtime_resolved_stripped():
    # write_files.client è runtime_resolved → NON deve comparire come prop
    # vincolata (il runtime lo inietta). Verifica indiretta: la grammar non
    # forza "client" come chiave required.
    from engine.grammar_framework import _strip_runtime_resolved
    cat = _cat()
    ex = next(e for e in cat if e.name == "write_files")
    sch = _strip_runtime_resolved(getattr(ex, "args_schema", None))
    if sch is not None:  # se ha props tipizzabili
        props = sch.get("properties", {})
        # nessuna prop rimasta deve essere runtime_resolved
        orig = getattr(ex, "args_schema", {}).get("properties", {})
        for k in props:
            assert not orig.get(k, {}).get("runtime_resolved"), \
                f"{k} runtime_resolved non strippato"


def test_fallback_without_catalog():
    from engine.grammar_framework import (build_framework_grammar_typed,
                                          build_framework_grammar)
    # catalog None → fallback alla grammar solo-nomi
    g = build_framework_grammar_typed(["find_files"], None)
    assert g == build_framework_grammar(["find_files"])


def test_fallback_empty_pool():
    from engine.grammar_framework import (build_framework_grammar_typed,
                                          GRAMMAR_FRAMEWORK)
    assert build_framework_grammar_typed([], _cat()) == \
        __import__("engine.grammar_framework", fromlist=["build_framework_grammar"]
                   ).build_framework_grammar([])
