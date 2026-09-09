"""Static guard for the RM-0007 contract-authority boundary.

The guard is deliberately small and conservative.  It discovers Python
scopes which touch contract authoring files or the publication API, then
compares them with the reviewed M0 inventory.  Discovery is structural (AST),
so line-number-only edits do not invalidate the inventory.

Run ``python runtime/contract_boundary_guard.py --render`` to regenerate a
candidate inventory on stdout.  Newly discovered scopes are emitted as
``unclassified``; regeneration never silently declares them safe.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence

from contract_boundary_analyzer_ast import (
    leaf_name_v1,
    dotted_name_v1,
    module_leaf_v1,
    target_names_v1,
    string_values_v1,
    static_string_v1,
    static_strings_v1,
    resolved_alias_name_v1,
    has_bound_root_v1,
)
from contract_boundary_analyzer_types import ScopeFacts, Finding
import contract_boundary_policy as _boundary_policy


_leaf_name = leaf_name_v1
_dotted_name = dotted_name_v1
_module_leaf = module_leaf_v1
_target_names = target_names_v1
_string_values = string_values_v1
_static_string = static_string_v1
_static_strings = static_strings_v1
_resolved_alias_name = resolved_alias_name_v1
_has_bound_root = has_bound_root_v1

SCHEMA = _boundary_policy.SCHEMA
BIRTH_CLOSED_SCHEMA = _boundary_policy.BIRTH_CLOSED_SCHEMA
BIRTH_CLOSED_GUARD_VERSION = _boundary_policy.BIRTH_CLOSED_GUARD_VERSION
BIRTH_CLOSED_SOURCE_REVIEW_DOMAIN = (
    b"metnos.executor-birth.closed-python-source-review/v1\0"
)
BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:35d3bc72194b4c8602b5a207651abdebef6b88b1bfec8fcf4a8ec5cc8090ef8d"
RM0008_ACCEPTANCE_EVOLUTION_SHA256 = "sha256:1babce04a78b8345cbacb9bf5677bebade3958e655f0dc45884ad70636322167"
DEFAULT_INVENTORY = Path("internal/reports/rm0007-m4-boundary-inventory.json")
SCAN_ROOTS = _boundary_policy.SCAN_ROOTS
MAX_BOUNDARY_SOURCE_FILES = _boundary_policy.MAX_BOUNDARY_SOURCE_FILES
MAX_BOUNDARY_SOURCE_BYTES = _boundary_policy.MAX_BOUNDARY_SOURCE_BYTES
MAX_BOUNDARY_TOTAL_SOURCE_BYTES = _boundary_policy.MAX_BOUNDARY_TOTAL_SOURCE_BYTES
MAX_BOUNDARY_AST_NODES = _boundary_policy.MAX_BOUNDARY_AST_NODES
MAX_BOUNDARY_TOTAL_AST_NODES = _boundary_policy.MAX_BOUNDARY_TOTAL_AST_NODES
MAX_BOUNDARY_AST_DEPTH = _boundary_policy.MAX_BOUNDARY_AST_DEPTH
MAX_BOUNDARY_SCOPES = _boundary_policy.MAX_BOUNDARY_SCOPES
MAX_BOUNDARY_CALLS = _boundary_policy.MAX_BOUNDARY_CALLS
_SOURCE_REVIEW_PIN_VALUE_V1 = (
    rb'(?:(?:"sha256:" \+ "0" \* 64)|(?:"sha256:[0-9a-f]{64}"))'
)
_SOURCE_REVIEW_PIN_ASSIGNMENT_V1 = re.compile(
    rb'(?m)^_?BIRTH_CLOSED_SOURCE_REVIEW_SHA256[ \t]*=[ \t]*'
    + _SOURCE_REVIEW_PIN_VALUE_V1 + rb'$'
)
_SOURCE_REVIEW_PIN_BINDINGS_V1 = {
    "runtime/contract_boundary_guard.py": re.compile(
        rb'(?m)^BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = '
        + _SOURCE_REVIEW_PIN_VALUE_V1 + rb'$'
    ),
    "runtime/executor_birth_admin_preflight.py": re.compile(
        rb'(?m)^_BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = '
        + _SOURCE_REVIEW_PIN_VALUE_V1 + rb'$'
    ),
}
_SOURCE_REVIEW_PIN_PLACEHOLDER = (
    b'BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:' + b"0" * 64 + b'"'
)
AUTHORING_FILES = _boundary_policy.AUTHORING_FILES
# Public policy names retain their historical concrete runtime types.
BOUNDARY_APIS: Mapping[str, Mapping[str, tuple[str, ...]]] = (
    _boundary_policy.BOUNDARY_APIS
)
BOUNDARY_MODULES: Mapping[str, frozenset[str]] = _boundary_policy.BOUNDARY_MODULES
BOUNDARY_SOURCE_OWNERS: Mapping[str, str] = (
    _boundary_policy.BOUNDARY_SOURCE_OWNERS
)
READ_OPERATIONS = _boundary_policy.READ_OPERATIONS
WRITE_OPERATIONS = _boundary_policy.WRITE_OPERATIONS
PROCESS_CALLS = _boundary_policy.PROCESS_CALLS
DYNAMIC_CODE_LOADER_APIS = _boundary_policy.DYNAMIC_CODE_LOADER_APIS
DYNAMIC_CODE_LOADER_CANONICALS = _boundary_policy.DYNAMIC_CODE_LOADER_CANONICALS
SENSITIVE_FIRST_CLASS_REFERENCES = (
    _boundary_policy.SENSITIVE_FIRST_CLASS_REFERENCES
)
SENSITIVE_IMPORT_NAMESPACES = _boundary_policy.SENSITIVE_IMPORT_NAMESPACES
SYS_MODULES_EXPOSING_METHODS = _boundary_policy.SYS_MODULES_EXPOSING_METHODS
SYS_MODULES_MUTATING_METHODS = _boundary_policy.SYS_MODULES_MUTATING_METHODS
AUTHENTICATED_EXECUTION_SCOPE = _boundary_policy.AUTHENTICATED_EXECUTION_SCOPE
AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE = (
    _boundary_policy.AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE
)
LIVE_READER_FORBIDDEN = _boundary_policy.LIVE_READER_FORBIDDEN
PUBLISH_CAPABILITIES = _boundary_policy.PUBLISH_CAPABILITIES
FLOW_CAPABILITIES = _boundary_policy.FLOW_CAPABILITIES
# Public Birth-closed policy names retain historical concrete runtime types.
BIRTH_CLOSED_SEALED_MODULES = _boundary_policy.BIRTH_CLOSED_SEALED_MODULES
BIRTH_CLOSED_OWNER = _boundary_policy.BIRTH_CLOSED_OWNER
BIRTH_CLOSED_COORDINATOR_STORE_OWNERS = (
    _boundary_policy.BIRTH_CLOSED_COORDINATOR_STORE_OWNERS
)
BIRTH_CLOSED_LEGACY_CAPABILITIES = (
    _boundary_policy.BIRTH_CLOSED_LEGACY_CAPABILITIES
)
BIRTH_CLOSED_EXCEPTIONS = _boundary_policy.BIRTH_CLOSED_EXCEPTIONS
BIRTH_CLOSED_EXCEPTION_SCOPES: Mapping[str, str] = (
    _boundary_policy.BIRTH_CLOSED_EXCEPTION_SCOPES
)
BIRTH_CLOSED_EXCEPTION_CAPABILITIES: Mapping[str, frozenset[str]] = (
    _boundary_policy.BIRTH_CLOSED_EXCEPTION_CAPABILITIES
)
VALID_ROLES = _boundary_policy.VALID_ROLES
LIVE_MUTATIONS = _boundary_policy.LIVE_MUTATIONS
DIRECT_MANIFEST_ALLOWED_ROLES = _boundary_policy.DIRECT_MANIFEST_ALLOWED_ROLES
DIRECT_MANIFEST_ALLOWED_PATHS = _boundary_policy.DIRECT_MANIFEST_ALLOWED_PATHS
BIRTH_OWNER_ALLOWED_PATHS = _boundary_policy.BIRTH_OWNER_ALLOWED_PATHS
BIRTH_OWNER_FORBIDDEN_CAPABILITIES = (
    _boundary_policy.BIRTH_OWNER_FORBIDDEN_CAPABILITIES
)
OPERATIONAL_BIRTH_FORBIDDEN_CAPABILITIES = (
    _boundary_policy.OPERATIONAL_BIRTH_FORBIDDEN_CAPABILITIES
)
BOOTSTRAP_CAPABILITIES = _boundary_policy.BOOTSTRAP_CAPABILITIES
BOOTSTRAP_ALLOWED_ROLES = _boundary_policy.BOOTSTRAP_ALLOWED_ROLES
LIVE_MUTATION_ALLOWED_ROLES = _boundary_policy.LIVE_MUTATION_ALLOWED_ROLES
DOCUMENTATION_CAPABILITY_EXEMPTIONS = (
    _boundary_policy.DOCUMENTATION_CAPABILITY_EXEMPTIONS
)
BIRTH_CLOSED_EXCEPTION_JUSTIFICATIONS = (
    _boundary_policy.BIRTH_CLOSED_EXCEPTION_JUSTIFICATIONS
)
BIRTH_CLOSED_COORDINATOR_REQUIRED_CAPABILITIES = (
    _boundary_policy.BIRTH_CLOSED_COORDINATOR_REQUIRED_CAPABILITIES
)
BOUNDARY_ENTRY_KEYS = _boundary_policy.BOUNDARY_ENTRY_KEYS
birth_closed_inventory_value_v1 = _boundary_policy.birth_closed_inventory_value_v1

_AUTHORING_NAME_RE = _boundary_policy._AUTHORING_NAME_RE
_AMBIGUOUS_AUTHORING_ARGUMENT_RE = (
    _boundary_policy._AMBIGUOUS_AUTHORING_ARGUMENT_RE
)
_STORE_NAME_RE = _boundary_policy._STORE_NAME_RE
_CONTRACT_SCOPE_RE = _boundary_policy._CONTRACT_SCOPE_RE
_GENERIC_PATH_NAME_RE = _boundary_policy._GENERIC_PATH_NAME_RE

def birth_migration_findings(
    facts: Sequence[ScopeFacts],
    inventory: Mapping[str, object],
) -> list[Finding]:
    """Describe the live RM-0008 bypass debt without enforcing cutover.

    F0 is observational: existing producers remain valid until later phases
    migrate them one at a time.  Keeping this report separate from ``check``
    freezes the exact debt while the reviewed RM-0007 inventory stays green.
    Retirement and localization retain dedicated boundaries.  Technical
    rollback is Birth debt: although it points at an existing immutable
    generation, it changes the live technical binding and must not remain an
    independently callable producer bypass.  Migration/bootstrap remains a
    separate, role-constrained boundary.  Reactivation is included because it
    creates a new technical generation.
    """

    raw_entries = inventory.get("entries", [])
    roles = {
        _entry_key(entry): entry.get("role")
        for entry in raw_entries
        if isinstance(entry, dict)
    } if isinstance(raw_entries, list) else {}
    findings: list[Finding] = []
    for fact in facts:
        if roles.get(fact.key) not in {"administrative_tool", "operational_producer"}:
            continue
        bypasses = sorted(
            set(fact.capabilities) & {
                "publish_technical", "reactivate", "rollback", "sign",
            },
        )
        if not bypasses:
            continue
        findings.append(Finding(
            "birth_migration_required",
            fact.key,
            f"path still owns {bypasses!r} instead of birth_executor",
        ))
    return sorted(findings, key=lambda finding: finding.scope)


def _boundary_owner(module: str) -> str | None:
    for owner, accepted in BOUNDARY_MODULES.items():
        if module in accepted:
            return owner
    return None


def _boundary_owner_or_descendant(module: str) -> str | None:
    for owner, accepted in BOUNDARY_MODULES.items():
        if any(module == value or module.startswith(value + ".") for value in accepted):
            return owner
    return None


def _relative_boundary_import(node: ast.ImportFrom) -> bool:
    if node.level <= 0:
        return False
    candidates = [node.module] if node.module else []
    candidates.extend(
        ".".join(filter(None, (node.module or "", alias.name)))
        for alias in node.names
    )
    return any(
        candidate is not None
        and (
            _boundary_owner_or_descendant(candidate) is not None
            or candidate in BOUNDARY_APIS
        )
        for candidate in candidates
    )


def _boundary_api_capabilities(canonical: str) -> tuple[str, ...]:
    """Resolve a reviewed public API by module and leaf name.

    A same-named local helper is deliberately not privileged.  Imported
    aliases retain their canonical module through ``_analyse_scope``.
    """

    module, separator, api = canonical.rpartition(".")
    if not separator:
        return ()
    owner = _boundary_owner(module)
    if owner is None:
        return ()
    # No module may import a private store implementation detail.  Treat every
    # such call as private store authority, rather than maintaining a brittle
    # nominal list of mutator names that a new helper could bypass.
    if owner == "contract_store" and api.startswith("_"):
        return ("store_write",)
    return tuple(BOUNDARY_APIS.get(owner, {}).get(api, ()))


def _normalized_source_review_bytes(relative: str, content: bytes) -> bytes:
    """Normalize exactly one pin in each of the two compiled bindings."""
    pattern = _SOURCE_REVIEW_PIN_BINDINGS_V1.get(relative)
    if pattern is None:
        return content
    expected = tuple(pattern.finditer(content))
    assignments = tuple(_SOURCE_REVIEW_PIN_ASSIGNMENT_V1.finditer(content))
    if (
        len(expected) != 1
        or len(assignments) != 1
        or expected[0].span() != assignments[0].span()
    ):
        raise ValueError(f"invalid source-review pin binding: {relative}")
    start, end = expected[0].span()
    return content[:start] + _SOURCE_REVIEW_PIN_PLACEHOLDER + content[end:]


def closed_python_source_review_sha256(
    sources: Mapping[str, bytes],
) -> str:
    """Bind the exact Python source set approved for one closed build."""
    digest = hashlib.sha256(BIRTH_CLOSED_SOURCE_REVIEW_DOMAIN)
    selected = []
    pin_targets = set()
    for relative, content in sources.items():
        components = relative.split("/")
        if (
            not relative.endswith(".py")
            or not components
            or components[0] not in SCAN_ROOTS
            or "__pycache__" in components
            or type(content) is not bytes
        ):
            continue
        normalized = _normalized_source_review_bytes(relative, content)
        selected.append((relative, normalized))
        if relative in _SOURCE_REVIEW_PIN_BINDINGS_V1:
            pin_targets.add(relative)
    if pin_targets != set(_SOURCE_REVIEW_PIN_BINDINGS_V1):
        raise ValueError("source-review pin binding missing")
    for relative, content in sorted(
        selected, key=lambda item: item[0].encode("utf-8"),
    ):
        encoded_path = relative.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return f"sha256:{digest.hexdigest()}"


def closed_python_sources_from_root(root: Path) -> dict[str, bytes]:
    sources: dict[str, bytes] = {}
    for base in SCAN_ROOTS:
        directory = root / base
        if not directory.exists():
            continue
        for path in directory.rglob("*.py"):
            if path.is_file() and "__pycache__" not in path.parts:
                sources[path.relative_to(root).as_posix()] = path.read_bytes()
    return sources


def closed_python_source_review_finding(root: Path) -> Finding | None:
    try:
        observed = closed_python_source_review_sha256(
            closed_python_sources_from_root(root),
        )
    except (OSError, MemoryError, ValueError) as exc:
        return Finding(
            "birth_closed_source_review_invalid", "<source-review>",
            f"cannot read reviewed Python sources: {type(exc).__name__}",
        )
    if observed != BIRTH_CLOSED_SOURCE_REVIEW_SHA256:
        return Finding(
            "birth_closed_source_review_mismatch", "<source-review>",
            "Python source root is not the compiled reviewed root",
        )
    return None


def _defined_boundary_capabilities(path: str, scope: str) -> tuple[str, ...]:
    """Classify the definitions that implement the public boundary itself."""

    if scope == "<module>":
        return ()
    module = BOUNDARY_SOURCE_OWNERS.get(path)
    if module is None:
        return ()
    api = scope.rsplit(".", 1)[-1]
    capabilities = set(BOUNDARY_APIS.get(module, {}).get(api, ()))
    return tuple(sorted(capabilities))


def _is_dynamic_code_loader_call(func: ast.AST, canonical: str) -> bool:
    """Recognize actual stdlib code-loader doors, not same-named local APIs."""

    if canonical in DYNAMIC_CODE_LOADER_CANONICALS:
        return True
    if canonical.startswith("importlib.") and canonical.rsplit(".", 1)[-1] in (
        DYNAMIC_CODE_LOADER_APIS - {"run_module", "run_path"}
    ):
        return True
    # ``spec`` is a runtime value, so it has no import alias to canonicalize.
    # The loader protocol spelling is nevertheless structural and specific.
    dotted = _dotted_name(func) or ""
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"exec_module", "load_module"}
        and (
            f".loader.{func.attr}" in dotted
            or dotted.startswith("__loader__.")
            or canonical.startswith("importlib.")
        )
    )


def _is_authenticated_preflight_runpy_v1(
    call: ast.Call, path: str, scope: str,
    aliases: Mapping[str, str], nodes: Sequence[ast.AST],
) -> bool:
    """Recognize the sole exact runpy door bound by the signed launch plan."""
    if (
        (path, scope) != AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE
        or not isinstance(call.func, ast.Attribute)
        or not isinstance(call.func.value, ast.Name)
        or call.func.value.id != "runpy" or call.func.attr != "run_module"
        or aliases.get("runpy") != "runpy"
        or len(call.args) != 1
        or not isinstance(call.args[0], ast.Attribute)
        or not isinstance(call.args[0].value, ast.Name)
        or call.args[0].value.id != "plan"
        or call.args[0].attr != "python_module"
        or len(call.keywords) != 2
        or any(item.arg is None for item in call.keywords)
    ):
        return False
    keywords = {item.arg: item.value for item in call.keywords}
    if set(keywords) != {"run_name", "alter_sys"}:
        return False
    run_name = keywords["run_name"]
    alter_sys = keywords["alter_sys"]
    if (
        not isinstance(run_name, ast.Constant) or run_name.value != "__main__"
        or not isinstance(alter_sys, ast.Constant) or alter_sys.value is not False
    ):
        return False
    for node in nodes:
        targets: set[str] = set()
        if isinstance(node, ast.Assign):
            for target in node.targets:
                targets.update(_target_names(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets.update(_target_names(node.target))
        if targets & {"runpy", "plan"}:
            return False
    return True


def _is_sys_modules_registry(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    """Track the module registry through direct and reflective derivations."""

    resolved = _resolved_alias_name(node, aliases)
    if resolved == "sys.modules" and _has_bound_root(node, aliases):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return (
            _is_sys_modules_registry(node.left, aliases)
            or _is_sys_modules_registry(node.right, aliases)
        )
    if isinstance(node, ast.Subscript):
        key = _static_string(node.slice)
        if key != "modules":
            return False
        owner = _resolved_alias_name(node.value, aliases)
        if owner == "sys.__dict__" and _has_bound_root(node.value, aliases):
            return True
        if isinstance(node.value, ast.Call):
            called = _resolved_alias_name(node.value.func, aliases)
            return bool(
                called in {"vars", "builtins.vars"}
                and node.value.args
                and _resolved_alias_name(node.value.args[0], aliases) == "sys"
                and _has_bound_root(node.value.args[0], aliases)
            )
        return False
    if isinstance(node, ast.Call):
        called = _resolved_alias_name(node.func, aliases)
        if called in {"dict", "builtins.dict"} and node.args:
            return _is_sys_modules_registry(node.args[0], aliases)
        if (
            called in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and _resolved_alias_name(node.args[0], aliases) == "sys"
            and _has_bound_root(node.args[0], aliases)
            and _static_string(node.args[1]) == "modules"
        ):
            return True
        if isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in {"copy", "__or__", "__ior__"}
                and _is_sys_modules_registry(node.func.value, aliases)
            ):
                return True
            if (
                called == "object.__getattribute__"
                and len(node.args) >= 2
                and _resolved_alias_name(node.args[0], aliases) == "sys"
                and _has_bound_root(node.args[0], aliases)
                and _static_string(node.args[1]) == "modules"
            ):
                return True
            if (
                node.func.attr == "__getattribute__"
                and _resolved_alias_name(node.func.value, aliases) == "sys"
                and _has_bound_root(node.func.value, aliases)
                and node.args
                and _static_string(node.args[0]) == "modules"
            ):
                return True
    return False


def _propagate_sys_modules_registry_aliases(
    aliases: dict[str, str], nodes: Sequence[ast.AST],
) -> None:
    pairs = _assignment_pairs(nodes)
    changed = True
    while changed:
        changed = False
        for targets, value in pairs:
            if not _is_sys_modules_registry(value, aliases):
                continue
            for target in targets:
                if aliases.get(target) != "sys.modules":
                    aliases[target] = "sys.modules"
                    changed = True


def _is_bounded_boundary_getattr(
    node: ast.AST,
    parent: ast.AST | None,
    aliases: Mapping[str, str],
) -> bool:
    """Allow only literal lookup of an already reviewed boundary API."""

    if (
        not isinstance(parent, ast.Call)
        or len(parent.args) < 2
        or parent.args[0] is not node
        or _resolved_alias_name(parent.func, aliases)
        not in {"getattr", "builtins.getattr"}
    ):
        return False
    module = _resolved_alias_name(node, aliases)
    reflected = _static_string(parent.args[1])
    return bool(
        module is not None
        and reflected is not None
        and _boundary_api_capabilities(f"{module}.{reflected}")
    )


def _contains_builtin_namespace_source(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Name) and item.id == "__builtins__"
        or isinstance(item, ast.Call)
        and _leaf_name(item.func) in {"globals", "locals", "vars"}
        for item in ast.walk(node)
    )


def _static_module_reference_may_reach_boundary(
    node: ast.AST, path: str,
) -> bool:
    value = _static_string(node)
    if value is None and isinstance(node, ast.Name) and node.id == "__name__":
        components = path.removesuffix(".py").split("/")
        if components[-1:] == ["__init__"]:
            components.pop()
        candidates = {".".join(components)}
        if components:
            candidates.add(components[-1])
        return any(
            candidate.startswith(".")
            or _boundary_owner_or_descendant(candidate) is not None
            for candidate in candidates
        )
    return (
        value is None
        or value.startswith(".")
        or _boundary_owner_or_descendant(value) is not None
    )


def _static_reflection_key_may_import(node: ast.AST) -> bool:
    value = _static_string(node)
    return value is None or value in {"__import__", "import_module", "importlib"}


def _static_reflection_key_may_execute(node: ast.AST) -> bool:
    value = _static_string(node)
    return value is None or value in {"compile", "eval", "exec"}


def _may_be_import_namespace(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    resolved = _resolved_alias_name(node, aliases)
    if resolved in {
        "__builtins__", "__loader__", "__spec__", "builtins",
        "builtins.__dict__", "importlib",
        "importlib.__dict__", "importlib.machinery", "importlib.util", "runpy",
        "sys.modules", "types",
    } and (
        resolved in {"__builtins__", "__loader__", "__spec__"}
        or _has_bound_root(node, aliases)
    ):
        return True
    if isinstance(node, ast.Call):
        called = _resolved_alias_name(node.func, aliases)
        if called in {"globals", "locals", "vars"}:
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "copy":
            return _may_be_import_namespace(node.func.value, aliases)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "__getitem__"}
        ):
            owner = _resolved_alias_name(node.func.value, aliases)
            key = _static_string(node.args[0]) if node.args else None
            if owner == "sys.modules":
                return key is None or key in {
                    "builtins", "importlib", "importlib.machinery",
                    "importlib.util", "runpy", "types",
                }
            if _may_be_import_namespace(node.func.value, aliases):
                return key is None or key in {
                    "__builtins__", "__loader__", "__spec__", "builtins", "importlib",
                    "importlib.machinery", "importlib.util", "runpy", "types",
                }
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return _may_be_import_namespace(node.value, aliases)
    if isinstance(node, ast.Subscript):
        key = _static_string(node.slice)
        return _may_be_import_namespace(node.value, aliases) and (
            key is None or key in {
                "__builtins__", "__loader__", "__spec__", "builtins", "importlib",
                "importlib.machinery", "importlib.util", "runpy", "types",
            }
        )
    return False


def _may_resolve_import_callable(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    resolved = _resolved_alias_name(node, aliases)
    if resolved in {
        "__import__", "builtins.__import__", "importlib.import_module",
    }:
        return True
    if isinstance(node, ast.Subscript):
        if not isinstance(node.ctx, ast.Load):
            return False
        if isinstance(node.slice, ast.Name) and node.slice.id == "__name__":
            return False
        return (
            _may_be_import_namespace(node.value, aliases)
            and _static_reflection_key_may_import(node.slice)
        )
    if isinstance(node, ast.Call):
        called = _resolved_alias_name(node.func, aliases)
        if called in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            return (
                _may_be_import_namespace(node.args[0], aliases)
                and _static_reflection_key_may_import(node.args[1])
            )
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "__getitem__"}
        ):
            return (
                _may_be_import_namespace(node.func.value, aliases)
                and (not node.args or _static_reflection_key_may_import(node.args[0]))
            )
    return False


def _may_resolve_dynamic_loader_callable(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    canonical = _resolved_alias_name(node, aliases) or ""
    if _is_dynamic_code_loader_call(node, canonical):
        return True
    return (
        isinstance(node, ast.Attribute)
        and node.attr in DYNAMIC_CODE_LOADER_APIS
        and _may_be_import_namespace(node.value, aliases)
    )


def _may_resolve_dynamic_eval_callable(
    node: ast.AST, aliases: Mapping[str, str],
) -> bool:
    """Recognize reflected access to eval/exec/compile in builtins."""

    if isinstance(node, ast.Subscript):
        return (
            isinstance(node.ctx, ast.Load)
            and _may_be_import_namespace(node.value, aliases)
            and _static_reflection_key_may_execute(node.slice)
        )
    if isinstance(node, ast.Call):
        called = _resolved_alias_name(node.func, aliases)
        if called in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            return (
                _may_be_import_namespace(node.args[0], aliases)
                and _static_reflection_key_may_execute(node.args[1])
            )
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "__getitem__"}
        ):
            return (
                _may_be_import_namespace(node.func.value, aliases)
                and (
                    not node.args
                    or _static_reflection_key_may_execute(node.args[0])
                )
            )
    return False



def _is_authoring_filename(value: object) -> bool:
    return (
        isinstance(value, str)
        and Path(value.replace("\\", "/")).name in AUTHORING_FILES
    )


def _has_authoring_literal(node: ast.AST) -> bool:
    """Recognize contract filenames only when they form a filesystem path.

    The same strings are also legitimate keys in immutable payload mappings;
    treating those keys as filesystem authority would misclassify verified
    store readers as authoring readers.
    """

    if isinstance(node, ast.Constant):
        return _is_authoring_filename(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and any(
        isinstance(part, ast.Constant) and _is_authoring_filename(part.value)
        for part in ast.walk(node)
    ):
        # Filename filters such as ``{"manifest.toml", ...}`` taint the
        # paths yielded by the associated filesystem traversal.  Mapping keys
        # remain excluded because immutable publication payloads legitimately
        # use the same names as data keys.
        return True
    for item in ast.walk(node):
        if (
            isinstance(item, ast.Call)
            and _leaf_name(item.func) == "Path"
            and any(
                isinstance(part, ast.Subscript)
                and isinstance(part.slice, ast.Constant)
                and part.slice.value in {
                    "authoring_manifest_path", "manifest_path",
                }
                for argument in item.args
                for part in ast.walk(argument)
            )
        ):
            return True
        if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Div):
            if any(
                isinstance(part, ast.Constant)
                and _is_authoring_filename(part.value)
                for part in ast.walk(item)
            ):
                return True
        if not isinstance(item, ast.Call):
            continue
        api = _leaf_name(item.func)
        if api == "with_name":
            base = item.func.value if isinstance(item.func, ast.Attribute) else None
            if (
                base is not None
                and any(
                    isinstance(part, ast.Name) and part.id == "__file__"
                    for part in ast.walk(base)
                )
                and any(
                    isinstance(part, ast.Constant)
                    and _is_authoring_filename(part.value)
                    for argument in item.args
                    for part in ast.walk(argument)
                )
            ):
                return True
            continue
        if api not in {
            "Path", "glob", "joinpath", "open", "rglob",
        }:
            continue
        if any(
            isinstance(part, ast.Constant)
            and _is_authoring_filename(part.value)
            for argument in item.args
            for part in ast.walk(argument)
        ):
            return True
    return False


def _has_store_literal(node: ast.AST) -> bool:
    return any(
        any(
            component in {
                "contract-publications",
                "contract-publications-shadow",
                "contract-publications.ACTIVE",
            }
            for component in value.replace("\\", "/").split("/")
        )
        for value in _string_values(node)
    )


def _name_matches(node: ast.AST, names: set[str], pattern: re.Pattern[str]) -> bool:
    for item in ast.walk(node):
        if isinstance(item, ast.Name):
            name = item.id
        elif isinstance(item, ast.Attribute):
            name = item.attr
        else:
            continue
        if name in names or pattern.search(name.lower()):
            return True
    return False


def _open_writes(call: ast.Call) -> bool:
    """Return true only when an ``open`` call explicitly permits mutation."""

    if (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "os"
    ):
        flags: list[ast.AST] = list(call.args[1:2])
        flags.extend(kw.value for kw in call.keywords if kw.arg == "flags")
        write_flags = {
            "O_APPEND", "O_CREAT", "O_EXCL", "O_RDWR", "O_TRUNC", "O_WRONLY",
        }
        if any(
            (
                isinstance(part, ast.Name) and part.id in write_flags
            ) or (
                isinstance(part, ast.Attribute) and part.attr in write_flags
            )
            for value in flags
            for part in ast.walk(value)
        ):
            return True
    values: list[ast.AST] = list(call.args[1:2])
    values.extend(kw.value for kw in call.keywords if kw.arg == "mode")
    if isinstance(call.func, ast.Attribute):
        values = list(call.args[:1]) + [
            kw.value for kw in call.keywords if kw.arg == "mode"
        ]
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return any(flag in value.value for flag in "wax+")
    return False


def _writes_path(api: str, call: ast.Call) -> bool:
    if api == "open":
        return _open_writes(call)
    if api in WRITE_OPERATIONS:
        return True
    # Native Windows store operations are invoked through ``ctypes`` and do
    # not retain Python's Path method names.  Recognize the operation family,
    # independent of the concrete kernel32 alias used by the caller.
    if re.match(
        r"^(?:create|delete|move|replace|write)file",
        api,
        flags=re.IGNORECASE,
    ):
        return True
    if re.match(r"^_?atomic_(?:bytes|json|save|text)$", api, re.IGNORECASE):
        return True
    return bool(re.search(
        r"(?:^|_)(?:atomic_)?(?:copy|delete|extract|move|remove|rename|"
        r"replace|restore|save|unlink|write)(?:_|$)",
        api.lower(),
    ))


def _pathlike(node: ast.AST) -> bool:
    """Distinguish filesystem rename/replace from ``str.replace``."""

    if _has_authoring_literal(node) or _has_store_literal(node):
        return True
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and re.search(
            r"(?:^|_)(?:path|dir|root|file|target|source|destination)(?:_|$)",
            item.id.lower(),
        ):
            return True
        if isinstance(item, ast.Attribute) and re.search(
            r"(?:^|_)(?:path|dir|root|file|target|source|destination)(?:_|$)",
            item.attr.lower(),
        ):
            return True
    return False


class _LocalVisitor(ast.NodeVisitor):
    """Visit one lexical scope without folding nested function bodies into it."""

    def __init__(self, root: ast.AST) -> None:
        self.root = root
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST) -> None:
        if node is not self.root and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            return
        self.nodes.append(node)
        super().generic_visit(node)


def _scope_nodes(node: ast.AST) -> list[ast.AST]:
    visitor = _LocalVisitor(node)
    visitor.visit(node)
    return visitor.nodes


def _scope_arguments(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return set()
    args = node.args
    result = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg:
        result.add(args.vararg.arg)
    if args.kwarg:
        result.add(args.kwarg.arg)
    return result


def _manifest_ref_arguments(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return set()
    args = node.args
    annotated = (*args.posonlyargs, *args.args, *args.kwonlyargs)
    result: set[str] = set()
    for argument in annotated:
        annotation = _dotted_name(argument.annotation) if argument.annotation else None
        if annotation and annotation.rsplit(".", 1)[-1] == "ManifestRef":
            result.add(argument.arg)
    return result


def _assignment_pairs(nodes: Iterable[ast.AST]) -> list[tuple[set[str], ast.AST]]:
    pairs: list[tuple[set[str], ast.AST]] = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            targets: set[str] = set()
            for target in node.targets:
                targets.update(_target_names(target))
            pairs.append((targets, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs.append((_target_names(node.target), node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            pairs.append((_target_names(node.target), node.iter))
        elif isinstance(node, ast.NamedExpr):
            pairs.append((_target_names(node.target), node.value))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"add", "append", "extend", "update"}
        ):
            # Preserve path authority when it flows through a local collection
            # before a later traversal.  This is common for deterministic
            # inventories assembled from several roots and filename filters.
            targets = _target_names(node.func.value)
            if targets and node.args:
                pairs.append((targets, ast.Tuple(elts=list(node.args), ctx=ast.Load())))
    return pairs


def _drops_contract_file_identity(node: ast.AST) -> bool:
    """A manifest's parent is a code/source directory, not a contract file."""

    return (
        not _has_authoring_literal(node)
        and any(
            isinstance(item, ast.Attribute) and item.attr == "parent"
            for item in ast.walk(node)
        )
    )


def _tainted_names(
    node: ast.AST,
    nodes: Sequence[ast.AST],
    *,
    scope: str,
) -> tuple[set[str], set[str]]:
    arguments = _scope_arguments(node)
    authoring = {
        name
        for name in arguments
        if _AUTHORING_NAME_RE.search(name.lower())
        or _AMBIGUOUS_AUTHORING_ARGUMENT_RE.search(name.lower())
    }
    authoring.update(_manifest_ref_arguments(node))
    # A contract/manifest transformer commonly accepts a deliberately generic
    # ``path`` parameter.  Treat that parameter as authoring authority based on
    # the semantic scope, not on any executor or language name.  This closes a
    # real bypass where ``refactor_manifest(path)`` rewrote a source contract
    # but escaped the census because only its caller named ``manifest_path``.
    scope_leaf = scope.rsplit(".", 1)[-1].lower()
    if _CONTRACT_SCOPE_RE.search(scope_leaf):
        authoring.update(
            name for name in arguments if _GENERIC_PATH_NAME_RE.search(name.lower())
        )
    store = {name for name in arguments if _STORE_NAME_RE.search(name.lower())}
    pairs = _assignment_pairs(nodes)
    changed = True
    while changed:
        changed = False
        for targets, value in pairs:
            if (
                _has_authoring_literal(value)
                or (
                    _name_matches(value, authoring, _AUTHORING_NAME_RE)
                    and not _drops_contract_file_identity(value)
                )
            ):
                before = len(authoring)
                authoring.update(targets)
                changed |= len(authoring) != before
            if _has_store_literal(value) or _name_matches(value, store, _STORE_NAME_RE):
                before = len(store)
                store.update(targets)
                changed |= len(store) != before
    return authoring, store


def _call_target(call: ast.Call) -> ast.AST:
    if isinstance(call.func, ast.Attribute):
        return call.func.value
    if call.args:
        return call.args[0]
    return call


def _touches(
    node: ast.AST,
    *,
    tainted: set[str],
    pattern: re.Pattern[str],
    literal_test,
) -> bool:
    return literal_test(node) or _name_matches(node, tainted, pattern)


def _analyse_scope(
    path: str,
    scope: str,
    node: ast.AST,
    imported_aliases: Mapping[str, str],
    local_callables: frozenset[str],
) -> ScopeFacts:
    nodes = _scope_nodes(node)
    aliases = dict(imported_aliases)
    dynamic_boundary_access = False
    closed_dynamic_boundary = False
    boundary_text = re.compile(
        r"(?:contract_store|runtime\.sign|(?:^|[/\\])sign\.py|"
        r"publish_technical_update|reactivate_technical_update|"
        r"publish_signed_source|rollback_executor_contract)",
    )
    scope_boundary_strings = {
        value for value in _static_strings(node) if boundary_text.search(value)
    }
    for item in nodes:
        if isinstance(item, ast.ImportFrom) and item.module:
            if _relative_boundary_import(item):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
                continue
            if (
                _boundary_owner(item.module) is None
                and _boundary_owner_or_descendant(item.module) is not None
            ):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
            if _boundary_owner(item.module) and any(
                alias.name == "*" for alias in item.names
            ):
                dynamic_boundary_access = True
            for alias in item.names:
                canonical_import = f"{item.module}.{alias.name}"
                _remember_alias(
                    aliases, alias.asname or alias.name, canonical_import,
                    local_callables,
                )
        elif isinstance(item, ast.ImportFrom) and item.module is None:
            if _relative_boundary_import(item):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
        elif isinstance(item, ast.Import):
            for alias in item.names:
                if (
                    _boundary_owner(alias.name) is None
                    and _boundary_owner_or_descendant(alias.name) is not None
                ):
                    dynamic_boundary_access = True
                    closed_dynamic_boundary = True
                bound = alias.asname or alias.name.split(".", 1)[0]
                _remember_alias(
                    aliases, bound, alias.name if alias.asname else bound,
                    local_callables,
                )
    ambiguous_callable_authority = _apply_callable_aliases(
        aliases, nodes, local_callables,
    )
    _propagate_sys_modules_registry_aliases(aliases, nodes)
    parents = {
        id(child): parent
        for parent in nodes
        for child in ast.iter_child_nodes(parent)
    }
    direct_call_targets = {id(item.func) for item in nodes if isinstance(item, ast.Call)}
    for item in nodes:
        if (
            isinstance(item, (ast.Call, ast.BinOp, ast.Subscript))
            and _is_sys_modules_registry(item, aliases)
        ):
            dynamic_boundary_access = True
            closed_dynamic_boundary = True
        if isinstance(item, ast.Subscript):
            resolved = (
                "sys.modules"
                if _is_sys_modules_registry(item.value, aliases)
                else _resolved_alias_name(item.value, aliases)
            )
            parent = parents.get(id(item))
            authenticated_registration = (
                (path, scope) == AUTHENTICATED_EXECUTION_SCOPE
                and isinstance(item.ctx, ast.Store)
                and isinstance(item.slice, ast.Name)
                and item.slice.id == "module_name"
                and isinstance(parent, ast.Assign)
                and isinstance(parent.value, ast.Name)
                and parent.value.id == "module"
            )
            if resolved == "sys.modules" and (
                not isinstance(item.ctx, ast.Load)
                and not authenticated_registration
                or _static_module_reference_may_reach_boundary(item.slice, path)
                and isinstance(item.ctx, ast.Load)
            ):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
            if (
                resolved in {
                    "__builtins__", "builtins.__dict__", "importlib.__dict__",
                }
                or _contains_builtin_namespace_source(item.value)
            ) and _static_reflection_key_may_import(item.slice):
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
            continue
        if (
            isinstance(item, ast.Attribute)
            and isinstance(item.ctx, (ast.Store, ast.Del))
            and _resolved_alias_name(item, aliases) == "sys.modules"
        ):
            dynamic_boundary_access = True
            closed_dynamic_boundary = True
            continue
        if (
            not isinstance(item, (ast.Name, ast.Attribute))
            or not isinstance(getattr(item, "ctx", None), ast.Load)
            or id(item) in direct_call_targets
        ):
            continue
        dotted = _dotted_name(item)
        if dotted is None:
            continue
        first, separator, remainder = dotted.partition(".")
        canonical = aliases.get(first, first) + (
            separator + remainder if separator else ""
        )
        if canonical in {
            "__import__", "builtins.__import__", "importlib.import_module",
        }:
            dynamic_boundary_access = True
            closed_dynamic_boundary = True
    authoring_names, store_names = _tainted_names(
        node,
        nodes,
        scope=scope,
    )
    capabilities: set[str] = set(
        _defined_boundary_capabilities(path, scope)
    )
    if ambiguous_callable_authority:
        capabilities.add("ambiguous_local_authority")
    if any(_may_resolve_import_callable(item, aliases) for item in nodes):
        capabilities.add("dynamic_boundary_access")
        closed_dynamic_boundary = True
    manifest_dir_locator_used = any(
        isinstance(item, ast.Attribute)
        and item.attr == "manifest_dir"
        and isinstance(item.ctx, ast.Load)
        for item in nodes
    )
    if dynamic_boundary_access:
        capabilities.add("dynamic_boundary_access")
    calls: set[str] = set()

    # Boundary functions can be passed as first-class callbacks (``partial``,
    # executor pools, futures) instead of appearing in ``Call.func``.  Any
    # loaded reference still grants the same authority and belongs in the
    # reviewed scope census.
    for item in nodes:
        if not isinstance(item, (ast.Name, ast.Attribute)):
            continue
        if not isinstance(getattr(item, "ctx", None), ast.Load):
            continue
        parent = parents.get(id(item))
        if isinstance(parent, ast.Attribute) and parent.value is item:
            continue
        canonical = _resolved_alias_name(item, aliases)
        if canonical is None:
            continue
        known_capabilities = _boundary_api_capabilities(canonical)
        capabilities.update(known_capabilities)
        direct_call = isinstance(parent, ast.Call) and parent.func is item
        direct_subscript = isinstance(parent, ast.Subscript) and parent.value is item
        if (
            not known_capabilities
            and _boundary_owner(canonical) is not None
            and canonical not in local_callables
            and _has_bound_root(item, aliases)
            and not _is_bounded_boundary_getattr(item, parent, aliases)
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if (
            canonical in SENSITIVE_IMPORT_NAMESPACES
            and not direct_subscript
            and (
                canonical == "__builtins__"
                or _has_bound_root(item, aliases)
            )
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if canonical in SENSITIVE_FIRST_CLASS_REFERENCES and not direct_call:
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True

    for item in nodes:
        if (
            isinstance(item, ast.Attribute)
            and item.attr == "__dict__"
            and (module_name := _dotted_name(item.value)) is not None
        ):
            first_module, separator_module, remainder_module = module_name.partition(".")
            resolved_module = aliases.get(first_module, first_module) + (
                separator_module + remainder_module if separator_module else ""
            )
            if _boundary_owner(resolved_module) is not None:
                closed_dynamic_boundary = True
            if resolved_module in {"builtins", "importlib"}:
                dynamic_boundary_access = True
                closed_dynamic_boundary = True
    for item in nodes:
        if not isinstance(item, ast.Call):
            continue
        reflected_dynamic_eval = _may_resolve_dynamic_eval_callable(
            item.func, aliases,
        )
        if reflected_dynamic_eval:
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        leaf = _leaf_name(item.func)
        if leaf is None:
            continue
        dotted = _dotted_name(item.func) or leaf
        first, separator, remainder = dotted.partition(".")
        canonical = aliases.get(first, first) + (
            separator + remainder if separator else ""
        )
        if not separator:
            canonical = aliases.get(leaf, leaf)
        api = canonical.rsplit(".", 1)[-1]
        if canonical in local_callables and (
            isinstance(item.func, ast.Name)
            or isinstance(item.func, ast.Attribute)
            and isinstance(item.func.value, ast.Name)
        ):
            calls.add(canonical.rsplit(".", 1)[-1])
        elif (
            isinstance(item.func, ast.Name)
            and item.func.id not in aliases
        ) or (
            isinstance(item.func, ast.Attribute)
            and isinstance(item.func.value, ast.Name)
            and item.func.value.id not in aliases
        ):
            calls.add(api)

        capabilities.update(_boundary_api_capabilities(canonical))
        if (
            isinstance(item.func, ast.Attribute)
            and _is_sys_modules_registry(item.func.value, aliases)
            and api in (
                SYS_MODULES_EXPOSING_METHODS | SYS_MODULES_MUTATING_METHODS
            )
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if (
            (
                _is_dynamic_code_loader_call(item.func, canonical)
                or _may_resolve_dynamic_loader_callable(item.func, aliases)
            )
            and not _is_authenticated_preflight_runpy_v1(
                item, path, scope, aliases, nodes,
            )
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if (
            isinstance(item.func, ast.Attribute)
            and _resolved_alias_name(item.func.value, aliases) == "sys.modules"
            and api == "get"
            and (
                not item.args
                or _static_module_reference_may_reach_boundary(item.args[0], path)
            )
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if api in {"getattr", "vars", "setattr", "delattr"} and item.args:
            reflected_namespace = _resolved_alias_name(item.args[0], aliases)
            if (
                reflected_namespace in {"builtins", "importlib", "__builtins__"}
                or reflected_namespace is not None
                and reflected_namespace.startswith("importlib.")
            ):
                capabilities.add("dynamic_boundary_access")
                closed_dynamic_boundary = True
        if (
            canonical in {
                "builtins.__getattribute__", "importlib.__getattribute__",
            }
            and item.args
            and _static_reflection_key_may_import(item.args[0])
        ):
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        if api == "getattr" and item.args:
            module_name = _dotted_name(item.args[0])
            if module_name is not None:
                first_module, separator_module, remainder_module = (
                    module_name.partition(".")
                )
                resolved_module = aliases.get(first_module, first_module) + (
                    separator_module + remainder_module
                    if separator_module else ""
                )
                reflected = (
                    item.args[1].value
                    if len(item.args) > 1
                    and isinstance(item.args[1], ast.Constant)
                    and isinstance(item.args[1].value, str)
                    else None
                )
                owner = _boundary_owner(resolved_module)
                if owner is not None:
                    closed_dynamic_boundary = True
                    reflected_caps = (
                        _boundary_api_capabilities(
                            f"{resolved_module}.{reflected}",
                        )
                        if reflected is not None else ()
                    )
                    if reflected_caps:
                        capabilities.update(reflected_caps)
                    else:
                        capabilities.add("dynamic_boundary_access")
                if (
                    resolved_module in {"builtins", "importlib"}
                    and (
                        reflected is None
                        or reflected in {"__import__", "import_module"}
                    )
                ):
                    capabilities.add("dynamic_boundary_access")
                    closed_dynamic_boundary = True
        if api == "vars" and item.args:
            module_name = _dotted_name(item.args[0])
            if module_name is not None:
                first_module, separator_module, remainder_module = module_name.partition(".")
                resolved_module = aliases.get(first_module, first_module) + (
                    separator_module + remainder_module if separator_module else ""
                )
                if _boundary_owner(resolved_module) is not None:
                    closed_dynamic_boundary = True
                if resolved_module in {"builtins", "importlib"}:
                    capabilities.add("dynamic_boundary_access")
                    closed_dynamic_boundary = True
        builtin_dynamic_eval = canonical in {
            "builtins.compile", "builtins.eval", "builtins.exec",
        } or (
            isinstance(item.func, ast.Name)
            and item.func.id in {"compile", "eval", "exec"}
            and item.func.id not in aliases
            and item.func.id not in local_callables
        )
        if builtin_dynamic_eval:
            if (path, scope) != AUTHENTICATED_EXECUTION_SCOPE:
                capabilities.add("dynamic_boundary_access")
                closed_dynamic_boundary = True
            elif scope_boundary_strings:
                closed_dynamic_boundary = True
        dynamic_import = api in {"__import__", "import_module"}
        if dynamic_import:
            capabilities.add("dynamic_boundary_access")
            closed_dynamic_boundary = True
        command_parts = _static_strings(item) if api in PROCESS_CALLS else set()
        sign_entrypoint = any(
            part.endswith("sign.py") or part == "runtime.sign"
            for part in command_parts
        )
        if api in PROCESS_CALLS and (
            "sign-all" in command_parts
            or ("sign" in command_parts and sign_entrypoint)
            or any(
                re.search(
                    r"(?:runtime\.sign|(?:^|[/\\])sign\.py)\s+"
                    r"(?:sign|sign-all)(?:\s|$)",
                    part,
                )
                for part in command_parts
            )
        ):
            capabilities.add("sign")
        if api in PROCESS_CALLS and (
            scope_boundary_strings
            or any(boundary_text.search(part) for part in command_parts)
        ):
            closed_dynamic_boundary = True

        reads = api in READ_OPERATIONS
        persistent_write = _writes_path(api, item)
        target = _call_target(item)
        authoring_touch = (reads or persistent_write) and (_touches(
            target,
            tainted=authoring_names,
            pattern=_AUTHORING_NAME_RE,
            literal_test=_has_authoring_literal,
        ) or any(
            _touches(
                arg,
                tainted=authoring_names,
                pattern=_AUTHORING_NAME_RE,
                literal_test=_has_authoring_literal,
            )
            for arg in item.args
        ) or any(
            _touches(
                keyword.value,
                tainted=authoring_names,
                pattern=_AUTHORING_NAME_RE,
                literal_test=_has_authoring_literal,
            )
            for keyword in item.keywords
        ))
        store_touch = (reads or persistent_write) and (_touches(
            target,
            tainted=store_names,
            pattern=_STORE_NAME_RE,
            literal_test=_has_store_literal,
        ) or any(
            _touches(
                arg,
                tainted=store_names,
                pattern=_STORE_NAME_RE,
                literal_test=_has_store_literal,
            )
            for arg in item.args
        ) or any(
            _touches(
                keyword.value,
                tainted=store_names,
                pattern=_STORE_NAME_RE,
                literal_test=_has_store_literal,
            )
            for keyword in item.keywords
        ))
        writes = persistent_write
        if api in {
            "copy", "copy2", "copyfile", "hardlink_to", "link", "remove",
            "rename", "replace", "rmdir", "rmtree", "symlink_to",
        }:
            writes = writes and (
                _pathlike(target)
                or any(_pathlike(arg) for arg in item.args)
                or any(_pathlike(keyword.value) for keyword in item.keywords)
            )
        if authoring_touch and reads:
            capabilities.add("authoring_read")
        if authoring_touch and writes:
            capabilities.add("authoring_write")
        if store_touch and reads:
            capabilities.add("verified_store_read")
        if store_touch and writes:
            capabilities.add("store_write")

        # Every filesystem mutation implemented by a declared store-writing
        # source owner is store authority. Descriptor and dir-fd operations
        # deliberately lose Path taint, so the reviewed owner is the semantic
        # boundary instead of a hard-coded filename or variable spelling.
        source_owner = BOUNDARY_SOURCE_OWNERS.get(path)
        descriptor_store_owners = {
            "contract_store", "executor_birth_preflight_attestation_store",
        }
        if persistent_write and source_owner in descriptor_store_owners:
            capabilities.add("store_write")

        # Importing any private store implementation detail is itself an
        # architectural breach; its target can be hidden behind a generic
        # ``Path`` and private read helpers are not a supported live boundary.
        if (
            api.startswith("_")
            and _boundary_owner(canonical.rpartition(".")[0]) == "contract_store"
        ):
            capabilities.add("store_write")

    return ScopeFacts(
        path=path,
        scope=scope,
        line=getattr(node, "lineno", 1),
        capabilities=tuple(sorted(capabilities)),
        calls=tuple(sorted(calls)),
        direct_manifest_dir_access=(
            manifest_dir_locator_used and "authoring_read" in capabilities
        ),
        closed_dynamic_boundary=closed_dynamic_boundary,
    )


class _ScopeCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.scopes: list[tuple[str, ast.AST]] = []
        self.containers: dict[str, ast.AST] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join((*self.stack, node.name))
        self.scopes.append((qualified, node))
        self.containers[qualified] = node
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(self, node: ast.AST, name: str) -> None:
        qualified = ".".join((*self.stack, name))
        self.scopes.append((qualified, node))
        self.containers[qualified] = node
        self.stack.append(name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, node.name)


def _apply_callable_aliases(
    aliases: dict[str, str],
    nodes: Sequence[ast.AST],
    local_callables: frozenset[str],
) -> bool:
    """Resolve aliases and report any rebinding of existing authority."""

    def trusted(value: str) -> bool:
        return bool(
            _boundary_owner(value) is not None
            or _boundary_api_capabilities(value)
            or value in local_callables
            or value in SENSITIVE_FIRST_CLASS_REFERENCES
            or value in SENSITIVE_IMPORT_NAMESPACES
            or value in DYNAMIC_CODE_LOADER_CANONICALS
            or value.rsplit(".", 1)[-1] in WRITE_OPERATIONS
            or value.startswith("importlib.")
            and value.rsplit(".", 1)[-1] in DYNAMIC_CODE_LOADER_APIS
        )

    def boundary_authoritative(value: str) -> bool:
        return bool(
            _boundary_owner(value) is not None
            or _boundary_api_capabilities(value)
        )

    ambiguous = False
    assignments = [
        item for item in nodes if isinstance(item, (ast.Assign, ast.AnnAssign))
    ]
    for item in assignments:
        value = item.value
        if value is None:
            continue
        dotted = _dotted_name(value)
        canonical = ""
        if dotted is not None:
            first, separator, remainder = dotted.partition(".")
            canonical = aliases.get(first, first) + (
                separator + remainder if separator else ""
            )
        targets = (
            set().union(*(_target_names(target) for target in item.targets))
            if isinstance(item, ast.Assign)
            else _target_names(item.target)
        )
        trusted_alias = bool(canonical and trusted(canonical))
        if not trusted_alias and _may_resolve_dynamic_loader_callable(
            value, aliases,
        ):
            canonical = "importlib." + (_leaf_name(value) or "dynamic_loader")
            trusted_alias = True
        for target in targets:
            previous = aliases.get(target)
            if (
                previous
                and boundary_authoritative(previous)
                and canonical != previous
            ):
                ambiguous = True
            if trusted_alias:
                _remember_alias(aliases, target, canonical, local_callables)
    return ambiguous


def _remember_alias(
    aliases: dict[str, str], bound: str, canonical: str,
    local_callables: frozenset[str],
) -> None:
    """Retain an earlier authority, or prefer a later authority over safety."""

    def authoritative(value: str) -> bool:
        return bool(
            _boundary_owner(value) is not None
            or _boundary_api_capabilities(value)
            or value in local_callables
            or value in SENSITIVE_FIRST_CLASS_REFERENCES
            or value in SENSITIVE_IMPORT_NAMESPACES
        )

    previous = aliases.get(bound)
    if previous is None or (authoritative(canonical) and not authoritative(previous)):
        aliases[bound] = canonical


def _import_aliases(
    tree: ast.Module,
    local_callables: frozenset[str],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    # Imports guarded by an optional-dependency ``try`` are still real module
    # aliases.  Walk the module lexical scope, but never descend into a
    # function or class where the alias has different lifetime.
    for node in _scope_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if _relative_boundary_import(node):
                continue
            for alias in node.names:
                _remember_alias(
                    aliases, alias.asname or alias.name,
                    f"{node.module}.{alias.name}", local_callables,
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                _remember_alias(
                    aliases, bound, alias.name if alias.asname else bound,
                    local_callables,
                )
    _apply_callable_aliases(aliases, _scope_nodes(tree), local_callables)
    return aliases


def _aliases_in_lexical_scope(
    scope: str,
    *,
    module_aliases: Mapping[str, str],
    containers: Mapping[str, ast.AST],
    local_callables: frozenset[str],
) -> dict[str, str]:
    """Return module plus enclosing-scope imports for one nested function."""

    aliases = dict(module_aliases)
    parts = scope.split(".")
    for size in range(1, len(parts)):
        parent = containers.get(".".join(parts[:size]))
        if parent is None:
            continue
        for item in _scope_nodes(parent):
            if isinstance(item, ast.ImportFrom) and item.module:
                if _relative_boundary_import(item):
                    continue
                for alias in item.names:
                    _remember_alias(
                        aliases, alias.asname or alias.name,
                        f"{item.module}.{alias.name}", local_callables,
                    )
            elif isinstance(item, ast.Import):
                for alias in item.names:
                    bound = alias.asname or alias.name.split(".", 1)[0]
                    _remember_alias(
                        aliases, bound, alias.name if alias.asname else bound,
                        local_callables,
                    )
        _apply_callable_aliases(aliases, _scope_nodes(parent), local_callables)
    return aliases


def _bounded_ast_metrics(tree: ast.AST) -> int:
    nodes = 0
    scopes = 1
    calls = 0
    stack = [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes += 1
        if isinstance(node, ast.Call):
            calls += 1
        if (
            nodes > MAX_BOUNDARY_AST_NODES
            or depth > MAX_BOUNDARY_AST_DEPTH
            or scopes > MAX_BOUNDARY_SCOPES
            or calls > MAX_BOUNDARY_CALLS
        ):
            raise ValueError("boundary AST budget exceeded")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return nodes


def _scan_file_with_metrics_unchecked(
    path: Path, *, repository_root: Path,
) -> tuple[list[ScopeFacts], int, int]:
    relative = path.relative_to(repository_root).as_posix()
    return _scan_source_with_metrics(relative, _read_boundary_source(path))


def _read_boundary_source(path: Path) -> bytes:
    try:
        with path.open("rb") as source:
            content = source.read(MAX_BOUNDARY_SOURCE_BYTES + 1)
        if len(content) > MAX_BOUNDARY_SOURCE_BYTES:
            raise ValueError("boundary source byte budget exceeded")
        return content
    except (OSError, MemoryError) as exc:
        raise ValueError(f"cannot read boundary source: {path}") from exc


def _scan_source_with_metrics(
    relative: str, content: bytes,
) -> tuple[list[ScopeFacts], int, int]:
    try:
        tree = ast.parse(content.decode("utf-8"), filename=relative)
        node_count = _bounded_ast_metrics(tree)
    except (
        OSError, SyntaxError, UnicodeError, RecursionError, ValueError,
        OverflowError,
    ) as exc:
        raise ValueError(f"cannot scan {relative}: {exc}") from exc
    collector = _ScopeCollector()
    try:
        collector.visit(tree)
    except (RecursionError, ValueError, OverflowError) as exc:
        raise ValueError(f"cannot scan {relative}: {exc}") from exc
    scopes: list[tuple[str, ast.AST]] = [("<module>", tree), *collector.scopes]
    local_callables = frozenset(
        name.rsplit(".", 1)[-1] for name, _node in collector.scopes
    )
    aliases = _import_aliases(tree, local_callables)
    direct = [
        _analyse_scope(
            relative,
            name,
            node,
            _aliases_in_lexical_scope(
                name,
                module_aliases=aliases,
                containers=collector.containers,
                local_callables=local_callables,
            ),
            local_callables,
        )
        for name, node in scopes
    ]
    by_leaf: dict[str, list[int]] = {}
    for index, fact in enumerate(direct):
        by_leaf.setdefault(fact.scope.rsplit(".", 1)[-1], []).append(index)
    effective = [set(fact.capabilities) for fact in direct]
    changed = True
    while changed:
        changed = False
        for index, fact in enumerate(direct):
            for callee in fact.calls:
                matches = by_leaf.get(callee, [])
                if len(matches) > 1:
                    authority = set().union(*(effective[item] for item in matches))
                    if authority & FLOW_CAPABILITIES:
                        before = len(effective[index])
                        effective[index].add("ambiguous_local_authority")
                        changed |= len(effective[index]) != before
                    continue
                if not matches:
                    continue
                before = len(effective[index])
                effective[index].update(effective[matches[0]] & FLOW_CAPABILITIES)
                changed |= len(effective[index]) != before
    result = [
        ScopeFacts(
            path=fact.path,
            scope=fact.scope,
            line=fact.line,
            capabilities=tuple(sorted(effective[index])),
            calls=fact.calls,
            direct_manifest_dir_access=fact.direct_manifest_dir_access,
            closed_dynamic_boundary=fact.closed_dynamic_boundary,
        )
        for index, fact in enumerate(direct)
    ]
    return result, len(content), node_count


def _scan_file_with_metrics(
    path: Path, *, repository_root: Path,
) -> tuple[list[ScopeFacts], int, int]:
    try:
        return _scan_file_with_metrics_unchecked(
            path, repository_root=repository_root,
        )
    except MemoryError as exc:
        raise ValueError("cannot scan boundary source: memory exhausted") from exc


def scan_file(path: Path, *, repository_root: Path) -> list[ScopeFacts]:
    return _scan_file_with_metrics(path, repository_root=repository_root)[0]


def discover(repository_root: Path) -> list[ScopeFacts]:
    repository_root = repository_root.resolve()
    paths: list[Path] = []
    for root_name in SCAN_ROOTS:
        scan_root = repository_root / root_name
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            paths.append(path)
    if len(paths) > MAX_BOUNDARY_SOURCE_FILES:
        raise ValueError("boundary source file budget exceeded")
    try:
        declared_source_bytes = [path.stat().st_size for path in paths]
    except OSError as exc:
        raise ValueError(f"cannot stat boundary source: {exc}") from exc
    if (
        any(size > MAX_BOUNDARY_SOURCE_BYTES for size in declared_source_bytes)
        or sum(declared_source_bytes) > MAX_BOUNDARY_TOTAL_SOURCE_BYTES
    ):
        raise ValueError("boundary source byte budget exceeded")
    total_source_bytes = 0
    sources = []
    for path in paths:
        content = _read_boundary_source(path)
        total_source_bytes += len(content)
        if total_source_bytes > MAX_BOUNDARY_TOTAL_SOURCE_BYTES:
            raise ValueError("boundary total source byte budget exceeded")
        sources.append((path.relative_to(repository_root).as_posix(), content))
    return list(_discover_source_facts(tuple(sources), _boundary_ast_limits()))


def _boundary_ast_limits() -> tuple[int, ...]:
    return (MAX_BOUNDARY_AST_NODES, MAX_BOUNDARY_AST_DEPTH,
            MAX_BOUNDARY_SCOPES, MAX_BOUNDARY_CALLS, MAX_BOUNDARY_TOTAL_AST_NODES)


@lru_cache(maxsize=1)
def _discover_source_facts(
    sources: tuple[tuple[str, bytes], ...], limits: tuple[int, ...],
) -> tuple[ScopeFacts, ...]:
    # One bounded syntax snapshot, keyed by exact paths, bytes and budgets.
    # Enumeration, reads and byte/file budgets remain live on every call.
    facts: list[ScopeFacts] = []
    total_ast_nodes = 0
    for relative, content in sources:
        try:
            discovered, _, ast_nodes = _scan_source_with_metrics(relative, content)
        except MemoryError as exc:
            raise ValueError("cannot scan boundary source: memory exhausted") from exc
        total_ast_nodes += ast_nodes
        if total_ast_nodes > MAX_BOUNDARY_TOTAL_AST_NODES:
            raise ValueError("boundary total AST node budget exceeded")
        facts.extend(discovered)
    return tuple(sorted(
        (
            fact for fact in facts
            if (
                fact.capabilities
                or fact.direct_manifest_dir_access
                or fact.closed_dynamic_boundary
            )
        ),
        key=lambda fact: (fact.path, fact.scope),
    ))


def load_inventory(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load boundary inventory {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError(f"unsupported boundary inventory schema in {path}")
    if not isinstance(payload.get("entries"), list):
        raise ValueError(f"boundary inventory entries must be a list in {path}")
    return payload


def _entry_key(entry: Mapping[str, object]) -> str:
    return f"{entry.get('path', '')}:{entry.get('scope', '')}"


def check(
    facts: Sequence[ScopeFacts],
    inventory: Mapping[str, object],
) -> list[Finding]:
    findings: list[Finding] = []
    if inventory.get("schema") != SCHEMA:
        findings.append(Finding(
            "inventory_invalid", "<inventory>", "schema does not match the guard",
        ))
    if inventory.get("scan_roots") != list(SCAN_ROOTS):
        findings.append(Finding(
            "inventory_invalid",
            "<inventory>",
            f"scan_roots must be {list(SCAN_ROOTS)!r}",
        ))
    raw_entries = inventory.get("entries", [])
    if not isinstance(raw_entries, list):
        return [Finding("inventory_invalid", "<inventory>", "entries is not a list")]

    entries: dict[str, Mapping[str, object]] = {}
    for raw in raw_entries:
        if not isinstance(raw, dict):
            findings.append(Finding(
                "inventory_invalid", "<inventory>", "entry is not an object",
            ))
            continue
        key = _entry_key(raw)
        if key in entries:
            findings.append(Finding("inventory_duplicate", key, "duplicate scope"))
        entries[key] = raw

    discovered = {
        fact.key: fact
        for fact in facts
        if fact.capabilities or fact.direct_manifest_dir_access
    }
    for key, fact in discovered.items():
        entry = entries.get(key)
        if entry is None:
            findings.append(Finding(
                "unclassified_boundary_scope",
                key,
                f"classify capabilities {list(fact.capabilities)!r}",
            ))
            continue
        role = entry.get("role")
        if role not in VALID_ROLES:
            findings.append(Finding(
                "inventory_role_invalid", key, f"unsupported role {role!r}",
            ))
            continue
        destination = entry.get("destination")
        if (
            not isinstance(destination, str)
            or not destination.strip()
            or destination.strip() == "review-required"
        ):
            findings.append(Finding(
                "inventory_invalid", key, "destination must explain the boundary",
            ))
            continue
        if entry.get("phase") != "M4":
            findings.append(Finding(
                "inventory_invalid", key, "phase must be M4",
            ))
            continue
        expected = entry.get("capabilities")
        if not isinstance(expected, list) or any(not isinstance(v, str) for v in expected):
            findings.append(Finding(
                "inventory_invalid", key, "capabilities must be a string list",
            ))
            continue
        if expected != sorted(set(expected)):
            findings.append(Finding(
                "inventory_invalid",
                key,
                "capabilities must be sorted and contain no duplicates",
            ))
            continue
        if tuple(expected) != fact.capabilities:
            findings.append(Finding(
                "boundary_scope_changed",
                key,
                f"inventory={sorted(set(expected))!r}, discovered={list(fact.capabilities)!r}",
            ))
            continue

        capabilities = set(fact.capabilities)
        if fact.direct_manifest_dir_access and not (
            role in DIRECT_MANIFEST_ALLOWED_ROLES
            or fact.path in DIRECT_MANIFEST_ALLOWED_PATHS
        ):
            findings.append(Finding(
                "direct_manifest_dir_read_without_token",
                key,
                "ManifestRef.manifest_dir is private to the versioned reader; "
                "use read_manifest_ref_versioned()",
            ))
        if role == "birth_owner" and (
            fact.path not in BIRTH_OWNER_ALLOWED_PATHS
            or bool(capabilities & BIRTH_OWNER_FORBIDDEN_CAPABILITIES)
        ):
            findings.append(Finding(
                "birth_owner_invalid",
                key,
                "birth ownership is restricted to the sealed Executor Birth modules and "
                "cannot absorb dedicated or migration boundaries",
            ))
        if role == "operational_producer" and "birth" in capabilities and (
            capabilities & OPERATIONAL_BIRTH_FORBIDDEN_CAPABILITIES
        ):
            findings.append(Finding(
                "operational_birth_mixed_authority",
                key,
                "a migrated producer may request birth but cannot retain low-level authority",
            ))
        if "ambiguous_local_authority" in capabilities:
            findings.append(Finding(
                "ambiguous_local_authority",
                key,
                "a same-named local helper hides which authority is invoked",
            ))
        if "dynamic_boundary_access" in capabilities:
            findings.append(Finding(
                "dynamic_boundary_access",
                key,
                "boundary authority must use a statically resolved canonical API",
            ))
        if "store_write" in capabilities and role != "store_owner":
            findings.append(Finding(
                "store_write_outside_boundary",
                key,
                "only a reviewed store_owner scope may mutate the publication store",
            ))
        if (
            capabilities & BOOTSTRAP_CAPABILITIES
            and role not in BOOTSTRAP_ALLOWED_ROLES
        ):
            findings.append(Finding(
                "legacy_bootstrap_outside_boundary",
                key,
                "initial publication and activation require a migration_boundary",
            ))
        if role == "migration_boundary" and "legacy_bootstrap" not in capabilities:
            findings.append(Finding(
                "migration_boundary_not_constrained",
                key,
                "migration boundary has no discovered legacy-bootstrap operation",
            ))
        if (
            capabilities & LIVE_MUTATIONS
            and role not in LIVE_MUTATION_ALLOWED_ROLES
        ):
            findings.append(Finding(
                "live_mutation_role_invalid",
                key,
                "live contract mutation is not an offline or documentation action",
            ))
        if role == "live_reader":
            forbidden = sorted(capabilities & LIVE_READER_FORBIDDEN)
            if forbidden:
                findings.append(Finding(
                    "live_reader_uses_authoring",
                    key,
                    f"live reader uses {forbidden!r} instead of a verified snapshot",
                ))
        if role == "operational_producer":
            if "sign" in capabilities:
                findings.append(Finding(
                    "operational_sign_after_cutover",
                    key,
                    "operational flow must call one publisher, never sign_executor",
                ))
            if "authoring_write" in capabilities and not (
                capabilities & PUBLISH_CAPABILITIES
            ):
                findings.append(Finding(
                    "operational_write_without_publish",
                    key,
                    "authoring mutation has no publication boundary in the same scope",
                ))
        if role == "documentation" and capabilities & (
            LIVE_READER_FORBIDDEN - DOCUMENTATION_CAPABILITY_EXEMPTIONS
        ):
            findings.append(Finding(
                "documentation_mutates_boundary",
                key,
                "documentation scope may inspect authoring, but cannot mutate authority",
            ))

    for key in sorted(set(entries) - set(discovered)):
        findings.append(Finding(
            "stale_boundary_classification",
            key,
            "remove or regenerate this no-longer-discovered scope",
        ))
    return sorted(findings, key=lambda finding: (finding.code, finding.scope))


def birth_closed_findings(
    facts: Sequence[ScopeFacts],
    inventory: Mapping[str, object],
) -> list[Finding]:
    """Enforce the irreversible RM-0008 F4 closed-build boundary."""

    findings = list(check(facts, inventory))
    if inventory.get("source_census") != BIRTH_CLOSED_SOURCE_REVIEW_SHA256:
        findings.append(Finding(
            "birth_closed_source_review_invalid", "<inventory>",
            "source_census must equal the compiled Python source-review root",
        ))
    policy = inventory.get("birth_closed")
    expected_policy = birth_closed_inventory_value_v1()
    if policy != expected_policy:
        findings.append(Finding(
            "birth_closed_inventory_invalid", "<inventory>",
            "birth_closed policy must exactly match the compiled closed policy",
        ))

    raw_entries = inventory.get("entries", [])
    entries = {
        _entry_key(entry): entry
        for entry in raw_entries
        if isinstance(entry, dict)
    } if isinstance(raw_entries, list) else {}
    owners = sorted(
        key for key, entry in entries.items() if entry.get("role") == "birth_owner"
    )
    if owners != [BIRTH_CLOSED_OWNER]:
        findings.append(Finding(
            "birth_closed_owner_invalid", "<inventory>",
            f"expected exactly {[BIRTH_CLOSED_OWNER]!r}, found {owners!r}",
        ))

    fact_keys = {fact.key for fact in facts}
    for scope in sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS - fact_keys):
        findings.append(Finding(
            "birth_closed_coordinator_scope_missing", scope,
            "compiled ownership coordinator has no discovered store boundary",
        ))
    for scope in sorted(set(BIRTH_CLOSED_EXCEPTION_SCOPES) - fact_keys):
        findings.append(Finding(
            "birth_closed_exception_scope_missing", scope,
            "compiled closed exception has no discovered boundary scope",
        ))

    for fact in facts:
        capabilities = set(fact.capabilities)
        entry = entries.get(fact.key, {})
        exception = entry.get("closed_exception")
        expected_exception = BIRTH_CLOSED_EXCEPTION_SCOPES.get(fact.key)
        if fact.key in BIRTH_CLOSED_COORDINATOR_STORE_OWNERS:
            if (
                entry.get("role") != "store_owner"
                or capabilities != BIRTH_CLOSED_COORDINATOR_REQUIRED_CAPABILITIES
            ):
                findings.append(Finding(
                    "birth_closed_coordinator_invalid", fact.key,
                    "ownership coordinator must be an exact store_write owner",
                ))
        if exception != expected_exception:
            findings.append(Finding(
                "birth_closed_exception_invalid", fact.key,
                f"expected compiled exception {expected_exception!r}, found {exception!r}",
            ))
            continue
        expected_capabilities = BIRTH_CLOSED_EXCEPTION_CAPABILITIES.get(fact.key)
        if expected_capabilities is not None and capabilities != expected_capabilities:
            findings.append(Finding(
                "birth_closed_exception_invalid", fact.key,
                "compiled exception must have exact capabilities "
                f"{sorted(expected_capabilities)!r}, found {sorted(capabilities)!r}",
            ))
        if "dynamic_boundary_access" in capabilities or fact.closed_dynamic_boundary:
            findings.append(Finding(
                "birth_closed_dynamic_boundary", fact.key,
                "closed builds permit no reflective, dynamic-import, or subprocess boundary",
            ))

        relevant_exception_capabilities = (
            BIRTH_CLOSED_EXCEPTION_JUSTIFICATIONS.get(exception, frozenset())
        )
        forbidden = capabilities & BIRTH_CLOSED_LEGACY_CAPABILITIES
        if not forbidden:
            if exception is not None and not (
                capabilities & relevant_exception_capabilities
            ):
                findings.append(Finding(
                    "birth_closed_exception_unused", fact.key,
                    "closed exception does not justify a discovered capability",
                ))
            continue
        if fact.path in BIRTH_CLOSED_SEALED_MODULES:
            continue
        if not forbidden <= relevant_exception_capabilities:
            findings.append(Finding(
                "birth_closed_legacy_authority", fact.key,
                f"legacy capabilities {sorted(forbidden)!r} remain outside sealed definitions",
            ))

    return sorted(
        set(findings),
        key=lambda finding: (finding.code, finding.scope, finding.message),
    )


def render_inventory(
    facts: Sequence[ScopeFacts],
    existing: Mapping[str, object] | None = None,
) -> str:
    previous: dict[str, Mapping[str, object]] = {}
    if existing is not None:
        entries = existing.get("entries", [])
        if isinstance(entries, list):
            previous = {
                _entry_key(entry): entry
                for entry in entries
                if isinstance(entry, dict)
            }
    rendered = []
    for fact in facts:
        if not (fact.capabilities or fact.direct_manifest_dir_access):
            continue
        old = previous.get(fact.key, {})
        rendered.append({
            "path": fact.path,
            "scope": fact.scope,
            "role": old.get("role", "unclassified"),
            "capabilities": list(fact.capabilities),
            "destination": old.get("destination", "review-required"),
            "phase": "M4",
        })
    payload = {
        "schema": SCHEMA,
        "source_census": BIRTH_CLOSED_SOURCE_REVIEW_SHA256,
        "scan_roots": list(SCAN_ROOTS),
        "entries": rendered,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def render_birth_closed_inventory(
    facts: Sequence[ScopeFacts],
    existing: Mapping[str, object] | None = None,
) -> str:
    """Render a candidate without inventing closed exceptions or ownership."""

    payload = json.loads(render_inventory(facts, existing))
    payload["birth_closed"] = birth_closed_inventory_value_v1()
    previous = {
        _entry_key(entry): entry
        for entry in (existing or {}).get("entries", [])
        if isinstance(entry, dict)
    }
    for entry in payload["entries"]:
        key = _entry_key(entry)
        if key in BIRTH_CLOSED_COORDINATOR_STORE_OWNERS:
            entry["role"] = "store_owner"
        old = previous.get(_entry_key(entry), {})
        if "closed_exception" in old:
            entry["closed_exception"] = old["closed_exception"]
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true", help="print candidate inventory")
    parser.add_argument(
        "--birth-closed", action="store_true",
        help="enforce the irreversible RM-0008 F4 closed-build policy",
    )
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--repository-root", type=Path, default=_repository_root())
    args = parser.parse_args(argv)

    root = args.repository_root.resolve()
    facts = discover(root)
    inventory_path = args.inventory
    if not inventory_path.is_absolute():
        inventory_path = root / inventory_path
    if args.render:
        existing = load_inventory(inventory_path) if inventory_path.exists() else None
        rendered = (
            render_birth_closed_inventory(facts, existing)
            if args.birth_closed else render_inventory(facts, existing)
        )
        print(rendered, end="")
        return 0
    inventory = load_inventory(inventory_path)
    findings = (
        birth_closed_findings(facts, inventory)
        if args.birth_closed else check(facts, inventory)
    )
    if args.birth_closed:
        source_finding = closed_python_source_review_finding(root)
        if source_finding is not None:
            findings.append(source_finding)
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
