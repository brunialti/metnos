"""Length-framed cryptographic digests for Executor Birth protocols."""
from __future__ import annotations

import hashlib
import re


_DIGEST_RE_V1 = re.compile(r"sha256:[0-9a-f]{64}\Z")


class CryptoFramingError(ValueError):
    """A digest domain or payload is outside the closed framing profile."""

    def __init__(self, detail: str) -> None:
        self.code = "birth_crypto_framing_invalid"
        self.detail = detail
        super().__init__(self.code)


def framed_sha256_v1(domain: bytes, payload: bytes) -> str:
    """Hash a domain, an unsigned 64-bit length and exact payload bytes."""
    if (
        type(domain) is not bytes or not domain or len(domain) > 255
        or not domain.endswith(b"\0") or type(payload) is not bytes
        or len(payload) >= 1 << 64
    ):
        raise CryptoFramingError("input")
    framed = domain + len(payload).to_bytes(8, "big") + payload
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def is_framed_sha256_v1(value: object) -> bool:
    """Recognise the sole textual SHA-256 representation used by protocols."""
    return type(value) is str and _DIGEST_RE_V1.fullmatch(value) is not None


__all__ = ["CryptoFramingError", "framed_sha256_v1", "is_framed_sha256_v1"]
