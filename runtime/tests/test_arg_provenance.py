"""FASE 0 architettura provenienza args (spec_args_provenance_architecture.md).
Classificatore deterministico runtime/clause/semantic. Dati puri, no side-effect.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

import arg_provenance as ap  # noqa: E402


def test_runtime_by_marker_and_convention():
    # marker esplicito
    assert ap.classify_arg("x", {"runtime_resolved": True, "type": "string"}) == "runtime"
    # convenzione §2.2/0136 anche senza marker
    assert ap.classify_arg("client", {"type": "string", "enum": ["local"]}) == "runtime"
    assert ap.classify_arg("account", {"type": "string"}) == "runtime"
    assert ap.classify_arg("provider", {"type": "string"}) == "runtime"


def test_clause_by_enum():
    assert ap.classify_arg("sort", {"type": "string", "enum": ["name", "mtime"]}) == "clause"
    assert ap.classify_arg("op", {"enum": ["max", "min"]}) == "clause"


def test_clause_by_extractable_name():
    for nm in ("path", "paths", "base_path", "pattern", "url", "max_results",
               "time_window", "date", "to", "repo"):
        assert ap.classify_arg(nm, {"type": "string"}) == "clause", nm


def test_semantic_default():
    assert ap.classify_arg("columns", {"type": "array"}) == "semantic"
    assert ap.classify_arg("content", {"type": "string"}) == "semantic"
    assert ap.classify_arg("summary", {"type": "string"}) == "semantic"


def test_runtime_wins_over_enum():
    # client ha enum ["local"] MA è config → runtime, non clause
    assert ap.classify_arg("client", {"enum": ["local"]}) == "runtime"


def test_provenance_map_and_report_on_catalog():
    from loader import load_catalog
    cat = load_catalog()
    rep = ap.provenance_report(cat)
    # ogni arg dichiarato è classificato (nessuna classe mancante)
    assert rep["total_args"] > 0
    assert sum(rep["by_class"].values()) == rep["total_args"]
    # client/account/provider marcati DEVONO risultare runtime
    ex = next(e for e in cat if e.name == "write_files")
    assert ap.provenance_map(ex).get("client") == "runtime"
    # il report espone la lista di config non-marcati (cleanup manifest)
    assert isinstance(rep["unmarked_config"], list)


def test_no_side_effects():
    # provenance_report è read-only: due chiamate danno lo stesso risultato
    from loader import load_catalog
    cat = load_catalog()
    r1 = ap.provenance_report(cat)["by_class"]
    r2 = ap.provenance_report(cat)["by_class"]
    assert r1 == r2
