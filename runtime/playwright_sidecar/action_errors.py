"""Closed diagnostics for a failed browser action.

A library message can quote selectors and page content, so nothing of it
leaves this module: only the phase in which the action failed, the public
library type of the exception and a closed set of recognised signals. The
signals are wording matches, not causes; quoted page text can also match,
and several may coexist. Pure: no I/O.
"""
from __future__ import annotations

VERSION = 1
PHASES = ("prepare", "dispatch", "after_dispatch", "cleanup", "commit")

# Library wording, stable across sites and user languages, read only from
# recognised Playwright exceptions.
_SIGNALS = (
    ("context_destroyed", ("execution context was destroyed", "frame was detached")),
    ("detached", ("not attached to the dom", "element is detached")),
    ("intercepted", ("intercepts pointer events",)),
    ("navigation_interrupted", ("interrupted by another navigation",)),
    ("not_enabled", ("element is not enabled", "element is disabled")),
    ("not_stable", ("element is not stable",)),
    ("not_visible", ("element is not visible",)),
    ("outside_viewport", ("outside of the viewport",)),
    ("strict_multiple", ("strict mode violation",)),
    ("target_closed", ("target page, context or browser has been closed",
                       "target closed")),
    ("timeout_exceeded", ("ms exceeded",)),
)
# Call logs repeat the same lines; the head is enough and bounds the scan.
_MAX_SCANNED = 8192


def _library_types():
    try:
        from playwright.async_api import Error, TimeoutError as LibraryTimeout
    except Exception:  # noqa: BLE001 - a missing library is "other"
        return None, None
    return Error, LibraryTimeout


def _kind(exc) -> str:
    error, timeout = _library_types()
    if timeout is not None and isinstance(exc, timeout):
        return "timeout"
    if error is not None and isinstance(exc, error):
        return "playwright"
    return "other"


def _message(exc) -> str:
    try:
        text = getattr(exc, "message", None)
        if not isinstance(text, str):
            text = str(exc)
        return text[:_MAX_SCANNED].casefold()
    except Exception:  # noqa: BLE001 - an unreadable message has no signals
        return ""


def diagnose(exc, phase: str) -> dict:
    """Return ``{v, phase, kind, signals}``; never raises, never quotes text."""
    phase = phase if phase in PHASES else "other"
    try:
        kind = _kind(exc)
        signals = []
        if kind != "other":
            text = _message(exc)
            signals = [name for name, forms in _SIGNALS
                       if any(form in text for form in forms)]
        return {"v": VERSION, "phase": phase, "kind": kind, "signals": signals}
    except Exception:  # noqa: BLE001 - the diagnostic must not add a failure
        return {"v": VERSION, "phase": phase, "kind": "other", "signals": []}
