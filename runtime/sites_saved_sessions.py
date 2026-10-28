"""Saved browser sessions of sites the user logged into (Roberto, 20/10/2026).

Logging in again for every task makes anti-robot checks (reCAPTCHA) escalate
to interactive challenges. A verified login saves the browser state (cookies
and the local storage of the private page's origin), encrypted with the key
of the credentials; the next login first reopens the saved private page and
logs in only when that page asks for it and shows again every control seen
right after the login (``landing``: hashes only, never names). A saved session
is removed with the site's credentials, when it fails, or after MAX_AGE_S.

Files: ``<state>/sites-sessions/<binding>__<owner hash>.age`` (mode 0600),
override ``METNOS_SITES_SESSION_DIR``. Never logged, never returned.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

import config as C
import credentials

MAX_AGE_S = 7 * 24 * 3600
MAX_BYTES = 2 * 1024 * 1024
MAX_LANDING_CONTROLS = 2000
_INFO = b"metnos-site-session-v1"
_CONTROL_HASH = re.compile(r"[0-9a-f]{16}")


def _landing(values) -> list[str]:
    """The valid control hashes of a private landing, or [] when unusable."""
    if not isinstance(values, (list, tuple)) or len(values) > MAX_LANDING_CONTROLS:
        return []
    if not all(isinstance(v, str) and _CONTROL_HASH.fullmatch(v) for v in values):
        return []
    return sorted(set(values))


def _dir() -> Path:
    override = os.getenv("METNOS_SITES_SESSION_DIR")
    return Path(override) if override else C.PATH_USER_STATE / "sites-sessions"


def _file(owner: str, binding: str) -> Path:
    credentials._validate_domain(binding)
    who = hashlib.sha256(str(owner or "").encode("utf-8")).hexdigest()[:16]
    return _dir() / f"{binding}__{who}.age"


def save(owner: str, binding: str, state: dict, verify_url: str, *, credential_digest: str,
         landing, saved_at: float | None = None, now: float | None = None) -> bool:
    """Encrypt and store a context state; False when there is nothing to keep.

    ``credential_digest`` ties the state to the stored credential it came
    from; ``landing`` holds the control hashes of the private page seen right
    after the verified login, without which a restore cannot be verified;
    ``saved_at`` keeps the first save of a reused state, so MAX_AGE_S bounds
    the whole chain.
    """
    now = now if now is not None else time.time()
    landing = _landing(landing)
    if (not isinstance(state, dict) or not credential_digest or not landing
            or not str(verify_url or "").startswith(("https://", "http://"))):
        return False
    payload = {"version": 2, "owner": str(owner), "binding": binding,
               "credential_digest": credential_digest,
               "saved_at": int(saved_at if saved_at is not None else now),
               "verify_url": str(verify_url), "landing": landing,
               "cookies": [c for c in state.get("cookies") or () if isinstance(c, dict)],
               "origins": [o for o in state.get("origins") or () if isinstance(o, dict)]}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if not payload["cookies"] or len(raw) > MAX_BYTES or now - payload["saved_at"] > MAX_AGE_S:
        return False
    directory = _dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    for old in directory.iterdir():
        # States nobody loads again still expire; another session may remove
        # one at the same time.
        try:
            if old.name.endswith(".age") and now - old.stat().st_mtime > MAX_AGE_S:
                old.unlink(missing_ok=True)
        except FileNotFoundError:
            continue
    salt = secrets.token_bytes(16)
    token = Fernet(credentials._derive_fernet_key(salt, info=_INFO)).encrypt(raw)
    path = _file(owner, binding)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(base64.urlsafe_b64encode(salt) + b"\n" + token)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return True


def load(owner: str, binding: str, *, credential_digest: str,
         now: float | None = None) -> dict | None:
    """The saved state without expired cookies, or None (absent, unreadable,
    too old, without a landing, or saved for another owner, site or
    credential)."""
    path = _file(owner, binding)
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        return None
    now = now if now is not None else time.time()
    try:
        salt_b64, token = blob.split(b"\n", 1)
        key = credentials._derive_fernet_key(base64.urlsafe_b64decode(salt_b64), info=_INFO)
        payload = json.loads(Fernet(key).decrypt(token).decode("utf-8"))
    except (ValueError, InvalidToken):
        remove(owner, binding)
        return None
    cookies = [c for c in payload.get("cookies") or ()
               if not (isinstance(c.get("expires"), (int, float)) and 0 < c["expires"] < now)]
    if (now - float(payload.get("saved_at") or 0) > MAX_AGE_S or not cookies
            or payload.get("version") != 2 or not _landing(payload.get("landing"))
            or payload.get("owner") != str(owner) or payload.get("binding") != binding
            or not credential_digest or payload.get("credential_digest") != credential_digest):
        remove(owner, binding)
        return None
    return {**payload, "cookies": cookies}


def remove(owner: str, binding: str) -> bool:
    try:
        _file(owner, binding).unlink()
        return True
    except FileNotFoundError:
        return False


def remove_binding(binding: str) -> int:
    """Remove every owner's saved session of one credential binding."""
    credentials._validate_domain(binding)
    prefix = binding + "__"
    try:
        names = [p for p in _dir().iterdir() if p.name.startswith(prefix) and p.name.endswith(".age")]
    except FileNotFoundError:
        return 0
    for path in names:
        path.unlink(missing_ok=True)
    return len(names)
