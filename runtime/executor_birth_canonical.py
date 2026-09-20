"""Closed canonical-document profiles shared by Executor Birth protocols."""
from __future__ import annotations

import json


class CanonicalDocumentError(ValueError):
    """Input is outside the bounded canonical ASCII JSON profile."""

    def __init__(self, detail: str) -> None:
        self.code = "birth_canonical_document_invalid"
        self.detail = detail
        super().__init__(self.code)


def _pairs_v1(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise CanonicalDocumentError("duplicate_key")
        result[key] = value
    return result


def encode_canonical_ascii_v1(value: object) -> bytes:
    """Encode JSON with one deterministic ASCII representation."""
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CanonicalDocumentError("encode") from exc


def decode_canonical_ascii_v1(raw: bytes, *, maximum: int) -> object:
    """Decode bounded JSON and reject duplicates and non-canonical bytes."""
    if (
        type(raw) is not bytes or type(maximum) is not int
        or maximum <= 0 or not raw or len(raw) > maximum
    ):
        raise CanonicalDocumentError("size")
    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs_v1)
    except CanonicalDocumentError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanonicalDocumentError("decode") from exc
    if encode_canonical_ascii_v1(value) != raw:
        raise CanonicalDocumentError("representation")
    return value


__all__ = [
    "CanonicalDocumentError", "decode_canonical_ascii_v1",
    "encode_canonical_ascii_v1",
]
