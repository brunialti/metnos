# SPDX-License-Identifier: AGPL-3.0-only
"""Runtime contract shared by Playwright clients and the sidecar server.

The fingerprint is deliberately content-derived instead of being a manually
bumped version.  A process keeps the value computed when it imported this
module; ``current_source_fingerprint()`` reads the files again.  Consequently
an already-running process becomes unhealthy as soon as one of the browser
boundary modules changes on disk.
"""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Iterable


HEADER_NAME = "X-Metnos-Playwright-Contract"
CONTRACT_SCHEME = "metnos.playwright/1"
ERROR_CLASS = "sidecar_contract_mismatch"

_PACKAGE_DIR = Path(__file__).resolve().parent
_RUNTIME_DIR = _PACKAGE_DIR.parent
_BOUNDARY_RUNTIME_MODULES = (
    "agentic_executor.py",
    "credential_mandates.py",
    "credentials.py",
    "sites_audit.py",
    "sites_observed.py",
    "sites_origin.py",
    "sites_url_scrub.py",
    "task_mandates.py",
)


def _contract_source_files() -> tuple[Path, ...]:
    """Return the complete local browser-boundary implementation surface."""
    package_files = tuple(sorted(_PACKAGE_DIR.glob("*.py")))
    runtime_files = tuple(
        _RUNTIME_DIR / name for name in _BOUNDARY_RUNTIME_MODULES)
    return (*package_files, *runtime_files)


def source_fingerprint(paths: Iterable[Path] | None = None) -> str:
    """Hash file names and contents into a deterministic protocol identity."""
    selected = tuple(paths) if paths is not None else _contract_source_files()
    digest = hashlib.sha256()
    for path in selected:
        resolved = Path(path).resolve()
        try:
            logical_name = resolved.relative_to(_RUNTIME_DIR).as_posix()
        except ValueError:
            logical_name = resolved.name
        digest.update(logical_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(resolved.read_bytes())
        digest.update(b"\0")
    return f"{CONTRACT_SCHEME}:{digest.hexdigest()}"


# Frozen for the lifetime of the importing process.  This is the value sent
# over HTTP and compared with a fresh disk fingerprint before every request.
LOADED_FINGERPRINT = source_fingerprint()


def current_source_fingerprint() -> str:
    """Return the fingerprint of the source tree currently present on disk."""
    return source_fingerprint()


def same_fingerprint(left: str | None, right: str | None) -> bool:
    """Compare public fingerprints without surprising type coercions."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return hmac.compare_digest(left, right)


def source_status() -> dict:
    """Describe whether this process still matches its source tree."""
    try:
        current = current_source_fingerprint()
        aligned = same_fingerprint(LOADED_FINGERPRINT, current)
        error = ""
    except (OSError, RuntimeError) as exc:
        current = "unreadable"
        aligned = False
        error = type(exc).__name__
    out = {
        "contract_loaded": LOADED_FINGERPRINT,
        "contract_current": current,
        "contract_aligned": aligned,
    }
    if error:
        out["contract_error"] = error
    return out


def failure(error_code: str, *, peer_fingerprint: str | None = None,
            process: str) -> dict:
    """Build the typed, non-sensitive failure returned at the boundary."""
    status = source_status()
    out = {
        "ok": False,
        "error": "Playwright components are not running the same source contract",
        "error_class": ERROR_CLASS,
        "error_code": error_code,
        "process": process,
        **status,
    }
    if peer_fingerprint is not None:
        out["peer_contract"] = peer_fingerprint
    return out
