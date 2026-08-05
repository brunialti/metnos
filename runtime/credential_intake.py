"""Canonical, locale-aware redaction at every conversational boundary.

This module is deliberately small: channels, Tutor and the planner can all
use the same structural recognizer without importing the full agent runtime.
It never stores a value.  Credential storage remains a separate, explicit
step after a trusted pending-dialog consumer has had the opportunity to use
the input.
"""

from __future__ import annotations

import functools
import re

import detection_lexicon as _detlex


_REDACTED = "<REDACTED:cred>"
_BEARER = re.compile(
    r"(?i)(\bBearer\s+)(?!<REDACTED:cred>)[A-Za-z0-9._~+/-]{12,}={0,2}"
)
_LONG_OPAQUE = re.compile(r"\b(?!<REDACTED:cred>)[A-Fa-f0-9]{40,}\b")
# Security must remain fail-closed even while the translated recognition DB is
# unavailable.  These are protocol/field identifiers, not routing phrases;
# normal operation reads the versioned, translatable concept below.
_LABEL_FALLBACK = (
    "password", "passwd", "passphrase", "pwd", "psw", "pass",
    "username", "user", "utente", "nome utente", "usr", "uname",
    "otp", "2fa", "one-time code", "verification code",
    "codice otp", "codice 2fa", "codice di verifica",
    "secret", "segreto", "token", "api key", "api-key", "chiave api",
)
_FIELD_FALLBACK = {
    "username": ("username", "user id", "userid", "utente",
                 "nome utente", "user", "usr", "email", "e-mail"),
    "password": ("password", "passwd", "passphrase", "pwd", "psw", "pass"),
}
_CONNECTOR_FALLBACK = ("e", "con", "and", "with")
_INTAKE_PREFIX_FALLBACK = (
    "credenziali", "dati di accesso", "credentials", "access credentials",
)
_PAIR_VALUE = (
    r'(?!(?:<REDACTED:cred(?::[^>]+)?>))'
    r'(?:(?:"[^"\r\n]+")|(?:\'[^\'\r\n]+\')|(?:[^\s,;]+))'
)


def _forms_pattern(forms: tuple[str, ...]) -> str:
    escaped = []
    for form in sorted(set(forms), key=len, reverse=True):
        value = str(form or "").strip()
        if value:
            escaped.append(re.escape(value).replace(r"\ ", r"\s+"))
    if not escaped:
        escaped = [re.escape(value) for value in _LABEL_FALLBACK]
    return r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)"


@functools.lru_cache(maxsize=16)
def credential_pair_patterns(lang: str) -> tuple[re.Pattern, re.Pattern]:
    """Closed grammar for a username/password pair in either order."""

    del lang
    try:
        labels = _detlex.mapping("credentials.field_label")
        connectors = tuple(_detlex.forms("credentials.pair_connector"))
    except Exception:
        labels = {}
        connectors = ()
    users = tuple(labels.get("username") or _FIELD_FALLBACK["username"])
    passwords = tuple(labels.get("password") or _FIELD_FALLBACK["password"])
    user_pattern = _forms_pattern(users)
    password_pattern = _forms_pattern(passwords)
    connector_pattern = _forms_pattern(connectors or _CONNECTOR_FALLBACK)
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


def _has_intake_prefix(text: str, match: re.Match) -> bool:
    """Admit only a closed intake prefix immediately before a whole pair."""

    prefix = text[:match.start()].strip(" \t\r\n:;,-")
    suffix = text[match.end():].strip()
    if not prefix or suffix:
        return False
    try:
        forms = tuple(_detlex.forms("credentials.intake_prefix"))
    except Exception:
        forms = ()
    normalized = " ".join(prefix.casefold().split())
    return normalized in {
        " ".join(str(form).casefold().split())
        for form in (forms or _INTAKE_PREFIX_FALLBACK)
    }


def credential_pair_matches(text: str, *,
                            for_storage: bool = False) -> tuple[re.Match, ...]:
    """Return structurally admitted pairs, never arbitrary prose fragments.

    Whitespace-only pairs are ambiguous in free prose. They are admitted for
    durable storage only with an attested credential/binding context. A bare
    whole-message pair remains redactable (so it cannot reach an LLM), but is
    not silently stored; the planner can reopen the typed credential intake.
    """

    stripped = str(text or "").strip()
    if not stripped:
        return ()
    offset = str(text).find(stripped)
    whole_span = (offset, offset + len(stripped))
    accepted: list[re.Match] = []
    seen: set[tuple[int, int]] = set()
    for pattern in credential_pair_patterns(_detlex.current_lang()):
        for match in pattern.finditer(str(text)):
            explicit = _explicit_pair_separators(match)
            whole = match.span() == whole_span
            # Durable storage from free text requires explicit assignment for
            # BOTH fields. A URL identifies a domain, not the semantics of the
            # nearby prose. Whitespace-only values are redacted defensively,
            # then collected through a typed credential dialog if needed.
            admitted = (
                explicit if for_storage else
                explicit or whole or _has_intake_prefix(str(text), match)
            )
            if not admitted:
                continue
            if match.span() in seen:
                continue
            seen.add(match.span())
            accepted.append(match)
    return tuple(accepted)


@functools.lru_cache(maxsize=16)
def _labelled_value_pattern(lang: str) -> re.Pattern:
    del lang  # The ContextVar-backed lexicon resolves the active locale.
    try:
        forms = tuple(_detlex.forms("credentials.redaction_label"))
    except Exception:
        forms = ()
    labels = _forms_pattern(forms or _LABEL_FALLBACK)
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

    The translated lexicon is authoritative when available.  The closed
    protocol fallback keeps password-first pairs correctly oriented during
    bootstrap or while the lexicon database is unavailable.
    """

    try:
        labels = _detlex.mapping("credentials.field_label")
    except Exception:
        labels = {}
    forms = tuple(labels.get("password") or _FIELD_FALLBACK["password"])
    folded = str(label or "").casefold()
    return any(str(form).casefold() in folded for form in forms if form)
