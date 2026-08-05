"""Test robustezza `_parse_predicate` (22/5/2026).

Coprono i 3 bug osservati su query reali "quali sono gli ip del server"
(turn a700ffa6 + 072b5eba): (1) crash su non-dict, (2) alias `field`/
`operator`, (3) hint health-field quando attribute richiesto vive in
`health.*` invece che fra gli attributi di processo.

Convergenza errore=0 §8.5: la query "quali sono gli ip del server" deve
risolvere in step ≤2 dopo il fix.

Run: `python3 -m pytest tests/runtime/executors/test_get_processes_predicate_robust.py -v`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EXEC_DIR = Path(__file__).resolve().parents[3] / "executors/get_processes"
sys.path.insert(0, str(_EXEC_DIR))


class TestNoCrashOnMalformedPredicate:
    """§2.8 no silent failure + §2.4 robustezza confine: mai crash Python."""

    def test_bare_string_predicate_returns_error_not_crash(self):
        """`filters=["ip"]` (stringa nuda) → errore strutturato, no AttributeError."""
        from get_processes import invoke
        r = invoke({"filters": ["ip"]})
        assert r["ok"] is False
        assert r["fail_count"] == 1
        assert "must be an object" in r["failed"][0]["error"]

    def test_int_predicate_returns_error(self):
        from get_processes import invoke
        r = invoke({"filters": [42]})
        assert r["ok"] is False
        assert "must be an object" in r["failed"][0]["error"]

    def test_list_predicate_returns_error(self):
        from get_processes import invoke
        r = invoke({"filters": [["nested", "list"]]})
        assert r["ok"] is False
        assert "must be an object" in r["failed"][0]["error"]


class TestFieldOperatorAliases:
    """Tolleranza confine NL: il planner LLM usa spesso `field`/`operator`."""

    def test_field_alias_for_attribute(self):
        from get_processes import _parse_predicate
        attr, op, val = _parse_predicate(
            {"field": "name", "op": "contains", "value": "python"}
        )
        assert attr == "name"

    def test_operator_alias_for_op(self):
        from get_processes import _parse_predicate
        attr, op, val = _parse_predicate(
            {"attribute": "name", "operator": "startswith", "value": "py"}
        )
        assert op == "startswith"

    def test_both_aliases_together(self):
        from get_processes import _parse_predicate
        attr, op, val = _parse_predicate(
            {"field": "pid", "operator": "=", "value": 1}
        )
        assert (attr, op, val) == ("pid", "=", 1)

    def test_canonical_attribute_op_still_works(self):
        from get_processes import _parse_predicate
        attr, op, val = _parse_predicate(
            {"attribute": "name", "op": "=", "value": "init"}
        )
        assert (attr, op, val) == ("name", "=", "init")

    def test_invoke_with_field_alias_filters_correctly(self):
        from get_processes import invoke
        r = invoke({
            "filters": [{"field": "name", "operator": "contains", "value": "python"}],
            "top": 50,
        })
        assert r["ok"] is True
        # Tutti gli entries restituiti devono contenere "python" nel name.
        for e in r["entries"]:
            assert "python" in e["name"].lower()


class TestHealthFieldHints:
    """Quando attribute è un campo health-known, errore actionable."""

    @pytest.mark.parametrize("bad_attr,expected_health_path", [
        ("ip", "health.network"),
        ("ipv4", "health.network"),
        ("ipv6", "health.network"),
        ("network", "health.network"),
        ("interface", "health.network"),
        ("iface", "health.network"),
        ("mac", "health.network"),
        ("temperature", "health.thermal"),
        ("temp", "health.thermal"),
        ("load", "health.load"),
        ("uptime", "health.load"),
        ("memory", "health.memory"),
        ("ram", "health.memory"),
        ("disk", "health.disk"),
        ("service", "health.services"),
    ])
    def test_health_hint_in_error(self, bad_attr, expected_health_path):
        from get_processes import invoke
        r = invoke({"filters": [{"attribute": bad_attr, "value": "x"}]})
        assert r["ok"] is False
        err = r["failed"][0]["error"]
        assert "HINT" in err
        assert expected_health_path in err
        assert "include_health=true" in err

    def test_no_hint_for_unrelated_invalid_attr(self):
        """Attribute fuori vocab + non-health → solo lista attributi validi."""
        from get_processes import invoke
        r = invoke({"filters": [{"attribute": "rotational", "value": "true"}]})
        assert r["ok"] is False
        err = r["failed"][0]["error"]
        assert "invalid attribute 'rotational'" in err
        assert "HINT" not in err

    def test_health_hint_case_insensitive(self):
        from get_processes import invoke
        r = invoke({"filters": [{"attribute": "IP", "value": "x"}]})
        err = r["failed"][0]["error"]
        assert "health.network" in err


class TestValidOpsList:
    """L'errore su op invalido elenca le opzioni valide (regression help)."""

    def test_invalid_op_lists_valid_ops(self):
        from get_processes import invoke
        r = invoke({
            "filters": [{"attribute": "name", "op": "matches", "value": "x"}],
        })
        assert r["ok"] is False
        err = r["failed"][0]["error"]
        assert "invalid op 'matches'" in err
        assert "contains" in err  # nella lista delle op valide


class TestSoftFailOnFilterErrorWithHealth:
    """Quando l'utente chiede include_health=true E il filtro e' malformato,
    l'executor deve comunque ritornare health (l'intent IP/network/etc.
    e' espresso dal flag, non dal filtro). Il filter error finisce in
    `failed[]` per audit; health.network resta accessibile."""

    def test_invalid_filter_plus_include_health_returns_health(self):
        from get_processes import invoke
        r = invoke({
            "filters": [{"attribute": "ip", "value": "x"}],
            "include_health": True,
        })
        # ok=True: l'esito utile e' health.network. filter_failed e'
        # solo un warning (l'LLM vede ok=True e procede al final_answer).
        assert r["ok"] is True
        assert "health" in r
        assert "network" in r["health"]
        assert len(r["health"]["network"]) >= 1
        assert "warnings" in r
        assert "filter ignored" in r["warnings"][0]
        assert "invalid attribute 'ip'" in r["warnings"][0]

    def test_invalid_filter_without_health_still_hard_fails(self):
        from get_processes import invoke
        r = invoke({
            "filters": [{"attribute": "ip", "value": "x"}],
            # niente include_health → comportamento storico: hard fail
        })
        assert r["ok"] is False
        assert "health" not in r
        assert "HINT" in r["failed"][0]["error"]

    def test_bare_string_filter_plus_health_still_returns_health(self):
        from get_processes import invoke
        r = invoke({"filters": ["ip"], "include_health": True})
        assert r["ok"] is True
        assert "health" in r
        assert "must be an object" in r["warnings"][0]
