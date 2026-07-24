from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "tests/runtime/tutor/data/f2_human_certification.json"


def _load():
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def test_human_certification_corpus_is_broad_unique_and_safe():
    data = _load()
    cases = data["answer_cases"]
    flows = data["conversation_flows"]
    all_ids = [case["id"] for case in cases] + [flow["id"] for flow in flows]

    assert data["schema_version"] == 1
    assert len(all_ids) == len(set(all_ids))
    assert len(cases) >= 70
    assert sum(len(flow["turns"]) for flow in flows) >= 20
    assert len(cases) + sum(len(flow["turns"]) for flow in flows) >= 90
    groups = Counter(case["group"] for case in cases)
    assert groups["admin_ui"] >= 26
    assert groups["typical_operations"] >= 30
    assert groups["boundary"] >= 12
    expected = Counter(case["expected"] for case in cases)
    assert expected["fallthrough"] >= 9
    assert expected["clarification"] >= 3


def test_every_canonical_admin_surface_has_multiple_human_checks():
    import sys
    sys.path.insert(0, str(ROOT / "runtime"))
    from ui_surfaces import catalog

    cases = _load()["answer_cases"]
    routes = Counter(
        case.get("route") for case in cases if case["group"] == "admin_ui"
    )
    for surface in catalog():
        assert routes[surface.route] >= 1, surface.key
    for required in ("/admin/turns", "/admin/timers", "/admin/changes",
                     "/admin/executors", "/admin/praxis", "/admin/services",
                     "/admin/safety", "/admin/users", "/admin/devices"):
        assert routes[required] >= 2


def test_typical_operations_cover_local_web_provider_and_system_domains():
    text = " ".join(
        case["query"] for case in _load()["answer_cases"]
        if case["group"] == "typical_operations"
    ).casefold()
    for alternatives in (
        ("file",), ("hash",), ("righe di codice",), ("zip",), ("scansione",),
        ("web",), ("pdf",), ("sito",), ("posizione",), ("farmacia",),
        ("foto",), ("google photos",), ("mailbox",), ("google workspace",),
        ("calendario", "appuntamenti"), ("contatto",), ("github",), ("csv",),
        ("xlsx",), ("credenzial",), ("pc collegato", "computer collegato"),
        ("processi",), ("lunedì",), ("annullare",),
    ):
        assert any(concept in text for concept in alternatives)


def test_conversation_set_contains_ellipses_context_switch_and_telegram():
    flows = _load()["conversation_flows"]
    followups = [flow["turns"][1]["query"] for flow in flows]
    assert any(query.startswith("E ") for query in followups)
    assert any("A parte questo" in query for query in followups)
    assert any(flow["channel"] == "telegram" for flow in flows)


def test_f1_equivalence_sets_cover_every_retirement_target_bilingually():
    sets = _load()["f1_equivalence_sets"]
    assert {item["id"] for item in sets} == {
        "f1-overview", "f1-github", "f1-photos", "f1-scheduled",
        "f1-devices", "f1-changes", "f1-changes-restricted",
    }
    for item in sets:
        languages = Counter(query["lang"] for query in item["queries"])
        if item["id"] == "f1-changes-restricted":
            assert languages == {"it": 1, "en": 1}
        else:
            assert languages == {"it": 3, "en": 3}
        assert set(item["must_cover_by_lang"]) == {"it", "en"}
