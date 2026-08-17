"""Pure offline extraction from raw model JSON bytes.

This module has no query parameter, transport, oracle or gold dependency.
"""
from __future__ import annotations

import base64
from hashlib import sha256
from typing import Any

from intent_shadow_io import (
    DEFAULT_OFFLINE_LIMITS,
    StrictJsonError,
    TechnicalLimits,
    canonical_json_bytes,
    strict_json_loads,
)
from intent_shadow_normalize import (
    NormalizationError,
    continuation_json,
    normalize_document,
    normalized_document_json,
    project_normalized,
    semantic_document_json,
)
from intent_shadow_registry import CONTRACT_VERSION, registry_payload_sha256, validate_registry_document
from intent_shadow_types import (
    ExtractionEnvelope,
    TechnicalFailure,
    Unrepresentable,
    ValidationIssue,
    ValidationResult,
)
from intent_shadow_validate import validate_model_document


def _technical_envelope(
    *,
    raw_model_output: bytes,
    raw_sha256: str,
    registry_sha256: str,
    code: str,
    path: str,
    message: str,
    decoded: bytes | None,
) -> ExtractionEnvelope:
    failure = TechnicalFailure(code, path, message)
    validation = ValidationResult(
        False,
        (ValidationIssue("TECHNICAL_INVALID", path, code),),
    )
    return ExtractionEnvelope(
        CONTRACT_VERSION,
        registry_sha256,
        raw_model_output,
        raw_sha256,
        decoded,
        failure,
        validation,
        None,
        None,
    )


def extract_raw_json(
    raw_model_output: bytes,
    registry: dict[str, Any],
    *,
    limits: TechnicalLimits = DEFAULT_OFFLINE_LIMITS,
) -> ExtractionEnvelope:
    if type(raw_model_output) is not bytes:
        raise TypeError("raw_model_output must be exact bytes")
    validate_registry_document(registry)
    registry_sha = registry_payload_sha256(registry)
    raw_sha = sha256(raw_model_output).hexdigest()
    try:
        document = strict_json_loads(raw_model_output, limits=limits)
    except StrictJsonError as exc:
        return _technical_envelope(
            raw_model_output=raw_model_output,
            raw_sha256=raw_sha,
            registry_sha256=registry_sha,
            code=exc.code,
            path=exc.path,
            message=exc.message,
            decoded=None,
        )
    except Exception as exc:  # final totality boundary for raw model bytes
        return _technical_envelope(
            raw_model_output=raw_model_output,
            raw_sha256=raw_sha,
            registry_sha256=registry_sha,
            code="JSON_INTERNAL_FAILURE",
            path="$",
            message=type(exc).__name__,
            decoded=None,
        )
    try:
        decoded = canonical_json_bytes(document)
        validation = validate_model_document(document, registry)
    except Exception as exc:  # total boundary for hostile but bounded JSON
        return _technical_envelope(
            raw_model_output=raw_model_output,
            raw_sha256=raw_sha,
            registry_sha256=registry_sha,
            code="VALIDATION_INTERNAL_FAILURE",
            path="$",
            message=type(exc).__name__,
            decoded=None,
        )
    if not validation.valid:
        return ExtractionEnvelope(
            CONTRACT_VERSION,
            registry_sha,
            raw_model_output,
            raw_sha,
            decoded,
            None,
            validation,
            None,
            None,
        )
    try:
        normalization = normalize_document(document, registry)
        projection = project_normalized(normalization)
    except Exception as exc:  # total boundary; normalization never becomes abstention
        code = (
            "NORMALIZATION_FAILURE"
            if isinstance(exc, NormalizationError)
            else "NORMALIZATION_INTERNAL_FAILURE"
        )
        return _technical_envelope(
            raw_model_output=raw_model_output,
            raw_sha256=raw_sha,
            registry_sha256=registry_sha,
            code=code,
            path="$",
            message=type(exc).__name__,
            decoded=decoded,
        )
    return ExtractionEnvelope(
        CONTRACT_VERSION,
        registry_sha,
        raw_model_output,
        raw_sha,
        decoded,
        None,
        validation,
        normalization,
        projection,
    )


def envelope_status(envelope: ExtractionEnvelope) -> str:
    if envelope.technical_failure is not None:
        return "technical_invalid"
    if not envelope.validation_result.valid:
        return "document_invalid"
    if envelope.normalization_result is None or envelope.projection_result is None:
        return "projection_invalid"
    if isinstance(envelope.projection_result.document, Unrepresentable):
        return "valid_unrepresentable"
    return "valid_representable"


def envelope_json(envelope: ExtractionEnvelope) -> dict[str, Any]:
    normalization = envelope.normalization_result
    projection = envelope.projection_result
    return {
        "contract_version": envelope.contract_version,
        "registry_sha256": envelope.registry_sha256,
        "status": envelope_status(envelope),
        "raw_model_output_b64": base64.b64encode(envelope.raw_model_output).decode("ascii"),
        "raw_model_output_sha256": envelope.raw_model_output_sha256,
        "decoded_document": (
            strict_json_loads(envelope.decoded_document_canonical)
            if envelope.decoded_document_canonical is not None
            else None
        ),
        "technical_failure": (
            {
                "code": envelope.technical_failure.code,
                "path": envelope.technical_failure.path,
                "message": envelope.technical_failure.message,
            }
            if envelope.technical_failure is not None
            else None
        ),
        "validation_result": {
            "valid": envelope.validation_result.valid,
            "issues": [
                {"code": issue.code, "path": issue.path, "message": issue.message}
                for issue in envelope.validation_result.issues
            ],
        },
        "normalization_result": (
            {
                "normalized_document": normalized_document_json(normalization.document),
                "normalized_document_sha256": normalization.normalized_document_sha256,
                "continuations": [
                    continuation_json(continuation)
                    for continuation in normalization.continuations
                ],
            }
            if normalization is not None
            else None
        ),
        "projection_result": (
            {
                "semantic_document": semantic_document_json(projection.document),
                "semantic_sha256": projection.semantic_sha256,
            }
            if projection is not None
            else None
        ),
    }
