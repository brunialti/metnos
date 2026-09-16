"""Stable, non-semantic outcomes for photos a decoder could not read."""
from __future__ import annotations

from collections.abc import Mapping

FAILURE_MARKER = "IMAGE_NOT_INDEXED"
DECODE_FAILURE_CODES = frozenset({"image_decode_failed", "image_format_unreadable"})
_DESCRIPTION_KEYS = {
    "image_decode_failed": "MSG_IMAGE_INDEX_DECODE_FAILED",
    "image_format_unreadable": "MSG_IMAGE_INDEX_FORMAT_UNREADABLE",
}


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
