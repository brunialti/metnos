"""Public facade and legacy materialization for contract boundary policy."""
from __future__ import annotations

from typing import Mapping

from contract_boundary_birth_policy import (
    BIRTH_CLOSED_POLICY_V1,
    birth_closed_inventory_value_v1,
)
from contract_boundary_api_policy import (
    BOUNDARY_API_OWNERS_V1 as _BOUNDARY_API_OWNERS_V1,
    BOUNDARY_MODULE_OWNERS_V1 as _BOUNDARY_MODULE_OWNERS_V1,
    BOUNDARY_SOURCE_OWNERS_V1 as _BOUNDARY_SOURCE_OWNERS_V1,
)
import contract_boundary_syntax_policy as _syntax


def _materialize_apis_v1() -> dict[str, dict[str, tuple[str, ...]]]:
    return {
        row.owner: {name: capabilities for name, capabilities in row.apis}
        for row in _BOUNDARY_API_OWNERS_V1
    }


def _materialize_modules_v1() -> dict[str, frozenset[str]]:
    return {
        row.owner: frozenset(row.module_names)
        for row in _BOUNDARY_MODULE_OWNERS_V1
    }


def _materialize_source_owners_v1() -> dict[str, str]:
    return {row.path: row.owner for row in _BOUNDARY_SOURCE_OWNERS_V1}


def _materialize_exception_scopes_v1() -> dict[str, str]:
    return {
        grant.scope: grant.exception
        for grant in BIRTH_CLOSED_POLICY_V1.exception_grants
    }


def _materialize_exception_capabilities_v1() -> dict[str, frozenset[str]]:
    return {
        grant.scope: frozenset(grant.capabilities)
        for grant in BIRTH_CLOSED_POLICY_V1.exception_grants
    }


SCHEMA = _syntax.SCHEMA
BIRTH_CLOSED_SCHEMA = _syntax.BIRTH_CLOSED_SCHEMA
BIRTH_CLOSED_GUARD_VERSION = _syntax.BIRTH_CLOSED_GUARD_VERSION
SCAN_ROOTS = _syntax.SCAN_ROOTS
AUTHORING_FILES = _syntax.AUTHORING_FILES
BOUNDARY_LIMITS_V1 = _syntax.BOUNDARY_LIMITS_V1
MAX_BOUNDARY_SOURCE_FILES = _syntax.MAX_BOUNDARY_SOURCE_FILES
MAX_BOUNDARY_SOURCE_BYTES = _syntax.MAX_BOUNDARY_SOURCE_BYTES
MAX_BOUNDARY_TOTAL_SOURCE_BYTES = _syntax.MAX_BOUNDARY_TOTAL_SOURCE_BYTES
MAX_BOUNDARY_AST_NODES = _syntax.MAX_BOUNDARY_AST_NODES
MAX_BOUNDARY_TOTAL_AST_NODES = _syntax.MAX_BOUNDARY_TOTAL_AST_NODES
MAX_BOUNDARY_AST_DEPTH = _syntax.MAX_BOUNDARY_AST_DEPTH
MAX_BOUNDARY_SCOPES = _syntax.MAX_BOUNDARY_SCOPES
MAX_BOUNDARY_CALLS = _syntax.MAX_BOUNDARY_CALLS

BOUNDARY_APIS: Mapping[str, Mapping[str, tuple[str, ...]]] = (
    _materialize_apis_v1()
)
BOUNDARY_MODULES: Mapping[str, frozenset[str]] = _materialize_modules_v1()
BOUNDARY_SOURCE_OWNERS: Mapping[str, str] = _materialize_source_owners_v1()
READ_OPERATIONS = _syntax.READ_OPERATIONS
WRITE_OPERATIONS = _syntax.WRITE_OPERATIONS
PROCESS_CALLS = _syntax.PROCESS_CALLS
DYNAMIC_CODE_LOADER_APIS = _syntax.DYNAMIC_CODE_LOADER_APIS
DYNAMIC_CODE_LOADER_CANONICALS = _syntax.DYNAMIC_CODE_LOADER_CANONICALS
SENSITIVE_FIRST_CLASS_REFERENCES = _syntax.SENSITIVE_FIRST_CLASS_REFERENCES
SENSITIVE_IMPORT_NAMESPACES = _syntax.SENSITIVE_IMPORT_NAMESPACES
SYS_MODULES_EXPOSING_METHODS = _syntax.SYS_MODULES_EXPOSING_METHODS
SYS_MODULES_MUTATING_METHODS = _syntax.SYS_MODULES_MUTATING_METHODS
AUTHENTICATED_EXECUTION_SCOPE = _syntax.AUTHENTICATED_EXECUTION_SCOPE
AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE = (
    _syntax.AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE
)
LIVE_READER_FORBIDDEN = _syntax.LIVE_READER_FORBIDDEN
PUBLISH_CAPABILITIES = _syntax.PUBLISH_CAPABILITIES
FLOW_CAPABILITIES = _syntax.FLOW_CAPABILITIES

_birth_authority = BIRTH_CLOSED_POLICY_V1.authority
_birth_roles = BIRTH_CLOSED_POLICY_V1.roles
BIRTH_CLOSED_SEALED_MODULES = _birth_authority.sealed_modules
BIRTH_CLOSED_OWNER = _birth_authority.owner
BIRTH_CLOSED_COORDINATOR_STORE_OWNERS = frozenset(
    _birth_authority.coordinator_store_owners
)
BIRTH_CLOSED_LEGACY_CAPABILITIES = frozenset(
    _birth_roles.birth_closed_legacy_capabilities
)
BIRTH_CLOSED_EXCEPTIONS = frozenset(BIRTH_CLOSED_POLICY_V1.exception_classes)
BIRTH_CLOSED_EXCEPTION_SCOPES: Mapping[str, str] = (
    _materialize_exception_scopes_v1()
)
BIRTH_CLOSED_EXCEPTION_CAPABILITIES: Mapping[str, frozenset[str]] = (
    _materialize_exception_capabilities_v1()
)
VALID_ROLES = frozenset(_birth_roles.valid_roles)
LIVE_MUTATIONS = frozenset(_birth_roles.live_mutations)
DIRECT_MANIFEST_ALLOWED_ROLES = frozenset(_birth_roles.direct_manifest_roles)
DIRECT_MANIFEST_ALLOWED_PATHS = frozenset(_birth_roles.direct_manifest_paths)
BIRTH_OWNER_ALLOWED_PATHS = frozenset(_birth_roles.birth_owner_paths)
BIRTH_OWNER_FORBIDDEN_CAPABILITIES = frozenset(_birth_roles.birth_owner_forbidden)
OPERATIONAL_BIRTH_FORBIDDEN_CAPABILITIES = frozenset(
    _birth_roles.operational_birth_forbidden
)
BOOTSTRAP_CAPABILITIES = frozenset(_birth_roles.bootstrap_capabilities)
BOOTSTRAP_ALLOWED_ROLES = frozenset(_birth_roles.bootstrap_roles)
LIVE_MUTATION_ALLOWED_ROLES = frozenset(_birth_roles.live_mutation_roles)
DOCUMENTATION_CAPABILITY_EXEMPTIONS = frozenset(
    _birth_roles.documentation_exemptions
)
BIRTH_CLOSED_EXCEPTION_JUSTIFICATIONS: Mapping[str, frozenset[str]] = {
    exception: frozenset(capabilities)
    for exception, capabilities in _birth_roles.exception_justification
}
BIRTH_CLOSED_COORDINATOR_REQUIRED_CAPABILITIES = frozenset(
    _birth_roles.coordinator_capabilities
)
BOUNDARY_ENTRY_KEYS = frozenset(_birth_roles.boundary_entry_keys)

AUTHORING_NAME_REGEX_V1 = _syntax.AUTHORING_NAME_REGEX_V1
AMBIGUOUS_AUTHORING_ARGUMENT_REGEX_V1 = (
    _syntax.AMBIGUOUS_AUTHORING_ARGUMENT_REGEX_V1
)
STORE_NAME_REGEX_V1 = _syntax.STORE_NAME_REGEX_V1
CONTRACT_SCOPE_REGEX_V1 = _syntax.CONTRACT_SCOPE_REGEX_V1
GENERIC_PATH_NAME_REGEX_V1 = _syntax.GENERIC_PATH_NAME_REGEX_V1
_AUTHORING_NAME_RE = _syntax._AUTHORING_NAME_RE
_AMBIGUOUS_AUTHORING_ARGUMENT_RE = _syntax._AMBIGUOUS_AUTHORING_ARGUMENT_RE
_STORE_NAME_RE = _syntax._STORE_NAME_RE
_CONTRACT_SCOPE_RE = _syntax._CONTRACT_SCOPE_RE
_GENERIC_PATH_NAME_RE = _syntax._GENERIC_PATH_NAME_RE


__all__ = [
    "AMBIGUOUS_AUTHORING_ARGUMENT_REGEX_V1", "AUTHENTICATED_EXECUTION_SCOPE",
    "AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE", "AUTHORING_FILES",
    "AUTHORING_NAME_REGEX_V1", "BIRTH_CLOSED_EXCEPTION_CAPABILITIES",
    "BIRTH_CLOSED_EXCEPTION_JUSTIFICATIONS", "BIRTH_CLOSED_EXCEPTION_SCOPES",
    "BIRTH_CLOSED_EXCEPTIONS", "BIRTH_CLOSED_GUARD_VERSION",
    "BIRTH_CLOSED_LEGACY_CAPABILITIES", "BIRTH_CLOSED_OWNER",
    "BIRTH_CLOSED_POLICY_V1", "BIRTH_CLOSED_SCHEMA",
    "BIRTH_CLOSED_SEALED_MODULES", "BIRTH_CLOSED_COORDINATOR_STORE_OWNERS",
    "BIRTH_CLOSED_COORDINATOR_REQUIRED_CAPABILITIES", "BIRTH_OWNER_ALLOWED_PATHS",
    "BIRTH_OWNER_FORBIDDEN_CAPABILITIES", "BOOTSTRAP_ALLOWED_ROLES",
    "BOOTSTRAP_CAPABILITIES", "BOUNDARY_APIS", "BOUNDARY_ENTRY_KEYS",
    "BOUNDARY_LIMITS_V1",
    "BOUNDARY_MODULES", "BOUNDARY_SOURCE_OWNERS", "CONTRACT_SCOPE_REGEX_V1",
    "DYNAMIC_CODE_LOADER_APIS", "DYNAMIC_CODE_LOADER_CANONICALS",
    "DIRECT_MANIFEST_ALLOWED_PATHS", "DIRECT_MANIFEST_ALLOWED_ROLES",
    "DOCUMENTATION_CAPABILITY_EXEMPTIONS", "FLOW_CAPABILITIES",
    "GENERIC_PATH_NAME_REGEX_V1", "LIVE_MUTATIONS",
    "LIVE_MUTATION_ALLOWED_ROLES", "LIVE_READER_FORBIDDEN",
    "MAX_BOUNDARY_AST_DEPTH",
    "MAX_BOUNDARY_AST_NODES", "MAX_BOUNDARY_CALLS", "MAX_BOUNDARY_SCOPES",
    "MAX_BOUNDARY_SOURCE_BYTES", "MAX_BOUNDARY_SOURCE_FILES",
    "MAX_BOUNDARY_TOTAL_AST_NODES", "MAX_BOUNDARY_TOTAL_SOURCE_BYTES",
    "OPERATIONAL_BIRTH_FORBIDDEN_CAPABILITIES", "PROCESS_CALLS",
    "PUBLISH_CAPABILITIES", "READ_OPERATIONS", "SCAN_ROOTS",
    "SCHEMA", "SENSITIVE_FIRST_CLASS_REFERENCES",
    "SENSITIVE_IMPORT_NAMESPACES", "STORE_NAME_REGEX_V1",
    "SYS_MODULES_EXPOSING_METHODS", "SYS_MODULES_MUTATING_METHODS",
    "VALID_ROLES", "WRITE_OPERATIONS", "birth_closed_inventory_value_v1",
]
