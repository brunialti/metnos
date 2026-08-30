"""Canonical, locale-aware redaction at every conversational boundary.

This module is deliberately small: channels, Tutor and the planner can all
use the same structural recognizer without importing the full agent runtime.
It never stores a value.  Credential storage remains a separate, explicit
step after a trusted pending-dialog consumer has had the opportunity to use
the input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import detection_lexicon as _detlex


_REDACTED = "<REDACTED:cred>"
_BEARER = re.compile(
    r"(?i)(\bBearer\s+)(?!<REDACTED:cred>)[A-Za-z0-9._~+/-]{12,}={0,2}"
)
_LONG_OPAQUE = re.compile(r"\b(?!<REDACTED:cred>)[A-Fa-f0-9]{40,}\b")
_PAIR_VALUE = (
    r'(?!(?:<REDACTED:cred(?::[^>]+)?>))'
    r'(?:(?:"[^"\r\n]+")|(?:\'[^\'\r\n]+\')|(?:[^\s,;]+))'
)


_STRUCTURAL_LABEL = (
    r"(?<!\w)[^\W\d_][\w-]*(?:\s+[^\W\d_][\w-]*)?(?!\w)"
)
_CREDENTIAL_CONCEPTS = {
    "credentials.field_label": "mapping",
    "credentials.pair_connector": "phrases",
    "credentials.redaction_label": "phrases",
    "credentials.intake_prefix": "phrases",
}


@dataclass(frozen=True, slots=True)
class CredentialLexiconSnapshot:
    """One complete native-ready credential grammar from a single DB epoch."""

    username_labels: tuple[str, ...]
    password_labels: tuple[str, ...]
    pair_connectors: tuple[str, ...]
    redaction_labels: tuple[str, ...]
    intake_prefixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CredentialPairEvidence:
    """A matched pair interpreted by the same snapshot that admitted it."""

    match: re.Match
    first_is_password: bool


def _merged_forms(resources: tuple[dict, ...]) -> tuple[str, ...]:
    forms: list[str] = []
    seen: set[str] = set()
    for resource in resources:
        payload = resource.get("payload")
        if not isinstance(payload, list) or not payload:
            return ()
        for raw in payload:
            value = str(raw or "").strip()
            folded = value.casefold()
            if not value:
                return ()
            if folded not in seen:
                seen.add(folded)
                forms.append(value)
    return tuple(forms)


def _credential_family_snapshot() -> CredentialLexiconSnapshot | None:
    """Load every storage-relevant resource atomically, or fail closed."""

    try:
        family = _detlex.native_ready_family_resources(
            _CREDENTIAL_CONCEPTS,
            require_manual=True,
            include_reviewed_baselines=True,
        )
    except Exception:
        return None
    if family is None:
        return None

    labels: dict[str, list[str]] = {"username": [], "password": []}
    owners: dict[str, str] = {}
    label_resources = family.get("credentials.field_label") or ()
    for resource in label_resources:
        payload = resource.get("payload")
        if not isinstance(payload, dict) or set(payload) != set(labels):
            return None
        for canonical, raw_forms in payload.items():
            if not isinstance(raw_forms, list) or not raw_forms:
                return None
            bucket = labels[canonical]
            known = {value.casefold() for value in bucket}
            for raw in raw_forms:
                value = str(raw or "").strip()
                folded = value.casefold()
                if not value or (
                    folded in owners and owners[folded] != canonical
                ):
                    return None
                owners[folded] = canonical
                if folded not in known:
                    known.add(folded)
                    bucket.append(value)

    connectors = _merged_forms(
        family.get("credentials.pair_connector") or (),
    )
    redaction = _merged_forms(
        family.get("credentials.redaction_label") or (),
    )
    prefixes = _merged_forms(
        family.get("credentials.intake_prefix") or (),
    )
    if not all(labels.values()) or not connectors or not redaction or not prefixes:
        return None
    return CredentialLexiconSnapshot(
        username_labels=tuple(labels["username"]),
        password_labels=tuple(labels["password"]),
        pair_connectors=connectors,
        redaction_labels=redaction,
        intake_prefixes=prefixes,
    )


def _forms_pattern(
    forms: tuple[str, ...], *, structural_fallback: bool = False,
) -> str:
    escaped = []
    for form in sorted(set(forms), key=len, reverse=True):
        value = str(form or "").strip()
        if value:
            escaped.append(re.escape(value).replace(r"\ ", r"\s+"))
    if not escaped:
        return _STRUCTURAL_LABEL if structural_fallback else r"(?!)"
    return r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)"


def credential_pair_patterns(
    lang: str,
    *,
    snapshot: CredentialLexiconSnapshot | None = None,
) -> tuple[re.Pattern, re.Pattern]:
    """Closed grammar for a username/password pair in either order."""

    del lang
    if snapshot is None:
        snapshot = _credential_family_snapshot()
    users = snapshot.username_labels if snapshot is not None else ()
    passwords = snapshot.password_labels if snapshot is not None else ()
    connectors = snapshot.pair_connectors if snapshot is not None else ()
    # If localization is unavailable, explicit key=value pairs are still
    # redacted with a language-neutral structural label. They are never stored.
    structural = not users or not passwords
    user_pattern = _forms_pattern(users, structural_fallback=structural)
    password_pattern = _forms_pattern(passwords, structural_fallback=structural)
    connector_pattern = _forms_pattern(connectors)
    separator = rf"(?:\s*[,;/|]\s*|\s+{connector_pattern}\s+|\s+)"
    user_then_password = re.compile(
        rf"({user_pattern})\s*[:=]?\s*({_PAIR_VALUE})"
        rf"(?:{separator})*"
        rf"({password_pattern})\s*[:=]?\s*({_PAIR_VALUE})",
        re.IGNORECASE,
    )
    password_then_user = re.compile(
        rf"({password_pattern})\s*[:=]?\s*({_PAIR_VALUE})"
        rf"(?:{separator})*"
        rf"({user_pattern})\s*[:=]?\s*({_PAIR_VALUE})",
        re.IGNORECASE,
    )
    return user_then_password, password_then_user


def _explicit_pair_separators(match: re.Match) -> bool:
    first = match.string[match.end(1):match.start(2)]
    second = match.string[match.end(3):match.start(4)]
    return any(char in first for char in ":=") and any(
        char in second for char in ":=")


def _has_intake_prefix(
    text: str,
    match: re.Match,
    snapshot: CredentialLexiconSnapshot | None,
) -> bool:
    """Admit only a closed intake prefix immediately before a whole pair."""

    prefix = text[:match.start()].strip(" \t\r\n:;,-")
    suffix = text[match.end():].strip()
    if not prefix or suffix:
        return False
    forms = snapshot.intake_prefixes if snapshot is not None else ()
    normalized = " ".join(prefix.casefold().split())
    return normalized in {
        " ".join(str(form).casefold().split())
        for form in forms
    }


def _credential_pair_matches(
    text: str,
    *,
    for_storage: bool,
    snapshot: CredentialLexiconSnapshot | None,
) -> tuple[re.Match, ...]:
    """Return structurally admitted pairs, never arbitrary prose fragments.

    Whitespace-only pairs are ambiguous in free prose. They are admitted for
    durable storage only with an attested credential/binding context. A bare
    whole-message pair remains redactable (so it cannot reach an LLM), but is
    not silently stored; the planner can reopen the typed credential intake.
    """

    stripped = str(text or "").strip()
    if not stripped:
        return ()
    if for_storage and snapshot is None:
        return ()
    offset = str(text).find(stripped)
    whole_span = (offset, offset + len(stripped))
    accepted: list[re.Match] = []
    seen: set[tuple[int, int]] = set()
    for pattern in credential_pair_patterns(
        _detlex.current_lang(), snapshot=snapshot,
    ):
        for match in pattern.finditer(str(text)):
            explicit = _explicit_pair_separators(match)
            whole = match.span() == whole_span
            # Durable storage from free text requires explicit assignment for
            # BOTH fields. A URL identifies a domain, not the semantics of the
            # nearby prose. Whitespace-only values are redacted defensively,
            # then collected through a typed credential dialog if needed.
            admitted = (
                explicit if for_storage else
                explicit or whole or _has_intake_prefix(
                    str(text), match, snapshot,
                )
            )
            if not admitted:
                continue
            if match.span() in seen:
                continue
            seen.add(match.span())
            accepted.append(match)
    return tuple(accepted)


def credential_pair_matches(text: str, *,
                            for_storage: bool = False) -> tuple[re.Match, ...]:
    """Compatibility view of matches admitted by one family snapshot."""

    snapshot = _credential_family_snapshot()
    return _credential_pair_matches(
        text, for_storage=for_storage, snapshot=snapshot,
    )


def credential_pairs_for_storage(text: str) -> tuple[CredentialPairEvidence, ...]:
    """Admit and interpret pairs through one complete atomic family snapshot."""

    snapshot = _credential_family_snapshot()
    if snapshot is None:
        return ()
    matches = _credential_pair_matches(
        text, for_storage=True, snapshot=snapshot,
    )
    password_labels = {
        " ".join(value.casefold().split())
        for value in snapshot.password_labels
    }
    return tuple(
        CredentialPairEvidence(
            match=match,
            first_is_password=(
                " ".join(match.group(1).casefold().split())
                in password_labels
            ),
        )
        for match in matches
    )


def _labelled_value_pattern(lang: str) -> re.Pattern:
    del lang  # The ContextVar-backed lexicon resolves the active locale.
    snapshot = _credential_family_snapshot()
    forms = snapshot.redaction_labels if snapshot is not None else ()
    labels = _forms_pattern(forms, structural_fallback=not forms)
    # An explicit separator is mandatory for an isolated label. Whitespace is
    # accepted only by ``credential_pair_patterns`` where a second typed field
    # closes the grammar; this avoids redacting ordinary phrases such as
    # "a user configures" or "token budget".
    value = (
        r'(?!(?:<REDACTED:cred(?::[^>]+)?>))'
        r'(?:"[^"\r\n]+"|\'[^\'\r\n]+\'|\S+)'
    )
    return re.compile(
        rf"({labels}\s*[:=]\s*)({value})",
        re.IGNORECASE,
    )


def scrub_sensitive_text(text: str) -> tuple[str, int]:
    """Return ``text`` with labelled and opaque secret values redacted.

    The operation is deterministic and idempotent.  It preserves labels so a
    downstream deterministic credential workflow can still understand which
    field was supplied, while no provider or persisted turn sees its value.
    """

    if not isinstance(text, str) or not text:
        return text, 0
    count = 0

    def labelled(match: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{_REDACTED}"

    def opaque(match: re.Match) -> str:
        nonlocal count
        count += 1
        prefix = match.group(1) if match.lastindex else ""
        return f"{prefix}{_REDACTED}"

    cleaned = text
    pair_spans: set[tuple[int, int]] = set()
    for match in credential_pair_matches(text):
        pair_spans.add((match.start(2), match.end(2)))
        pair_spans.add((match.start(4), match.end(4)))
    for start, end in sorted(pair_spans, reverse=True):
        cleaned = cleaned[:start] + _REDACTED + cleaned[end:]
        count += 1
    cleaned = _labelled_value_pattern(_detlex.current_lang()).sub(
        labelled, cleaned)
    cleaned = _BEARER.sub(opaque, cleaned)
    cleaned = _LONG_OPAQUE.sub(opaque, cleaned)
    return cleaned, count


def contains_sensitive_input(text: str) -> bool:
    """Whether canonical redaction would remove at least one value."""

    _cleaned, count = scrub_sensitive_text(text)
    return count > 0


def is_password_label(label: str) -> bool:
    """Classify one admitted field label through the canonical lexicon.

    The manually reviewed native lexicon is authoritative. If it is not ready,
    durable storage has already been denied and this classifier returns false.
    """

    snapshot = _credential_family_snapshot()
    if snapshot is None:
        return False
    folded = " ".join(str(label or "").casefold().split())
    return folded in {
        " ".join(form.casefold().split())
        for form in snapshot.password_labels
    }
