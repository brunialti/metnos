"""Mechanical standalone projection of the boundary analyzer primitives."""
from __future__ import annotations

import ast
from pathlib import Path
import symtable

from executor_birth_crypto_framing import framed_sha256_v1


PROJECTION_DIGEST_DOMAIN_V1 = b"metnos.contract-boundary-analyzer-projection/v1\0"
BEGIN_MARKER_V1 = b"# BEGIN GENERATED CONTRACT BOUNDARY ANALYZER V1"
END_MARKER_V1 = b"# END GENERATED CONTRACT BOUNDARY ANALYZER V1"
OWNER_PATHS_V1 = (
    "runtime/contract_boundary_analyzer_types.py",
    "runtime/contract_boundary_analyzer_ast.py",
)
_PROJECTED_BUILTIN_NAMES_V1 = frozenset({
    "all", "bool", "int", "isinstance", "list", "property", "set", "str",
    "tuple",
})


class ContractBoundaryAnalyzerProjectionError(ValueError):
    """Canonical analyzer source cannot be projected deterministically."""


def _read_owner_sources_v1() -> tuple[tuple[str, bytes], ...]:
    root = Path(__file__).resolve().parent.parent
    result = []
    for relative in OWNER_PATHS_V1:
        try:
            source = root.joinpath(*relative.split("/")).read_bytes()
        except OSError as exc:
            raise ContractBoundaryAnalyzerProjectionError("owner_source") from exc
        if not source.endswith(b"\n") or b"\r" in source:
            raise ContractBoundaryAnalyzerProjectionError("owner_format")
        result.append((relative, source))
    return tuple(result)


def _parse_owner_v1(relative: str, source: bytes) -> tuple[str, ast.Module]:
    try:
        text = source.decode("ascii")
        tree = ast.parse(text, filename=relative)
    except (SyntaxError, UnicodeError, ValueError) as exc:
        raise ContractBoundaryAnalyzerProjectionError("owner_syntax") from exc
    return text, tree


def _literal_assignment_v1(tree: ast.Module, name: str) -> object:
    matches = [
        node for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
    ]
    if len(matches) != 1:
        raise ContractBoundaryAnalyzerProjectionError("owner_catalog")
    try:
        return ast.literal_eval(matches[0].value)
    except (ValueError, TypeError) as exc:
        raise ContractBoundaryAnalyzerProjectionError("owner_catalog") from exc


def _closed_names_v1(value: object, *, label: str) -> tuple[str, ...]:
    if type(value) is not tuple or any(
        type(name) is not str or not name for name in value
    ):
        raise ContractBoundaryAnalyzerProjectionError(label)
    names = tuple(value)
    if len(set(names)) != len(names):
        raise ContractBoundaryAnalyzerProjectionError(label)
    return names


def _type_names_v1(tree: ast.Module) -> tuple[str, ...]:
    names = _closed_names_v1(
        _literal_assignment_v1(tree, "ANALYZER_TYPE_NAMES_V1"),
        label="type_catalog",
    )
    exports = _literal_assignment_v1(tree, "__all__")
    if type(exports) is not list or tuple(exports) != (
        "ANALYZER_TYPE_NAMES_V1", *names,
    ):
        raise ContractBoundaryAnalyzerProjectionError("type_exports")
    return names


def _helper_catalog_v1(tree: ast.Module) -> tuple[tuple[str, str], ...]:
    value = _literal_assignment_v1(tree, "ANALYZER_AST_HELPER_CATALOG_V1")
    if type(value) is not tuple or any(
        type(row) is not tuple or len(row) != 2
        or any(type(name) is not str or not name for name in row)
        for row in value
    ):
        raise ContractBoundaryAnalyzerProjectionError("helper_catalog")
    catalog = tuple(value)
    _closed_names_v1(tuple(row[0] for row in catalog), label="helper_catalog")
    _closed_names_v1(tuple(row[1] for row in catalog), label="helper_catalog")
    exports = _literal_assignment_v1(tree, "__all__")
    if type(exports) is not list or tuple(exports) != (
        "ANALYZER_AST_HELPER_CATALOG_V1", *(row[0] for row in catalog),
    ):
        raise ContractBoundaryAnalyzerProjectionError("helper_exports")
    return catalog


def _top_level_signature_v1(node: ast.AST) -> tuple[object, ...]:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        if type(node.value.value) is str:
            return ("docstring",)
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        if isinstance(call.func, ast.Name) and not call.args and not call.keywords:
            return ("call", call.func.id)
    if isinstance(node, ast.Import):
        return ("import", tuple((item.name, item.asname) for item in node.names))
    if isinstance(node, ast.ImportFrom):
        names = tuple((item.name, item.asname) for item in node.names)
        return ("from", node.module, node.level, names)
    if isinstance(node, ast.ClassDef):
        return ("class", node.name)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ("function", node.name)
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        if isinstance(node.targets[0], ast.Name):
            return ("assign", node.targets[0].id)
    raise ContractBoundaryAnalyzerProjectionError("owner_top_level")


def _expected_types_profile_v1(names: tuple[str, ...]) -> tuple[object, ...]:
    return (
        ("docstring",),
        ("from", "__future__", 0, (("annotations", None),)),
        ("from", "dataclasses", 0, (("dataclass", None),)),
        *(("class", name) for name in names),
        ("assign", "ANALYZER_TYPE_NAMES_V1"),
        ("assign", "__all__"),
        ("function", "_validate_analyzer_type_catalog_v1"),
        ("call", "_validate_analyzer_type_catalog_v1"),
    )


def _expected_ast_profile_v1(
    catalog: tuple[tuple[str, str], ...],
) -> tuple[object, ...]:
    return (
        ("docstring",),
        ("from", "__future__", 0, (("annotations", None),)),
        ("import", (("ast", None),)),
        ("from", "typing", 0, (("Iterable", None), ("Mapping", None))),
        *(("function", public) for public, _legacy in catalog),
        ("assign", "ANALYZER_AST_HELPER_CATALOG_V1"),
        ("assign", "__all__"),
        ("function", "_validate_analyzer_ast_catalog_v1"),
        ("call", "_validate_analyzer_ast_catalog_v1"),
    )


def _require_profile_v1(
    tree: ast.Module, expected: tuple[object, ...],
) -> None:
    observed = tuple(_top_level_signature_v1(node) for node in tree.body)
    if observed != expected:
        raise ContractBoundaryAnalyzerProjectionError("owner_profile")


def _definition_source_v1(
    text: str, tree: ast.Module, name: str,
) -> str:
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name
    ]
    if len(matches) != 1:
        raise ContractBoundaryAnalyzerProjectionError("definition_source")
    node = matches[0]
    starts = [node.lineno, *(item.lineno for item in node.decorator_list)]
    lines = text.splitlines()
    return "\n".join(lines[min(starts) - 1:node.end_lineno])


def _global_references_v1(table: symtable.SymbolTable) -> frozenset[str]:
    references = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_global() and symbol.is_referenced()
    }
    for child in table.get_children():
        references.update(_global_references_v1(child))
    return frozenset(references)


def _require_definition_closure_v1(
    source: str, allowed: frozenset[str],
) -> None:
    try:
        table = symtable.symtable(source, "<projected-definition>", "exec")
    except (SyntaxError, ValueError) as exc:
        raise ContractBoundaryAnalyzerProjectionError("definition_syntax") from exc
    if not _global_references_v1(table).issubset(allowed):
        raise ContractBoundaryAnalyzerProjectionError("definition_free_names")


def _owner_material_v1():
    sources = _read_owner_sources_v1()
    parsed = tuple(
        (relative, source, *_parse_owner_v1(relative, source))
        for relative, source in sources
    )
    type_names = _type_names_v1(parsed[0][3])
    helper_catalog = _helper_catalog_v1(parsed[1][3])
    _require_profile_v1(parsed[0][3], _expected_types_profile_v1(type_names))
    _require_profile_v1(parsed[1][3], _expected_ast_profile_v1(helper_catalog))
    return parsed, type_names, helper_catalog


def _canonical_source_from_material_v1(material) -> bytes:
    parsed, type_names, helper_catalog = material
    type_text, type_tree = parsed[0][2], parsed[0][3]
    ast_text, ast_tree = parsed[1][2], parsed[1][3]
    type_definitions = [
        _definition_source_v1(type_text, type_tree, name) for name in type_names
    ]
    helper_definitions = [
        _definition_source_v1(ast_text, ast_tree, public)
        for public, _legacy in helper_catalog
    ]
    type_allowed = _PROJECTED_BUILTIN_NAMES_V1 | {"dataclass", *type_names}
    helper_allowed = _PROJECTED_BUILTIN_NAMES_V1 | {
        "ast", "Iterable", "Mapping",
        *(public for public, _legacy in helper_catalog),
    }
    for definition in type_definitions:
        _require_definition_closure_v1(definition, frozenset(type_allowed))
    for definition in helper_definitions:
        _require_definition_closure_v1(definition, frozenset(helper_allowed))
    definitions = [*type_definitions, *helper_definitions]
    aliases = "\n".join(
        f"{legacy} = {public}" for public, legacy in helper_catalog
    )
    return ("\n\n\n".join(definitions) + f"\n\n{aliases}\n").encode("ascii")


def _binding_payload_v1(material, body: bytes) -> bytes:
    parsed = material[0]
    payload = bytearray()
    for relative, source, _text, _tree in parsed:
        encoded = relative.encode("ascii")
        payload.extend(len(encoded).to_bytes(8, "big"))
        payload.extend(encoded)
        payload.extend(len(source).to_bytes(8, "big"))
        payload.extend(source)
    payload.extend(len(body).to_bytes(8, "big"))
    payload.extend(body)
    return bytes(payload)


def canonical_source_v1() -> bytes:
    """Return projected source from validated, path-bound owner bytes."""
    material = _owner_material_v1()
    return _canonical_source_from_material_v1(material)


def projection_digest_v1() -> str:
    material = _owner_material_v1()
    body = _canonical_source_from_material_v1(material)
    payload = _binding_payload_v1(material, body)
    return framed_sha256_v1(PROJECTION_DIGEST_DOMAIN_V1, payload)


def _render_material_v1(material) -> bytes:
    body = _canonical_source_from_material_v1(material)
    payload = _binding_payload_v1(material, body)
    digest = framed_sha256_v1(PROJECTION_DIGEST_DOMAIN_V1, payload)
    prefix = BEGIN_MARKER_V1 + b"\n" + (
        f'_BOUNDARY_ANALYZER_PROJECTION_SHA256_V1 = "{digest}"\n'.encode("ascii")
    )
    return prefix + body + END_MARKER_V1 + b"\n"


def render_generated_region_v1() -> bytes:
    return _render_material_v1(_owner_material_v1())


def _source_bytes_v1(source: bytes | str) -> bytes:
    if type(source) is bytes:
        return source
    if type(source) is str:
        return source.encode("utf-8")
    raise ContractBoundaryAnalyzerProjectionError("source_type")


def _marker_bounds_v1(source: bytes) -> tuple[int, int]:
    begin = BEGIN_MARKER_V1 + b"\n"
    end = END_MARKER_V1 + b"\n"
    if source.count(begin) != 1 or source.count(end) != 1:
        raise ContractBoundaryAnalyzerProjectionError("markers")
    start = source.index(begin)
    end_start = source.index(end)
    if end_start < start:
        raise ContractBoundaryAnalyzerProjectionError("marker_order")
    stop = end_start + len(end)
    if b"\r" in source[start:stop]:
        raise ContractBoundaryAnalyzerProjectionError("line_endings")
    return start, stop


def _require_no_external_collisions_v1(
    source: bytes, start: int, stop: int, material,
) -> None:
    blank = b"\n" * source[start:stop].count(b"\n")
    try:
        text = (source[:start] + blank + source[stop:]).decode("utf-8")
        table = symtable.symtable(text, "<standalone-preflight>", "exec")
    except (SyntaxError, UnicodeError, ValueError) as exc:
        raise ContractBoundaryAnalyzerProjectionError("preflight_syntax") from exc
    _parsed, type_names, helper_catalog = material
    reserved = {*type_names}
    reserved.update(public for public, _legacy in helper_catalog)
    reserved.update(legacy for _public, legacy in helper_catalog)
    rebound = {
        symbol.get_name() for symbol in table.get_symbols()
        if symbol.get_name() in reserved and (
            symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
        )
    }
    if rebound:
        raise ContractBoundaryAnalyzerProjectionError("external_name_collision")


def replace_generated_region_v1(source: bytes | str) -> bytes:
    raw = _source_bytes_v1(source)
    start, stop = _marker_bounds_v1(raw)
    material = _owner_material_v1()
    _require_no_external_collisions_v1(raw, start, stop, material)
    return raw[:start] + _render_material_v1(material) + raw[stop:]


def check_generated_region_v1(source: bytes | str) -> bool:
    raw = _source_bytes_v1(source)
    start, stop = _marker_bounds_v1(raw)
    material = _owner_material_v1()
    _require_no_external_collisions_v1(raw, start, stop, material)
    return raw[start:stop] == _render_material_v1(material)


__all__ = [
    "BEGIN_MARKER_V1", "END_MARKER_V1", "OWNER_PATHS_V1",
    "ContractBoundaryAnalyzerProjectionError", "canonical_source_v1",
    "check_generated_region_v1", "projection_digest_v1",
    "render_generated_region_v1", "replace_generated_region_v1",
]
