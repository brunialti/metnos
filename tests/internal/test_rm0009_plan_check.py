"""Dependency checks for RM-0009 preparation, without importing the runtime."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from internal.tools import rm0009_plan_check as checker


ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "internal/roadmap/RM-0009-crescita-allineata-delle-capacita.md"


@pytest.fixture
def document() -> str:
    return DOCUMENT.read_text(encoding="utf-8")


@pytest.fixture
def graph(document: str) -> dict[str, tuple[str, ...]]:
    return checker.parse_plan(document)


def _codes(report: dict) -> set[str]:
    return {issue["code"] for issue in report["errors"]}


def _small_document(rows: str) -> str:
    return "## Appendice D — Piano\n\n" + rows + "\n## Appendice E — Storia\n"


def test_current_document_is_structurally_valid_not_a_release_proof(document):
    first = checker.check_document(document)
    second = checker.check_document(document)
    assert first == second
    assert first["valid"] is True
    assert first["scope"] == "plan_dependencies_only"
    assert first["node_count"] == len(checker.parse_plan(document))
    assert len(first["document_sha256"]) == 64
    assert "approval" not in first
    assert "certified" not in first


@pytest.mark.parametrize("node,dependency", [
    ("D-F5.1", "D-FS-B.4c"),
    ("D-F5.4", "D-FS-A.4"),
    ("D-F6.1", "D-FS-B.4c"),
])
def test_security_work_must_not_block_preliminary_implementation(graph, node, dependency):
    graph[node] += (dependency,)
    report = checker.validate_plan(graph)
    assert not report["valid"]
    assert "forbidden_ancestor" in _codes(report)


@pytest.mark.parametrize("node,dependency", [
    ("D-I0.1", "D-FS-A.4"),
    ("D-I0.1", "D-X0.1"),
    ("D-F6.3", "D-FS-B.4c"),
])
def test_real_execution_must_keep_its_security_dependencies(graph, node, dependency):
    graph[node] = tuple(value for value in graph[node] if value != dependency)
    report = checker.validate_plan(graph)
    assert not report["valid"]
    assert "required_ancestor_missing" in _codes(report)


def test_missing_dependency_is_not_silently_ignored(graph):
    graph["D-G0.1"] = ("D-ABSENT.1",)
    assert "unknown_dependency" in _codes(checker.validate_plan(graph))


def test_cycle_has_a_reproducible_witness(graph):
    graph["D-G0.1"] = ("D-G0.2",)
    report = checker.validate_plan(graph)
    assert "cycle" in _codes(report)
    issue = next(item for item in report["errors"] if item["code"] == "cycle")
    assert issue["path"][0] == issue["path"][-1]
    assert report == checker.validate_plan(graph)


def test_self_dependency_is_a_cycle(graph):
    graph["D-G0.1"] = ("D-G0.1",)
    assert "cycle" in _codes(checker.validate_plan(graph))


def test_required_milestone_cannot_disappear(graph):
    del graph["D-I0.7"]
    assert "required_node_missing" in _codes(checker.validate_plan(graph))


def test_templates_block_a_concrete_freeze_but_not_draft_analysis(graph):
    draft = checker.validate_plan(graph)
    assert draft["templates"] == ["D-FS-A.3.N", "D-FS-B.3.N"]
    assert draft["concrete"] is False
    assert draft["valid"] is True
    frozen = checker.validate_plan(graph, require_concrete=True)
    assert not frozen["valid"]
    assert "unexpanded_template" in _codes(frozen)


def test_expanded_groups_must_be_ancestors_of_their_barrier(graph):
    del graph["D-FS-A.3.N"]
    graph["D-FS-A.3.001"] = ("D-FS-A.2",)
    graph["D-FS-A.3.002"] = ("D-FS-A.2",)
    report = checker.validate_plan(graph)
    assert "unjoined_expansion" in _codes(report)
    graph["D-FS-A.3.barrier"] += ("D-FS-A.3.001", "D-FS-A.3.002")
    assert checker.validate_plan(graph)["valid"] is True


def _expand_templates(graph):
    inventory = {}
    for family in ("D-FS-A.3", "D-FS-B.3"):
        prerequisites = graph.pop(family + ".N")
        members = [family + ".001", family + ".002"]
        for node in members:
            graph[node] = prerequisites
        graph[family + ".barrier"] += tuple(members)
        inventory[family] = members
    return inventory


def test_deleting_templates_cannot_masquerade_as_concrete_work(graph):
    del graph["D-FS-A.3.N"]
    del graph["D-FS-B.3.N"]
    report = checker.validate_plan(graph, require_concrete=True)
    assert report["valid"] is False
    assert report["concrete"] is False
    assert "missing_expansion_family" in _codes(report)


def test_concrete_mode_requires_an_explicit_expansion_inventory(graph):
    _expand_templates(graph)
    report = checker.validate_plan(graph, require_concrete=True)
    assert report["valid"] is False
    assert report["concrete"] is False
    assert "expansion_inventory_required" in _codes(report)


def test_concrete_mode_matches_exact_inventory_not_approval(graph):
    inventory = _expand_templates(graph)
    report = checker.validate_plan(
        graph, require_concrete=True, expected_expansions=inventory,
    )
    assert report["valid"] is True
    assert report["concrete"] is True
    assert report["expansion_inventory_checked"] is True
    assert report["scope"] == "plan_dependencies_only"

    inventory["D-FS-A.3"].append("D-FS-A.3.003")
    report = checker.validate_plan(
        graph, require_concrete=True, expected_expansions=inventory,
    )
    assert report["valid"] is False
    assert report["concrete"] is False
    assert "missing_expansion" in _codes(report)


def test_concrete_mode_rejects_uninventoried_groups(graph):
    inventory = _expand_templates(graph)
    inventory["D-FS-B.3"].pop()
    report = checker.validate_plan(graph, expected_expansions=inventory)
    assert report["valid"] is False
    assert "unexpected_expansion" in _codes(report)


@pytest.mark.parametrize("inventory", [
    {}, [], {"D-FS-A.3": []},
    {"D-FS-A.3": "D-FS-A.3.001", "D-FS-B.3": ["D-FS-B.3.001"]},
    {"D-FS-A.3": ["D-FS-A.3.001"] * 2, "D-FS-B.3": ["D-FS-B.3.001"]},
    {"D-FS-A.3": ["D-FS-A.3.N"], "D-FS-B.3": ["D-FS-B.3.001"]},
    {"D-FS-A.3": ["D-FS-B.3.001"], "D-FS-B.3": ["D-FS-B.3.001"]},
])
def test_invalid_expansion_inventory_is_rejected(graph, inventory):
    report = checker.validate_plan(graph, expected_expansions=inventory)
    assert report["valid"] is False
    assert "invalid_expansion_inventory" in _codes(report)


def test_explicit_empty_inventory_is_distinct_from_missing_inventory(graph):
    del graph["D-FS-A.3.N"]
    del graph["D-FS-B.3.N"]
    report = checker.validate_plan(
        graph, require_concrete=True,
        expected_expansions={"D-FS-A.3": [], "D-FS-B.3": []},
    )
    assert report["valid"] is True
    assert report["concrete"] is True
    assert report["expansion_inventory_checked"] is True


def test_nested_template_cannot_hide_between_inventory_and_template_checks(graph):
    del graph["D-FS-A.3.N"]
    del graph["D-FS-B.3.N"]
    graph["D-FS-A.3.N.001"] = ("D-FS-A.2",)
    report = checker.validate_plan(
        graph, require_concrete=True,
        expected_expansions={"D-FS-A.3": [], "D-FS-B.3": []},
    )
    assert report["valid"] is False
    assert report["concrete"] is False
    assert "unexpanded_template" in _codes(report)


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_unclosed_fence_is_reported(document, fence):
    with pytest.raises(checker.PlanFormatError) as error:
        checker.parse_plan(document + "\n" + fence + "\n")
    assert error.value.code == "unclosed_fence"


def test_fences_follow_length_and_closing_line_rules():
    document = _small_document(
        "````text\n"
        "```\n| D-FAKE.1 | — | x | y | z |\n"
        "````not a closing fence\n| D-FAKE.2 | — | x | y | z |\n"
        "`````\n"
        "| D-G0.1 | — | x | y | z |"
    )
    assert checker.parse_plan(document) == {"D-G0.1": ()}


@pytest.mark.parametrize("content", [
    "null", '{"D-FS-A.3": [], "D-FS-B.3": [], "D-FS-A.3": []}',
])
def test_cli_rejects_null_and_duplicate_key_inventories(tmp_path, document, capsys, content):
    target = tmp_path / "plan.md"
    target.write_text(document, encoding="utf-8")
    inventory = tmp_path / "expansions.json"
    inventory.write_text(content, encoding="utf-8")
    assert checker.main([str(target), "--expansion-inventory", str(inventory)]) == 1
    assert "invalid_expansion_inventory" in _codes(json.loads(capsys.readouterr().out))


@pytest.mark.parametrize("node", ["D-F6.4a", "D-F6.4b", "D-F6.4c", "D-F6.4d"])
def test_remote_intermediate_steps_cannot_defer_security_to_final_switch(graph, node):
    graph[node] = ("D-F6.2.barrier",)
    graph["D-F6.4e"] += (
        "D-X0.1", "D-FS-A.4", "D-FS-B.4c",
        "D-F6.4a", "D-F6.4b", "D-F6.4c", "D-F6.4d",
    )
    report = checker.validate_plan(graph)
    assert report["valid"] is False
    assert any(
        issue["code"] == "required_ancestor_missing"
        and issue["node"] == node and issue["dependency"] == "D-FS-A.4"
        for issue in report["errors"]
    )


def test_cli_reads_expansion_inventory_without_modifying_it(tmp_path, document, capsys):
    target = tmp_path / "plan.md"
    target.write_text(document, encoding="utf-8")
    inventory = tmp_path / "expansions.json"
    inventory.write_text("{}", encoding="utf-8")
    before = inventory.read_bytes()
    assert checker.main([str(target), "--expansion-inventory", str(inventory)]) == 1
    assert "invalid_expansion_inventory" in _codes(json.loads(capsys.readouterr().out))
    assert inventory.read_bytes() == before


@pytest.mark.parametrize("content", [None, "not JSON", "\ufffd"])
def test_cli_reports_unreadable_expansion_inventory(tmp_path, document, capsys, content):
    target = tmp_path / "plan.md"
    target.write_text(document, encoding="utf-8")
    inventory = tmp_path / "expansions.json"
    if content is not None:
        inventory.write_text(content, encoding="utf-8")
    assert checker.main([str(target), "--expansion-inventory", str(inventory)]) == 2
    assert "expansion_inventory_unreadable" in _codes(json.loads(capsys.readouterr().out))


@pytest.mark.parametrize("rows,code", [
    ("| D-G0.1 | — | x | y | z |\n| D-G0.1 | — | x | y | z |", "duplicate_id"),
    ("| D-G0.1 | manual approval | x | y | z |", "invalid_dependency"),
    ("| D-G0.1 | G0.2, G0.2 | x | y | z |", "duplicate_dependency"),
    ("| D-G0.1 | | x | y | z |", "missing_dependency_cell"),
    ("| D-G0.1 | — |", "malformed_row"),
    ("", "empty_plan"),
])
def test_malformed_plans_are_rejected(rows, code):
    with pytest.raises(checker.PlanFormatError) as error:
        checker.parse_plan(_small_document(rows))
    assert error.value.code == code


def test_rows_outside_the_plan_or_in_examples_are_not_dependencies():
    text = (
        "| D-FAKE.1 | — | x | y | z |\n"
        + _small_document(
            "```text\n| D-FAKE.2 | — | x | y | z |\n```\n"
            "| D-G0.1 | — | x | y | z |"
        )
        + "| D-FAKE.3 | — | x | y | z |\n"
    )
    assert checker.parse_plan(text) == {"D-G0.1": ()}


def test_cli_is_read_only_and_reports_unexpanded_templates(tmp_path, document, capsys):
    target = tmp_path / "plan.md"
    target.write_text(document, encoding="utf-8")
    before = target.read_bytes()
    assert checker.main([str(target)]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["valid"] is True
    assert checker.main([str(target), "--require-concrete"]) == 1
    assert "unexpanded_template" in _codes(json.loads(capsys.readouterr().out))
    assert target.read_bytes() == before
    assert list(tmp_path.iterdir()) == [target]


def test_cli_reports_missing_document_without_a_traceback(tmp_path, capsys):
    assert checker.main([str(tmp_path / "missing.md")]) == 2
    assert "document_unreadable" in _codes(json.loads(capsys.readouterr().out))
