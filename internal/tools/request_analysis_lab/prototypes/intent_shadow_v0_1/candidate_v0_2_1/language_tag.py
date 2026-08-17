"""BCP47 normalizer prerequisite layered on the frozen v0.2 parser."""
from __future__ import annotations

from ..candidate_v0_2.language_tag import normalize_language_tag as _normalize_modern_tag
from .grandfathered_tags import BY_CASEFOLDED_TAG


def normalize_language_tag(value: str) -> str:
    """Canonicalize modern and every registered grandfathered BCP47 tag."""
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("language tag must be a nonempty BCP47 string")
    if not value.isascii() or any(
        not (character.isalnum() or character == "-") for character in value
    ):
        raise ValueError("language tag must contain ASCII alphanumerics and hyphens only")
    registered = BY_CASEFOLDED_TAG.get(value.casefold())
    if registered is None:
        return _normalize_modern_tag(value)
    if registered.preferred is not None:
        return _normalize_modern_tag(registered.preferred)
    return registered.canonical
