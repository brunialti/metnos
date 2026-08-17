"""RUN 1 arm B: one strict structured response, byte-preserving, no repair."""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import base64
from typing import Any

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.api import compile_ir
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.compiler import semantic_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.registry_projection import load_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.structured_client import build_request as build_structured_request

from .protocol import (
    LIVE_LIMITS,
    ProtocolError,
    apply_generation_profile,
    canonical_json_bytes,
    strict_json_loads,
    verify_candidate_environment,
)


ARM_ID = "B"
CRITIC_ENABLED = False


@lru_cache(maxsize=1)
def _registry() -> dict[str, Any]:
    verify_candidate_environment()
    return load_projection()


def build_request(query: str, language: str) -> dict[str, Any]:
    request = apply_generation_profile(
        build_structured_request(query, _registry(), language),
    )
    response_format = request.get("response_format")
    if (
        type(response_format) is not dict
        or response_format.get("type") != "json_schema"
        or type(response_format.get("json_schema")) is not dict
        or response_format["json_schema"].get("strict") is not True
        or "grammar" in request
        or "tools" in request
    ):
        raise RuntimeError("arm B structured-output contract mismatch")
    return request


def extract_response(raw_content: bytes) -> dict[str, Any]:
    if type(raw_content) is not bytes:
        raise TypeError("raw_content")
    raw_sha = sha256(raw_content).hexdigest()
    raw_b64 = base64.b64encode(raw_content).decode("ascii")
    try:
        decoded = strict_json_loads(raw_content, LIVE_LIMITS)
    except ProtocolError as exc:
        return {
            "adapter_version": "candidate-v0.2/run1", "status": "technical_invalid",
            "raw_model_output_b64": raw_b64, "raw_model_output_sha256": raw_sha,
            "decoded_document": None, "semantic_document": None,
            "technical_failure": str(exc), "issues": ["TECHNICAL_INVALID"],
            "compilation_sha256": None, "continuation_sha256": [],
            "critic_enabled": False, "repair_attempted": False, "model_calls": 1,
        }
    compilation = compile_ir(decoded, _registry())
    if not compilation.valid:
        return {
            "adapter_version": "candidate-v0.2/run1", "status": "document_invalid",
            "raw_model_output_b64": raw_b64, "raw_model_output_sha256": raw_sha,
            "decoded_document": decoded, "semantic_document": None,
            "technical_failure": None,
            "issues": [
                {"code": issue.code, "path": issue.path, "message": issue.message}
                for issue in compilation.issues
            ],
            "compilation_sha256": None, "continuation_sha256": [],
            "critic_enabled": False, "repair_attempted": False, "model_calls": 1,
        }
    semantic = semantic_projection(compilation)
    status = "valid_unrepresentable" if semantic["kind"] == "unrepresentable" else "valid_representable"
    return {
        "adapter_version": "candidate-v0.2/run1", "status": status,
        "raw_model_output_b64": raw_b64, "raw_model_output_sha256": raw_sha,
        "decoded_document": decoded, "semantic_document": semantic,
        "semantic_sha256": sha256(canonical_json_bytes(semantic)).hexdigest(),
        "technical_failure": None, "issues": [],
        "compilation_sha256": compilation.document_sha256,
        "continuation_sha256": [item.continuation_sha256 for item in compilation.continuations],
        "critic_enabled": False, "repair_attempted": False, "model_calls": 1,
    }
