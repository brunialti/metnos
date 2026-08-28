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
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence


SCHEMA = "metnos.contract-boundary-inventory/2"
BIRTH_CLOSED_SCHEMA = "metnos.contract-boundary-birth-closed/1"
BIRTH_CLOSED_GUARD_VERSION = f"{SCHEMA}+birth-closed/1"
DEFAULT_INVENTORY = Path("internal/reports/rm0007-m4-boundary-inventory.json")
SCAN_ROOTS = ("runtime", "install", "scripts", "executors")
AUTHORING_FILES = frozenset({
    "manifest.toml",
    "manifest.toml.sig",
    "manifest.lang_state.json",
})
# Public boundary APIs are classified by their owning module, never by a
# language, executor name or caller-chosen helper name.  Local wrappers inherit
# these capabilities through the per-file call graph below.
BOUNDARY_APIS: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "executor_birth": {
        "birth_executor": ("birth",),
    },
    "executor_birth_intent": {
        "submit_builtin_generation_birth": ("birth",),
        "submit_change_extend_birth": ("birth",),
        "submit_change_rollback_birth": ("birth",),
        "submit_installer_birth": ("birth",),
        "submit_promote_birth": ("birth",),
        "submit_promoter_rollback_birth": ("birth",),
        "submit_skills_birth": ("birth",),
        "submit_stack_reconcile_birth": ("birth",),
        "submit_synth_producer_birth": ("birth",),
    },
    "executor_birth_operational": {
        "birth_executor": ("birth",),
    },
    "executor_birth_synth": {
        "submit_synth_multistage": ("birth",),
        "submit_synth_specialize": ("birth",),
        "submit_synth_approve": ("birth",),
    },
    "contract_store": {
        "verify_manifest_source": ("authoring_read", "authoring_verify"),
        "prepare_technical_draft": ("authoring_read", "authoring_verify"),
        "read_binding": ("verified_store_read",),
        "current_revision_id": ("verified_store_read",),
        "current_contract": ("verified_store_read",),
        "current_manifest": ("verified_store_read",),
        "diagnose_store": ("verified_store_read",),
        "publish_localization": ("publish_localization",),
        "publish_technical_update": ("publish_technical",),
        "publish_signed_source": ("publish_bootstrap",),
        "retire": ("retire",),
        "reactivate_technical_update": ("reactivate",),
        "rollback": ("rollback",),
        "activate_store": ("legacy_bootstrap",),
    },
    "sign": {
        "sign_executor": ("sign",),
        "verify_executor": ("authoring_read", "authoring_verify"),
        "publish_executor": ("publish_technical",),
        "publish_authoring_update": ("publish_technical",),
        "retire_executor_contract": ("retire",),
        "reactivate_executor_contract": ("reactivate",),
        "rollback_executor_contract": ("rollback",),
    },
    "loader": {
        "load_catalog": ("live_artifact_read",),
    },
    "invocations": {
        "load_executor_artifact": ("live_artifact_read",),
    },
    "i18n_migrate_manifests": {
        "prepare_contract_store_shadow": ("legacy_bootstrap",),
        "activate_prepared_contract_store": ("legacy_bootstrap",),
    },
    "contract_cutover_guard": {
        "contract_cutover_guard": ("cutover_guard",),
        "verify_store_only_catalog": (
            "live_artifact_read",
            "verified_store_read",
        ),
    },
    "manifest_inventory": {
        "inventory_authoring_manifests": ("authoring_read",),
        "inventory_manifests": ("authoring_read", "verified_store_read"),
        "inventory_store_manifests": ("verified_store_read",),
    },
    "executor_birth_authoring": {
        "read_manifest_ref_versioned": ("authoring_versioned_read",),
    },
    "executor_birth_ownership_chain": {
        "_append_pair": ("store_write",),
        "_replace_required_pointer": ("store_write",),
        "_required_head_lock": ("store_write",),
        "_update_required_head_locked": ("store_write",),
        "append_authenticated_build": ("store_write",),
        "append_cutover": ("store_write",),
        "append_head": ("store_write",),
        "initialize": ("store_write",),
        "update_required_head": ("store_write",),
    },
}
BOUNDARY_MODULES: Mapping[str, frozenset[str]] = {
    "executor_birth": frozenset({"executor_birth", "runtime.executor_birth"}),
    "executor_birth_intent": frozenset({
        "executor_birth_intent", "runtime.executor_birth_intent",
    }),
    "executor_birth_operational": frozenset({
        "executor_birth_operational", "runtime.executor_birth_operational",
    }),
    "executor_birth_synth": frozenset({
        "executor_birth_synth", "runtime.executor_birth_synth",
    }),
    "contract_store": frozenset({"contract_store", "runtime.contract_store"}),
    "sign": frozenset({"sign", "runtime.sign"}),
    "loader": frozenset({"loader", "runtime.loader"}),
    "invocations": frozenset({"invocations", "runtime.invocations"}),
    "i18n_migrate_manifests": frozenset({
        "admin.i18n_migrate_manifests",
        "runtime.admin.i18n_migrate_manifests",
    }),
    "contract_cutover_guard": frozenset({
        "contract_cutover_guard",
        "runtime.contract_cutover_guard",
    }),
    "manifest_inventory": frozenset({
        "manifest_inventory",
        "runtime.manifest_inventory",
    }),
    "executor_birth_authoring": frozenset({
        "executor_birth_authoring", "runtime.executor_birth_authoring",
    }),
    "executor_birth_ownership_chain": frozenset({
        "executor_birth_ownership_chain", "runtime.executor_birth_ownership_chain",
    }),
}
BOUNDARY_SOURCE_OWNERS: Mapping[str, str] = {
    "runtime/executor_birth.py": "executor_birth",
    "runtime/executor_birth_intent.py": "executor_birth_intent",
    "runtime/executor_birth_operational.py": "executor_birth_operational",
    "runtime/contract_store.py": "contract_store",
    "runtime/sign.py": "sign",
    "runtime/loader.py": "loader",
    "runtime/invocations.py": "invocations",
    "runtime/admin/i18n_migrate_manifests.py": "i18n_migrate_manifests",
    "runtime/contract_cutover_guard.py": "contract_cutover_guard",
    "runtime/manifest_inventory.py": "manifest_inventory",
    "runtime/executor_birth_authoring.py": "executor_birth_authoring",
    "runtime/executor_birth_ownership_chain.py": "executor_birth_ownership_chain",
}
READ_OPERATIONS = frozenset({
    "exists",
    "glob",
    "is_dir",
    "is_file",
    "iterdir",
    "load",
    "loads",
    "open",
    "parse",
    "read",
    "read_bytes",
    "read_text",
    "resolve",
    "rglob",
    "stat",
})
WRITE_OPERATIONS = frozenset({
    "NamedTemporaryFile",
    "chmod",
    "chown",
    "copy",
    "copy2",
    "copyfile",
    "extract",
    "extractall",
    "fchmod",
    "fchown",
    "ftruncate",
    "fsync",
    "mkdir",
    "mkdtemp",
    "mkstemp",
    "open",
    "remove",
    "rename",
    "replace",
    "rmdir",
    "rmtree",
    "hardlink_to",
    "link",
    "symlink_to",
    "touch",
    "truncate",
    "unlink",
    "write",
    "write_bytes",
    "write_text",
})
PROCESS_CALLS = frozenset({"Popen", "call", "check_call", "check_output", "run", "system"})
LIVE_READER_FORBIDDEN = frozenset({
    "ambiguous_local_authority",
    "authoring_read",
    "authoring_write",
    "authoring_verify",
    "birth",
    "legacy_bootstrap",
    "publish_bootstrap",
    "publish_localization",
    "publish_technical",
    "reactivate",
    "retire",
    "rollback",
    "sign",
    "store_write",
    "dynamic_boundary_access",
})
PUBLISH_CAPABILITIES = frozenset({
    "birth",
    "publish_bootstrap",
    "publish_localization",
    "publish_technical",
    "reactivate",
    "retire",
    "rollback",
})
FLOW_CAPABILITIES = PUBLISH_CAPABILITIES | frozenset({
    "ambiguous_local_authority",
    "authoring_write",
    "cutover_guard",
    "legacy_bootstrap",
    "sign",
    "store_write",
    "dynamic_boundary_access",
})

# These are implementation boundaries, not a caller-extensible allow-list.
BIRTH_CLOSED_SEALED_MODULES = (
    "runtime/contract_store.py",
    "runtime/executor_birth.py",
    "runtime/executor_birth_operational.py",
    "runtime/sign.py",
)
BIRTH_CLOSED_OWNER = "runtime/executor_birth_operational.py:birth_executor"
BIRTH_CLOSED_COORDINATOR_STORE_OWNERS = frozenset({
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore._append_pair",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore._update_required_head_locked",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.append_authenticated_build",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.append_cutover",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.append_head",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.initialize",
    "runtime/executor_birth_ownership_chain.py:OwnershipChainStore.update_required_head",
    "runtime/executor_birth_ownership_chain.py:_replace_required_pointer",
    "runtime/executor_birth_ownership_chain.py:_required_head_lock",
})
BIRTH_CLOSED_LEGACY_CAPABILITIES = frozenset({
    "publish_localization", "publish_technical", "reactivate", "retire",
    "rollback", "sign",
})
BIRTH_CLOSED_EXCEPTIONS = frozenset({
    "localization_only", "retirement_only", "offline_nonproductive_authoring",
})
BIRTH_CLOSED_EXCEPTION_SCOPES: Mapping[str, str] = {
    "runtime/admin/manifest_refactor.py:<module>": "offline_nonproductive_authoring",
    "runtime/admin/manifest_refactor.py:main": "offline_nonproductive_authoring",
    "runtime/admin/manifest_refactor.py:refactor_manifest": "offline_nonproductive_authoring",
    "runtime/i18n_pipeline.py:live_contract_context": "localization_only",
    "runtime/i18n_translator.py:<module>": "offline_nonproductive_authoring",
    "runtime/i18n_translator.py:_align_one_manifest": "offline_nonproductive_authoring",
    "runtime/i18n_translator.py:align_manifest_descriptions": "offline_nonproductive_authoring",
    "runtime/manifest_normalize.py:<module>": "offline_nonproductive_authoring",
    "runtime/manifest_normalize.py:apply_one": "offline_nonproductive_authoring",
    "runtime/manifest_normalize.py:main": "offline_nonproductive_authoring",
    "runtime/migrate_manifest_descriptions.py:<module>": "offline_nonproductive_authoring",
    "runtime/migrate_manifest_descriptions.py:main": "offline_nonproductive_authoring",
    "runtime/migrate_manifest_descriptions.py:migrate_dirs": "offline_nonproductive_authoring",
    "runtime/migrate_manifest_descriptions.py:migrate_one": "offline_nonproductive_authoring",
    "runtime/change_rollback.py:_rollback_create_executor": "retirement_only",
    "runtime/cli/skills_cli.py:_cmd_uninstall": "retirement_only",
}
BIRTH_CLOSED_EXCEPTION_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "runtime/admin/manifest_refactor.py:<module>": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/admin/manifest_refactor.py:main": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/admin/manifest_refactor.py:refactor_manifest": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/i18n_pipeline.py:live_contract_context": frozenset({
        "publish_localization", "verified_store_read",
    }),
    "runtime/i18n_translator.py:<module>": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/i18n_translator.py:_align_one_manifest": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/i18n_translator.py:align_manifest_descriptions": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/manifest_normalize.py:<module>": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/manifest_normalize.py:apply_one": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/manifest_normalize.py:main": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/migrate_manifest_descriptions.py:<module>": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/migrate_manifest_descriptions.py:main": frozenset({
        "authoring_write", "sign",
    }),
    "runtime/migrate_manifest_descriptions.py:migrate_dirs": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/migrate_manifest_descriptions.py:migrate_one": frozenset({
        "authoring_read", "authoring_write", "sign",
    }),
    "runtime/change_rollback.py:_rollback_create_executor": frozenset({
        "retire",
    }),
    "runtime/cli/skills_cli.py:_cmd_uninstall": frozenset({
        "authoring_read", "retire",
    }),
}
VALID_ROLES = frozenset({
    "administrative_tool",
    "birth_owner",
    "documentation",
    "live_reader",
    "migration_boundary",
    "offline_authoring",
    "operational_producer",
    "store_owner",
})
LIVE_MUTATIONS = frozenset({
    "birth",
    "publish_localization",
    "publish_technical",
    "reactivate",
    "retire",
    "rollback",
})

_AUTHORING_NAME_RE = re.compile(
    r"(?:^|_)(?:(?:authoring_manifest|manifest_source|source_manifest)_"
    r"(?:path|dir|root)|executor_(?:path|dir|root))(?:_|$)",
)
_AMBIGUOUS_AUTHORING_ARGUMENT_RE = re.compile(
    r"(?:^|_)manifest_(?:path|dir|root)(?:_|$)",
)
_STORE_NAME_RE = re.compile(
    r"(?:^|_)(?:(?:contract_publication|contract_store|publication_store)_"
    r"(?:path|dir|root)|store_root|shadow_root|active_marker|store_relative|"
    r"shadow_relative|active_relative)(?:_|$)",
)
_CONTRACT_SCOPE_RE = re.compile(r"(?:^|_)(?:contract|manifest)(?:_|$)")
_GENERIC_PATH_NAME_RE = re.compile(
    r"(?:^|_)(?:path|dir|root|file)(?:_|$)",
)


@dataclass(frozen=True)
class ScopeFacts:
    path: str
    scope: str
    line: int
    capabilities: tuple[str, ...]
    calls: tuple[str, ...]
    direct_manifest_dir_access: bool = False
    closed_dynamic_boundary: bool = False

    @property
    def key(self) -> str:
        return f"{self.path}:{self.scope}"


@dataclass(frozen=True)
class Finding:
    code: str
    scope: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.scope}: {self.message}"


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


def _leaf_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _module_leaf(module: str) -> str:
    return module.rsplit(".", 1)[-1]


def _boundary_owner(module: str) -> str | None:
    for owner, accepted in BOUNDARY_MODULES.items():
        if module in accepted:
            return owner
    return None


def _relative_boundary_import(node: ast.ImportFrom) -> bool:
    if node.level <= 0:
        return False
    if node.module and _boundary_owner(node.module) is not None:
        return True
    return node.module is None and any(
        alias.name in BOUNDARY_APIS for alias in node.names
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


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        result: set[str] = set()
        for item in node.elts:
            result.update(_target_names(item))
        return result
    return set()


def _string_values(node: ast.AST) -> Iterable[str]:
    for item in ast.walk(node):
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            yield item.value


def _static_string(node: ast.AST) -> str | None:
    """Evaluate only syntax that is unambiguously a constant string."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts = [_static_string(value) for value in node.values]
        return "".join(parts) if all(part is not None for part in parts) else None
    return None


def _static_strings(node: ast.AST) -> set[str]:
    return {
        value
        for item in ast.walk(node)
        if (value := _static_string(item)) is not None
    }


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
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
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
                continue
            if _boundary_owner(item.module) and any(
                alias.name == "*" for alias in item.names
            ):
                dynamic_boundary_access = True
            for alias in item.names:
                aliases[alias.asname or alias.name] = f"{item.module}.{alias.name}"
        elif isinstance(item, ast.ImportFrom) and item.module is None:
            if _relative_boundary_import(item):
                dynamic_boundary_access = True
        elif isinstance(item, ast.Import):
            for alias in item.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
    _apply_callable_aliases(aliases, nodes, local_callables)
    authoring_names, store_names = _tainted_names(
        node,
        nodes,
        scope=scope,
    )
    capabilities: set[str] = set(
        _defined_boundary_capabilities(path, scope)
    )
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
        dotted = _dotted_name(item)
        if dotted is None:
            continue
        first, separator, remainder = dotted.partition(".")
        canonical = aliases.get(first, first) + (
            separator + remainder if separator else ""
        )
        capabilities.update(_boundary_api_capabilities(canonical))

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
    for item in nodes:
        if not isinstance(item, ast.Call):
            continue
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
        if canonical in local_callables:
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
                owner = _boundary_owner(resolved_module)
                if owner is not None:
                    closed_dynamic_boundary = True
                    reflected = (
                        item.args[1].value
                        if len(item.args) > 1
                        and isinstance(item.args[1], ast.Constant)
                        and isinstance(item.args[1].value, str)
                        else None
                    )
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
        if api == "vars" and item.args:
            module_name = _dotted_name(item.args[0])
            if module_name is not None:
                first_module, separator_module, remainder_module = module_name.partition(".")
                resolved_module = aliases.get(first_module, first_module) + (
                    separator_module + remainder_module if separator_module else ""
                )
                if _boundary_owner(resolved_module) is not None:
                    closed_dynamic_boundary = True
        if api in {"eval", "exec"} and scope_boundary_strings:
            closed_dynamic_boundary = True
        dynamic_import = api in {"__import__", "import_module"}
        if dynamic_import:
            imported = tuple(set(_string_values(item)) | _static_strings(item))
            if any(
                _boundary_owner(value) is not None for value in imported
            ):
                capabilities.add("dynamic_boundary_access")
                closed_dynamic_boundary = True
        command_parts = set(_string_values(item)) | _static_strings(item)
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

        target = _call_target(item)
        authoring_touch = _touches(
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
        )
        store_touch = _touches(
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
        )

        reads = api in READ_OPERATIONS
        writes = _writes_path(api, item)
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

        # Every filesystem mutation implemented by the single store-owner
        # module is publication-store authority.  File-handle writes and
        # ``os.open`` flags do not retain their originating Path expression,
        # so requiring path-name taint here would create an easy bypass.
        if path == "runtime/contract_store.py" and writes:
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
) -> None:
    """Resolve trusted aliases and invalidate every later untrusted rebind."""

    assignments = [
        item for item in nodes if isinstance(item, (ast.Assign, ast.AnnAssign))
    ]
    for _iteration in range(len(assignments) + 1):
        before = dict(aliases)
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
            trusted = bool(
                canonical
                and (
                    _boundary_owner(canonical) is not None
                    or _boundary_api_capabilities(canonical)
                    or canonical in local_callables
                )
            )
            targets = (
                set().union(*(_target_names(target) for target in item.targets))
                if isinstance(item, ast.Assign)
                else _target_names(item.target)
            )
            for target in targets:
                if trusted:
                    aliases[target] = canonical
                else:
                    aliases.pop(target, None)
        if aliases == before:
            break


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
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
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
                    aliases[alias.asname or alias.name] = (
                        f"{item.module}.{alias.name}"
                    )
            elif isinstance(item, ast.Import):
                for alias in item.names:
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        _apply_callable_aliases(aliases, _scope_nodes(parent), local_callables)
    return aliases


def scan_file(path: Path, *, repository_root: Path) -> list[ScopeFacts]:
    relative = path.relative_to(repository_root).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise ValueError(f"cannot scan {relative}: {exc}") from exc
    collector = _ScopeCollector()
    collector.visit(tree)
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
    return [
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


def discover(repository_root: Path) -> list[ScopeFacts]:
    repository_root = repository_root.resolve()
    facts: list[ScopeFacts] = []
    for root_name in SCAN_ROOTS:
        scan_root = repository_root / root_name
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            facts.extend(scan_file(path, repository_root=repository_root))
    return sorted(
        (
            fact for fact in facts
            if (
                fact.capabilities
                or fact.direct_manifest_dir_access
                or fact.closed_dynamic_boundary
            )
        ),
        key=lambda fact: (fact.path, fact.scope),
    )


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
            role in {"offline_authoring", "migration_boundary", "store_owner"}
            or fact.path == "runtime/executor_birth_authoring.py"
        ):
            findings.append(Finding(
                "direct_manifest_dir_read_without_token",
                key,
                "ManifestRef.manifest_dir is private to the versioned reader; "
                "use read_manifest_ref_versioned()",
            ))
        if role == "birth_owner" and (
            fact.path not in {
                "runtime/executor_birth.py",
                "runtime/executor_birth_intent.py",
                "runtime/executor_birth_operational.py",
            }
            or bool(capabilities & {
                "legacy_bootstrap", "publish_bootstrap", "publish_localization",
                "retire", "rollback", "sign",
            })
        ):
            findings.append(Finding(
                "birth_owner_invalid",
                key,
                "birth ownership is restricted to the sealed Executor Birth modules and "
                "cannot absorb dedicated or migration boundaries",
            ))
        if role == "operational_producer" and "birth" in capabilities and (
            capabilities & {"publish_technical", "reactivate", "sign"}
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
            capabilities & {"legacy_bootstrap", "publish_bootstrap"}
            and role not in {"migration_boundary", "store_owner"}
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
        if capabilities & LIVE_MUTATIONS and role not in {
            "administrative_tool",
            "birth_owner",
            "migration_boundary",
            "operational_producer",
            "store_owner",
        }:
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
            LIVE_READER_FORBIDDEN - {"authoring_read", "authoring_verify"}
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
    policy = inventory.get("birth_closed")
    expected_policy = {
        "schema": BIRTH_CLOSED_SCHEMA,
        "guard_version": BIRTH_CLOSED_GUARD_VERSION,
        "owner": BIRTH_CLOSED_OWNER,
        "coordinator_store_owners": sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS),
        "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
        "exceptions": [
            {"scope": scope, "exception": exception}
            for scope, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items())
        ],
    }
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
            if entry.get("role") != "store_owner" or capabilities != {"store_write"}:
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

        relevant_exception_capabilities = {
            "localization_only": {"publish_localization"},
            "retirement_only": {"retire"},
            "offline_nonproductive_authoring": {"sign"},
        }.get(exception, set())
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
        "source_census": "internal/reports/rm0007-m0-census-20260825.md",
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
    payload["birth_closed"] = {
        "schema": BIRTH_CLOSED_SCHEMA,
        "guard_version": BIRTH_CLOSED_GUARD_VERSION,
        "owner": BIRTH_CLOSED_OWNER,
        "coordinator_store_owners": sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS),
        "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
        "exceptions": [
            {"scope": scope, "exception": exception}
            for scope, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items())
        ],
    }
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
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
