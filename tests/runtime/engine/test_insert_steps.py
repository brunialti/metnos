"""Un solo modo di inserire uno step in un piano (6/8/2026).

Ogni guardia che inseriva uno step si costruiva la propria rinumerazione a
mano, e ne rimappava un pezzo: `route_folder_size` aggiornava `from_step` e
lasciava indietro `${stepN.field}` e `final_message` (con la docstring che lo
metteva per iscritto), `route_filename_pattern_to_find` non rimappava nulla.
E' la classe-bug T3 dell'audit 21/7, «il fix a piu' alto ritorno
anti-regressione dell'intero audit».

`insert_steps` e' l'unica porta: rimappa insieme args, `final_message` e
`fillers`. Il test di divieto qui sotto e' quello che impedisce che la classe
di bug si riapra alla prossima guardia.
"""
from __future__ import annotations

import ast
from pathlib import Path

from engine.dispatch import insert_steps
from engine.types import Framework, StepSpec

_DISPATCH = Path(__file__).resolve().parents[3] / "runtime" / "engine" / "dispatch.py"

# `ensure_extracted_period_scope` non INSERISCE soltanto: ricostruisce il piano
# e ricabla i consumatori dell'extract sul filtro appena creato, con una mappa
# che NON e' uno slittamento uniforme. Resta fuori, dichiarato.
_ALLOWED = {"_ensure_extracted_period_scope"}


def _fw():
    return Framework(
        steps=[StepSpec(tool="find_files", args={"base_path": "/tmp"}),
               StepSpec(tool="filter_entries", args={"from_step": 1}),
               StepSpec(tool="write_files",
                        args={"from_step": 2, "content": "${step2.text}"}),
               StepSpec(tool="final_answer", args={})],
        final_message="pronti ${step3.path} da ${step1.@count}")


def test_rimappa_from_step_stepref_e_final_message_insieme():
    fw = insert_steps(_fw(), 1, [StepSpec(tool="classify_entries",
                                          args={"from_step": 1})])
    assert [s.tool for s in fw.steps] == [
        "find_files", "classify_entries", "filter_entries", "write_files",
        "final_answer"]
    # il filtro consumava lo step 1 (find_files, non spostato) → resta 1
    assert fw.steps[2].args["from_step"] == 1
    # il write consumava lo step 2 (il filtro, ora terzo) → 3
    assert fw.steps[3].args["from_step"] == 3
    # i ${stepN} seguono la STESSA mappa, negli args e nel messaggio finale
    assert fw.steps[3].args["content"] == "${step3.text}"
    assert fw.final_message == "pronti ${step4.path} da ${step1.@count}"


def test_i_nuovi_step_non_vengono_rinumerati():
    """Chi costruisce lo step nuovo conosce gia' la numerazione finale: se lo
    rimappassimo, punterebbe a se stesso."""
    nuovo = StepSpec(tool="compute_entries", args={"from_step": 1})
    fw = insert_steps(_fw(), 1, [nuovo])
    assert fw.steps[1].args["from_step"] == 1


def test_inserire_in_coda_non_muove_nessuno():
    fw = insert_steps(_fw(), 4, [StepSpec(tool="describe_entries",
                                          args={"from_step": 2})])
    assert fw.steps[1].args["from_step"] == 1
    assert fw.steps[2].args["from_step"] == 2
    assert fw.final_message == "pronti ${step3.path} da ${step1.@count}"


def test_lista_vuota_e_no_op():
    prima = _fw()
    dopo = insert_steps(prima, 1, [])
    assert [s.tool for s in dopo.steps] == [
        "find_files", "filter_entries", "write_files", "final_answer"]
    assert dopo.final_message == "pronti ${step3.path} da ${step1.@count}"


def test_no_direct_steps_insert():
    """DIVIETO: nessuna guardia inserisce uno step a mano. Chi lo fa si porta
    dietro la propria rinumerazione, e la rinumerazione a mano e' sempre
    parziale — e' cosi' che nasce la classe-bug T3."""
    tree = ast.parse(_DISPATCH.read_text())
    colpevoli = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name in _ALLOWED or fn.name == "insert_steps":
            continue
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "insert"):
                continue
            target = node.func.value
            name = getattr(target, "id", None) or getattr(target, "attr", None)
            if name in ("steps", "new_steps"):
                colpevoli.append(f"{fn.name} (riga {node.lineno})")
    assert not colpevoli, (
        "inserimento di step a mano: usare insert_steps(framework, at, nuovi), "
        "che rimappa from_step, ${stepN} e final_message insieme.\n  "
        + "\n  ".join(colpevoli))
