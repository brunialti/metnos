"""Public offline API: analyze natural text or compile an already emitted IR."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from .canonical import StrictJsonError, canonical_json_bytes, strict_json_loads
from .compiler import compile_document
from .critic import MechanicalDisagreementDetector, NoDisagreement, critic_is_allowed
from .projection import contract_hashes
from .registry_projection import load_projection, projection_sha256
from .structured_client import StructuredClient
from .types import (
    AnalysisResult,
    CompilationResult,
    CriticRequest,
    DisagreementSignal,
    Issue,
)


def _json_failure(exc: StrictJsonError) -> CompilationResult:
    message = str(exc)
    if "duplicate key" in message:
        code = "D01_DUPLICATE_KEY"
    elif "non-finite" in message or "overflow" in message:
        code = "D02_NONFINITE"
    else:
        code = "JSON_INVALID"
    return CompilationResult(False, None, None, (), (Issue(code, "$", message),))


def compile_ir(value: bytes | dict[str, Any], registry: dict[str, Any] | None = None) -> CompilationResult:
    selected = registry or load_projection()
    if type(value) is bytes:
        try:
            decoded = strict_json_loads(value)
        except StrictJsonError as exc:
            return _json_failure(exc)
    elif type(value) is dict:
        decoded = value
    else:
        return CompilationResult(
            False,
            None,
            None,
            (),
            (Issue("D03_ROOT_TYPE", "$", "bytes or exact object required"),),
        )
    return compile_document(decoded, selected)


def analyze(
    query: str,
    fake_responses: list[bytes] | tuple[bytes, ...],
    registry: dict[str, Any] | None = None,
    *,
    language_tag: str,
    disagreement_detector: MechanicalDisagreementDetector | None = None,
) -> AnalysisResult:
    selected = registry or load_projection()
    exchange = StructuredClient.from_fake_responses(fake_responses).run(
        query, selected, language_tag,
    )
    compilation = compile_ir(exchange.content, selected)
    detector = disagreement_detector or NoDisagreement()
    signal = detector.detect(query, compilation)
    if type(signal) is not DisagreementSignal:
        raise TypeError("detector must return DisagreementSignal")
    critic_request = None
    if critic_is_allowed(signal, 1):
        hashes = contract_hashes(selected)
        critic_request = CriticRequest(
            query=query,
            primary_ir=exchange.content,
            registry_sha256=projection_sha256(selected),
            prompt_sha256=hashes["prompt_sha256"],
            schema_sha256=hashes["schema_sha256"],
        )
    return AnalysisResult(
        exchange.request_sha256,
        exchange.content_sha256,
        compilation,
        signal,
        critic_request,
        1,
    )
