"""Deterministic identities for the RM-0008 executor Birth boundary.

This module is deliberately side-effect free.  It accepts only already copied
bytes and never opens an authoring path or calls the contract publisher.
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Mapping, TypeAlias

from code_file_paths import PortableCodePathError, validate_portable_code_files
from manifest_inventory import ContractId


CANDIDATE_DOMAIN_V1 = b"metnos.executor-birth.candidate/v1\0"
SEMANTIC_CORE_DOMAIN_V1 = b"metnos.executor-birth.semantic-core/v1\0"
ADMISSION_CONTEXT_DOMAIN_V1 = b"metnos.executor-birth.admission-context/v1\0"
IDENTITY_SCHEMA_VERSION = 1
SEMANTIC_PROJECTION_VERSION = 1
LINGUISTIC_SURFACE_PATHS_VERSION = 1

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTEXT_NAMES = (
    "standard", "linter", "vocabulary", "authority_registry",
    "sandbox_registry", "property_catalog", "runner", "review_policy",
    "template_allowlist", "primitive_allowlist", "dependency_allowlist",
)


class ExecutorOrigin(str, Enum):
    CORE = "core"
    BUILTIN = "builtin"
    HUMAN = "human"
    IMPORTED = "imported"
    SYNTHESIZED = "synthesized"


class RevisionAuthor(str, Enum):
    MODEL = "model"
    HUMAN = "human"
    IMPORTER = "importer"
    MAINTENANCE = "maintenance"


class IdentityError(ValueError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


FramedValue: TypeAlias = None | str | int | bool | list["FramedValue"] | dict[str, "FramedValue"]


def _u64(value: int) -> bytes:
    return value.to_bytes(8, "big", signed=False)


def _signed_integer(value: int) -> bytes:
    if value == 0:
        return b"\0"
    size = max(1, (value.bit_length() + 8) // 8)
    payload = value.to_bytes(size, "big", signed=True)
    while len(payload) > 1 and (
        (payload[0] == 0 and payload[1] < 0x80)
        or (payload[0] == 0xFF and payload[1] >= 0x80)
    ):
        payload = payload[1:]
    return payload


def encode_framed_v1(value: FramedValue) -> bytes:
    """Encode the normative V1 typed, length-delimited wire format."""
    if value is None:
        tag, payload = b"n", b""
    elif type(value) is bool:
        tag, payload = b"b", b"\x01" if value else b"\x00"
    elif type(value) is int:
        tag, payload = b"i", _signed_integer(value)
    elif isinstance(value, str):
        tag, payload = b"s", value.encode("utf-8")
    elif isinstance(value, list):
        tag = b"a"
        payload = _u64(len(value)) + b"".join(encode_framed_v1(item) for item in value)
    elif isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise IdentityError("semantic_core_type_unsupported", "map key")
        items = sorted(value.items(), key=lambda item: item[0].encode("utf-8"))
        tag = b"m"
        payload = _u64(len(items)) + b"".join(
            encode_framed_v1(key) + encode_framed_v1(item) for key, item in items
        )
    else:
        raise IdentityError("semantic_core_type_unsupported", type(value).__name__)
    return tag + _u64(len(payload)) + payload


def _identity(domain: bytes, value: FramedValue) -> str:
    return "sha256:" + hashlib.sha256(domain + encode_framed_v1(value)).hexdigest()


def _plain(value: object, *, path: str = "") -> FramedValue:
    if value is None or isinstance(value, str) or type(value) in {bool, int}:
        return value
    if isinstance(value, list):
        return [_plain(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        result: dict[str, FramedValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise IdentityError("semantic_core_type_unsupported", path)
            result[key] = _plain(item, path=f"{path}.{key}" if path else key)
        return result
    raise IdentityError("semantic_core_type_unsupported", path or type(value).__name__)


def _parse_manifest(manifest_bytes: bytes) -> dict[str, FramedValue]:
    try:
        parsed = tomllib.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise IdentityError("birth_request_invalid", "manifest_toml") from exc
    plain = _plain(parsed)
    if not isinstance(plain, dict):  # pragma: no cover - TOML root is a map
        raise IdentityError("birth_request_invalid", "manifest_root")
    return plain


def _parse_language_state(state_bytes: bytes) -> FramedValue:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise IdentityError("birth_request_invalid", "language_state_duplicate")
            result[key] = value
        return result
    try:
        parsed = json.loads(state_bytes.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityError("birth_request_invalid", "language_state_json") from exc
    return _plain(parsed, path="language_state")


def _validate_code_files(
    manifest: Mapping[str, FramedValue], code_files: Mapping[str, bytes],
) -> dict[str, bytes]:
    code = manifest.get("code")
    declared = code.get("files") if isinstance(code, dict) else None
    if not isinstance(declared, list) or not declared or any(not isinstance(x, str) for x in declared):
        raise IdentityError("candidate_path_invalid", "code.files")
    if len(declared) != len(set(declared)):
        raise IdentityError("candidate_path_invalid", "duplicate")
    try:
        validate_portable_code_files(declared)
    except PortableCodePathError as exc:
        raise IdentityError("candidate_path_invalid", str(exc)) from exc
    # Keep this local POSIX round-trip assertion even though the portable wire
    # grammar is stricter: it documents the identity boundary's own invariant.
    for path in declared:
        assert isinstance(path, str)
        if PurePosixPath(path).as_posix() != path:
            raise IdentityError("candidate_path_invalid", path)
    if any(not isinstance(path, str) for path in code_files):
        raise IdentityError("candidate_path_invalid", "code_files key")
    supplied = set(code_files)
    expected = set(declared)
    if expected - supplied:
        raise IdentityError("candidate_file_missing", sorted(expected - supplied)[0])
    if supplied - expected:
        raise IdentityError("candidate_file_extra", sorted(supplied - expected)[0])
    if any(not isinstance(payload, bytes) for payload in code_files.values()):
        raise IdentityError("birth_request_invalid", "code_file_bytes")
    return dict(sorted(code_files.items(), key=lambda item: item[0].encode("utf-8")))


@dataclass(frozen=True, slots=True)
class CandidateIdentityInput:
    contract_id: ContractId
    manifest_bytes: bytes
    language_state_bytes: bytes
    code_files: Mapping[str, bytes]
    executor_origin: ExecutorOrigin
    revision_authorship: RevisionAuthor
    objective_hash: str


@dataclass(frozen=True, slots=True)
class CandidateIdentities:
    candidate_id: str
    semantic_core_id: str
    admission_context_id: str


def _candidate_projection(value: CandidateIdentityInput) -> tuple[dict[str, FramedValue], dict[str, bytes]]:
    manifest = _parse_manifest(value.manifest_bytes)
    if "birth" in manifest:
        raise IdentityError("birth_request_invalid", "producer_birth_block")
    if not isinstance(value.contract_id, ContractId):
        raise IdentityError("birth_request_invalid", "contract_id")
    if not isinstance(value.executor_origin, ExecutorOrigin) or not isinstance(
        value.revision_authorship, RevisionAuthor
    ):
        raise IdentityError("birth_request_invalid", "provenance")
    if not isinstance(value.objective_hash, str) or not _DIGEST_RE.fullmatch(value.objective_hash):
        raise IdentityError("birth_request_invalid", "objective_hash")
    return manifest, _validate_code_files(manifest, value.code_files)


def candidate_id(value: CandidateIdentityInput) -> str:
    manifest, files = _candidate_projection(value)
    payload: dict[str, FramedValue] = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "contract_id": value.contract_id.value,
        "manifest": manifest,
        "language_state": _parse_language_state(value.language_state_bytes),
        "code_files": {path: payload.hex() for path, payload in files.items()},
        "executor_origin": value.executor_origin.value,
        "revision_authorship": value.revision_authorship.value,
        "objective_hash": value.objective_hash,
    }
    return _identity(CANDIDATE_DOMAIN_V1, payload)


# Closed V1 manifest grammar.  Named maps are represented by the ``*`` child.
_SCHEMA_NODE: dict[str, object] = {}
_SCHEMA_NODE.update({
    "type": None, "description": {"*": None}, "enum": None, "const": None,
    "default": None, "required": None, "requires_one_of": None,
    "minimum": None, "maximum": None, "minLength": None, "maxLength": None,
    "minItems": None, "maxItems": None, "uniqueItems": None, "pattern": None,
    "format": None, "runtime_resolved": None, "additionalProperties": None,
    "runtime_source": None, "forbid_placeholder_values": None,
    "source_cardinality": None, "from_step_alternatives": None,
    "complete_value": None, "from_entries_complete": None,
    "from_entries_key": None, "from_entries_required": None,
    "from_result_key": None, "pipeline_role": None, "semantic_type": None,
    "properties": {"*": _SCHEMA_NODE}, "patternProperties": {"*": _SCHEMA_NODE},
    "dependentSchemas": {"*": _SCHEMA_NODE}, "items": _SCHEMA_NODE,
    "prefixItems": [_SCHEMA_NODE], "allOf": [_SCHEMA_NODE],
    "anyOf": [_SCHEMA_NODE], "oneOf": [_SCHEMA_NODE], "not": _SCHEMA_NODE,
})
_FREE_MAP = {"*": None}
MANIFEST_FIELD_GRAMMAR_V1: dict[str, object] = {
    "manifest_format": None, "executor_standard": None, "name": None,
    "version": None, "author": None, "affinity": None, "revertible": None,
    "reversible": None, "reverse_pattern": None, "lifecycle": None,
    "critical": None, "intelligence": None, "platforms": None,
    "timeout_s": None, "superseded_by": None, "deprecated_at": None,
    "deprecation_ttl_hours": None, "origin": None,
    "description": {"*": None}, "args": _SCHEMA_NODE,
    "code": {"files": None, "digest": None},
    "output": {"schema_inline": None},
    "execution": {"effect": None, "parallelism_class": None, "resource_class": None,
                  "concurrency_key": None, "equivalence_gate": None},
    "placement": {"scope": None, "min_sandbox": None, "device_ok": None},
    "sandbox": {"network_allowed": None, "fs_read": None, "fs_write": None,
                "exec_allowed": None},
    "provenance": {"skill_id": None, "imported_from": None,
                   "source_version": None, "source_sha256": None,
                   "imported_at": None},
    "undo": {"outcome": None, "remedy": None, "remedy_executor": None,
             "remedy_args_fixed": _FREE_MAP, "remedy_args_from": None,
             "remedy_prompt_key": None},
    "capabilities": [{"name": None, "hint": None, "outbound_args": None,
                       "path_args": None, "parent_path_args": None,
                       "when": {"arg": None, "nonempty": None,
                                "values": None, "any_item_has": None}}],
    "tests": [{"name": None, "setup": None, "teardown": None,
               "reference": None, "input": None, "expect": None,
               "expect_full": None, "env": _FREE_MAP,
               "equivalence_runs": None}],
    "presentation": {
        "default_view": None,
        "list": {"mode": None, "columns": [{"key": None, "source": None,
                    "type": None, "fallback": None, "label_key": None,
                    "derived_by": None, "cell_max": None, "nowrap": None}],
                 "max_rows": None, "max_chars": None, "overflow": None},
    },
    "planning": {"companions": None, "object_aliases": None},
    "credential_form": {
        "kind": None, "label_key": None, "binding_prefix": None,
        "detect_prefixes": None,
        "fields": [{"name": None, "prompt_key": None, "input": None,
                    "required": None, "secret": None, "default": None}],
    },
    "managed_dependencies": [{
        "key": None, "package_id": None, "interface": None, "mode": None,
        "entry_type": None, "assembly": None, "domains_arg": None,
        "sensor_types_arg": None,
    }],
}


def _check_grammar(value: FramedValue, grammar: object, path: tuple[str, ...] = ()) -> None:
    if grammar is None:
        return
    if isinstance(grammar, list):
        if not isinstance(value, list):
            raise IdentityError("semantic_core_unknown_field", ".".join(path))
        for index, item in enumerate(value):
            _check_grammar(item, grammar[0], (*path, str(index)))
        return
    if not isinstance(grammar, dict) or not isinstance(value, dict):
        raise IdentityError("semantic_core_unknown_field", ".".join(path))
    wildcard = grammar.get("*")
    for key, item in value.items():
        if key in grammar:
            child = grammar[key]
        elif "*" in grammar:
            child = wildcard
        else:
            raise IdentityError("semantic_core_unknown_field", ".".join((*path, key)))
        _check_grammar(item, child, (*path, key))


_SCHEMA_NAMED_CHILDREN = ("properties", "patternProperties", "dependentSchemas")
_SCHEMA_SINGLE_CHILDREN = ("items", "not")
_SCHEMA_LIST_CHILDREN = ("prefixItems", "allOf", "anyOf", "oneOf")


def _strip_schema_descriptions(node: FramedValue) -> FramedValue:
    """Remove prose only at nodes in the V1 JSON-Schema path grammar."""
    if not isinstance(node, dict):
        return node
    result = dict(node)
    if isinstance(result.get("description"), dict):
        result.pop("description")
    for keyword in _SCHEMA_NAMED_CHILDREN:
        children = result.get(keyword)
        if isinstance(children, dict):
            result[keyword] = {
                name: _strip_schema_descriptions(child)
                for name, child in children.items()
            }
    for keyword in _SCHEMA_SINGLE_CHILDREN:
        child = result.get(keyword)
        if isinstance(child, dict):
            result[keyword] = _strip_schema_descriptions(child)
    for keyword in _SCHEMA_LIST_CHILDREN:
        children = result.get(keyword)
        if isinstance(children, list):
            result[keyword] = [_strip_schema_descriptions(child) for child in children]
    return result


def _remove_linguistic_surfaces(manifest: dict[str, FramedValue]) -> dict[str, FramedValue]:
    result = dict(manifest)
    if isinstance(result.get("description"), dict):
        result.pop("description")
    if "args" in result:
        result["args"] = _strip_schema_descriptions(result["args"])
    return result


def semantic_core_id(value: CandidateIdentityInput) -> str:
    manifest, files = _candidate_projection(value)
    _check_grammar(manifest, MANIFEST_FIELD_GRAMMAR_V1)
    projection = _remove_linguistic_surfaces(manifest)
    return _identity(SEMANTIC_CORE_DOMAIN_V1, {
        "projection_version": SEMANTIC_PROJECTION_VERSION,
        "linguistic_surface_paths_version": LINGUISTIC_SURFACE_PATHS_VERSION,
        "manifest": projection,
        "code_files": {path: payload.hex() for path, payload in files.items()},
    })


@dataclass(frozen=True, slots=True)
class ContextComponent:
    version: str
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise IdentityError("birth_request_invalid", "context_version")
        if not isinstance(self.digest, str) or not _DIGEST_RE.fullmatch(self.digest):
            raise IdentityError("birth_request_invalid", "context_digest")


@dataclass(frozen=True, slots=True)
class AdmissionContextV1:
    standard: ContextComponent
    linter: ContextComponent
    vocabulary: ContextComponent
    authority_registry: ContextComponent
    sandbox_registry: ContextComponent
    property_catalog: ContextComponent
    runner: ContextComponent
    review_policy: ContextComponent
    template_allowlist: ContextComponent
    primitive_allowlist: ContextComponent
    dependency_allowlist: ContextComponent


def admission_context_id(context: AdmissionContextV1) -> str:
    if not isinstance(context, AdmissionContextV1):
        raise IdentityError("birth_request_invalid", "admission_context")
    components: dict[str, FramedValue] = {}
    for name in _CONTEXT_NAMES:
        component = getattr(context, name)
        if not isinstance(component, ContextComponent):
            raise IdentityError("birth_request_invalid", name)
        components[name] = {"version": component.version, "digest": component.digest}
    return _identity(ADMISSION_CONTEXT_DOMAIN_V1, {
        "schema_version": IDENTITY_SCHEMA_VERSION, "components": components,
    })


def compute_candidate_identities(
    value: CandidateIdentityInput, context: AdmissionContextV1,
) -> CandidateIdentities:
    return CandidateIdentities(
        candidate_id=candidate_id(value),
        semantic_core_id=semantic_core_id(value),
        admission_context_id=admission_context_id(context),
    )
