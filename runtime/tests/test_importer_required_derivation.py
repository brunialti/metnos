"""Importer: derivazione di `required` dagli ESEMPI di SKILL.md (non argparse).

Bug pre-2/6: l'importer marcava TUTTI i flag optional (argparse non ha il
concetto per i flag) -> i manifest skill nascevano senza required, e i 13
*_github andavano corretti a mano. Fix (skill_translator): un flag e' required
IFF compare in OGNI esempio del sub-command. Deterministico §7.9, universale.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

from skill_parser import SkillFlag, SkillSubCommand
from skill_translator import build_args


def _sc(examples, flags):
    sc = SkillSubCommand(domain="github", action="send")
    sc.flags = {name: SkillFlag(name=name, **kw) for name, kw in flags.items()}
    sc.examples = examples
    return sc


def test_flag_in_all_examples_is_required():
    sc = _sc(
        examples=[
            "api comments send --repo o/x --target issue:1 --body hi --label q",
            "api comments send --repo o/y --target pr:2 --body ok",
        ],
        flags={"repo": {}, "target": {}, "body": {}, "label": {}},
    )
    req = sorted(a.name for a in build_args(sc, has_entries_output=False) if a.required)
    # repo/target/body in TUTTI gli esempi -> required; label solo in 1 -> no.
    assert req == ["body", "repo", "target"]


def test_bool_switch_never_required():
    sc = _sc(
        examples=["api x --repo o/x --draft", "api x --repo o/y --draft"],
        flags={"repo": {}, "draft": {"is_bool_switch": True}},
    )
    req = {a.name for a in build_args(sc, has_entries_output=False) if a.required}
    assert "repo" in req
    assert "draft" not in req       # presenza = valore, mai required


def test_no_examples_means_no_required():
    sc = _sc(examples=[], flags={"repo": {}})
    req = [a.name for a in build_args(sc, has_entries_output=False) if a.required]
    assert req == []                # conservativo: niente esempi -> niente required


def test_positional_id_not_hard_required():
    # ISSUE_NUMBER positional -> singolare/plurale/entries, NESSUNO hard-required
    # (§2.1 vettoriale: l'executor valida 'almeno uno').
    sc = _sc(
        examples=["api issues read --repo o/x ISSUE_NUMBER"],
        flags={"repo": {}},
    )
    sc.positional_args = ["ISSUE_NUMBER"]
    args = build_args(sc, has_entries_output=False)
    req = {a.name for a in args if a.required}
    assert "repo" in req
    assert "number" not in req and "numbers" not in req and "entries" not in req
