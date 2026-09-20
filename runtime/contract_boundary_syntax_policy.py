"""Canonical syntax and capability classification for contract boundaries."""
from __future__ import annotations

from dataclasses import dataclass
import re


SCHEMA = "metnos.contract-boundary-inventory/2"
BIRTH_CLOSED_SCHEMA = "metnos.contract-boundary-birth-closed/1"
BIRTH_CLOSED_GUARD_VERSION = f"{SCHEMA}+birth-closed/2"
SCAN_ROOTS = ("runtime", "install", "scripts", "executors")
AUTHORING_FILES = frozenset({
    "manifest.toml",
    "manifest.toml.sig",
    "manifest.lang_state.json",
})


@dataclass(frozen=True, slots=True)
class BoundaryLimitsV1:
    """Closed resource limits for one boundary scan."""

    source_files: int
    source_bytes: int
    total_source_bytes: int
    ast_nodes: int
    total_ast_nodes: int
    ast_depth: int
    scopes: int
    calls: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"boundary_limit_invalid:{name}")


BOUNDARY_LIMITS_V1 = BoundaryLimitsV1(
    source_files=2_048,
    source_bytes=1 * 1024 * 1024,
    total_source_bytes=32 * 1024 * 1024,
    ast_nodes=100_000,
    total_ast_nodes=4_000_000,
    ast_depth=64,
    scopes=512,
    calls=8_192,
)
MAX_BOUNDARY_SOURCE_FILES = BOUNDARY_LIMITS_V1.source_files
MAX_BOUNDARY_SOURCE_BYTES = BOUNDARY_LIMITS_V1.source_bytes
MAX_BOUNDARY_TOTAL_SOURCE_BYTES = BOUNDARY_LIMITS_V1.total_source_bytes
MAX_BOUNDARY_AST_NODES = BOUNDARY_LIMITS_V1.ast_nodes
MAX_BOUNDARY_TOTAL_AST_NODES = BOUNDARY_LIMITS_V1.total_ast_nodes
MAX_BOUNDARY_AST_DEPTH = BOUNDARY_LIMITS_V1.ast_depth
MAX_BOUNDARY_SCOPES = BOUNDARY_LIMITS_V1.scopes
MAX_BOUNDARY_CALLS = BOUNDARY_LIMITS_V1.calls

READ_OPERATIONS = frozenset({
    "exists", "glob", "is_dir", "is_file", "iterdir", "load", "loads",
    "open", "parse", "read", "read_bytes", "read_text", "resolve",
    "rglob", "stat",
})
WRITE_OPERATIONS = frozenset({
    "NamedTemporaryFile", "chmod", "chown", "copy", "copy2", "copyfile",
    "extract", "extractall", "fchmod", "fchown", "ftruncate", "fsync",
    "mkdir", "mkdtemp", "mkstemp", "open", "remove", "rename", "replace",
    "rmdir", "rmtree", "hardlink_to", "link", "symlink_to", "touch",
    "truncate", "unlink", "write", "write_bytes", "write_text",
})
PROCESS_CALLS = frozenset({
    "Popen", "call", "check_call", "check_output", "run", "system",
})
DYNAMIC_CODE_LOADER_APIS = frozenset({
    "FunctionType", "SourceFileLoader", "SourcelessFileLoader",
    "exec_module", "load_module", "module_from_spec", "run_module",
    "run_path", "spec_from_file_location",
})
DYNAMIC_CODE_LOADER_CANONICALS = frozenset({
    "importlib.machinery.SourceFileLoader",
    "importlib.machinery.SourcelessFileLoader",
    "importlib.util.module_from_spec",
    "importlib.util.spec_from_file_location",
    "runpy.run_module", "runpy.run_path", "types.FunctionType",
})
SENSITIVE_FIRST_CLASS_REFERENCES = frozenset({
    "getattr", "builtins.getattr", "builtins.__getattribute__",
    "importlib.__getattribute__", "sys.modules.get",
})
SENSITIVE_IMPORT_NAMESPACES = frozenset({
    "__builtins__", "__loader__", "__spec__", "builtins",
    "builtins.__dict__", "importlib", "importlib.__dict__",
    "importlib.machinery", "importlib.util", "runpy", "sys.modules", "types",
})
SYS_MODULES_EXPOSING_METHODS = frozenset({
    "copy", "items", "pop", "popitem", "setdefault", "values",
})
SYS_MODULES_MUTATING_METHODS = frozenset({
    "__delitem__", "__setitem__", "clear", "pop", "popitem", "setdefault",
    "update",
})
AUTHENTICATED_EXECUTION_SCOPE = (
    "runtime/admitted_module_v1.py", "load_admitted_module_v1",
)
AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE = (
    "runtime/executor_birth_admin_preflight.py", "_launch_python_target_v1",
)
LIVE_READER_FORBIDDEN = frozenset({
    "ambiguous_local_authority", "authoring_read", "authoring_write",
    "authoring_verify", "birth", "legacy_bootstrap", "publish_bootstrap",
    "publish_localization", "publish_technical", "reactivate", "retire",
    "rollback", "sign", "store_write", "dynamic_boundary_access",
})
PUBLISH_CAPABILITIES = frozenset({
    "birth", "publish_bootstrap", "publish_localization", "publish_technical",
    "reactivate", "retire", "rollback",
})
FLOW_CAPABILITIES = PUBLISH_CAPABILITIES | frozenset({
    "ambiguous_local_authority", "authoring_write", "cutover_guard",
    "legacy_bootstrap", "sign", "store_write", "dynamic_boundary_access",
})


@dataclass(frozen=True, slots=True)
class BoundaryRegexV1:
    """One validated regex authoring fact."""

    pattern: str
    flags: int = 0

    def __post_init__(self) -> None:
        if type(self.pattern) is not str or not self.pattern:
            raise ValueError("boundary_regex_pattern_invalid")
        if type(self.flags) is not int or self.flags < 0:
            raise ValueError("boundary_regex_flags_invalid")
        try:
            re.compile(self.pattern, self.flags)
        except (OverflowError, re.error, ValueError) as exc:
            raise ValueError("boundary_regex_invalid") from exc

    def compile(self) -> re.Pattern[str]:
        return re.compile(self.pattern, self.flags)


AUTHORING_NAME_REGEX_V1 = BoundaryRegexV1(
    r"(?:^|_)(?:(?:authoring_manifest|manifest_source|source_manifest)_"
    r"(?:path|dir|root)|executor_(?:path|dir|root))(?:_|$)",
)
AMBIGUOUS_AUTHORING_ARGUMENT_REGEX_V1 = BoundaryRegexV1(
    r"(?:^|_)manifest_(?:path|dir|root)(?:_|$)",
)
STORE_NAME_REGEX_V1 = BoundaryRegexV1(
    r"(?:^|_)(?:(?:contract_publication|contract_store|publication_store)_"
    r"(?:path|dir|root)|store_root|shadow_root|active_marker|store_relative|"
    r"shadow_relative|active_relative)(?:_|$)",
)
CONTRACT_SCOPE_REGEX_V1 = BoundaryRegexV1(
    r"(?:^|_)(?:contract|manifest)(?:_|$)",
)
GENERIC_PATH_NAME_REGEX_V1 = BoundaryRegexV1(
    r"(?:^|_)(?:path|dir|root|file)(?:_|$)",
)

_AUTHORING_NAME_RE = AUTHORING_NAME_REGEX_V1.compile()
_AMBIGUOUS_AUTHORING_ARGUMENT_RE = AMBIGUOUS_AUTHORING_ARGUMENT_REGEX_V1.compile()
_STORE_NAME_RE = STORE_NAME_REGEX_V1.compile()
_CONTRACT_SCOPE_RE = CONTRACT_SCOPE_REGEX_V1.compile()
_GENERIC_PATH_NAME_RE = GENERIC_PATH_NAME_REGEX_V1.compile()


__all__ = [
    "AMBIGUOUS_AUTHORING_ARGUMENT_REGEX_V1", "AUTHENTICATED_EXECUTION_SCOPE",
    "AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE", "AUTHORING_FILES",
    "AUTHORING_NAME_REGEX_V1", "BIRTH_CLOSED_GUARD_VERSION",
    "BIRTH_CLOSED_SCHEMA", "BOUNDARY_LIMITS_V1", "BoundaryLimitsV1",
    "BoundaryRegexV1", "CONTRACT_SCOPE_REGEX_V1",
    "DYNAMIC_CODE_LOADER_APIS", "DYNAMIC_CODE_LOADER_CANONICALS",
    "FLOW_CAPABILITIES", "GENERIC_PATH_NAME_REGEX_V1",
    "LIVE_READER_FORBIDDEN", "MAX_BOUNDARY_AST_DEPTH",
    "MAX_BOUNDARY_AST_NODES", "MAX_BOUNDARY_CALLS", "MAX_BOUNDARY_SCOPES",
    "MAX_BOUNDARY_SOURCE_BYTES", "MAX_BOUNDARY_SOURCE_FILES",
    "MAX_BOUNDARY_TOTAL_AST_NODES", "MAX_BOUNDARY_TOTAL_SOURCE_BYTES",
    "PROCESS_CALLS", "PUBLISH_CAPABILITIES", "READ_OPERATIONS", "SCAN_ROOTS",
    "SCHEMA", "SENSITIVE_FIRST_CLASS_REFERENCES",
    "SENSITIVE_IMPORT_NAMESPACES", "STORE_NAME_REGEX_V1",
    "SYS_MODULES_EXPOSING_METHODS", "SYS_MODULES_MUTATING_METHODS",
    "WRITE_OPERATIONS",
]
