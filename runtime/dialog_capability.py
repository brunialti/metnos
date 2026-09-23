"""Authenticated, short-lived capabilities for one HTTP dialog form."""
from __future__ import annotations

import hashlib
import hmac
import time


TTL_S = 15 * 60


def sign(dialog_id: str, admin_key: str, *, now: int | None = None) -> str:
    if not admin_key:
        return ""
    exp = int(now if now is not None else time.time()) + TTL_S
    payload = f"dialog-v1:{dialog_id}:{exp}"
    digest = hmac.new(admin_key.encode("utf-8"), payload.encode("utf-8"),
                      hashlib.sha256).hexdigest()
    return f"{exp}.{digest}"


def verify(dialog_id: str, token: str, admin_key: str, *,
           now: int | None = None) -> bool:
    if not token or not admin_key or len(token) > 160:
        return False
    try:
        exp_s, digest = token.split(".", 1)
        exp = int(exp_s)
    except (TypeError, ValueError):
        return False
    current = int(now if now is not None else time.time())
    if exp < current or exp > current + TTL_S + 60:
        return False
    payload = f"dialog-v1:{dialog_id}:{exp}"
    expected = hmac.new(admin_key.encode("utf-8"), payload.encode("utf-8"),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)
