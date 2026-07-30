"""Semantic, fail-soft help boundary for Metnos.

F2 compiles reviewed guides, admitted executor manifests and explicitly
allowlisted local documentation into one signed catalog.  Its local LLM can
formulate informational text but receives no tools or execution authority;
administrative and safety procedures remain deterministic.  The public entry
point returns ``None`` when the request must continue through the normal
runtime.
"""

from .models import TutorAnswer, TutorEvidence, TutorPrincipal, TutorRequest
from .service import answer_request

__all__ = [
    "TutorAnswer",
    "TutorEvidence",
    "TutorPrincipal",
    "TutorRequest",
    "answer_request",
]
