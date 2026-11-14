# SPDX-License-Identifier: MIT
"""Remember that identical content was already examined.

Two examinations of an installed distribution are pure functions of the bytes
they read, and they cost seconds: the static boundary analysis and the syntax
of the local imports.  Each new process repeated both (24 seconds at every
start of the service and at every undo, measured on 14/11/2026).

A receipt is keyed by the digests of the very bytes read in this run, so it can
only speak for identical content.  What compares each installed file with the
signed release is elsewhere and is not touched: it runs every time.  Receipts
live in the private state of the account that made the examination; one that
is not a private regular file of this very account is no receipt.

This is a change to a protection, approved by Roberto on 14/11/2026.  Only the
running service remembers: the release authority always examines in full.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

_DIRECTORY = "examined-content-v1"
_KEY = re.compile(r"[0-9a-f]{64}")
_MAXIMUM_BYTES = 16 * 1024 * 1024


def key(domain: bytes, parts) -> str:
    """One digest over a domain and its parts, each framed by its length."""
    digest = hashlib.sha256()
    digest.update(len(domain).to_bytes(4, "big") + domain)
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def sources_identity(sources) -> bytes:
    """The identity of `(path, bytes)` pairs: every path with the digest of
    the bytes read for it, in the order given."""
    return "\n".join(
        f"{path}\0{hashlib.sha256(content).hexdigest()}" for path, content in sources
    ).encode("utf-8")


def _directory() -> Path | None:
    """The folder of this account's receipts, or None when nothing is to be
    remembered.

    The administrator never remembers: it examines in full, and an
    administrative step that runs with another account's state path must not
    leave a folder there that the account cannot use (seen on 14/11/2026: the
    installation created it and the service kept examining).  The folder
    carries the account in its name, so two accounts sharing a state path
    never share receipts.
    """
    if os.name != "posix":
        return None
    account = os.geteuid()
    if account == 0:
        return None
    try:
        import config as C

        return Path(C.PATH_USER_STATE) / f"{_DIRECTORY}-{account}"
    except Exception:  # noqa: BLE001 - without a state root nothing is remembered
        return None


def _private(info: os.stat_result) -> bool:
    return info.st_uid == os.geteuid() and not info.st_mode & 0o077


def load(name: str):
    """The value remembered under a key, or None.  Anything unexpected about
    the file or its folder means there is no receipt."""
    directory = _directory()
    if directory is None or not isinstance(name, str) or not _KEY.fullmatch(name):
        return None
    try:
        folder = directory.lstat()
        if not stat.S_ISDIR(folder.st_mode) or not _private(folder):
            return None
        descriptor = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or not _private(info) or info.st_nlink != 1
                or info.st_size > _MAXIMUM_BYTES):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(_MAXIMUM_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def store(name: str, value) -> None:
    """Remember a value.  Failing to write only costs the next examination."""
    directory = _directory()
    if directory is None or not isinstance(name, str) or not _KEY.fullmatch(name):
        return
    try:
        data = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        if len(data) > _MAXIMUM_BYTES:
            return
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder = directory.lstat()
        if not stat.S_ISDIR(folder.st_mode) or not _private(folder):
            return
        temporary = directory / f".{name}.{os.getpid()}.tmp"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
        finally:
            os.close(descriptor)
        os.replace(temporary, directory / name)
    except (OSError, TypeError, ValueError):
        return
