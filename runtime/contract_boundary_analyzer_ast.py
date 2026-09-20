"""Pure AST and name primitives for the contract-boundary analyzer."""
from __future__ import annotations

import ast
from typing import Iterable, Mapping


def leaf_name_v1(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def dotted_name_v1(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name_v1(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def module_leaf_v1(module: str) -> str:
    return module.rsplit(".", 1)[-1]


def target_names_v1(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        result: set[str] = set()
        for item in node.elts:
            result.update(target_names_v1(item))
        return result
    return set()


def string_values_v1(node: ast.AST) -> Iterable[str]:
    for item in ast.walk(node):
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            yield item.value


def static_string_v1(node: ast.AST) -> str | None:
    """Evaluate only syntax that is unambiguously a constant string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = static_string_v1(node.left)
        right = static_string_v1(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts = [static_string_v1(value) for value in node.values]
        return "".join(parts) if all(part is not None for part in parts) else None
    return None


def static_strings_v1(node: ast.AST) -> set[str]:
    return {
        value
        for item in ast.walk(node)
        if (value := static_string_v1(item)) is not None
    }


def resolved_alias_name_v1(
    node: ast.AST, aliases: Mapping[str, str],
) -> str | None:
    dotted = dotted_name_v1(node)
    if dotted is None:
        return None
    first, separator, remainder = dotted.partition(".")
    return aliases.get(first, first) + (
        separator + remainder if separator else ""
    )


def has_bound_root_v1(node: ast.AST, aliases: Mapping[str, str]) -> bool:
    """Return whether the first name is an observed import or alias."""
    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return isinstance(current, ast.Name) and current.id in aliases


ANALYZER_AST_HELPER_CATALOG_V1 = (
    ("leaf_name_v1", "_leaf_name"),
    ("dotted_name_v1", "_dotted_name"),
    ("module_leaf_v1", "_module_leaf"),
    ("target_names_v1", "_target_names"),
    ("string_values_v1", "_string_values"),
    ("static_string_v1", "_static_string"),
    ("static_strings_v1", "_static_strings"),
    ("resolved_alias_name_v1", "_resolved_alias_name"),
    ("has_bound_root_v1", "_has_bound_root"),
)

__all__ = [
    "ANALYZER_AST_HELPER_CATALOG_V1",
    "leaf_name_v1", "dotted_name_v1", "module_leaf_v1", "target_names_v1",
    "string_values_v1", "static_string_v1", "static_strings_v1",
    "resolved_alias_name_v1", "has_bound_root_v1",
]


def _validate_analyzer_ast_catalog_v1() -> None:
    catalog = ANALYZER_AST_HELPER_CATALOG_V1
    if type(catalog) is not tuple or any(
        type(row) is not tuple or len(row) != 2
        or any(type(name) is not str or not name for name in row)
        for row in catalog
    ):
        raise ValueError("contract_boundary_analyzer_ast_catalog_invalid")
    public = tuple(row[0] for row in catalog)
    legacy = tuple(row[1] for row in catalog)
    if len(set(public)) != len(public) or len(set(legacy)) != len(legacy):
        raise ValueError("contract_boundary_analyzer_ast_catalog_duplicate")
    if type(__all__) is not list or tuple(__all__) != (
        "ANALYZER_AST_HELPER_CATALOG_V1", *public,
    ):
        raise ValueError("contract_boundary_analyzer_ast_catalog_exports")


_validate_analyzer_ast_catalog_v1()
