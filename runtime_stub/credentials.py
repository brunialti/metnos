# SPDX-License-Identifier: AGPL-3.0-only
"""Encrypted credential store — public skeleton.

Single-store for every secret Metnos needs (SMTP/IMAP passwords,
Telegram BOT_TOKEN, Anthropic/OpenAI API keys, GitHub PAT, …).
Per-secret rows under ``$METNOS_HOME/credentials/`` are wrapped with
Fernet (AES-128-CBC + HMAC), with a key derived via HKDF from a master
key stored at ``$METNOS_CONFIG/credentials.master`` (mode 0600,
auto-generated on first call).

Public API:

- ``store(domain, secret, description="")`` — encrypt + write
- ``fetch(domain)`` — read + decrypt, returns the plaintext str
- ``delete(domain)`` — remove the row
- ``list_domains()`` — enumerate domain keys (no plaintext returned)
- ``fingerprint(domain)`` — short non-reversible label for logs

Caller never sees the master key. Caller never logs the plaintext.
The runtime's planner is never given the plaintext — it only ever sees
metadata via ``find_credentials`` (ADR 0123 invariant).

Notes
-----

This is the **minimum viable** version of the store described in
ADR 0131. The dev environment has a richer variant with:

- binding kinds (``_BINDING_STRONG`` / ``_BINDING_WEAK``)
- per-domain rotation, scopes, fingerprint comparison
- migration from legacy ``~/.config/metnos/credentials.env``

Those features land in follow-up releases. The on-disk format declared
here is forward-compatible — a future migration can read these rows
and add metadata without re-encrypting the secret material.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


# ─── Paths ────────────────────────────────────────────────────────

def _home() -> Path:
    return Path(os.environ.get("METNOS_HOME") or (Path.home() / ".local" / "share" / "metnos"))


def _config() -> Path:
    return Path(os.environ.get("METNOS_CONFIG") or (Path.home() / ".config" / "metnos"))


def _store_dir() -> Path:
    d = _home() / "credentials"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _master_path() -> Path:
    return _config() / "credentials.master"


# ─── Master key + per-domain key derivation ──────────────────────

def _load_or_create_master() -> bytes:
    """Return 32 raw bytes used as HKDF salt + IKM source.

    Stored at ``$METNOS_CONFIG/credentials.master`` (mode 0600).
    Auto-generated on first call. Backing it up means backing up
    everyone's credentials at once.
    """
    p = _master_path()
    if p.exists():
        raw = p.read_bytes().strip()
        if len(raw) == 64:        # 32-byte hex
            return bytes.fromhex(raw.decode())
        if len(raw) == 32:        # 32-byte binary (back-compat)
            return raw
    # First run: generate and persist
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(32)
    p.write_text(raw.hex())
    p.chmod(0o600)
    return raw


def _derive_key(domain: str, master: bytes) -> bytes:
    """HKDF-Extract-then-Expand → 32 bytes → urlsafe-base64 for Fernet."""
    # Extract
    prk = hmac.new(domain.encode("utf-8"), master, hashlib.sha256).digest()
    # Expand (single-block, "metnos.credentials.v1" as info)
    okm = hmac.new(prk, b"metnos.credentials.v1\x01", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(okm)


def _cipher(domain: str) -> Fernet:
    return Fernet(_derive_key(domain, _load_or_create_master()))


# ─── Row file format ──────────────────────────────────────────────
#
# One row per domain, file name = domain (sanitised). JSON envelope:
#
#   {
#     "v": 1,
#     "created_at": 1715990400,
#     "description": "Telegram BotFather token",
#     "token": "<urlsafe-b64 of Fernet output>"
#   }

_ROW_VERSION = 1


def _row_path(domain: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ("_", "-", ".") else "_" for c in domain)
    return _store_dir() / safe


# ─── Public API ───────────────────────────────────────────────────

def store(domain: str, secret: str, *, description: str = "") -> None:
    """Encrypt and persist ``secret`` under ``domain``."""
    if not domain or not secret:
        raise ValueError("domain and secret must be non-empty")
    token = _cipher(domain).encrypt(secret.encode("utf-8")).decode("ascii")
    row = {
        "v": _ROW_VERSION,
        "created_at": int(time.time()),
        "description": description or "",
        "token": token,
    }
    p = _row_path(domain)
    p.write_text(json.dumps(row))
    p.chmod(0o600)


def fetch(domain: str) -> str:
    """Decrypt and return the plaintext stored under ``domain``.

    Raises ``KeyError`` if the row is missing, ``ValueError`` if it
    cannot be decrypted (master key mismatch or corrupted file).
    """
    p = _row_path(domain)
    if not p.exists():
        raise KeyError(f"no credential row for domain={domain!r}")
    row = json.loads(p.read_text())
    if row.get("v") != _ROW_VERSION:
        raise ValueError(f"unsupported credential row version: {row.get('v')!r}")
    try:
        return _cipher(domain).decrypt(row["token"].encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError(f"credential for {domain!r} cannot be decrypted "
                         f"(master key changed?): {e}") from e


def delete(domain: str) -> bool:
    """Remove the row for ``domain``. Returns True if a row was removed."""
    p = _row_path(domain)
    if not p.exists():
        return False
    p.unlink()
    return True


def list_domains() -> list[str]:
    """Enumerate every stored domain (no plaintext)."""
    return sorted(p.name for p in _store_dir().iterdir() if p.is_file())


def fingerprint(domain: str) -> str:
    """Short non-reversible label suitable for logs (first 8 hex of HMAC)."""
    master = _load_or_create_master()
    h = hmac.new(b"metnos.fingerprint.v1", domain.encode() + master, hashlib.sha256).hexdigest()
    return h[:8]
