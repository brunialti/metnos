"""Single authority for manifests emitted by every executor generator.

The three generation paths intentionally remain separate because imported
skills, reactive Synt candidates and human-reviewed proposals have different
inputs and lifecycles.  They share this immutable envelope and pre-write gate,
so standard/policy changes are made once and cannot be overridden by LLM text.
"""
from __future__ import annotations

import tomllib

from executor_metadata import (
    DEFAULT_EXECUTION_POLICY,
    generated_execution_policy_toml,
)
from executor_standard import STANDARD_ID, SUPPORTED_MANIFEST_FORMAT


GENERATED_LIFECYCLES = frozenset({"active", "proposed", "synthesized"})


class GeneratedContractError(ValueError):
    pass


def generated_header_toml(*, lifecycle: str = "active") -> str:
    """Render root-level standard identity for a generated manifest."""
    if lifecycle not in GENERATED_LIFECYCLES:
        raise GeneratedContractError(f"unsupported generated lifecycle: {lifecycle}")
    lines = [
        f'manifest_format = "{SUPPORTED_MANIFEST_FORMAT}"',
        f'executor_standard = "{STANDARD_ID}"',
    ]
    if lifecycle != "active":
        lines.append(f'lifecycle = "{lifecycle}"')
    return "\n".join(lines)


def generated_contract_context(*, lifecycle: str = "active") -> dict[str, str]:
    """Context fragments consumed by deterministic and LLM-assisted renderers."""
    return {
        "generated_header_toml": generated_header_toml(lifecycle=lifecycle),
        "execution_policy_toml": generated_execution_policy_toml(),
    }


def validate_generated_manifest_text(
        text: str, *, expected_lifecycle: str = "active") -> dict:
    """Fail closed if a renderer drifts from the runtime-owned envelope.

    This is a generation-integrity gate, not final admission: active manifests
    can still carry a placeholder digest before signing, and candidates can be
    incomplete by design.  The full standard validator remains mandatory at
    admission/signing.
    """
    if expected_lifecycle not in GENERATED_LIFECYCLES:
        raise GeneratedContractError(
            f"unsupported expected lifecycle: {expected_lifecycle}")
    try:
        manifest = tomllib.loads(text)
    except (TypeError, tomllib.TOMLDecodeError) as exc:
        raise GeneratedContractError(f"generated manifest is not TOML: {exc}") from exc

    checks = {
        "manifest_format": SUPPORTED_MANIFEST_FORMAT,
        "executor_standard": STANDARD_ID,
    }
    for field, expected in checks.items():
        if manifest.get(field) != expected:
            raise GeneratedContractError(
                f"generated {field} must be {expected!r}, got {manifest.get(field)!r}")
    actual_lifecycle = str(manifest.get("lifecycle") or "active")
    if actual_lifecycle != expected_lifecycle:
        raise GeneratedContractError(
            f"generated lifecycle must be {expected_lifecycle!r}, "
            f"got {actual_lifecycle!r}")

    raw_policy = manifest.get("execution")
    if raw_policy != DEFAULT_EXECUTION_POLICY:
        raise GeneratedContractError(
            "generated executor must use the canonical serial execution policy")
    return manifest

