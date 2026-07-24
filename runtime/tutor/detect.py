"""Structural admission checks before semantic Tutor routing."""

from __future__ import annotations

from dataclasses import dataclass
import re

KINDS = frozenset({"pure_help", "pure_action", "mixed", "unknown"})

_OPAQUE_SECRET = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|api[_ -]?key|otp)\s*[:=]\s*\S+"
    r"|\bBearer\s+[A-Za-z0-9._~+/-]{12,}={0,2}\b"
    r"|\b[A-Fa-f0-9]{40,}\b"
)
_CONTROL_COMMAND = re.compile(r"^\s*/(?:pair|start|admin)\b", re.I)


@dataclass(frozen=True, slots=True)
class Detection:
    kind: str
    reason: str
    intent: str = ""
    scope: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"invalid tutor detection kind: {self.kind}")


def contains_sensitive_input(query: str) -> bool:
    """Structural backstop: secrets never enter tutor matching or cards."""

    return bool(_OPAQUE_SECRET.search(query or ""))


def classify(query: str) -> Detection:
    text = (query or "").strip()
    if not text or len(text) < 4:
        return Detection("unknown", "empty_or_short")
    if _CONTROL_COMMAND.search(text):
        return Detection("pure_action", "control_command")
    if contains_sensitive_input(text):
        return Detection("unknown", "sensitive_shape")

    # Language and topic are deliberately absent here.  The unified semantic
    # retriever and the closed mode classifier decide relevance and intent.
    return Detection("unknown", "semantic_mode_required")
