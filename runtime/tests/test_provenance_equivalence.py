"""PROV.2 — ORACOLO DI EQUIVALENZA (architettura provenienza args, 6/7/2026).

La RETE che protegge errore=0 durante il refactor FASE 2 (clause-derive
autoritativo che sussume fill_clause_args & co.). Cattura l'output della
GUARD_PIPELINE ATTUALE su un corpus di framework grezzi (raw proposer output)
come GOLDEN congelato su file. Ogni fase del refactor deve produrre output
BYTE-IDENTICO sui casi che oggi funzionano — se diverge, si vede esattamente
quale caso/campo cambia PRIMA di procedere.

Il golden è generato una volta da `--regen` (con il pipeline attuale) e
committato. Il test lo ricarica e verifica che il pipeline lo riproduca.
Quando PROV.3 lo modifica, questo test è il gate: verde = comportamento
osservabile preservato; rosso = divergenza da capire.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))
os.environ.setdefault("METNOS_ENGINE", "v3")

_GOLDEN = Path(__file__).resolve().parent / "data" / "guard_equivalence_golden.json"


def _catalog():
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    return filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER)


def _corpus():
    """Casi (name, framework, intent, query). Riusa i 7 strutturali del
    contratto + casi ARGS-PESANTI (args clause-derivabili OMESSI dal proposer
    che fill_clause_args oggi riempie — quelli che PROV.3 deve riprodurre)."""
    from engine.types import Framework, StepSpec, Intent
    import test_guard_pipeline_contract as _T

    cases = []
    for cname, fwk, intent, query in _T._cases():
        cases.append((cname, fwk, intent, query))

    def fw(*steps):
        return Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in steps]
                         + [StepSpec(tool="final_answer", args={})])

    # ARGS-PESANTI: il proposer OMETTE pattern/path/count → fill_clause_args li deriva.
    cases += [
        ("args_missing_pattern",
         fw(("find_files", {"base_path": "/tmp"})),
         Intent(verb="find", object="files",
                actions=[{"verb": "find", "object": "files"}]),
         "trova i file .log in /tmp"),
        ("args_missing_path",
         fw(("list_dirs", {})),
         Intent(verb="list", object="files",
                actions=[{"verb": "list", "object": "files"}]),
         "elenca la cartella /opt/metnos/decisions"),
        ("args_count_in_clause",
         fw(("find_files", {"base_path": "/opt/metnos/runtime", "pattern": "*.py"})),
         Intent(verb="find", object="files",
                actions=[{"verb": "find", "object": "files"}]),
         "trova i primi 5 file .py in /opt/metnos/runtime"),
        ("args_universal_pattern",
         fw(("find_files", {"base_path": "/tmp", "pattern": "*"})),
         Intent(verb="find", object="files",
                actions=[{"verb": "find", "object": "files"}]),
         "trova i file .txt in /tmp"),
    ]
    return cases


def _apply(fwk, intent, query, cat):
    from engine import dispatch as D
    import copy
    return D._apply_deterministic_structure_guards(
        copy.deepcopy(fwk), intent, query, cat).to_dict()


def regen():
    """Genera il golden dal pipeline ATTUALE. Chiamare SOLO deliberatamente
    (quando il comportamento cambia INTENZIONALMENTE), mai in CI."""
    cat = _catalog()
    golden = {name: _apply(fwk, intent, query, cat)
              for name, fwk, intent, query in _corpus()}
    _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    _GOLDEN.write_text(json.dumps(golden, ensure_ascii=False, indent=2,
                                  sort_keys=True))
    print(f"golden rigenerato: {len(golden)} casi → {_GOLDEN}")


def test_pipeline_matches_golden():
    assert _GOLDEN.exists(), "golden mancante: python3 -m runtime.tests.test_provenance_equivalence --regen"
    golden = json.loads(_GOLDEN.read_text())
    cat = _catalog()
    diffs = []
    for name, fwk, intent, query in _corpus():
        got = _apply(fwk, intent, query, cat)
        exp = golden.get(name)
        if exp is None:
            diffs.append(f"{name}: assente dal golden (rigenera)")
        elif got != exp:
            diffs.append(f"{name}: DIVERGE\n  atteso: {json.dumps(exp, ensure_ascii=False)[:200]}\n  ottenuto: {json.dumps(got, ensure_ascii=False)[:200]}")
    assert not diffs, "EQUIVALENZA ROTTA (comportamento cambiato):\n" + "\n".join(diffs)


def test_golden_covers_corpus():
    golden = json.loads(_GOLDEN.read_text()) if _GOLDEN.exists() else {}
    corpus_names = {name for name, *_ in _corpus()}
    assert corpus_names <= set(golden), \
        f"golden non copre: {corpus_names - set(golden)}"


if __name__ == "__main__":
    if "--regen" in sys.argv:
        regen()
