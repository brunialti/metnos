from __future__ import annotations

import pytest

from generated_executor_contract import (
    GeneratedContractError,
    generated_contract_context,
    validate_generated_manifest_text,
    transition_generated_manifest_text,
)


def _manifest(*, lifecycle: str = "synthesized", policy: str | None = None) -> str:
    context = generated_contract_context(lifecycle=lifecycle)
    return "\n".join((
        context["generated_header_toml"],
        'name = "find_files"',
        'version = "0.1.0"',
        'description = { it = "SCOPO: x. PATTERN: x. NON: x. OUT: x." }',
        '[code]',
        'files = ["find_files.py"]',
        '[args]',
        'type = "object"',
        'required = []',
        policy or context["execution_policy_toml"],
        "",
    ))


def test_generated_contract_is_shared_and_serial() -> None:
    manifest = validate_generated_manifest_text(
        _manifest(), expected_lifecycle="synthesized")
    assert manifest["execution"]["parallelism_class"] == 0
    assert manifest["execution"]["equivalence_gate"] == "unverified"


def test_generated_contract_rejects_lifecycle_or_policy_drift() -> None:
    with pytest.raises(GeneratedContractError, match="lifecycle"):
        validate_generated_manifest_text(
            _manifest(lifecycle="proposed"),
            expected_lifecycle="synthesized",
        )
    unsafe = "\n".join((
        "[execution]",
        'effect = "read_only"',
        'parallelism_class = 2',
        'resource_class = "local_io"',
        'concurrency_key = "none"',
        'equivalence_gate = "verified"',
    ))
    with pytest.raises(GeneratedContractError, match="serial execution policy"):
        validate_generated_manifest_text(
            _manifest(policy=unsafe), expected_lifecycle="synthesized")


def _active_ready_manifest() -> str:
    context = generated_contract_context(lifecycle="synthesized")
    return "\n".join((
        context["generated_header_toml"],
        'name = "find_files"',
        'version = "0.1.0"',
        'platforms = ["linux"]',
        'description = { it = "SCOPO: legge. PATTERN: trova. NON: scrive. OUT: risultati.", en = "SCOPO: reads. PATTERN: finds. NON: writes. OUT: results." }',
        '[[capabilities]]',
        'name = "fs:read"',
        'hint = ["arg:paths"]',
        '[code]',
        'files = ["find_files.py"]',
        'digest = "sha256:' + "a" * 64 + '"',
        '[args]',
        'type = "object"',
        'required = ["paths"]',
        '[args.properties.paths]',
        'type = "array"',
        'description = { it = "Percorsi.", en = "Paths." }',
        '[output]',
        'schema_inline = "{ok: bool, entries: list}"',
        '[presentation]',
        'default_view = "list"',
        '[presentation.list]',
        'columns = [{ key = "item", source = "$entry" }]',
        '[[tests]]',
        'name = "empty"',
        'input = { paths = [] }',
        'expect = { ok = true }',
        context["execution_policy_toml"],
        "",
    ))


def test_transition_changes_only_lifecycle_after_active_profile_passes() -> None:
    candidate = _active_ready_manifest()

    active, manifest = transition_generated_manifest_text(
        candidate, expected_lifecycle="synthesized")

    assert 'lifecycle = "active"' in active
    assert active.replace('lifecycle = "active"', 'lifecycle = "synthesized"') == candidate
    assert manifest["lifecycle"] == "active"


def test_transition_refuses_candidate_that_is_not_active_ready() -> None:
    with pytest.raises(GeneratedContractError, match="active admission failed"):
        transition_generated_manifest_text(
            _manifest(), expected_lifecycle="synthesized")
