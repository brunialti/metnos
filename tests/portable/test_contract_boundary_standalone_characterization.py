"""Characterization contract for the duplicated standalone boundary engine.

This module intentionally compares the imported guard with the self-contained
copy embedded in the administrative preflight.  It is the extraction baseline:
future refactors may change ownership, but not these observable results.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re

import pytest

import contract_boundary_guard as guard
import executor_birth_admin_preflight as standalone


_CORPUS = {
    "runtime/01_import_alias.py": (
        b"from runtime.sign import sign_executor as approve\n"
        b"def alias_case():\n"
        b"    return approve(None)\n"
    ),
    "runtime/02_dynamic_loader.py": (
        b"from importlib import import_module as load\n"
        b"def dynamic_case():\n"
        b"    return load('runtime.sign')\n"
    ),
    "runtime/03_sys_modules.py": (
        b"import sys\n"
        b"def registry_case():\n"
        b"    return sys.modules['runtime.sign'].sign_executor(None)\n"
    ),
    "runtime/04_path_write.py": (
        b"from pathlib import Path\n"
        b"def write_case(contract_store_root: Path):\n"
        b"    target = contract_store_root / 'active.json'\n"
        b"    target.write_text('fixture', encoding='utf-8')\n"
    ),
}

_GUARD_PIN_PATH = "runtime/contract_boundary_guard.py"
_ADMIN_PIN_PATH = "runtime/executor_birth_admin_preflight.py"
_GUARD_PIN_NAME = b"BIRTH_CLOSED_SOURCE_REVIEW_SHA256"
_ADMIN_PIN_NAME = b"_BIRTH_CLOSED_SOURCE_REVIEW_SHA256"


def _pin_line(name: bytes, digit: bytes) -> bytes:
    return name + b' = "sha256:' + digit * 64 + b'"\n'


def _review_sources(
    guard_digit: bytes = b"1", admin_digit: bytes = b"2",
) -> dict[str, bytes]:
    return {
        **_CORPUS,
        _GUARD_PIN_PATH: _pin_line(_GUARD_PIN_NAME, guard_digit),
        _ADMIN_PIN_PATH: _pin_line(_ADMIN_PIN_NAME, admin_digit),
    }

_EXPECTED_FACT_PAYLOADS = (
    {
        "calls": [],
        "capabilities": ["sign"],
        "closed_dynamic_boundary": False,
        "direct_manifest_dir_access": False,
        "line": 2,
        "path": "runtime/01_import_alias.py",
        "scope": "alias_case",
    },
    {
        "calls": [],
        "capabilities": ["dynamic_boundary_access"],
        "closed_dynamic_boundary": True,
        "direct_manifest_dir_access": False,
        "line": 2,
        "path": "runtime/02_dynamic_loader.py",
        "scope": "dynamic_case",
    },
    {
        "calls": [],
        "capabilities": ["dynamic_boundary_access"],
        "closed_dynamic_boundary": True,
        "direct_manifest_dir_access": False,
        "line": 2,
        "path": "runtime/03_sys_modules.py",
        "scope": "registry_case",
    },
    {
        "calls": ["write_text"],
        "capabilities": ["store_write"],
        "closed_dynamic_boundary": False,
        "direct_manifest_dir_access": False,
        "line": 2,
        "path": "runtime/04_path_write.py",
        "scope": "write_case",
    },
)

_EXPECTED_FINDING_PAYLOADS = (
    {
        "code": "dynamic_boundary_access",
        "message": "boundary authority must use a statically resolved canonical API",
        "scope": "runtime/02_dynamic_loader.py:dynamic_case",
    },
    {
        "code": "dynamic_boundary_access",
        "message": "boundary authority must use a statically resolved canonical API",
        "scope": "runtime/03_sys_modules.py:registry_case",
    },
    {
        "code": "operational_sign_after_cutover",
        "message": "operational flow must call one publisher, never sign_executor",
        "scope": "runtime/01_import_alias.py:alias_case",
    },
    {
        "code": "store_write_outside_boundary",
        "message": "only a reviewed store_owner scope may mutate the publication store",
        "scope": "runtime/04_path_write.py:write_case",
    },
)

_EXPECTED_NORMALIZED_SOURCE_REVIEW_SHA256 = (
    "sha256:117ed968866f3da95963f9d54ac333621f66db66ef218b81d078ba9a8c6aa742"
)

_ESSENTIAL_POLICY_NAMES = (
    "SCHEMA",
    "BIRTH_CLOSED_SCHEMA",
    "BIRTH_CLOSED_GUARD_VERSION",
    "BIRTH_CLOSED_SOURCE_REVIEW_SHA256",
    "BIRTH_CLOSED_SEALED_MODULES",
    "BIRTH_CLOSED_OWNER",
    "BIRTH_CLOSED_COORDINATOR_STORE_OWNERS",
    "BIRTH_CLOSED_EXCEPTION_SCOPES",
    "BIRTH_CLOSED_EXCEPTION_CAPABILITIES",
    "SCAN_ROOTS",
    "AUTHORING_FILES",
    "BOUNDARY_APIS",
    "BOUNDARY_MODULES",
    "BOUNDARY_SOURCE_OWNERS",
    "READ_OPERATIONS",
    "WRITE_OPERATIONS",
    "PROCESS_CALLS",
    "VALID_ROLES",
    "LIVE_MUTATIONS",
)

_LIMIT_NAMES = (
    "SOURCE_FILES",
    "SOURCE_BYTES",
    "TOTAL_SOURCE_BYTES",
    "AST_NODES",
    "TOTAL_AST_NODES",
    "AST_DEPTH",
    "SCOPES",
    "CALLS",
)


def _frozen(value):
    if type(value) is dict:
        return ("dict", tuple((key, _frozen(item)) for key, item in value.items()))
    if type(value) is tuple:
        return ("tuple", tuple(_frozen(item) for item in value))
    if type(value) is list:
        return ("list", tuple(_frozen(item) for item in value))
    if type(value) in (set, frozenset):
        members = tuple(sorted((_frozen(item) for item in value), key=repr))
        return (type(value).__name__, members)
    if isinstance(value, re.Pattern):
        return (
            "regex", type(value).__module__, type(value).__qualname__,
            type(value.pattern).__name__, value.pattern, value.flags,
        )
    return (type(value).__name__, value)


def _serialized_model(value) -> str:
    return json.dumps(asdict(value), sort_keys=True, separators=(",", ":"))


def _serialized_facts(facts) -> tuple[str, ...]:
    return tuple(_serialized_model(fact) for fact in facts)


def _serialized_findings(findings) -> tuple[str, ...]:
    return tuple(_serialized_model(finding) for finding in findings)


def _serialized_payloads(payloads) -> tuple[str, ...]:
    return tuple(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
        for payload in payloads
    )


def _write_corpus(root: Path) -> None:
    for relative, content in _CORPUS.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _inventory_for(facts) -> dict:
    roles = {
        "runtime/01_import_alias.py": "operational_producer",
        "runtime/02_dynamic_loader.py": "administrative_tool",
        "runtime/03_sys_modules.py": "administrative_tool",
        "runtime/04_path_write.py": "administrative_tool",
    }
    return {
        "schema": guard.SCHEMA,
        "scan_roots": list(guard.SCAN_ROOTS),
        "entries": [
            {
                "path": fact.path,
                "scope": fact.scope,
                "role": roles[fact.path],
                "capabilities": list(fact.capabilities),
                "destination": "characterization fixture",
                "phase": "M4",
            }
            for fact in facts
        ],
    }


def test_boundary_models_have_the_same_serialized_contract() -> None:
    fact_values = {
        "path": "runtime/probe.py",
        "scope": "probe",
        "line": 7,
        "capabilities": ("sign", "store_write"),
        "calls": ("approve", "write_text"),
        "direct_manifest_dir_access": True,
        "closed_dynamic_boundary": True,
    }
    finding_values = {
        "code": "characterization",
        "scope": "runtime/probe.py:probe",
        "message": "stable finding payload",
    }
    imported_fact = guard.ScopeFacts(**fact_values)
    standalone_fact = standalone.ScopeFacts(**fact_values)
    imported_finding = guard.Finding(**finding_values)
    standalone_finding = standalone.Finding(**finding_values)

    assert _serialized_model(imported_fact) == _serialized_model(standalone_fact)
    assert imported_fact.key == standalone_fact.key
    assert _serialized_model(imported_finding) == _serialized_model(
        standalone_finding,
    )
    assert str(imported_finding) == str(standalone_finding)


@pytest.mark.parametrize("name", _ESSENTIAL_POLICY_NAMES)
def test_boundary_essential_policy_is_identical(name: str) -> None:
    assert _frozen(getattr(guard, name)) == _frozen(getattr(standalone, name))


@pytest.mark.parametrize("suffix", _LIMIT_NAMES)
def test_boundary_resource_limits_are_identical(suffix: str) -> None:
    assert getattr(guard, f"MAX_BOUNDARY_{suffix}") == getattr(
        standalone, f"MAX_BOUNDARY_{suffix}_V1",
    )


def test_boundary_facts_and_findings_match_for_representative_corpus(
    tmp_path: Path,
) -> None:
    _write_corpus(tmp_path)
    imported_facts = guard.discover(tmp_path)
    standalone_facts = standalone._discover_boundary_from_verified_v1(_CORPUS)

    expected_facts = _serialized_payloads(_EXPECTED_FACT_PAYLOADS)

    assert _serialized_facts(imported_facts) == expected_facts
    assert _serialized_facts(standalone_facts) == expected_facts

    inventory = _inventory_for(imported_facts)
    imported_findings = guard.check(imported_facts, inventory)
    standalone_findings = standalone.check(standalone_facts, inventory)
    serialized = _serialized_findings(imported_findings)

    assert serialized == _serialized_payloads(_EXPECTED_FINDING_PAYLOADS)
    assert _serialized_findings(standalone_findings) == serialized
    assert serialized == tuple(sorted(
        serialized,
        key=lambda payload: (
            json.loads(payload)["code"],
            json.loads(payload)["scope"],
        ),
    ))
    assert {json.loads(payload)["code"] for payload in serialized} == {
        "dynamic_boundary_access",
        "operational_sign_after_cutover",
        "store_write_outside_boundary",
    }


def test_declared_descriptor_store_owner_cannot_hide_writes(tmp_path: Path) -> None:
    relative = "runtime/executor_birth_preflight_attestation_store.py"
    source = (
        b"import os\n"
        b"def direct(fd):\n    os.write(fd, b'x')\n"
        b"def aliased(fd):\n    writer = os.write\n    writer(fd, b'x')\n"
        b"def deferred(fd):\n    effect = lambda: os.fsync(fd)\n    effect()\n"
        b"def linked(directory):\n"
        b"    os.link('a', 'b', src_dir_fd=directory, dst_dir_fd=directory)\n"
    )
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(source)

    imported = guard.discover(tmp_path)
    standalone_facts = standalone._discover_boundary_from_verified_v1({
        relative: source,
    })
    expected = {"aliased", "deferred", "direct", "linked"}
    for facts in (imported, standalone_facts):
        assert {fact.scope for fact in facts} == expected
        assert all(fact.capabilities == ("store_write",) for fact in facts)


def _review_hashes(sources: dict[str, bytes]) -> tuple[str, str]:
    return (
        guard.closed_python_source_review_sha256(sources),
        standalone._closed_python_source_review_sha256_v1(sources),
    )


def test_boundary_source_review_normalizes_only_the_two_exact_bindings() -> None:
    first = _review_hashes(_review_sources(b"1", b"2"))
    second = _review_hashes(_review_sources(b"3", b"4"))
    assert first[0] == first[1]
    assert second[0] == second[1]
    assert first == second
    assert first[0] == _EXPECTED_NORMALIZED_SOURCE_REVIEW_SHA256


def test_pin_like_assignment_outside_targets_remains_reviewed_material() -> None:
    first = _review_sources()
    second = _review_sources()
    first["runtime/pin_probe.py"] = _pin_line(_GUARD_PIN_NAME, b"5")
    second["runtime/pin_probe.py"] = _pin_line(_GUARD_PIN_NAME, b"6")
    first_hashes = _review_hashes(first)
    second_hashes = _review_hashes(second)
    assert first_hashes[0] == first_hashes[1]
    assert second_hashes[0] == second_hashes[1]
    assert first_hashes != second_hashes


def test_admin_target_accepts_the_nonliteral_public_alias_as_material() -> None:
    sources = _review_sources()
    baseline = _review_hashes(sources)
    sources[_ADMIN_PIN_PATH] += (
        _GUARD_PIN_NAME + b" = " + _ADMIN_PIN_NAME + b"\n"
    )
    with_alias = _review_hashes(sources)
    assert with_alias[0] == with_alias[1]
    assert with_alias != baseline


@pytest.mark.parametrize(
    "case",
    [
        "missing_path", "missing_literal", "duplicate",
        "wrong_guard_name", "wrong_admin_name", "extra_wrong_name",
    ],
)
def test_source_review_pin_binding_shape_fails_closed(case: str) -> None:
    sources = _review_sources()
    if case == "missing_path":
        del sources[_GUARD_PIN_PATH]
    elif case == "missing_literal":
        sources[_ADMIN_PIN_PATH] = b"VALUE = 1\n"
    elif case == "duplicate":
        sources[_GUARD_PIN_PATH] *= 2
    elif case == "wrong_guard_name":
        sources[_GUARD_PIN_PATH] = _pin_line(_ADMIN_PIN_NAME, b"1")
    elif case == "wrong_admin_name":
        sources[_ADMIN_PIN_PATH] = _pin_line(_GUARD_PIN_NAME, b"2")
    else:
        sources[_GUARD_PIN_PATH] += _pin_line(_ADMIN_PIN_NAME, b"3")
    with pytest.raises(ValueError, match="pin binding"):
        guard.closed_python_source_review_sha256(sources)
    with pytest.raises(standalone.PreflightError, match="pin binding"):
        standalone._closed_python_source_review_sha256_v1(sources)


def test_pin_assignment_matcher_never_crosses_a_newline() -> None:
    split = _GUARD_PIN_NAME + b'\n= "sha256:' + b"1" * 64 + b'"\n'
    assert guard._SOURCE_REVIEW_PIN_ASSIGNMENT_V1.search(split) is None
    assert standalone._SOURCE_REVIEW_PIN_ASSIGNMENT_V1.search(split) is None
