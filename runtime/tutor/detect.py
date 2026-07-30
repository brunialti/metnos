"""Structural admission checks before semantic Tutor routing."""

from __future__ import annotations

from dataclasses import dataclass
import re

from credential_intake import contains_sensitive_input

KINDS = frozenset({"pure_help", "pure_action", "mixed", "unknown"})

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
