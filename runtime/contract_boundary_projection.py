"""Pure standalone-source projection of the canonical boundary policy."""
from __future__ import annotations

import contract_boundary_api_policy as api_policy
import contract_boundary_syntax_policy as syntax_policy
from executor_birth_canonical import encode_canonical_ascii_v1
from executor_birth_crypto_framing import framed_sha256_v1


PROJECTION_DIGEST_DOMAIN_V1 = b"metnos.contract-boundary-policy-projection/v1\0"
BEGIN_MARKER_V1 = b"# BEGIN GENERATED CONTRACT BOUNDARY POLICY V1"
END_MARKER_V1 = b"# END GENERATED CONTRACT BOUNDARY POLICY V1"


class ContractBoundaryProjectionError(ValueError):
    """Source or policy cannot be represented by the closed projection."""


def _ordered_apis_v1() -> list[object]:
    return [
        [row.owner, [[name, list(caps)] for name, caps in row.apis]]
        for row in api_policy.BOUNDARY_API_OWNERS_V1
    ]


def _ordered_modules_v1() -> list[object]:
    return [
        [row.owner, list(row.module_names)]
        for row in api_policy.BOUNDARY_MODULE_OWNERS_V1
    ]


def _ordered_source_owners_v1() -> list[object]:
    return [
        [row.path, row.owner]
        for row in api_policy.BOUNDARY_SOURCE_OWNERS_V1
    ]


def _limits_v1() -> dict[str, int]:
    value = syntax_policy.BOUNDARY_LIMITS_V1
    return {
        "ast_depth": value.ast_depth,
        "ast_nodes": value.ast_nodes,
        "calls": value.calls,
        "scopes": value.scopes,
        "source_bytes": value.source_bytes,
        "source_files": value.source_files,
        "total_ast_nodes": value.total_ast_nodes,
        "total_source_bytes": value.total_source_bytes,
    }


def _sorted_sets_v1() -> dict[str, list[str]]:
    return {
        "dynamic_code_loader_apis": sorted(syntax_policy.DYNAMIC_CODE_LOADER_APIS),
        "dynamic_code_loader_canonicals": sorted(
            syntax_policy.DYNAMIC_CODE_LOADER_CANONICALS
        ),
        "flow_capabilities": sorted(syntax_policy.FLOW_CAPABILITIES),
        "live_reader_forbidden": sorted(syntax_policy.LIVE_READER_FORBIDDEN),
        "process_calls": sorted(syntax_policy.PROCESS_CALLS),
        "publish_capabilities": sorted(syntax_policy.PUBLISH_CAPABILITIES),
        "read_operations": sorted(syntax_policy.READ_OPERATIONS),
        "sensitive_first_class_references": sorted(
            syntax_policy.SENSITIVE_FIRST_CLASS_REFERENCES
        ),
        "sensitive_import_namespaces": sorted(
            syntax_policy.SENSITIVE_IMPORT_NAMESPACES
        ),
        "sys_modules_exposing_methods": sorted(
            syntax_policy.SYS_MODULES_EXPOSING_METHODS
        ),
        "sys_modules_mutating_methods": sorted(
            syntax_policy.SYS_MODULES_MUTATING_METHODS
        ),
        "write_operations": sorted(syntax_policy.WRITE_OPERATIONS),
    }


def _regexes_v1() -> list[object]:
    rows = (
        ("authoring_name", syntax_policy.AUTHORING_NAME_REGEX_V1),
        ("ambiguous_authoring_argument", syntax_policy.AMBIGUOUS_AUTHORING_ARGUMENT_REGEX_V1),
        ("store_name", syntax_policy.STORE_NAME_REGEX_V1),
        ("contract_scope", syntax_policy.CONTRACT_SCOPE_REGEX_V1),
        ("generic_path_name", syntax_policy.GENERIC_PATH_NAME_REGEX_V1),
    )
    return [[name, value.pattern, value.flags] for name, value in rows]


def policy_payload_v1() -> dict[str, object]:
    """Return the JSON-profile policy value with explicit ordering facts."""
    return {
        "apis": _ordered_apis_v1(),
        "authenticated_execution_scope": list(syntax_policy.AUTHENTICATED_EXECUTION_SCOPE),
        "authenticated_preflight_execution_scope": list(
            syntax_policy.AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE
        ),
        "authoring_files": sorted(syntax_policy.AUTHORING_FILES),
        "birth_closed_guard_version": syntax_policy.BIRTH_CLOSED_GUARD_VERSION,
        "birth_closed_schema": syntax_policy.BIRTH_CLOSED_SCHEMA,
        "limits": _limits_v1(),
        "modules": _ordered_modules_v1(),
        "regexes": _regexes_v1(),
        "scan_roots": list(syntax_policy.SCAN_ROOTS),
        "schema": syntax_policy.SCHEMA,
        "sets": _sorted_sets_v1(),
        "source_owners": _ordered_source_owners_v1(),
    }


def canonical_payload_v1() -> bytes:
    return encode_canonical_ascii_v1(policy_payload_v1())


def _payload_digest_v1(payload: bytes) -> str:
    return framed_sha256_v1(PROJECTION_DIGEST_DOMAIN_V1, payload)


def projection_digest_v1() -> str:
    return _payload_digest_v1(canonical_payload_v1())


def _assignment_lines_v1() -> tuple[str, ...]:
    return (
        'SCHEMA = _BOUNDARY_POLICY_DATA_V1["schema"]',
        'BIRTH_CLOSED_SCHEMA = _BOUNDARY_POLICY_DATA_V1["birth_closed_schema"]',
        'BIRTH_CLOSED_GUARD_VERSION = _BOUNDARY_POLICY_DATA_V1["birth_closed_guard_version"]',
        'SCAN_ROOTS = tuple(_BOUNDARY_POLICY_DATA_V1["scan_roots"])',
        'AUTHORING_FILES = frozenset(_BOUNDARY_POLICY_DATA_V1["authoring_files"])',
        'BOUNDARY_APIS = {owner: {name: tuple(caps) for name, caps in apis} for owner, apis in _BOUNDARY_POLICY_DATA_V1["apis"]}',
        'BOUNDARY_MODULES = {owner: frozenset(names) for owner, names in _BOUNDARY_POLICY_DATA_V1["modules"]}',
        'BOUNDARY_SOURCE_OWNERS = dict(_BOUNDARY_POLICY_DATA_V1["source_owners"])',
        '_BOUNDARY_INVENTORY_SCHEMA = SCHEMA',
        '_BIRTH_CLOSED_SCHEMA = BIRTH_CLOSED_SCHEMA',
        '_BIRTH_CLOSED_GUARD_VERSION = BIRTH_CLOSED_GUARD_VERSION',
        '_BOUNDARY_SCAN_ROOTS = SCAN_ROOTS',
    )


def _limit_lines_v1() -> tuple[str, ...]:
    return (
        'MAX_BOUNDARY_SOURCE_FILES_V1 = _BOUNDARY_POLICY_DATA_V1["limits"]["source_files"]',
        'MAX_BOUNDARY_SOURCE_BYTES_V1 = _BOUNDARY_POLICY_DATA_V1["limits"]["source_bytes"]',
        'MAX_BOUNDARY_TOTAL_SOURCE_BYTES_V1 = _BOUNDARY_POLICY_DATA_V1["limits"]["total_source_bytes"]',
        'MAX_BOUNDARY_AST_NODES_V1 = _BOUNDARY_POLICY_DATA_V1["limits"]["ast_nodes"]',
        'MAX_BOUNDARY_TOTAL_AST_NODES_V1 = _BOUNDARY_POLICY_DATA_V1["limits"]["total_ast_nodes"]',
        'MAX_BOUNDARY_AST_DEPTH_V1 = _BOUNDARY_POLICY_DATA_V1["limits"]["ast_depth"]',
        'MAX_BOUNDARY_SCOPES_V1 = _BOUNDARY_POLICY_DATA_V1["limits"]["scopes"]',
        'MAX_BOUNDARY_CALLS_V1 = _BOUNDARY_POLICY_DATA_V1["limits"]["calls"]',
    )


def _set_lines_v1() -> tuple[str, ...]:
    names = (
        ("READ_OPERATIONS", "read_operations"),
        ("WRITE_OPERATIONS", "write_operations"),
        ("PROCESS_CALLS", "process_calls"),
        ("DYNAMIC_CODE_LOADER_APIS", "dynamic_code_loader_apis"),
        ("DYNAMIC_CODE_LOADER_CANONICALS", "dynamic_code_loader_canonicals"),
        ("SENSITIVE_FIRST_CLASS_REFERENCES", "sensitive_first_class_references"),
        ("SENSITIVE_IMPORT_NAMESPACES", "sensitive_import_namespaces"),
        ("SYS_MODULES_EXPOSING_METHODS", "sys_modules_exposing_methods"),
        ("SYS_MODULES_MUTATING_METHODS", "sys_modules_mutating_methods"),
        ("LIVE_READER_FORBIDDEN", "live_reader_forbidden"),
        ("PUBLISH_CAPABILITIES", "publish_capabilities"),
        ("FLOW_CAPABILITIES", "flow_capabilities"),
    )
    return tuple(
        f'{target} = frozenset(_BOUNDARY_POLICY_DATA_V1["sets"]["{source}"])'
        for target, source in names
    )


def _scope_regex_lines_v1() -> tuple[str, ...]:
    return (
        'AUTHENTICATED_EXECUTION_SCOPE = tuple(_BOUNDARY_POLICY_DATA_V1["authenticated_execution_scope"])',
        'AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE = tuple(_BOUNDARY_POLICY_DATA_V1["authenticated_preflight_execution_scope"])',
        '_BOUNDARY_REGEX_DATA_V1 = {name: (pattern, flags) for name, pattern, flags in _BOUNDARY_POLICY_DATA_V1["regexes"]}',
        '_AUTHORING_NAME_RE = re.compile(*_BOUNDARY_REGEX_DATA_V1["authoring_name"])',
        '_AMBIGUOUS_AUTHORING_ARGUMENT_RE = re.compile(*_BOUNDARY_REGEX_DATA_V1["ambiguous_authoring_argument"])',
        '_STORE_NAME_RE = re.compile(*_BOUNDARY_REGEX_DATA_V1["store_name"])',
        '_CONTRACT_SCOPE_RE = re.compile(*_BOUNDARY_REGEX_DATA_V1["contract_scope"])',
        '_GENERIC_PATH_NAME_RE = re.compile(*_BOUNDARY_REGEX_DATA_V1["generic_path_name"])',
    )


def render_generated_region_v1() -> bytes:
    """Render one ASCII/LF generated region from canonical policy facts."""
    payload = canonical_payload_v1()
    lines = [
        BEGIN_MARKER_V1.decode("ascii"),
        f'_BOUNDARY_POLICY_PROJECTION_SHA256_V1 = "{_payload_digest_v1(payload)}"',
        f"_BOUNDARY_POLICY_CANONICAL_ASCII_V1 = {payload!r}",
        '_BOUNDARY_POLICY_DATA_V1 = json.loads(_BOUNDARY_POLICY_CANONICAL_ASCII_V1.decode("ascii"))',
        *_assignment_lines_v1(),
        *_limit_lines_v1(),
        *_set_lines_v1(),
        *_scope_regex_lines_v1(),
        "del _BOUNDARY_POLICY_DATA_V1, _BOUNDARY_REGEX_DATA_V1",
        END_MARKER_V1.decode("ascii"),
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def _source_bytes_v1(source: bytes | str) -> bytes:
    if type(source) is bytes:
        return source
    if type(source) is str:
        return source.encode("utf-8")
    raise ContractBoundaryProjectionError("source_type")


def _marker_bounds_v1(source: bytes) -> tuple[int, int]:
    begin = BEGIN_MARKER_V1 + b"\n"
    end = END_MARKER_V1 + b"\n"
    if source.count(begin) != 1 or source.count(end) != 1:
        raise ContractBoundaryProjectionError("markers")
    start = source.index(begin)
    end_start = source.index(end)
    if end_start < start:
        raise ContractBoundaryProjectionError("marker_order")
    stop = end_start + len(end)
    if b"\r" in source[start:stop]:
        raise ContractBoundaryProjectionError("line_endings")
    return start, stop


def replace_generated_region_v1(source: bytes | str) -> bytes:
    raw = _source_bytes_v1(source)
    start, stop = _marker_bounds_v1(raw)
    return raw[:start] + render_generated_region_v1() + raw[stop:]


def check_generated_region_v1(source: bytes | str) -> bool:
    raw = _source_bytes_v1(source)
    start, stop = _marker_bounds_v1(raw)
    return raw[start:stop] == render_generated_region_v1()


__all__ = [
    "BEGIN_MARKER_V1", "END_MARKER_V1", "ContractBoundaryProjectionError",
    "canonical_payload_v1", "check_generated_region_v1", "policy_payload_v1",
    "projection_digest_v1", "render_generated_region_v1",
    "replace_generated_region_v1",
]
