# SPDX-License-Identifier: MIT
"""Short-lived, exact user actions signed by the authenticated HTTP dialog.

The executor-facing broker is not an authority for human clicks. This proof
never enters a page, model, executor result, URL or browser response.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

TTL_S = 30


def _material(action: dict, expires: int) -> bytes:
    return ("browser-user-v1\n" + str(expires) + "\n" + json.dumps(
        action, sort_keys=True, separators=(",", ":"), allow_nan=False)).encode()


def sign(action: dict, key: str) -> str:
    if not key:
        raise ValueError("user control signing key missing")
    expires = int(time.time()) + TTL_S
    digest = hmac.new(key.encode(), _material(action, expires), hashlib.sha256)
    return f"{expires}.{digest.hexdigest()}"


def verify(action: dict, proof: str, key: str) -> bool:
    try:
        expires_s, signature = proof.split(".")
        expires = int(expires_s)
        now = int(time.time())
        if not key or not now <= expires <= now + TTL_S:
            return False
        expected = hmac.new(key.encode(), _material(action, expires), hashlib.sha256)
        return hmac.compare_digest(signature, expected.hexdigest())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def instance_key() -> str:
    import config as C
    try:
        return (C.PATH_USER_CONFIG / "admin.key").read_text().strip()
    except OSError:
        return ""
