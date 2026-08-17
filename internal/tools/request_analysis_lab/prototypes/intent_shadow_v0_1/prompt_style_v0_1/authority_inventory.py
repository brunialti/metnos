"""Canonical rule inventory extracted from the frozen candidate-v0.3 prompt."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from ..candidate_v0_2.canonical import canonical_sha256
from ..candidate_v0_3.projection import build_prompt as build_current_prompt


INVENTORY_FORMAT = "metnos.intent-prompt-authority-inventory/0.1"
CURRENT_PROMPT_VERSION = "metnos.intent-ir-prompt-overlay/0.3"


@dataclass(frozen=True, slots=True)
class AuthorityRule:
    rule_id: str
    label: str
    constraint: str


@dataclass(frozen=True, slots=True)
class CurrentStatement:
    line_number: int
    text: str
    rule_ids: tuple[str, ...]
    role: str


RULES = (
    AuthorityRule(
        "root",
        "ROOT",
        "Select exactly one root that matches the request before writing details; emit one JSON object with kind first.",
    ),
    AuthorityRule(
        "operations",
        "OPERATIONS",
        "Emit only operations required by the request; never enumerate the registry or alternatives.",
    ),
    AuthorityRule(
        "edges",
        "EDGES",
        "Omit from unless an operation has a real data dependency; every index identifies a strictly previous operation visible on the same path; operation ordinal 0 omits from; never reference the current or a later ordinal.",
    ),
    AuthorityRule(
        "ports",
        "PORTS",
        "Emit no ports, input, or output; the compiler derives unique registry ports.",
    ),
    AuthorityRule(
        "barrier",
        "BARRIER",
        "Put every operation that requires approval in the barrier body; emit no outcomes or continuations; the body means approved and the compiler creates an empty rejection.",
    ),
    AuthorityRule(
        "output",
        "OUTPUT",
        "Emit no explanations or extra keys; emit no path, ordinal, or free inputs.",
    ),
)

ROOT_TEMPLATES = (
    '- {"kind":"operation_graph","steps":[{"route":"<registry-route>"}]}',
    '- {"kind":"system_control","control":"<registry-control>"}',
    '- {"kind":"unrepresentable","reason":"<registry-reason>"}',
)

CURRENT_STATEMENTS = (
    CurrentStatement(2, "CHOOSE ROOT FIRST: select exactly one root that matches the request, then emit it as one JSON object with kind first.", ("root",), "instruction"),
    CurrentStatement(3, "EQUIVALENT ROOT TEMPLATES:", ("root",), "heading"),
    CurrentStatement(4, ROOT_TEMPLATES[0], ("root",), "minimal_template"),
    CurrentStatement(5, ROOT_TEMPLATES[1], ("root",), "minimal_template"),
    CurrentStatement(6, ROOT_TEMPLATES[2], ("root",), "minimal_template"),
    CurrentStatement(7, "FORBIDDEN: explanations, extra keys, ports, path, ordinal, outcome, continuation, or free inputs.", ("output", "ports", "barrier"), "instruction"),
    CurrentStatement(8, "OPERATIONS: emit only operations required by the request; never enumerate the registry or alternatives.", ("operations",), "instruction"),
    CurrentStatement(9, "OPERATION ORDINAL 0 MUST OMIT FROM: it has no previous operation.", ("edges",), "instruction"),
    CurrentStatement(10, "FROM: omit from unless an operation has a real data dependency; each index must identify a strictly previous operation visible on the same path.", ("edges",), "instruction"),
    CurrentStatement(11, "A STEP MUST NEVER REFERENCE ITS OWN ORDINAL OR A LATER ORDINAL.", ("edges",), "instruction"),
    CurrentStatement(12, 'VALID COMPLETE GRAPH: {"kind":"operation_graph","steps":[{"route":"<source-route>"},{"route":"<consumer-route>","from":[0]}]}', ("operations", "edges"), "long_example"),
    CurrentStatement(13, "FORBIDDEN: input/output; the compiler derives unique registry ports.", ("ports",), "instruction"),
    CurrentStatement(14, "BARRIER: body contains every operation that requires approval.", ("barrier",), "instruction"),
    CurrentStatement(15, "FORBIDDEN: outcomes or continuations; body means approved and the compiler creates an empty rejection.", ("barrier",), "instruction"),
    CurrentStatement(16, 'VALID BARRIER: {"barrier":"<registry-barrier>","body":[{"route":"<route>"}]}', ("barrier",), "long_example"),
    CurrentStatement(-5, "FINAL MINI-CHECK:", tuple(rule.rule_id for rule in RULES), "heading"),
    CurrentStatement(-4, "- Exactly one root was selected before writing details.", ("root",), "summary"),
    CurrentStatement(-3, "- An operation graph contains only necessary operations; step 0 omits from.", ("operations", "edges"), "summary"),
    CurrentStatement(-2, "- Every from denotes a real dependency on a strictly previous visible operation.", ("edges",), "summary"),
    CurrentStatement(-1, "- No registry enumeration, alternative plan, forbidden field, or explanatory text is present.", ("operations", "output", "ports", "barrier"), "summary"),
)


def _current_lines(registry: dict[str, Any]) -> list[str]:
    return build_current_prompt(registry).rstrip("\n").split("\n")


def split_current_prompt(registry: dict[str, Any]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Return language line, exact structural templates, and exact registry data."""
    lines = _current_lines(registry)
    if not lines or lines[0] != "INPUT_LANGUAGE_TAG: {input_language_tag}":
        raise ValueError("current prompt language header drift")
    for statement in CURRENT_STATEMENTS:
        index = statement.line_number - 1 if statement.line_number > 0 else len(lines) + statement.line_number
        if not 0 <= index < len(lines) or lines[index] != statement.text:
            raise ValueError(f"current prompt statement drift:{statement.line_number}")
    if lines[16] != "" or lines[17] != "OPERATION REGISTRY (exact identifiers; descriptions are language-tagged authority data):":
        raise ValueError("current prompt registry boundary drift")
    if lines[-5] != "FINAL MINI-CHECK:":
        raise ValueError("current prompt final boundary drift")
    data_lines = tuple(lines[17:-5])
    if not data_lines:
        raise ValueError("current prompt registry data missing")
    return lines[0], ROOT_TEMPLATES, data_lines


def validate_inventory(registry: dict[str, Any]) -> None:
    lines = _current_lines(registry)
    split_current_prompt(registry)
    rule_ids = tuple(rule.rule_id for rule in RULES)
    if len(rule_ids) != len(set(rule_ids)) or len({rule.label for rule in RULES}) != len(RULES):
        raise ValueError("duplicate authority rule")
    if any(not rule.constraint or "\n" in rule.constraint for rule in RULES):
        raise ValueError("authority constraint must be one line")
    seen_positions: set[int] = set()
    covered_rules: set[str] = set()
    for statement in CURRENT_STATEMENTS:
        position = statement.line_number if statement.line_number > 0 else len(lines) + statement.line_number + 1
        if position in seen_positions:
            raise ValueError("current statement mapped more than once")
        seen_positions.add(position)
        if not statement.rule_ids or any(rule_id not in rule_ids for rule_id in statement.rule_ids):
            raise ValueError("current statement has invalid rule mapping")
        covered_rules.update(statement.rule_ids)
    expected_positions = set(range(2, 17)) | set(range(len(lines) - 4, len(lines) + 1))
    if seen_positions != expected_positions:
        raise ValueError("current instruction coverage is not exact")
    if covered_rules != set(rule_ids):
        raise ValueError("authority rule coverage is incomplete")


def inventory_document(registry: dict[str, Any]) -> dict[str, Any]:
    validate_inventory(registry)
    current = build_current_prompt(registry).encode("utf-8")
    _language, templates, data = split_current_prompt(registry)
    document = {
        "inventory_format": INVENTORY_FORMAT,
        "current_prompt_version": CURRENT_PROMPT_VERSION,
        "current_prompt_sha256": sha256(current).hexdigest(),
        "current_prompt_bytes": len(current),
        "current_prompt_lines": len(current.decode("utf-8").splitlines()),
        "registry_data_sha256": sha256(("\n".join(data) + "\n").encode("utf-8")).hexdigest(),
        "root_templates": list(templates),
        "rules": [
            {"rule_id": rule.rule_id, "label": rule.label, "constraint": rule.constraint}
            for rule in RULES
        ],
        "current_statements": [
            {
                "line_number": item.line_number,
                "text": item.text,
                "rule_ids": list(item.rule_ids),
                "role": item.role,
            }
            for item in CURRENT_STATEMENTS
        ],
        "equivalence_contract": {
            "same_rules_once_in_s1_s2": True,
            "same_rule_order_in_s1_s2": True,
            "same_root_templates": True,
            "same_registry_data_bytes": True,
            "long_examples_removed_because_they_duplicate_schema_and_mapped_rules": True,
            "new_semantic_rules": 0,
        },
        "integrity": {"algorithm": "sha256", "inventory_payload_sha256": ""},
    }
    payload = dict(document)
    payload["integrity"] = {"algorithm": "sha256"}
    document["integrity"]["inventory_payload_sha256"] = canonical_sha256(payload)
    return document
