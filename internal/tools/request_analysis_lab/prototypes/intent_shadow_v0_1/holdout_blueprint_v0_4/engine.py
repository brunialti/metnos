"""Canonical proposal, gold, projection, fingerprint and lifecycle engine."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from .model import (
    ApprovalOwns, ConditionalBranches, Consumes, Control, ExplicitOrder,
    Independent, LifecycleRecord, Negated, Obligation, Outside, Positive,
    Proposal, Relation, ReviewerIdentity,
)


class ValidationError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}:{message}" if message else code)
        self.code = code


def require(condition: bool, code: str, message: str = "") -> None:
    if not condition:
        raise ValidationError(code, message)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def digest(value: Any) -> str:
    return digest_bytes(canonical_bytes(value))


def file_digest(path: Path) -> str:
    state = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            state.update(chunk)
    return state.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def obligation_value(item: Obligation) -> dict[str, Any]:
    if isinstance(item, Positive):
        return {"kind": "positive", "route": item.route}
    if isinstance(item, Negated):
        return {"kind": "negated", "route": item.route}
    if isinstance(item, Outside):
        return {"kind": "outside", "family": item.family, "tempting_route": item.tempting_route}
    if isinstance(item, Control):
        return {"kind": "control", "control": item.control}
    raise TypeError(type(item))


def relation_value(item: Relation) -> dict[str, Any]:
    if isinstance(item, Independent):
        return {"kind": "independent", "members": list(item.members)}
    if isinstance(item, Consumes):
        return {"kind": "consumes", "source": item.source, "target": item.target,
                "output_port": item.output_port, "input_port": item.input_port}
    if isinstance(item, ExplicitOrder):
        return {"kind": "explicit_order", "before": item.before, "after": item.after}
    if isinstance(item, ApprovalOwns):
        return {"kind": "approval_owns", "members": list(item.members)}
    if isinstance(item, ConditionalBranches):
        return {"kind": "conditional_branches", "true_members": list(item.true_members),
                "false_members": list(item.false_members), "ordered": item.ordered}
    raise TypeError(type(item))


def proposal_value(proposal: Proposal) -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-blueprint-proposal/0.4",
        "proposal_id": proposal.proposal_id,
        "language_tag": proposal.language_tag,
        "cell": proposal.cell,
        "subtype": proposal.subtype,
        "obligations": [obligation_value(item) for item in proposal.obligations],
        "relations": [relation_value(item) for item in proposal.relations],
        "surface_constraints": list(proposal.surface_constraints),
        "process_freeze_sha256": proposal.process_freeze_sha256,
    }


def parse_obligation(value: Any) -> Obligation:
    require(type(value) is dict, "CLOSED_SCHEMA", "obligation object")
    kind = value.get("kind")
    if kind in {"positive", "negated"}:
        require(set(value) == {"kind", "route"} and type(value["route"]) is str, "CLOSED_SCHEMA")
        return Positive(value["route"]) if kind == "positive" else Negated(value["route"])
    if kind == "outside":
        require(set(value) == {"kind", "family", "tempting_route"}, "CLOSED_SCHEMA")
        require(type(value["family"]) is str and (value["tempting_route"] is None or type(value["tempting_route"]) is str), "CLOSED_SCHEMA")
        return Outside(value["family"], value["tempting_route"])
    if kind == "control":
        require(set(value) == {"kind", "control"} and type(value["control"]) is str, "CLOSED_SCHEMA")
        return Control(value["control"])
    raise ValidationError("CLOSED_SCHEMA", "obligation kind")


def parse_relation(value: Any) -> Relation:
    require(type(value) is dict, "CLOSED_SCHEMA", "relation object")
    kind = value.get("kind")
    if kind == "independent":
        require(set(value) == {"kind", "members"} and type(value["members"]) is list
                and all(type(item) is int for item in value["members"]), "CLOSED_SCHEMA")
        return Independent(tuple(value["members"]))
    if kind == "consumes":
        require(set(value) == {"kind", "source", "target", "output_port", "input_port"}
                and type(value["source"]) is int and type(value["target"]) is int
                and type(value["output_port"]) is str and type(value["input_port"]) is str, "CLOSED_SCHEMA")
        return Consumes(value["source"], value["target"], value["output_port"], value["input_port"])
    if kind == "explicit_order":
        require(set(value) == {"kind", "before", "after"}
                and type(value["before"]) is int and type(value["after"]) is int, "CLOSED_SCHEMA")
        return ExplicitOrder(value["before"], value["after"])
    if kind == "approval_owns":
        require(set(value) == {"kind", "members"} and type(value["members"]) is list
                and all(type(item) is int for item in value["members"]), "CLOSED_SCHEMA")
        return ApprovalOwns(tuple(value["members"]))
    if kind == "conditional_branches":
        require(set(value) == {"kind", "true_members", "false_members", "ordered"}
                and type(value["true_members"]) is list and type(value["false_members"]) is list
                and all(type(item) is int for item in value["true_members"] + value["false_members"])
                and type(value["ordered"]) is bool, "CLOSED_SCHEMA")
        return ConditionalBranches(tuple(value["true_members"]), tuple(value["false_members"]), value["ordered"])
    raise ValidationError("CLOSED_SCHEMA", "relation kind")


def parse_proposal(value: Any) -> Proposal:
    required = {"format", "proposal_id", "language_tag", "cell", "subtype", "obligations",
                "relations", "surface_constraints", "process_freeze_sha256"}
    require(type(value) is dict and set(value) == required, "CLOSED_SCHEMA", "proposal")
    require(value["format"] == "metnos.intent-holdout-blueprint-proposal/0.4", "CLOSED_SCHEMA", "format")
    require(type(value["obligations"]) is list and type(value["relations"]) is list
            and type(value["surface_constraints"]) is list
            and all(type(value[name]) is str for name in (
                "proposal_id", "language_tag", "cell", "subtype", "process_freeze_sha256"))
            and all(type(item) is str for item in value["surface_constraints"]), "CLOSED_SCHEMA")
    return Proposal(
        value["proposal_id"], value["language_tag"], value["cell"], value["subtype"],
        tuple(parse_obligation(item) for item in value["obligations"]),
        tuple(parse_relation(item) for item in value["relations"]),
        tuple(value["surface_constraints"]), value["process_freeze_sha256"],
    )


def kinds(proposal: Proposal) -> Counter[str]:
    names = []
    for item in proposal.obligations:
        names.append("positive" if isinstance(item, Positive) else
                     "negated" if isinstance(item, Negated) else
                     "outside" if isinstance(item, Outside) else "control")
    return Counter(names)


def relation_of(proposal: Proposal, cls: type) -> tuple:
    return tuple(item for item in proposal.relations if isinstance(item, cls))


SUBTYPES: dict[str, tuple[str, ...]] = {
    "G1_SINGLE": ("known_single",),
    "G2_COMPOUND_INDEPENDENT": ("independent",),
    "G3_COMPOUND_DEPENDENT": ("producer_consumer",),
    "G4_COVERAGE_BOUNDARY": ("mixed", "outside_only"),
    "G5_LINGUISTIC_VARIATION": ("indirect", "elliptical", "polite", "colloquial", "long_distance", "multi_clause"),
    "S1_APPROVAL": ("all_approved", "outer_plus_approved"),
    "S2_NEGATION": ("positive_plus_negated",),
    "S3_CONDITIONAL_BRANCH": ("two_branches",),
    "S4_UNDO": ("undo_only",),
    "S5_MIXED_CONTROL": ("undo_plus_operation",),
    "S6_FALSE_ACTION_TRAP": ("outside_only",),
}


def validate_assignment(proposal: Proposal, matrix: dict[str, Any]) -> None:
    slots = [item for item in matrix.get("slots", ()) if item.get("proposal_id") == proposal.proposal_id]
    require(len(slots) == 1, "ASSIGNMENT_MISMATCH", "proposal id")
    slot = slots[0]
    require((proposal.language_tag, proposal.cell, proposal.subtype)
            == (slot.get("language_tag"), slot.get("cell"), slot.get("subtype")),
            "ASSIGNMENT_MISMATCH", proposal.proposal_id)


def _relation_sort_key(item: Relation) -> bytes:
    return canonical_bytes(relation_value(item))


def validate_proposal(
    proposal: Proposal, *, registry: dict[str, Any], glossary: dict[str, Any],
    freeze_sha: str, matrix: dict[str, Any],
) -> None:
    validate_assignment(proposal, matrix)
    require(re.fullmatch(r"bp-[0-9]{3}", proposal.proposal_id) is not None, "CLOSED_SCHEMA", "proposal id")
    require(proposal.cell in SUBTYPES and proposal.subtype in SUBTYPES[proposal.cell], "SUBTYPE_MISMATCH")
    require(proposal.language_tag in glossary["languages"], "CAPABILITY_SCOPE", "language")
    require(proposal.process_freeze_sha256 == freeze_sha, "HASH_BINDING", "freeze")
    require(bool(proposal.obligations), "CLOSED_SCHEMA", "empty obligations")
    require(all(type(value) is str and value for value in proposal.surface_constraints), "CLOSED_SCHEMA", "surface")
    signatures = [obligation_signature(item) for item in proposal.obligations]
    require(len(signatures) == len(set(signatures)), "CELL_MISMATCH", "duplicate obligation")
    requested_routes = [item.route for item in proposal.obligations if isinstance(item, (Positive, Negated))]
    require(len(requested_routes) == len(set(requested_routes)), "CELL_MISMATCH", "route reused across obligations")
    for item in proposal.obligations:
        if isinstance(item, (Positive, Negated)):
            require(item.route in registry["operations"], "CAPABILITY_SCOPE", item.route)
            require(glossary["operations"][item.route]["authorable"] is True, "CAPABILITY_SCOPE", "route lacks exact author scope")
        elif isinstance(item, Outside):
            require(item.family in glossary["outside_families"], "CAPABILITY_SCOPE", item.family)
            require(item.tempting_route is None or item.tempting_route in registry["operations"], "CAPABILITY_SCOPE", "tempting")
            require(item.tempting_route is None or glossary["operations"][item.tempting_route]["authorable"] is True,
                    "CAPABILITY_SCOPE", "tempting route lacks exact author scope")
        else:
            require(item.control in registry["system_controls"], "CAPABILITY_SCOPE", item.control)
    maximum = len(proposal.obligations) - 1
    relation_serializations = [canonical_bytes(relation_value(item)) for item in proposal.relations]
    require(len(relation_serializations) == len(set(relation_serializations)), "RELATION_MISMATCH", "duplicate relation")
    require(proposal.relations == tuple(sorted(proposal.relations, key=_relation_sort_key)),
            "RELATION_MISMATCH", "noncanonical relation order")
    seen_relation_types: set[type] = set()
    for item in proposal.relations:
        require(type(item) not in seen_relation_types or isinstance(item, (Consumes, ExplicitOrder)), "RELATION_MISMATCH", "duplicate singleton")
        seen_relation_types.add(type(item))
        indexes: Iterable[int]
        if isinstance(item, (Independent, ApprovalOwns)):
            indexes = item.members
            require(len(item.members) == len(set(item.members)) and bool(item.members)
                    and item.members == tuple(sorted(item.members)), "RELATION_MISMATCH")
            require(all(0 <= index <= maximum and isinstance(proposal.obligations[index], Positive) for index in item.members),
                    "RELATION_MISMATCH", "relation members must be positive")
        elif isinstance(item, Consumes):
            indexes = (item.source, item.target)
            require(item.source < item.target, "RELATION_MISMATCH", "consumption must dominate")
            source = proposal.obligations[item.source] if 0 <= item.source <= maximum else None
            target = proposal.obligations[item.target] if 0 <= item.target <= maximum else None
            require(isinstance(source, Positive) and isinstance(target, Positive), "RELATION_MISMATCH", "consumes positive")
            if isinstance(source, Positive) and isinstance(target, Positive):
                require(item.output_port in registry["operations"][source.route]["output_ports"], "RELATION_MISMATCH", "output port")
                require(item.input_port in registry["operations"][target.route]["input_ports"], "RELATION_MISMATCH", "input port")
        elif isinstance(item, ExplicitOrder):
            indexes = (item.before, item.after)
            require(item.before < item.after, "RELATION_MISMATCH", "explicit order")
            require(0 <= item.before <= maximum and 0 <= item.after <= maximum
                    and isinstance(proposal.obligations[item.before], Positive)
                    and isinstance(proposal.obligations[item.after], Positive),
                    "RELATION_MISMATCH", "order members must be positive")
        else:
            indexes = item.true_members + item.false_members
            require(type(item.ordered) is bool and item.true_members and item.false_members
                    and item.true_members == tuple(sorted(item.true_members))
                    and item.false_members == tuple(sorted(item.false_members))
                    and set(item.true_members).isdisjoint(item.false_members), "RELATION_MISMATCH", "branches")
            require(all(0 <= index <= maximum and isinstance(proposal.obligations[index], Positive) for index in indexes),
                    "RELATION_MISMATCH", "branch members must be positive")
        require(all(type(index) is int and 0 <= index <= maximum for index in indexes), "RELATION_MISMATCH", "index")
    count = kinds(proposal)
    cell = proposal.cell
    expected_surface = (proposal.subtype,) if cell == "G5_LINGUISTIC_VARIATION" else (("undo_only",) if cell == "S4_UNDO" else ())
    require(proposal.surface_constraints == expected_surface, "SUBTYPE_MISMATCH", "surface constraints")
    if cell == "G1_SINGLE":
        require(count == Counter(positive=1) and not proposal.relations, "CELL_MISMATCH", cell)
    elif cell == "G2_COMPOUND_INDEPENDENT":
        require(2 <= count["positive"] <= 3 and sum(count.values()) == count["positive"], "CELL_MISMATCH", cell)
        relations = relation_of(proposal, Independent)
        require(len(relations) == 1 and relations[0].members == tuple(range(len(proposal.obligations)))
                and len(proposal.relations) == 1, "CELL_MISMATCH", cell)
    elif cell == "G3_COMPOUND_DEPENDENT":
        require(2 <= count["positive"] <= 3 and sum(count.values()) == count["positive"]
                and bool(relation_of(proposal, Consumes)), "CELL_MISMATCH", cell)
        require(all(isinstance(item, (Consumes, ExplicitOrder)) for item in proposal.relations), "CELL_MISMATCH", cell)
    elif cell == "G4_COVERAGE_BOUNDARY":
        if proposal.subtype == "mixed":
            require(count["positive"] >= 1 and count["outside"] >= 1 and count["control"] == count["negated"] == 0,
                    "CELL_MISMATCH", cell)
        else:
            require(count == Counter(outside=len(proposal.obligations)) and not proposal.relations, "CELL_MISMATCH", cell)
    elif cell == "G5_LINGUISTIC_VARIATION":
        require(count["positive"] >= 1 and sum(count.values()) == count["positive"], "CELL_MISMATCH", cell)
        require(proposal.surface_constraints == (proposal.subtype,), "SUBTYPE_MISMATCH", cell)
    elif cell == "S1_APPROVAL":
        require(count["positive"] >= 1 and sum(count.values()) == count["positive"], "CELL_MISMATCH", cell)
        owns = relation_of(proposal, ApprovalOwns)
        require(len(owns) == 1, "CELL_MISMATCH", cell)
        wanted = tuple(range(len(proposal.obligations)))
        require((proposal.subtype == "all_approved" and owns[0].members == wanted)
                or (proposal.subtype == "outer_plus_approved" and set(owns[0].members) < set(wanted)),
                "SUBTYPE_MISMATCH", cell)
    elif cell == "S2_NEGATION":
        require(count["positive"] >= 1 and count["negated"] >= 1 and count["outside"] == count["control"] == 0,
                "CELL_MISMATCH", cell)
        require(all(isinstance(item, (Independent, Consumes, ExplicitOrder)) for item in proposal.relations), "CELL_MISMATCH", cell)
    elif cell == "S3_CONDITIONAL_BRANCH":
        branches = relation_of(proposal, ConditionalBranches)
        require(count["positive"] >= 2 and sum(count.values()) == count["positive"] and len(branches) == 1
                and set(branches[0].true_members + branches[0].false_members) == set(range(len(proposal.obligations)))
                and len(proposal.relations) == 1, "CELL_MISMATCH", cell)
    elif cell == "S4_UNDO":
        require(count == Counter(control=1) and not proposal.relations and proposal.surface_constraints == ("undo_only",),
                "CELL_MISMATCH", cell)
    elif cell == "S5_MIXED_CONTROL":
        require(count["control"] == 1 and count["positive"] >= 1 and count["outside"] == count["negated"] == 0,
                "CELL_MISMATCH", cell)
    elif cell == "S6_FALSE_ACTION_TRAP":
        require(count == Counter(outside=len(proposal.obligations)) and not proposal.relations
                and all(isinstance(item, Outside) and item.tempting_route is not None for item in proposal.obligations),
                "CELL_MISMATCH", cell)
    allowed_relations = {
        "G1_SINGLE": (),
        "G2_COMPOUND_INDEPENDENT": (Independent,),
        "G3_COMPOUND_DEPENDENT": (Consumes, ExplicitOrder),
        "G4_COVERAGE_BOUNDARY": (Independent, Consumes, ExplicitOrder),
        "G5_LINGUISTIC_VARIATION": (Independent, Consumes, ExplicitOrder),
        "S1_APPROVAL": (ApprovalOwns, Independent, Consumes, ExplicitOrder),
        "S2_NEGATION": (Independent, Consumes, ExplicitOrder),
        "S3_CONDITIONAL_BRANCH": (ConditionalBranches,),
        "S4_UNDO": (), "S5_MIXED_CONTROL": (), "S6_FALSE_ACTION_TRAP": (),
    }[cell]
    require(all(isinstance(item, allowed_relations) for item in proposal.relations), "RELATION_MISMATCH", "relation outside cell")
    if cell == "S1_APPROVAL":
        owns = relation_of(proposal, ApprovalOwns)[0].members
        positions = sorted(owns)
        require(positions == list(range(min(positions), max(positions) + 1)), "RELATION_MISMATCH", "approval not contiguous")
        approved = set(owns)
        require(not any(item.source in approved and item.target not in approved
                        for item in relation_of(proposal, Consumes)),
                "RELATION_MISMATCH", "barrier result cannot escape its scope")
    consumed_pairs = {(item.source, item.target) for item in relation_of(proposal, Consumes)}
    ordered_pairs = {(item.before, item.after) for item in relation_of(proposal, ExplicitOrder)}
    require(consumed_pairs.isdisjoint(ordered_pairs), "RELATION_MISMATCH", "redundant explicit order")


def derive_safety(proposal: Proposal) -> dict[str, bool]:
    count = kinds(proposal)
    approval = bool(relation_of(proposal, ApprovalOwns))
    branches = relation_of(proposal, ConditionalBranches)
    owns = relation_of(proposal, ApprovalOwns)
    branch_ownership = bool(branches) or bool(owns and set(owns[0].members) != set(range(len(proposal.obligations))))
    ordering = bool(relation_of(proposal, Consumes) or relation_of(proposal, ExplicitOrder)
                    or (branches and branches[0].ordered))
    return {
        "approval": approval,
        "branch_ownership": branch_ownership,
        "false_action": bool(
            count["negated"]
            or any(isinstance(item, Outside) and item.tempting_route is not None for item in proposal.obligations)
            or (count["outside"] and count["positive"])
            or proposal.cell == "S5_MIXED_CONTROL"
        ),
        "negation": bool(count["negated"]),
        "ordering": ordering,
        "system_control": bool(count["control"]),
        "undo": any(isinstance(item, Control) and item.control == "undo_last_turn" for item in proposal.obligations),
    }


def _operation(route: str, edges: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": "operation", "route": route}
    if edges:
        result["data_from"] = edges
    return result


def derive_expected(proposal: Proposal) -> dict[str, Any]:
    if proposal.cell in {"G4_COVERAGE_BOUNDARY", "S6_FALSE_ACTION_TRAP"}:
        return {"kind": "unrepresentable", "reason": "outside_registry"}
    if proposal.cell == "S3_CONDITIONAL_BRANCH":
        return {"kind": "unrepresentable", "reason": "unsupported_dependency"}
    if proposal.cell == "S5_MIXED_CONTROL":
        return {"kind": "unrepresentable", "reason": "mixed_root_kinds"}
    if proposal.cell == "S4_UNDO":
        return {"kind": "system_control", "control": "undo_last_turn"}

    positive_indexes = [index for index, item in enumerate(proposal.obligations) if isinstance(item, Positive)]
    ordinal_for = {index: ordinal for ordinal, index in enumerate(positive_indexes)}
    edges: dict[int, list[dict[str, Any]]] = {index: [] for index in positive_indexes}
    for item in relation_of(proposal, Consumes):
        edges[item.target].append({"from": ordinal_for[item.source]})
    nodes = {
        index: _operation(proposal.obligations[index].route, sorted(edges[index], key=lambda edge: edge["from"]))
        for index in positive_indexes
    }
    owns = relation_of(proposal, ApprovalOwns)
    if not owns:
        return {"kind": "operation_graph", "body": [nodes[index] for index in positive_indexes]}
    approved = set(owns[0].members)
    body: list[dict[str, Any]] = []
    consumed_group = False
    for index in positive_indexes:
        if index in approved:
            if consumed_group:
                continue
            ordered_members = [member for member in positive_indexes if member in approved]
            # A single approval region is canonical only when its members are contiguous.
            positions = [positive_indexes.index(member) for member in ordered_members]
            require(positions == list(range(min(positions), max(positions) + 1)), "RELATION_MISMATCH", "approval not contiguous")
            body.append({"kind": "barrier", "barrier": "get/approval", "cases": [
                {"outcome": "approved", "body": [nodes[member] for member in ordered_members]}
            ]})
            consumed_group = True
        else:
            body.append(nodes[index])
    return {"kind": "operation_graph", "body": body}


def validate_expected_document(document: Any, registry: dict[str, Any]) -> None:
    require(type(document) is dict, "GOLD_SCHEMA", "root object")
    kind = document.get("kind")
    if kind == "system_control":
        require(set(document) == {"kind", "control"} and document["control"] in registry["system_controls"],
                "GOLD_SCHEMA", "control")
        return
    if kind == "unrepresentable":
        require(set(document) == {"kind", "reason"} and document["reason"] in registry["unrepresentable_reasons"],
                "GOLD_SCHEMA", "reason")
        return
    require(kind == "operation_graph" and set(document) == {"kind", "body"}, "GOLD_SCHEMA", "root")
    operations: dict[int, dict[str, Any]] = {}
    next_ordinal = [0]

    def body(value: Any, visible: set[int]) -> None:
        require(type(value) is list and bool(value), "GOLD_SCHEMA", "body")
        current = set(visible)
        for node in value:
            require(type(node) is dict, "GOLD_SCHEMA", "node")
            if node.get("kind") == "operation":
                require(set(node) in ({"kind", "route"}, {"kind", "route", "data_from"}), "GOLD_SCHEMA", "operation")
                route = node.get("route")
                require(route in registry["operations"], "GOLD_SCHEMA", "route")
                metadata = registry["operations"][route]
                edges = node.get("data_from", [])
                require(type(edges) is list and ("data_from" not in node or bool(edges)), "GOLD_SCHEMA", "edges")
                signatures = set()
                for edge in edges:
                    require(type(edge) is dict and "from" in edge
                            and set(edge) <= {"from", "output", "input"}, "GOLD_SCHEMA", "edge")
                    source = edge["from"]
                    require(type(source) is int and source in current, "GOLD_SCHEMA", "dominance")
                    require("output" not in edge or edge["output"] in operations[source]["output_ports"],
                            "GOLD_SCHEMA", "output port")
                    require("input" not in edge or edge["input"] in metadata["input_ports"],
                            "GOLD_SCHEMA", "input port")
                    signature = tuple(sorted(edge.items()))
                    require(signature not in signatures, "GOLD_SCHEMA", "duplicate edge")
                    signatures.add(signature)
                ordinal = next_ordinal[0]; next_ordinal[0] += 1
                operations[ordinal] = metadata; current.add(ordinal)
            elif node.get("kind") == "barrier":
                require(set(node) == {"kind", "barrier", "cases"} and node["barrier"] in registry["barriers"],
                        "GOLD_SCHEMA", "barrier")
                cases = node["cases"]
                require(type(cases) is list and 1 <= len(cases) <= 2, "GOLD_SCHEMA", "cases")
                outcomes = []
                for case in cases:
                    require(type(case) is dict and set(case) == {"outcome", "body"}, "GOLD_SCHEMA", "case")
                    outcomes.append(case["outcome"])
                    body(case["body"], set(current))
                registered = registry["barriers"][node["barrier"]]["outcomes"]
                require(len(outcomes) == len(set(outcomes)) and outcomes == [x for x in registered if x in outcomes],
                        "GOLD_SCHEMA", "outcomes")
            else:
                raise ValidationError("GOLD_SCHEMA", "node kind")

    body(document["body"], set())


def semantic_gold(proposal: Proposal, process_freeze_sha256: str) -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-semantic-gold/0.4",
        "proposal_sha256": digest(proposal_value(proposal)),
        "process_freeze_sha256": process_freeze_sha256,
        "expected": derive_expected(proposal),
        "safety_applicability": derive_safety(proposal),
        "confidence": "high",
    }


def obligation_signature(item: Obligation) -> tuple:
    if isinstance(item, Positive): return ("positive", item.route)
    if isinstance(item, Negated): return ("negated", item.route)
    if isinstance(item, Outside): return ("outside", item.family, item.tempting_route)
    return ("control", item.control)


def fingerprint(proposal: Proposal) -> str:
    signatures = [obligation_signature(item) for item in proposal.obligations]
    referenced = set()
    for relation in proposal.relations:
        if isinstance(relation, (Independent, ApprovalOwns)):
            referenced.update(relation.members)
        elif isinstance(relation, Consumes):
            referenced.update((relation.source, relation.target))
        elif isinstance(relation, ExplicitOrder):
            referenced.update((relation.before, relation.after))
        else:
            referenced.update(relation.true_members + relation.false_members)
    unreferenced = sorted(signatures[index] for index in range(len(signatures)) if index not in referenced)
    fingerprint_obligations = [signatures[index] for index in range(len(signatures)) if index in referenced] + unreferenced
    relations = []
    for item in proposal.relations:
        if isinstance(item, Independent):
            relations.append(("independent", tuple(sorted(signatures[index] for index in item.members))))
        elif isinstance(item, Consumes):
            relations.append(("consumes", signatures[item.source], signatures[item.target], item.output_port, item.input_port))
        elif isinstance(item, ExplicitOrder):
            relations.append(("explicit_order", signatures[item.before], signatures[item.after]))
        elif isinstance(item, ApprovalOwns):
            relations.append(("approval_owns", tuple(signatures[index] for index in item.members)))
        else:
            relations.append(("branches", tuple(signatures[index] for index in item.true_members),
                              tuple(signatures[index] for index in item.false_members), item.ordered))
    payload = {
        "obligations": fingerprint_obligations,
        "relations": sorted(relations, key=repr),
        "expected": derive_expected(proposal),
        "safety": derive_safety(proposal),
    }
    return digest(payload)


def validate_fingerprint_set(proposals: tuple[Proposal, ...], *, scale: str) -> None:
    groups: dict[str, list[Proposal]] = {}
    for proposal in proposals:
        groups.setdefault(fingerprint(proposal), []).append(proposal)
    for values in groups.values():
        if len(values) == 1:
            continue
        require(scale == "full" and all(item.cell == "S4_UNDO" for item in values), "FINGERPRINT_COLLISION")


def _projection_relation(item: Relation) -> dict[str, Any]:
    value = relation_value(item)
    value.pop("output_port", None)
    value.pop("input_port", None)
    return value


def projection_value(proposal: Proposal, glossary: dict[str, Any]) -> dict[str, Any]:
    obligations = []
    for item in proposal.obligations:
        signature = obligation_signature(item)
        if isinstance(item, (Positive, Negated)):
            operation = glossary["operations"][item.route]
            neutral = {"author_gloss": operation["author_gloss"]}
        elif isinstance(item, Outside):
            neutral = {"author_gloss": glossary["outside_families"][item.family] + "."}
            if item.tempting_route is not None:
                neutral["tempting_but_insufficient_gloss"] = glossary["operations"][item.tempting_route]["author_gloss"]
        else:
            neutral = glossary["system_controls"][item.control]
        obligations.append({"kind": signature[0], **neutral})
    return {
        "format": "metnos.intent-holdout-author-projection/0.4",
        "proposal_sha256": digest(proposal_value(proposal)),
        "process_freeze_sha256": proposal.process_freeze_sha256,
        "language_tag": proposal.language_tag,
        "obligations": obligations,
        "relations": [_projection_relation(item) for item in proposal.relations],
        "mention_order": list(range(len(proposal.obligations))),
        "surface_constraints": list(proposal.surface_constraints),
    }


def author_bundle_value(projection: dict[str, Any], constitution_bytes: bytes) -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-author-bundle/0.4",
        "projection": projection,
        "constitution_sha256": digest_bytes(constitution_bytes),
        "one_query": True,
        "network": False,
        "gpu": False,
    }


def validate_projection(projection: dict[str, Any], proposal: Proposal, glossary: dict[str, Any]) -> None:
    expected = projection_value(proposal, glossary)
    require(canonical_bytes(projection) == canonical_bytes(expected), "PROJECTION_MISMATCH")
    raw = canonical_bytes(projection)
    forbidden = (b'"expected"', b'"reason"', b'"route"', b"/home/", b"from_step", b"with_step", b"on_keys", b"credentials.store")
    require(all(item not in raw for item in forbidden), "PROJECTION_LEAK")
    require(all(route.encode("utf-8") not in raw for route in glossary["operations"]), "PROJECTION_LEAK", "route substring")
    stack: list[Any] = [projection]
    route_names = set(glossary["operations"])
    while stack:
        value = stack.pop()
        if type(value) is dict: stack.extend(value.values())
        elif type(value) is list: stack.extend(value)
        elif type(value) is str: require(value not in route_names, "PROJECTION_LEAK", value)


def validate_author_bundle(bundle: dict[str, Any], projection: dict[str, Any], constitution_bytes: bytes) -> None:
    require(bool(constitution_bytes), "BUNDLE_MISMATCH", "empty constitution")
    expected = author_bundle_value(projection, constitution_bytes)
    require(canonical_bytes(bundle) == canonical_bytes(expected), "BUNDLE_MISMATCH")


def reviewer_value(identity: ReviewerIdentity) -> dict[str, str]:
    return {"reviewer_id": identity.reviewer_id, "context_id": identity.context_id}


def pool_a_bundle_value(
    proposal: Proposal, registry: dict[str, Any], schema: dict[str, Any],
    glossary: dict[str, Any], authority: dict[str, Any], rubrics: dict[str, Any],
) -> dict[str, Any]:
    return {"format": "metnos.intent-holdout-pool-a-bundle/0.4", "proposal": proposal_value(proposal),
            "registry": registry, "canonical_schema": schema, "glossary": glossary,
            "authority": authority, "rubrics": rubrics, "query_visible": False}


def pool_b_bundle_value(
    proposal: Proposal, query_bytes: bytes, registry: dict[str, Any], schema: dict[str, Any],
    glossary: dict[str, Any], authority: dict[str, Any], rubrics: dict[str, Any],
) -> dict[str, Any]:
    _validate_query_bytes(query_bytes)
    return {"format": "metnos.intent-holdout-pool-b-bundle/0.4", "proposal_sha256": digest(proposal_value(proposal)),
            "language_tag": proposal.language_tag, "query": query_bytes.decode("utf-8"),
            "registry": registry, "canonical_schema": schema, "glossary": glossary,
            "authority": authority, "rubrics": rubrics, "proposal_visible": False}


def review_input_value(role: str, case_id: str, query_panel: dict[str, bytes],
                       expected_cases: dict[str, str], rubrics: dict[str, Any]) -> dict[str, Any]:
    require(role in {"native_reviewer", "cross_language_reviewer"}, "LIFECYCLE_ROLE")
    rows = sorted(query_panel) if role == "cross_language_reviewer" else [case_id]
    return {"format": "metnos.intent-holdout-review-bundle/0.4", "role": role,
            "cases": [{"case_id": item, "language_tag": expected_cases[item],
                       "query": query_panel[item].decode("utf-8")} for item in rows],
            "rubrics": rubrics}


def pool_a_judgment(identity: ReviewerIdentity, proposal: Proposal) -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-pool-a-judgment/0.4",
        "reviewer": reviewer_value(identity),
        "gold": semantic_gold(proposal, proposal.process_freeze_sha256),
    }


def validate_pool_a(records: tuple[dict[str, Any], dict[str, Any]], proposal: Proposal, registry: dict[str, Any]) -> dict[str, Any]:
    require(len(records) == 2, "POOL_IDENTITY")
    require(all(type(item) is dict and set(item) == {"format", "reviewer", "gold"}
                and type(item["reviewer"]) is dict and set(item["reviewer"]) == {"reviewer_id", "context_id"}
                for item in records), "CLOSED_SCHEMA", "pool A")
    identities = [(item["reviewer"].get("reviewer_id"), item["reviewer"].get("context_id")) for item in records]
    require(all(type(value) is str and bool(value) for identity in identities for value in identity)
            and identities[0][0] != identities[1][0] and identities[0][1] != identities[1][1], "POOL_IDENTITY")
    expected = semantic_gold(proposal, proposal.process_freeze_sha256)
    validate_expected_document(expected["expected"], registry)
    for item in records:
        require(item["format"] == "metnos.intent-holdout-pool-a-judgment/0.4" and item["gold"] == expected,
                "GOLD_MISMATCH")
    return expected


def pool_b_judgment(identity: ReviewerIdentity, proposal: Proposal, query_bytes: bytes) -> dict[str, Any]:
    _validate_query_bytes(query_bytes)
    return {
        "format": "metnos.intent-holdout-pool-b-judgment/0.4",
        "reviewer": reviewer_value(identity),
        "query_sha256": digest_bytes(query_bytes),
        "proposal_sha256": digest(proposal_value(proposal)),
        "process_freeze_sha256": proposal.process_freeze_sha256,
        "language_tag": proposal.language_tag,
        "reconstructed_obligations": [obligation_value(item) for item in proposal.obligations],
        "reconstructed_relations": [relation_value(item) for item in proposal.relations],
        "expected": derive_expected(proposal),
        "safety_applicability": derive_safety(proposal),
        "confidence": "high",
    }


def validate_pool_b(
    records: tuple[dict[str, Any], dict[str, Any]], *, proposal: Proposal,
    gold: dict[str, Any], query_bytes: bytes, pool_a_records: tuple[dict[str, Any], dict[str, Any]],
    registry: dict[str, Any],
) -> None:
    _validate_query_bytes(query_bytes)
    require(validate_pool_a(pool_a_records, proposal, registry) == gold,
            "GOLD_MISMATCH", "pool A binding")
    require(len(records) == 2, "POOL_IDENTITY")
    required = {"format", "reviewer", "query_sha256", "proposal_sha256", "process_freeze_sha256",
                "language_tag", "reconstructed_obligations", "reconstructed_relations",
                "expected", "safety_applicability", "confidence"}
    require(all(type(item) is dict and set(item) == required and type(item["reviewer"]) is dict
                and set(item["reviewer"]) == {"reviewer_id", "context_id"} for item in records),
            "CLOSED_SCHEMA", "pool B")
    identities = [(item["reviewer"].get("reviewer_id"), item["reviewer"].get("context_id")) for item in records]
    pool_a_ids = {(item["reviewer"]["reviewer_id"], item["reviewer"]["context_id"]) for item in pool_a_records}
    pool_a_reviewer_ids = {item[0] for item in pool_a_ids}
    require(all(type(value) is str and bool(value) for identity in identities for value in identity)
            and identities[0][0] != identities[1][0] and identities[0][1] != identities[1][1]
            and not {item[0] for item in identities} & pool_a_reviewer_ids
            and not {item[1] for item in identities} & {item[1] for item in pool_a_ids},
            "POOL_IDENTITY")
    expected = pool_b_judgment(ReviewerIdentity(*identities[0]), proposal, query_bytes)
    validate_expected_document(gold["expected"], registry)
    expected.pop("reviewer")
    for item in records:
        core = dict(item)
        core.pop("reviewer", None)
        require(core == expected, "RECONSTRUCTION_MISMATCH")
        require(item["expected"] == gold["expected"] and item["safety_applicability"] == gold["safety_applicability"],
                "GOLD_MISMATCH")


def _validate_query_bytes(query_bytes: bytes) -> None:
    require(type(query_bytes) is bytes and bool(query_bytes), "QUERY_INVALID", "empty query")
    try:
        query = query_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("QUERY_INVALID", "utf8") from exc
    require(query == query.strip() and bool(query.strip()) and "\x00" not in query, "QUERY_INVALID", "text")


def review_value(
    role: str, identity: ReviewerIdentity, case_id: str, language_tag: str,
    query_bytes: bytes, rubric_bytes: bytes,
) -> dict[str, Any]:
    require(role in {"native_reviewer", "cross_language_reviewer"}, "LIFECYCLE_ROLE")
    return {
        "format": "metnos.intent-holdout-review/0.4",
        "role": role,
        "reviewer": reviewer_value(identity),
        "case_id": case_id,
        "language_tag": language_tag,
        "query_sha256": digest_bytes(query_bytes),
        "rubric_sha256": digest_bytes(rubric_bytes),
        "verdict": "pass",
        "findings": [],
    }


def validate_lifecycle(
    records: tuple[LifecycleRecord, ...], *, expected_cases: dict[str, str],
    artifact_bytes: dict[tuple[str, str, str], tuple[bytes, bytes]],
    query_bytes: dict[str, bytes], rubric_bytes: bytes,
) -> None:
    require(bool(records), "LIFECYCLE_INCOMPLETE")
    expected_author_cases = set(expected_cases)
    context_owner: dict[str, tuple[str, str]] = {}
    reviewer_owner: dict[str, tuple[str, str]] = {}
    emitted: set[tuple[str, str, str]] = set()
    author_cases: set[str] = set()
    pool_cases: dict[str, dict[tuple[str, str], set[str]]] = {"pool_a": {}, "pool_b": {}}
    review_cases: dict[str, dict[tuple[str, str], set[str]]] = {
        "native_reviewer": {}, "cross_language_reviewer": {},
    }
    allowed_roles = {"author", "pool_a", "pool_b", "native_reviewer", "cross_language_reviewer"}
    actual_keys = {(item.role, item.identity.context_id, item.case_id) for item in records}
    require(set(artifact_bytes) == actual_keys, "HASH_BINDING", "lifecycle artifacts")
    require(set(query_bytes) == expected_author_cases and bool(rubric_bytes), "HASH_BINDING", "query/rubric artifacts")
    for raw_query in query_bytes.values():
        _validate_query_bytes(raw_query)
    for item in records:
        require(item.role in allowed_roles, "LIFECYCLE_ROLE")
        require(type(item.identity.reviewer_id) is str and bool(item.identity.reviewer_id)
                and type(item.identity.context_id) is str and bool(item.identity.context_id),
                "POOL_IDENTITY", "empty identity")
        owner = (item.role, item.identity.reviewer_id)
        require(context_owner.get(item.identity.context_id, owner) == owner, "ISOLATION_REUSE", "context crosses identity or role")
        context_owner[item.identity.context_id] = owner
        reviewer_key = (item.role, item.identity.context_id)
        require(reviewer_owner.get(item.identity.reviewer_id, reviewer_key) == reviewer_key,
                "ISOLATION_REUSE", "reviewer crosses context or role")
        reviewer_owner[item.identity.reviewer_id] = reviewer_key
        record_key = (item.role, item.identity.context_id, item.case_id)
        require(record_key not in emitted, "ISOLATION_REUSE", "duplicate lifecycle record")
        emitted.add(record_key)
        require(item.case_id in expected_cases and item.language_tag == expected_cases[item.case_id],
                "LIFECYCLE_VIOLATION", "case/language binding")
        require(re.fullmatch(r"[0-9a-f]{64}", item.input_bundle_sha256) is not None
                and re.fullmatch(r"[0-9a-f]{64}", item.output_sha256) is not None, "HASH_BINDING")
        input_bytes, output_bytes = artifact_bytes[record_key]
        require(bool(input_bytes) and bool(output_bytes)
                and item.input_bundle_sha256 == digest_bytes(input_bytes)
                and item.output_sha256 == digest_bytes(output_bytes), "HASH_BINDING", "lifecycle byte binding")
        require(item.forbidden_reads == item.network_calls == item.gpu_calls == item.postseal_edits == 0,
                "LIFECYCLE_VIOLATION")
        if item.role == "author":
            require(item.query_count == 1, "LIFECYCLE_VIOLATION")
            require(output_bytes == query_bytes[item.case_id], "HASH_BINDING", "author query")
            author_cases.add(item.case_id)
        else:
            require(item.query_count == 0, "LIFECYCLE_VIOLATION")
            if item.role in pool_cases:
                identity = (item.identity.reviewer_id, item.identity.context_id)
                pool_cases[item.role].setdefault(identity, set()).add(item.case_id)
            elif item.role in review_cases:
                identity = (item.identity.reviewer_id, item.identity.context_id)
                review_cases[item.role].setdefault(identity, set()).add(item.case_id)
                try:
                    review = json.loads(output_bytes)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValidationError("LIFECYCLE_VIOLATION", "review json") from exc
                require(output_bytes == canonical_bytes(review)
                        and review == review_value(item.role, item.identity, item.case_id, item.language_tag,
                                                  query_bytes[item.case_id], rubric_bytes),
                        "LIFECYCLE_VIOLATION", "review verdict")
    require(author_cases == expected_author_cases, "LIFECYCLE_INCOMPLETE")
    author_identities: dict[tuple[str, str], set[str]] = {}
    for item in records:
        if item.role == "author":
            author_identities.setdefault((item.identity.reviewer_id, item.identity.context_id), set()).add(item.case_id)
    require(len(author_identities) == len(expected_author_cases)
            and all(len(cases) == 1 for cases in author_identities.values()), "ISOLATION_REUSE", "one author context per case")
    for role in ("pool_a", "pool_b"):
        require(len(pool_cases[role]) == 2 and all(cases == expected_author_cases for cases in pool_cases[role].values()),
                "LIFECYCLE_INCOMPLETE", role)
    require(set(pool_cases["pool_a"]).isdisjoint(pool_cases["pool_b"]), "ISOLATION_REUSE", "pools overlap")
    native_counts = {case_id: 0 for case_id in expected_author_cases}
    for identity, cases in review_cases["native_reviewer"].items():
        require(len({expected_cases[case_id] for case_id in cases}) == 1,
                "LIFECYCLE_INCOMPLETE", f"native reviewer crosses languages: {identity[0]}")
        for case_id in cases:
            native_counts[case_id] += 1
    require(native_counts and set(native_counts.values()) == {1}, "LIFECYCLE_INCOMPLETE", "native review")
    require(len(review_cases["cross_language_reviewer"]) == 1
            and next(iter(review_cases["cross_language_reviewer"].values())) == expected_author_cases,
            "LIFECYCLE_INCOMPLETE", "cross-language review")


def lifecycle_value(records: tuple[LifecycleRecord, ...]) -> list[dict[str, Any]]:
    result = []
    for item in sorted(records, key=lambda row: (row.role, row.identity.context_id, row.case_id)):
        result.append({
            "role": item.role, "identity": reviewer_value(item.identity), "case_id": item.case_id,
            "language_tag": item.language_tag, "input_bundle_sha256": item.input_bundle_sha256,
            "output_sha256": item.output_sha256, "query_count": item.query_count,
            "forbidden_reads": item.forbidden_reads, "network_calls": item.network_calls,
            "gpu_calls": item.gpu_calls, "postseal_edits": item.postseal_edits,
        })
    return result


def parse_lifecycle_bytes(raw: bytes) -> tuple[LifecycleRecord, ...]:
    value = _canonical_json(raw, "LIFECYCLE_VIOLATION")
    require(type(value) is list, "LIFECYCLE_VIOLATION", "lifecycle list")
    required = {"role", "identity", "case_id", "language_tag", "input_bundle_sha256", "output_sha256",
                "query_count", "forbidden_reads", "network_calls", "gpu_calls", "postseal_edits"}
    records = []
    for item in value:
        require(type(item) is dict and set(item) == required and type(item["identity"]) is dict
                and set(item["identity"]) == {"reviewer_id", "context_id"}
                and all(type(item[name]) is str for name in (
                    "role", "case_id", "language_tag", "input_bundle_sha256", "output_sha256"))
                and all(type(item[name]) is int for name in (
                    "query_count", "forbidden_reads", "network_calls", "gpu_calls", "postseal_edits")),
                "LIFECYCLE_VIOLATION", "lifecycle schema")
        records.append(LifecycleRecord(
            item["role"], ReviewerIdentity(item["identity"]["reviewer_id"], item["identity"]["context_id"]),
            item["case_id"], item["language_tag"], item["input_bundle_sha256"], item["output_sha256"],
            item["query_count"], item["forbidden_reads"], item["network_calls"], item["gpu_calls"],
            item["postseal_edits"],
        ))
    result = tuple(records)
    require(raw == canonical_bytes(lifecycle_value(result)), "LIFECYCLE_VIOLATION", "noncanonical lifecycle")
    return result


def _canonical_json(raw: bytes, code: str) -> Any:
    require(type(raw) is bytes and bool(raw), code, "empty bytes")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(code, "json") from exc
    require(raw == canonical_bytes(value), code, "noncanonical json")
    return value


def envelope_value(
    *, proposal_bytes: bytes, process_freeze_sha256: str, projection_bytes: bytes, author_bundle_bytes: bytes,
    gold_bytes: bytes, query_bytes: bytes, pool_a_bytes: tuple[bytes, bytes],
    pool_b_bytes: tuple[bytes, bytes], rubric_bytes: bytes, lifecycle_bytes: bytes,
) -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-case-envelope/0.4",
        "proposal_sha256": digest_bytes(proposal_bytes),
        "projection_sha256": digest_bytes(projection_bytes),
        "author_bundle_sha256": digest_bytes(author_bundle_bytes),
        "gold_sha256": digest_bytes(gold_bytes),
        "query_sha256": digest_bytes(query_bytes),
        "pool_a_sha256": [digest_bytes(item) for item in pool_a_bytes],
        "pool_b_sha256": [digest_bytes(item) for item in pool_b_bytes],
        "rubric_sha256": digest_bytes(rubric_bytes),
        "lifecycle_sha256": digest_bytes(lifecycle_bytes),
        "process_freeze_sha256": process_freeze_sha256,
        "exact_match": True,
    }


def validate_case_artifacts(
    envelope: dict[str, Any], *, proposal_bytes: bytes, projection_bytes: bytes,
    author_bundle_bytes: bytes, constitution_bytes: bytes, gold_bytes: bytes, query_bytes: bytes,
    pool_a_bytes: tuple[bytes, bytes], pool_b_bytes: tuple[bytes, bytes], rubric_bytes: bytes,
    lifecycle_bytes: bytes, lifecycle_artifacts: dict[tuple[str, str, str], tuple[bytes, bytes]],
    query_panel: dict[str, bytes], registry: dict[str, Any], schema: dict[str, Any],
    glossary: dict[str, Any], authority: dict[str, Any], matrix: dict[str, Any],
    rubrics: dict[str, Any], process_freeze_bytes: bytes, expected_process_freeze_sha256: str,
) -> None:
    require(type(process_freeze_bytes) is bytes and bool(process_freeze_bytes), "HASH_BINDING", "process freeze")
    try:
        process_freeze = json.loads(process_freeze_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("HASH_BINDING", "process freeze json") from exc
    freeze_sha = digest_bytes(process_freeze_bytes)
    require(freeze_sha == expected_process_freeze_sha256, "HASH_BINDING", "trusted process freeze")
    require(process_freeze["local_files"]["FORMAL_SPEC.md"] == digest_bytes(constitution_bytes)
            and process_freeze["generated"]["glossary.json"]["payload_sha256"] == digest(glossary)
            and process_freeze["generated"]["pilot_matrix.json"]["payload_sha256"] == digest(matrix)
            and process_freeze["generated"]["rubrics.json"]["payload_sha256"] == digest(rubrics)
            and rubric_bytes == canonical_bytes(rubrics), "HASH_BINDING", "frozen case authorities")
    proposal_value_raw = _canonical_json(proposal_bytes, "ENVELOPE_MISMATCH")
    proposal = parse_proposal(proposal_value_raw)
    validate_proposal(proposal, registry=registry, glossary=glossary, freeze_sha=freeze_sha, matrix=matrix)
    projection = _canonical_json(projection_bytes, "ENVELOPE_MISMATCH")
    validate_projection(projection, proposal, glossary)
    bundle = _canonical_json(author_bundle_bytes, "ENVELOPE_MISMATCH")
    validate_author_bundle(bundle, projection, constitution_bytes)
    _validate_query_bytes(query_bytes)
    require(query_panel.get(proposal.proposal_id) == query_bytes, "ENVELOPE_MISMATCH", "query panel")
    gold = _canonical_json(gold_bytes, "ENVELOPE_MISMATCH")
    pool_a = tuple(_canonical_json(raw, "ENVELOPE_MISMATCH") for raw in pool_a_bytes)
    derived_gold = validate_pool_a(pool_a, proposal, registry)
    require(gold == derived_gold, "GOLD_MISMATCH", "sealed gold")
    pool_b = tuple(_canonical_json(raw, "ENVELOPE_MISMATCH") for raw in pool_b_bytes)
    validate_pool_b(pool_b, proposal=proposal, gold=gold, query_bytes=query_bytes,
                    pool_a_records=pool_a, registry=registry)
    lifecycle = parse_lifecycle_bytes(lifecycle_bytes)
    expected_cases = {item["proposal_id"]: item["language_tag"] for item in matrix["slots"]}
    validate_lifecycle(lifecycle, expected_cases=expected_cases, artifact_bytes=lifecycle_artifacts,
                       query_bytes=query_panel, rubric_bytes=rubric_bytes)
    case_records = [item for item in lifecycle if item.case_id == proposal.proposal_id]
    for role, expected_outputs in (("author", (query_bytes,)), ("pool_a", pool_a_bytes), ("pool_b", pool_b_bytes)):
        records_for_role = [item for item in case_records if item.role == role]
        outputs = tuple(lifecycle_artifacts[(item.role, item.identity.context_id, item.case_id)][1]
                        for item in records_for_role)
        require(set(outputs) == set(expected_outputs) and len(outputs) == len(expected_outputs),
                "ENVELOPE_MISMATCH", f"{role} lifecycle output")
        if role in {"pool_a", "pool_b"}:
            lifecycle_identities = {(item.identity.reviewer_id, item.identity.context_id) for item in records_for_role}
            output_identities = {
                (record["reviewer"]["reviewer_id"], record["reviewer"]["context_id"])
                for record in (_canonical_json(raw, "ENVELOPE_MISMATCH") for raw in expected_outputs)
            }
            require(lifecycle_identities == output_identities, "ENVELOPE_MISMATCH", f"{role} identity binding")
    author_record = next(item for item in case_records if item.role == "author")
    require(lifecycle_artifacts[("author", author_record.identity.context_id, proposal.proposal_id)][0]
            == author_bundle_bytes, "ENVELOPE_MISMATCH", "author input bundle")
    expected_inputs = {
        "pool_a": canonical_bytes(pool_a_bundle_value(proposal, registry, schema, glossary, authority, rubrics)),
        "pool_b": canonical_bytes(pool_b_bundle_value(proposal, query_bytes, registry, schema, glossary, authority, rubrics)),
        "native_reviewer": canonical_bytes(review_input_value(
            "native_reviewer", proposal.proposal_id, query_panel, expected_cases, rubrics)),
        "cross_language_reviewer": canonical_bytes(review_input_value(
            "cross_language_reviewer", proposal.proposal_id, query_panel, expected_cases, rubrics)),
    }
    for item in case_records:
        if item.role in expected_inputs:
            require(lifecycle_artifacts[(item.role, item.identity.context_id, item.case_id)][0]
                    == expected_inputs[item.role], "ENVELOPE_MISMATCH", f"{item.role} input bundle")
    expected = envelope_value(
        proposal_bytes=proposal_bytes, process_freeze_sha256=proposal.process_freeze_sha256,
        projection_bytes=projection_bytes, author_bundle_bytes=author_bundle_bytes, gold_bytes=gold_bytes,
        query_bytes=query_bytes, pool_a_bytes=pool_a_bytes, pool_b_bytes=pool_b_bytes,
        rubric_bytes=rubric_bytes, lifecycle_bytes=lifecycle_bytes,
    )
    require(canonical_bytes(envelope) == canonical_bytes(expected), "ENVELOPE_MISMATCH")


def verify_frozen_files(
    *, process_freeze_bytes: bytes, expected_process_freeze_sha256: str,
    frozen_files: dict[str, bytes],
) -> dict[str, Any]:
    require(digest_bytes(process_freeze_bytes) == expected_process_freeze_sha256,
            "HASH_BINDING", "trusted process freeze")
    try:
        freeze = json.loads(process_freeze_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("HASH_BINDING", "process freeze json") from exc
    required = ({f"local:{name}" for name in freeze["local_files"]}
                | {f"source:{name}" for name in freeze["source_files"]}
                | {f"generated:{name}" for name in freeze["generated"]})
    require(set(frozen_files) == required, "HASH_BINDING", "frozen file closure")
    for name, expected in freeze["local_files"].items():
        require(digest_bytes(frozen_files[f"local:{name}"]) == expected, "HASH_BINDING", f"local:{name}")
    for name, expected in freeze["source_files"].items():
        require(digest_bytes(frozen_files[f"source:{name}"]) == expected, "HASH_BINDING", f"source:{name}")
    for name, expected in freeze["generated"].items():
        raw = frozen_files[f"generated:{name}"]
        require(digest_bytes(raw) == expected["file_sha256"], "HASH_BINDING", f"generated:{name}")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("HASH_BINDING", f"generated json:{name}") from exc
        require(digest(value) == expected["payload_sha256"], "HASH_BINDING", f"generated payload:{name}")
    return freeze


def validate_panel(
    *, cases: tuple[dict[str, Any], ...], lifecycle_bytes: bytes,
    lifecycle_artifacts: dict[tuple[str, str, str], tuple[bytes, bytes]],
    query_panel: dict[str, bytes], process_freeze_bytes: bytes,
    expected_process_freeze_sha256: str, frozen_files: dict[str, bytes],
) -> dict[str, Any]:
    freeze = verify_frozen_files(process_freeze_bytes=process_freeze_bytes,
                                 expected_process_freeze_sha256=expected_process_freeze_sha256,
                                 frozen_files=frozen_files)
    generated = {name: json.loads(frozen_files[f"generated:{name}"]) for name in freeze["generated"]}
    authority = generated["authority.json"]
    registry_path = authority["semantic"]["registry"]["path"]
    schema_path = authority["semantic"]["canonical_schema"]["path"]
    registry_raw = frozen_files[f"source:{registry_path}"]
    schema_raw = frozen_files[f"source:{schema_path}"]
    require(digest_bytes(registry_raw) == authority["semantic"]["registry"]["sha256"]
            and digest_bytes(schema_raw) == authority["semantic"]["canonical_schema"]["sha256"],
            "HASH_BINDING", "semantic authority")
    registry = json.loads(registry_raw); schema = json.loads(schema_raw)
    glossary = generated["glossary.json"]; matrix = generated["pilot_matrix.json"]
    rubrics = generated["rubrics.json"]
    expected_ids = [item["proposal_id"] for item in matrix["slots"]]
    require(len(cases) == len(expected_ids) == 20 and {item.get("case_id") for item in cases} == set(expected_ids),
            "PANEL_INCOMPLETE", "cases")
    require(set(query_panel) == set(expected_ids), "PANEL_INCOMPLETE", "queries")
    proposals = []
    for item in sorted(cases, key=lambda row: row["case_id"]):
        proposal_raw = item.get("proposal_bytes")
        proposal = parse_proposal(_canonical_json(proposal_raw, "PANEL_INCOMPLETE"))
        require(item["case_id"] == proposal.proposal_id, "ASSIGNMENT_MISMATCH", "case id")
        validate_proposal(proposal, registry=registry, glossary=glossary,
                          freeze_sha=expected_process_freeze_sha256, matrix=matrix)
        proposals.append(proposal)
    validate_fingerprint_set(tuple(proposals), scale="pilot")
    expected_cases = {item["proposal_id"]: item["language_tag"] for item in matrix["slots"]}
    lifecycle = parse_lifecycle_bytes(lifecycle_bytes)
    validate_lifecycle(lifecycle, expected_cases=expected_cases, artifact_bytes=lifecycle_artifacts,
                       query_bytes=query_panel, rubric_bytes=canonical_bytes(rubrics))
    constitution = frozen_files["local:FORMAL_SPEC.md"]
    for item in cases:
        validate_case_artifacts(
            item["envelope"], proposal_bytes=item["proposal_bytes"], projection_bytes=item["projection_bytes"],
            author_bundle_bytes=item["author_bundle_bytes"], constitution_bytes=constitution,
            gold_bytes=item["gold_bytes"], query_bytes=query_panel[item["case_id"]],
            pool_a_bytes=item["pool_a_bytes"], pool_b_bytes=item["pool_b_bytes"],
            rubric_bytes=canonical_bytes(rubrics), lifecycle_bytes=lifecycle_bytes,
            lifecycle_artifacts=lifecycle_artifacts, query_panel=query_panel, registry=registry, schema=schema,
            glossary=glossary, authority=authority, matrix=matrix, rubrics=rubrics,
            process_freeze_bytes=process_freeze_bytes,
            expected_process_freeze_sha256=expected_process_freeze_sha256,
        )
    return {"status": "passed", "cases": len(cases), "queries": len(query_panel),
            "process_freeze_sha256": expected_process_freeze_sha256}


def run_panel_once(ledger_path: Path, **panel: Any) -> dict[str, Any]:
    initial = canonical_bytes({"format": "metnos.intent-holdout-attempt/0.4", "attempt": 1, "state": "open"})
    try:
        descriptor = os.open(ledger_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValidationError("ATTEMPT_CONSUMED", str(ledger_path)) from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(initial); stream.flush(); os.fsync(stream.fileno())
    try:
        result = validate_panel(**panel)
    except BaseException:
        ledger_path.write_bytes(canonical_bytes({"format": "metnos.intent-holdout-attempt/0.4",
                                                 "attempt": 1, "state": "closed_failed"}))
        raise
    ledger_path.write_bytes(canonical_bytes({"format": "metnos.intent-holdout-attempt/0.4",
                                             "attempt": 1, "state": "passed"}))
    return result
