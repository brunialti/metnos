from __future__ import annotations

import pytest

from generated_executor_contract import (
    GeneratedContractError,
    generated_contract_context,
    validate_generated_manifest_text,
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
