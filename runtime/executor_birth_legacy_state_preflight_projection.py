"""Mechanical projection of the canonical legacy-state wire decoder."""
from __future__ import annotations

import ast
from pathlib import Path
import symtable

from executor_birth_canonical import encode_canonical_ascii_v1
from executor_birth_crypto_framing import framed_sha256_v1
import executor_birth_legacy_state_journal as journal
import executor_birth_legacy_state_wire as wire


PROJECTION_DIGEST_DOMAIN_V1 = (
    b"metnos.executor-birth.legacy-state-preflight-projection/v1\0"
)
BEGIN_MARKER_V1 = b"# BEGIN GENERATED LEGACY STATE PREFLIGHT V1"
END_MARKER_V1 = b"# END GENERATED LEGACY STATE PREFLIGHT V1"
OWNER_PATH_V1 = "runtime/executor_birth_legacy_state_wire.py"
_PROJECTED_BUILTINS_V1 = frozenset({
    "any", "bytes", "dict", "int", "len", "set", "str", "tuple", "type",
    "zip",
})


class LegacyStatePreflightProjectionError(ValueError):
    """Owner source or projected standalone source is outside its profile."""


def _read_owner_v1() -> bytes:
    root = Path(__file__).resolve().parent.parent
    try:
        source = root.joinpath(*OWNER_PATH_V1.split("/")).read_bytes()
    except OSError as exc:
        raise LegacyStatePreflightProjectionError("owner_source") from exc
    if not source.endswith(b"\n") or b"\r" in source:
        raise LegacyStatePreflightProjectionError("owner_format")
    return source


def _parse_owner_v1(source: bytes) -> tuple[str, ast.Module]:
    try:
        text = source.decode("ascii")
        return text, ast.parse(text, filename=OWNER_PATH_V1)
    except (SyntaxError, UnicodeError, ValueError) as exc:
        raise LegacyStatePreflightProjectionError("owner_syntax") from exc


def _literal_assignment_v1(tree: ast.Module, name: str) -> object:
    matches = [
        node for node in tree.body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    if len(matches) != 1:
        raise LegacyStatePreflightProjectionError("owner_catalog")
    try:
        return ast.literal_eval(matches[0].value)
    except (TypeError, ValueError) as exc:
        raise LegacyStatePreflightProjectionError("owner_catalog") from exc


def _top_level_signature_v1(node: ast.AST) -> tuple[object, ...]:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        if type(node.value.value) is str:
            return ("docstring",)
    if isinstance(node, ast.ImportFrom):
        names = tuple((item.name, item.asname) for item in node.names)
        return ("from", node.module, node.level, names)
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return ("assign", target.id)
    if isinstance(node, ast.FunctionDef):
        return ("function", node.name)
    raise LegacyStatePreflightProjectionError("owner_top_level")


def _owner_catalog_v1(tree: ast.Module) -> tuple[tuple[str, str], tuple[str, ...]]:
    fields = _literal_assignment_v1(tree, "LEGACY_STATE_WIRE_OUTPUT_FIELDS_V1")
    helpers = _literal_assignment_v1(tree, "LEGACY_STATE_WIRE_HELPER_CATALOG_V1")
    exports = _literal_assignment_v1(tree, "__all__")
    valid_fields = (
        type(fields) is tuple and fields
        and all(type(row) is tuple and len(row) == 2 for row in fields)
        and all(type(item) is str and item for row in fields for item in row)
        and len({row[0] for row in fields}) == len(fields)
    )
    valid_helpers = (
        type(helpers) is tuple and helpers
        and all(type(name) is str and name for name in helpers)
        and len(set(helpers)) == len(helpers)
    )
    if (
        not valid_fields or not valid_helpers or type(exports) is not list
        or set(exports) != {
            "LEGACY_STATE_WIRE_HELPER_CATALOG_V1",
            "LEGACY_STATE_WIRE_OUTPUT_FIELDS_V1", *helpers,
        }
        or tuple(fields) != wire.LEGACY_STATE_WIRE_OUTPUT_FIELDS_V1
        or tuple(helpers) != wire.LEGACY_STATE_WIRE_HELPER_CATALOG_V1
    ):
        raise LegacyStatePreflightProjectionError("owner_catalog")
    return tuple(fields), tuple(helpers)


def _require_owner_profile_v1(
    tree: ast.Module, helpers: tuple[str, ...],
) -> None:
    expected = (
        ("docstring",),
        ("from", "__future__", 0, (("annotations", None),)),
        ("assign", "LEGACY_STATE_WIRE_OUTPUT_FIELDS_V1"),
        ("assign", "LEGACY_STATE_WIRE_HELPER_CATALOG_V1"),
        *(("function", name) for name in helpers),
        ("assign", "__all__"),
    )
    if tuple(_top_level_signature_v1(node) for node in tree.body) != expected:
        raise LegacyStatePreflightProjectionError("owner_profile")


def _definition_source_v1(text: str, tree: ast.Module, name: str) -> str:
    matches = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if len(matches) != 1:
        raise LegacyStatePreflightProjectionError("definition_source")
    node = matches[0]
    return "\n".join(text.splitlines()[node.lineno - 1:node.end_lineno])


def _global_references_v1(table: symtable.SymbolTable) -> frozenset[str]:
    names = {
        symbol.get_name() for symbol in table.get_symbols()
        if symbol.is_global() and symbol.is_referenced()
    }
    for child in table.get_children():
        names.update(_global_references_v1(child))
    return frozenset(names)


def _require_definition_closure_v1(
    source: str, helpers: tuple[str, ...],
) -> None:
    try:
        table = symtable.symtable(source, "<legacy-wire>", "exec")
    except (SyntaxError, ValueError) as exc:
        raise LegacyStatePreflightProjectionError("definition_syntax") from exc
    allowed = _PROJECTED_BUILTINS_V1 | frozenset(helpers)
    if not _global_references_v1(table).issubset(allowed):
        raise LegacyStatePreflightProjectionError("definition_free_names")


def projection_payload_v1() -> dict[str, object]:
    profile = journal.legacy_state_wire_profile_v1()
    return {
        "dispositions": sorted(profile["dispositions"]),
        "fsm": [list(item) for item in profile["fsm"]],
        "maximum_record_bytes": profile["maximum_record_bytes"],
        "output_fields": [list(item) for item in profile["output_fields"]],
        "policy_sha256": profile["policy_sha256"],
        "protocol": profile["protocol"],
        "record_domain": profile["record_domain"].decode("ascii"),
        "record_keys": sorted(profile["record_keys"]),
    }


def canonical_payload_v1() -> bytes:
    return encode_canonical_ascii_v1(projection_payload_v1())


def _owner_material_v1():
    source = _read_owner_v1()
    text, tree = _parse_owner_v1(source)
    fields, helpers = _owner_catalog_v1(tree)
    _require_owner_profile_v1(tree, helpers)
    definitions = tuple(
        _definition_source_v1(text, tree, name) for name in helpers
    )
    for definition in definitions:
        _require_definition_closure_v1(definition, helpers)
    body = ("\n\n\n".join(definitions) + "\n").encode("ascii")
    return source, fields, helpers, body


def _binding_payload_v1(owner: bytes, payload: bytes, body: bytes) -> bytes:
    path = OWNER_PATH_V1.encode("ascii")
    framed = bytearray()
    for item in (path, owner, payload, body):
        framed.extend(len(item).to_bytes(8, "big"))
        framed.extend(item)
    return bytes(framed)


def _payload_digest_v1(owner: bytes, payload: bytes, body: bytes) -> str:
    return framed_sha256_v1(
        PROJECTION_DIGEST_DOMAIN_V1,
        _binding_payload_v1(owner, payload, body),
    )


def projection_digest_v1() -> str:
    owner, _fields, _helpers, body = _owner_material_v1()
    payload = canonical_payload_v1()
    return _payload_digest_v1(owner, payload, body)


def _profile_lines_v1(payload: bytes) -> tuple[str, ...]:
    return (
        f"_LEGACY_STATE_CANONICAL_ASCII_V1 = {payload!r}",
        "_LEGACY_STATE_PROFILE_DATA_V1 = json.loads(",
        "    _LEGACY_STATE_CANONICAL_ASCII_V1.decode('ascii'))",
        "MAX_LEGACY_STATE_RECORD_BYTES_V1 = _LEGACY_STATE_PROFILE_DATA_V1['maximum_record_bytes']",
        "LEGACY_STATE_RECORD_DOMAIN_V1 = _LEGACY_STATE_PROFILE_DATA_V1['record_domain'].encode('ascii')",
        "_LEGACY_STATE_PROTOCOL_V1 = _LEGACY_STATE_PROFILE_DATA_V1['protocol']",
        "_LEGACY_STATE_POLICY_SHA256_V1 = _LEGACY_STATE_PROFILE_DATA_V1['policy_sha256']",
        "_LEGACY_STATE_FSM_V1 = tuple(tuple(item) for item in _LEGACY_STATE_PROFILE_DATA_V1['fsm'])",
        "_LEGACY_STATE_DISPOSITIONS_V1 = frozenset(_LEGACY_STATE_PROFILE_DATA_V1['dispositions'])",
        "_LEGACY_STATE_RECORD_KEYS_V1 = frozenset(_LEGACY_STATE_PROFILE_DATA_V1['record_keys'])",
        "_LEGACY_STATE_RECORD_OUTPUT_FIELDS_V1 = tuple(item[0] for item in _LEGACY_STATE_PROFILE_DATA_V1['output_fields'])",
        "_LEGACY_STATE_RECORD_NAMES_V1 = tuple(f'record-{index:03d}.json' for index in range(len(_LEGACY_STATE_FSM_V1)))",
        "_LEGACY_STATE_JOURNAL_NAMES_V1 = ('journal.lock', *_LEGACY_STATE_RECORD_NAMES_V1)",
        "_LEGACY_STATE_WIRE_PROFILE_V1 = {'dispositions': _LEGACY_STATE_DISPOSITIONS_V1, 'fsm': _LEGACY_STATE_FSM_V1, 'maximum_record_bytes': MAX_LEGACY_STATE_RECORD_BYTES_V1, 'policy_sha256': _LEGACY_STATE_POLICY_SHA256_V1, 'protocol': _LEGACY_STATE_PROTOCOL_V1, 'record_domain': LEGACY_STATE_RECORD_DOMAIN_V1, 'record_keys': _LEGACY_STATE_RECORD_KEYS_V1}",
        "del _LEGACY_STATE_PROFILE_DATA_V1",
    )


def _adapter_lines_v1(fields: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    lines = ["class _DecodedLegacyStateRecordV1(NamedTuple):"]
    lines.extend(f"    {name}: {annotation}" for name, annotation in fields)
    lines.extend((
        "", "", "def _legacy_state_decoded_record_v1(value):",
        "    return _DecodedLegacyStateRecordV1(*(",
        "        value[name] for name in _LEGACY_STATE_RECORD_OUTPUT_FIELDS_V1))",
        "", "", "def _decode_legacy_state_record_v1(encoded):",
        "    value = legacy_state_wire_record_v1(",
        "        encoded, _LEGACY_STATE_WIRE_PROFILE_V1, _DIGEST_RE,",
        "        decode_canonical_json_v1, _canonical_json,",
        "        _framed_sha256_v1, _invalid)",
        "    return _legacy_state_decoded_record_v1(value)",
        "", "", "def _decode_legacy_state_chain_v1(encoded_records):",
        "    values = decode_legacy_state_wire_chain_v1(",
        "        encoded_records, _LEGACY_STATE_WIRE_PROFILE_V1, _DIGEST_RE,",
        "        decode_canonical_json_v1, _canonical_json,",
        "        _framed_sha256_v1, _invalid)",
        "    return tuple(_legacy_state_decoded_record_v1(value) for value in values)",
    ))
    return tuple(lines)


def render_generated_region_v1() -> bytes:
    owner, fields, _helpers, body = _owner_material_v1()
    payload = canonical_payload_v1()
    prefix = (
        BEGIN_MARKER_V1.decode("ascii"),
        f'_LEGACY_STATE_PROJECTION_SHA256_V1 = "{_payload_digest_v1(owner, payload, body)}"',
        *_profile_lines_v1(payload),
    )
    lines = (*prefix, "", body.decode("ascii").rstrip("\n"), *_adapter_lines_v1(fields))
    return ("\n".join(lines) + "\n" + END_MARKER_V1.decode("ascii") + "\n").encode("ascii")


def _source_bytes_v1(source: bytes | str) -> bytes:
    if type(source) is bytes:
        return source
    if type(source) is str:
        return source.encode("utf-8")
    raise LegacyStatePreflightProjectionError("source_type")


def _marker_bounds_v1(source: bytes) -> tuple[int, int]:
    begin, end = BEGIN_MARKER_V1 + b"\n", END_MARKER_V1 + b"\n"
    if source.count(begin) != 1 or source.count(end) != 1:
        raise LegacyStatePreflightProjectionError("markers")
    start, end_start = source.index(begin), source.index(end)
    if end_start < start:
        raise LegacyStatePreflightProjectionError("marker_order")
    stop = end_start + len(end)
    if b"\r" in source[start:stop]:
        raise LegacyStatePreflightProjectionError("line_endings")
    return start, stop


def replace_generated_region_v1(source: bytes | str) -> bytes:
    raw = _source_bytes_v1(source)
    start, stop = _marker_bounds_v1(raw)
    return raw[:start] + render_generated_region_v1() + raw[stop:]


def check_generated_region_v1(source: bytes | str) -> bool:
    raw = _source_bytes_v1(source)
    start, stop = _marker_bounds_v1(raw)
    return raw[start:stop] == render_generated_region_v1()


def require_generated_region_v1(source: bytes | str) -> None:
    if not check_generated_region_v1(source):
        raise LegacyStatePreflightProjectionError("stale")


__all__ = [
    "BEGIN_MARKER_V1", "END_MARKER_V1", "OWNER_PATH_V1",
    "PROJECTION_DIGEST_DOMAIN_V1", "LegacyStatePreflightProjectionError",
    "canonical_payload_v1", "check_generated_region_v1",
    "projection_digest_v1", "projection_payload_v1",
    "render_generated_region_v1", "replace_generated_region_v1",
    "require_generated_region_v1",
]
