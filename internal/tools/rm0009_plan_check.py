"""Read-only dependency checks for RM-0009's development plan.

This tool verifies the plan, not implementation, approval, or certification.
It deliberately does not import the runtime or open any application store.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path


Graph = Mapping[str, tuple[str, ...]]
_ID = re.compile(r"D-[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)+\Z")
_EXPANSION_FAMILIES = ("D-FS-A.3", "D-FS-B.3")
_ENFORCEMENT_PREREQUISITES = (
    "D-X0.1", "D-FS-A.4", "D-FS-B.4c", "D-F6.2.barrier",
)
_REQUIRED = {
    "D-I1.1": (
        "D-G0.10", "D-F1.1", "D-F1.2", "D-F1.3", "D-F5.6",
        "D-F5.7", "D-F5.8", "D-F5.9", "D-P2.9", "D-F6.2.barrier",
    ),
    "D-I0.1": ("D-I1.1", "D-X0.1", "D-FS-A.4", "D-S0.1"),
    "D-I0.7": ("D-I1.1", "D-X0.1", "D-FS-A.4", "D-S0.1"),
    "D-F6.3": _ENFORCEMENT_PREREQUISITES + ("D-F5.8",),
    "D-F6.4a": _ENFORCEMENT_PREREQUISITES,
    "D-F6.4b": _ENFORCEMENT_PREREQUISITES + ("D-F6.4a", "D-F5.8"),
    "D-F6.4c": _ENFORCEMENT_PREREQUISITES + ("D-F6.4a", "D-F6.4b"),
    "D-F6.4d": _ENFORCEMENT_PREREQUISITES + ("D-F6.4c",),
    "D-F6.4e": (
        "D-X0.1", "D-FS-A.4", "D-FS-B.4c", "D-F6.2.barrier",
        "D-F6.4a", "D-F6.4b", "D-F6.4c", "D-F6.4d",
    ),
    "D-F6.5": _ENFORCEMENT_PREREQUISITES + ("D-F5.8",),
    "D-F6.6": ("D-F6.3", "D-F6.4e", "D-F6.5", "D-P2.9"),
}
_SECURITY = re.compile(r"D-(?:FS-[AB]\.|S0\.|X0\.|F6\.[3456](?:[a-z.]|$))")


class PlanFormatError(ValueError):
    """An explicit format error, distinct from a valid but incomplete plan."""

    def __init__(self, code: str, line: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.line = line


def parse_plan(document: str) -> dict[str, tuple[str, ...]]:
    """Read the five-column Appendix D tables, ignoring fenced examples.

    This is a restricted plan format, not a general Markdown parser. Headings,
    fences and task rows may have at most three leading spaces. Fences use
    CommonMark's character, minimum-length and whitespace-only closing rules.
    """
    graph: dict[str, tuple[str, ...]] = {}
    in_plan = False
    found_plan = False
    fence: tuple[str, int, int] | None = None
    for number, line in enumerate(document.splitlines(), 1):
        stripped = line.strip()
        if fence:
            closing = re.match(r"^ {0,3}(`+|~+)[ \t]*$", line)
            if (closing and closing[1][0] == fence[0]
                    and len(closing[1]) >= fence[1]):
                fence = None
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            fence = (opening[1][0], len(opening[1]), number)
            continue
        if re.match(r"^ {0,3}## Appendice D(?:\s|$)", line):
            if found_plan:
                raise PlanFormatError("duplicate_plan_section", number)
            found_plan = in_plan = True
            continue
        if re.match(r"^ {0,3}## ", line):
            in_plan = False
        if not in_plan or not re.match(r"^ {0,3}\|\s*D-", line):
            continue
        cells = stripped.split("|")
        if len(cells) < 7:
            raise PlanFormatError("malformed_row", number)
        node, raw_dependencies = cells[1].strip(), cells[2].strip()
        if not _ID.fullmatch(node):
            raise PlanFormatError("invalid_id", number)
        if node in graph:
            raise PlanFormatError("duplicate_id", number)
        if not raw_dependencies:
            raise PlanFormatError("missing_dependency_cell", number)
        dependencies: list[str] = []
        if raw_dependencies != "—":
            for raw in raw_dependencies.split(","):
                value = raw.strip()
                dependency = value if value.startswith("D-") else f"D-{value}"
                if not _ID.fullmatch(dependency):
                    raise PlanFormatError("invalid_dependency", number)
                if dependency in dependencies:
                    raise PlanFormatError("duplicate_dependency", number)
                dependencies.append(dependency)
        graph[node] = tuple(dependencies)
    if fence:
        raise PlanFormatError("unclosed_fence", fence[2])
    if not graph:
        raise PlanFormatError("empty_plan")
    return graph


def _ancestors(graph: Graph, node: str) -> set[str]:
    visited: set[str] = set()
    pending = list(graph.get(node, ()))
    while pending:
        item = pending.pop()
        if item in visited:
            continue
        visited.add(item)
        pending.extend(graph.get(item, ()))
    return visited


def _cycle_witness(graph: Graph) -> list[str]:
    """Find a deterministic cycle without recursion or a stack depth limit."""
    completed: set[str] = set()
    for start in sorted(graph):
        if start in completed:
            continue
        path = [start]
        active = {start: 0}
        stack = [iter(sorted(graph[start]))]
        while stack:
            child = next(stack[-1], None)
            if child is None:
                completed_node = path.pop()
                completed.add(completed_node)
                del active[completed_node]
                stack.pop()
            elif child in active:
                return path[active[child]:] + [child]
            elif child in graph and child not in completed:
                active[child] = len(path)
                path.append(child)
                stack.append(iter(sorted(graph[child])))
    return []


def _is_expansion(node: object, family: str) -> bool:
    return (
        isinstance(node, str) and bool(_ID.fullmatch(node))
        and node.startswith(family + ".")
        and node != family + ".barrier" and "N" not in node.split(".")
    )


def _valid_expansion_inventory(inventory: object) -> bool:
    if not isinstance(inventory, Mapping) or set(inventory) != set(_EXPANSION_FAMILIES):
        return False
    for family, members in inventory.items():
        if (not isinstance(members, (list, tuple))
                or not all(_is_expansion(node, family) for node in members)):
            return False
        if len(set(members)) != len(members):
            return False
    return True


def validate_plan(
    graph: Graph, *, require_concrete: bool = False,
    expected_expansions: Mapping[str, Sequence[str]] | None = None,
) -> dict:
    """Check the development/execution separation specified in D.1-ter."""
    errors: list[dict] = []
    for node in sorted(graph):
        for dependency in sorted(graph[node]):
            if dependency not in graph:
                errors.append({
                    "code": "unknown_dependency", "node": node,
                    "dependency": dependency,
                })
    cycle = _cycle_witness(graph)
    if cycle:
        errors.append({"code": "cycle", "path": cycle})
    for node, required in _REQUIRED.items():
        if node not in graph:
            errors.append({"code": "required_node_missing", "node": node})
            continue
        ancestors = _ancestors(graph, node)
        for dependency in required:
            if dependency not in ancestors:
                errors.append({
                    "code": "required_ancestor_missing", "node": node,
                    "dependency": dependency,
                })
    preliminary = _ancestors(graph, "D-I1.1")
    for dependency in sorted(preliminary):
        if _SECURITY.match(dependency):
            errors.append({
                "code": "forbidden_ancestor", "node": "D-I1.1",
                "dependency": dependency,
            })
    templates = sorted(node for node in graph if "N" in node.split("."))
    if require_concrete:
        for node in templates:
            errors.append({"code": "unexpanded_template", "node": node})
        if expected_expansions is None:
            errors.append({"code": "expansion_inventory_required"})
    inventory_valid = _valid_expansion_inventory(expected_expansions)
    inventory_matches = inventory_valid
    if expected_expansions is not None and not inventory_valid:
        errors.append({"code": "invalid_expansion_inventory"})
    for family in _EXPANSION_FAMILIES:
        prefix = family + "."
        barrier = prefix + "barrier"
        members = {node for node in graph if _is_expansion(node, family)}
        if (not members and prefix + "N" not in graph
                and not (inventory_valid and not expected_expansions[family])):
            errors.append({"code": "missing_expansion_family", "family": family})
        if barrier not in graph:
            errors.append({"code": "required_node_missing", "node": barrier})
        if inventory_valid:
            expected = set(expected_expansions[family])
            for node in sorted(expected - members):
                errors.append({"code": "missing_expansion", "node": node})
            for node in sorted(members - expected):
                errors.append({"code": "unexpected_expansion", "node": node})
            inventory_matches = inventory_matches and expected == members
        dependencies = _ancestors(graph, barrier)
        for node in sorted(members):
            if node not in dependencies:
                errors.append({
                    "code": "unjoined_expansion", "node": node,
                    "barrier": barrier,
                })
    errors.sort(key=lambda item: json.dumps(item, sort_keys=True))
    return {
        "scope": "plan_dependencies_only",
        "valid": not errors,
        "concrete": not templates and inventory_matches,
        "expansion_inventory_checked": inventory_valid,
        "node_count": len(graph),
        "templates": templates,
        "preliminary_ancestor_count": len(preliminary),
        "real_integration_ancestor_count": len(_ancestors(graph, "D-I0.1")),
        "errors": errors,
    }


def check_document(
    document: str, *, require_concrete: bool = False,
    expected_expansions: Mapping[str, Sequence[str]] | None = None,
) -> dict:
    report = validate_plan(
        parse_plan(document), require_concrete=require_concrete,
        expected_expansions=expected_expansions,
    )
    report["document_sha256"] = hashlib.sha256(document.encode("utf-8")).hexdigest()
    return report


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise PlanFormatError("invalid_expansion_inventory")
        result[key] = value
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument(
        "--require-concrete", action="store_true",
        help="require expanded groups matching --expansion-inventory; not an approval",
    )
    parser.add_argument(
        "--expansion-inventory", type=Path,
        help='JSON mapping D-FS-A.3 and D-FS-B.3 to exact task-ID lists; [] asserts no work',
    )
    args = parser.parse_args(argv)
    inventory = None
    inventory_digest = None
    if args.expansion_inventory is not None:
        try:
            inventory_bytes = args.expansion_inventory.read_bytes()
            inventory = json.loads(
                inventory_bytes.decode("utf-8"), object_pairs_hook=_unique_json_object,
            )
            inventory_digest = hashlib.sha256(inventory_bytes).hexdigest()
            if not _valid_expansion_inventory(inventory):
                raise PlanFormatError("invalid_expansion_inventory")
        except (OSError, UnicodeError, json.JSONDecodeError):
            print(json.dumps({
                "scope": "plan_dependencies_only", "valid": False,
                "errors": [{"code": "expansion_inventory_unreadable"}],
            }, indent=2, sort_keys=True))
            return 2
        except PlanFormatError as exc:
            print(json.dumps({
                "scope": "plan_dependencies_only", "valid": False,
                "errors": [{"code": exc.code}],
            }, indent=2, sort_keys=True))
            return 1
    try:
        # Preserve newlines so the digest identifies the actual UTF-8 bytes.
        document = args.document.read_bytes().decode("utf-8")
        report = check_document(
            document, require_concrete=args.require_concrete,
            expected_expansions=inventory,
        )
        if inventory_digest is not None:
            report["expansion_inventory_sha256"] = inventory_digest
        result = 0 if report["valid"] else 1
    except (OSError, UnicodeError):
        report = {"valid": False, "errors": [{"code": "document_unreadable"}]}
        result = 2
    except PlanFormatError as exc:
        report = {"valid": False, "errors": [{"code": exc.code, "line": exc.line}]}
        result = 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
