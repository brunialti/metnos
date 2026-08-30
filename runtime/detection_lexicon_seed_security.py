"""Manual-review input grammars used by security-sensitive consumers."""
from __future__ import annotations

import detection_lexicon as _dl


def register_all() -> None:
    register = _dl.register
    email_page = [
        "e-mail", "email", "email address", "sent to",
        "posta elettronica", "posta",
    ]
    register(
        "sites.factor.email_page", "phrases", match_mode="word",
        review_policy="manual",
        it=email_page, en=email_page,
    )
    factor_markers = [
        "verification", "verify", "security", "one-time", "one time",
        "otp", "passcode", "sign-in", "sign in", "login", "access",
        "codice", "verifica", "sicurezza", "accesso", "conferma",
    ]
    register(
        "sites.factor.marker", "phrases", match_mode="word",
        review_policy="manual",
        it=factor_markers, en=factor_markers,
    )
    non_codes = [
        "access", "accesso", "codice", "code", "confirm", "conferma",
        "email", "login", "passcode", "security", "sicurezza", "verify",
        "verifica", "verification",
    ]
    register(
        "sites.factor.non_code", "phrases", match_mode="word",
        review_policy="manual",
        it=non_codes, en=non_codes,
    )
    code_patterns = [
        r"\b(?:verification|security|one[- ]?time|access|login)?\s*"
        r"(?:code|codice|passcode|otp)\s*(?:is|e|\u00e8|:|-)\s*"
        r"([A-Za-z0-9]{4,12})\b",
        r"\b([A-Za-z0-9]{4,12})\b\s+(?:is|e|\u00e8)\s+"
        r"(?:your|il tuo)\s+(?:verification\s+)?"
        r"(?:code|codice|passcode|otp)\b",
        r"\b([A-Za-z0-9]{4,12})\b\s*[-:]\s*"
        r"(?:verification|security|one[- ]?time|access)\s+"
        r"(?:code|codice)\b",
        r"\b(?:verification|security|one[- ]?time|access|login)?\s*"
        r"(?:code|codice|passcode|otp)\s+([A-Za-z0-9]{4,12})\b",
    ]
    register(
        "sites.factor.code_pattern", "regex", match_mode="word",
        review_policy="manual", it=code_patterns, en=code_patterns,
    )
    register(
        "safety.paternalism_marker", "phrases", match_mode="word",
        review_policy="manual",
        it=[
            "dire all'utente", "dire all utente", "impedire all'utente",
            "impedire all utente", "correggere l'utente", "correggere l utente",
            "consigliare all'utente di", "consigliare all utente di",
            "consigliare all'utente di non", "consigliare all utente di non",
        ],
        en=[
            "tell the user", "prevent the user", "warn the user about",
            "advise the user to",
        ],
    )
