"""Image-index outcomes and bounded diagnostics, independent of the LRE core."""
from __future__ import annotations

from collections.abc import Mapping

FAILURE_MARKER = "IMAGE_NOT_INDEXED"
DECODE_FAILURE_CODES = frozenset({"image_decode_failed", "image_format_unreadable"})
# These are attempt failures, never accepted negative image records. The
# general runner applies its frozen retry/usage policy to the declared class.
DESCRIPTION_FAILURE_CLASSES = {
    "image_description_unavailable": "executor_transient",
    "image_description_truncated": "executor_transient",
    "image_description_invalid": "executor_transient",
    "image_description_empty": "executor_transient",
    "image_description_schema_invalid": "capability_unavailable",
}
_DESCRIPTION_KEYS = {
    "image_decode_failed": "MSG_IMAGE_INDEX_DECODE_FAILED",
    "image_format_unreadable": "MSG_IMAGE_INDEX_FORMAT_UNREADABLE",
}


def description_failure_code(observation: Mapping) -> str | None:
    """Retain a closed cause, never provider exception text or source paths."""
    reason = observation.get("_vlm_error")
    if reason:
        if reason == "output_truncated":
            return "image_description_truncated"
        if reason == "response_schema_invalid":
            return "image_description_schema_invalid"
        if isinstance(reason, str) and reason in {
            "invalid_response_type", "invalid_json_text", "no_json_found",
            "not_dict", "response_schema_mismatch", "invalid_keywords", "resp_unparseable",
        }:
            return "image_description_invalid"
        return "image_description_unavailable"
    description = observation.get("description")
    if not isinstance(description, str):
        return "image_description_invalid"
    if not description.strip():
        return "image_description_empty"
    return None


def is_not_indexed(entry: Mapping) -> bool:
    """Machine fields are authoritative; arbitrary description text is not."""
    return (entry.get("indexing_status") == "not_indexed"
            and isinstance(entry.get("indexing_error_code"), str)
            and entry["indexing_error_code"] in DECODE_FAILURE_CODES)


def failure_description(code: str) -> str:
    from messages import get

    if code not in DECODE_FAILURE_CODES:
        raise ValueError("unsupported image indexing outcome")
    return f"{FAILURE_MARKER}:{code} {get(_DESCRIPTION_KEYS[code])}"
