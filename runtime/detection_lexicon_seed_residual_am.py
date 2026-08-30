#!/usr/bin/env python3
"""Residual A--M natural-language resources found by the RM-0005 census.

Consumers use only a ready native row.  The reviewed Italian and English
sources are additive after that gate, preserving the historical bilingual
union while preventing a pending third language from silently borrowing it.
Calendar identity aliases additionally require manual review.
"""
from __future__ import annotations

import detection_lexicon as _dl


MANIFEST_VERBOSITY = "residual_am.manifest.verbosity"
AGENT_AFFINITY_STOPWORD = "residual_am.agent.affinity_stopword"
AGENT_BINDING_WEAK = "residual_am.agent.binding_weak"
AGENT_SIMPLE_CONJUNCTION = "residual_am.agent.simple_conjunction"
NOTIFY_CONTINUATION = "residual_am.agent.notify_continuation"
CALENDAR_IDENTITY_ALIAS = "residual_am.calendar.identity_alias"
GMAIL_FOLDER_ALIAS = "residual_am.gmail.folder_alias"
MAIL_TIME_WINDOW = "residual_am.mail.time_window"
MAIL_FOLDER_SPECIAL = "residual_am.mail.folder_special"
PACKAGE_DIRECTION_ALIAS = "residual_am.packages.direction_alias"
CLUSTER_STOPWORD = "residual_am.cluster.stopword"

_KINDS = {
    MANIFEST_VERBOSITY: "mapping",
    AGENT_AFFINITY_STOPWORD: "phrases",
    AGENT_BINDING_WEAK: "mapping",
    AGENT_SIMPLE_CONJUNCTION: "phrases",
    NOTIFY_CONTINUATION: "regex",
    CALENDAR_IDENTITY_ALIAS: "mapping",
    GMAIL_FOLDER_ALIAS: "mapping",
    MAIL_TIME_WINDOW: "mapping",
    MAIL_FOLDER_SPECIAL: "mapping",
    PACKAGE_DIRECTION_ALIAS: "mapping",
    CLUSTER_STOPWORD: "phrases",
}
_MANUAL = frozenset({
    AGENT_BINDING_WEAK,
    AGENT_SIMPLE_CONJUNCTION,
    NOTIFY_CONTINUATION,
    CALENDAR_IDENTITY_ALIAS,
    GMAIL_FOLDER_ALIAS,
    MAIL_FOLDER_SPECIAL,
    PACKAGE_DIRECTION_ALIAS,
})
_COMPLETE_MAPPING_KEYS = {
    GMAIL_FOLDER_ALIAS: frozenset({
        "inbox", "trash", "spam", "sent", "drafts", "important",
        "starred", "archive",
    }),
    PACKAGE_DIRECTION_ALIAS: frozenset({"install", "uninstall"}),
}
_registered_target: tuple[str, int] | None = None


def register_all() -> None:
    """Register the exact historical IT/EN surfaces idempotently."""

    R = _dl.register
    R(
        MANIFEST_VERBOSITY,
        "mapping",
        match_mode="substring",
        it={
            "marker": ["USO CORRETTO", "ARG NAMES (nomi esatti"],
            "example": ["Esempio:"],
        },
        en={
            "marker": ["USAGE: query", "ARG NAMES (exact names"],
            "example": ["Example:"],
        },
    )
    R(
        AGENT_AFFINITY_STOPWORD,
        "phrases",
        match_mode="word",
        it=[
            "il", "la", "i", "gli", "le", "un", "una", "di", "da",
            "del", "della", "dei", "delle", "a", "al", "alla", "ai",
            "alle", "in", "con", "su", "per", "tra", "fra", "e", "o",
            "ma", "che", "mi", "ci", "ti", "si", "ho", "ha", "hai",
        ],
        en=[
            "the", "a", "an", "of", "to", "in", "is", "it", "for",
            "on", "with", "and", "or", "but", "this", "that",
        ],
    )
    R(
        AGENT_BINDING_WEAK,
        "mapping",
        match_mode="substring",
        review_policy="manual",
        it={
            "cifs": ["share", "smb", "cifs", "nas", "monta", "mount", "samba"],
            "ssh": ["ssh", "scp", "sftp"],
            "web": ["login", "sito", "portale", "registro", "banca",
                    "browser", "webmail"],
        },
        en={
            "cifs": ["share", "smb", "cifs", "nas", "mount", "samba"],
            "ssh": ["ssh", "scp", "sftp"],
            "web": ["login", "browser", "webmail"],
        },
    )
    R(
        AGENT_SIMPLE_CONJUNCTION,
        "phrases",
        match_mode="word",
        review_policy="manual",
        it=["e"],
        en=["and"],
    )
    notify_pattern = (
        r"("
        r"\b(?:mandami|inviami|spediscimi|notificami|avvisami|scrivimi)\b|"
        r"\bfammi\s+sapere\b|"
        r"\b(?:e|and|poi)\s+(?:mi\s+)?(?:mandi|invii|spedisci|notifichi|"
        r"avvisi|invia|manda|notifica|avvisa)\s+"
        r"(?:una\s+|un\s+|la\s+)?"
        r"(?:email|mail|messaggio|notifica|conferma|sms|telegram|whatsapp)\b|"
        r"\b(?:email|notify|alert|message|text|ping)\s+me\b|"
        r"\bsend\s+me\s+(?:a\s+|an\s+)?"
        r"(?:email|message|text|notification|notify)\b|"
        r"\blet\s+me\s+know\b|"
        r"\bvia\s+(?:email|mail|telegram|sms|whatsapp|notifica|notification|"
        r"message)\b|"
        r"(?:[\s\+,]|\b)(?:e|and|poi|\+|,)\s+"
        r"(?:invia|manda|notifica|send|notify)\s+"
        r"(?:una\s+|un\s+|la\s+|a\s+|an\s+|the\s+)?"
        r"(?:conferma|confirmation|notifica|notification|messaggio|email|"
        r"mail)\b|"
        r"(?:[\s\+,]|\b)(?:e|and|poi|\+|,)\s+"
        r"(?:notifica|notify)(?=\s*[.!?]|\s*$)|"
        r"(?:\s*[\+,])\s+"
        r"(?:una\s+|un\s+|la\s+|a\s+|an\s+|the\s+)?"
        r"(?:email|mail|telegram|sms|whatsapp|notifica|notification|"
        r"messaggio|message)\s*"
        r"(?:di\s+|of\s+)?"
        r"(?:conferma|confirmation|riassunto|summary|notifica|notification|"
        r"avviso|alert|update|aggiornamento)?\b"
        r")"
    )
    R(
        NOTIFY_CONTINUATION,
        "regex",
        review_policy="manual",
        it=[notify_pattern],
        en=[notify_pattern],
    )
    R(
        CALENDAR_IDENTITY_ALIAS,
        "mapping",
        match_mode="word",
        review_policy="manual",
        it={
            "primary": ["primary", "default", "utente"],
            "all": ["all", "tutti"],
        },
        en={
            "primary": ["primary", "default", "me", "self", "user"],
            "all": ["all"],
        },
    )
    R(
        GMAIL_FOLDER_ALIAS,
        "mapping",
        match_mode="word",
        review_policy="manual",
        it={
            "inbox": ["inbox", "posta-in-arrivo", "posta in arrivo"],
            "trash": ["trash", "cestino", "trashed"],
            "spam": ["spam", "junk", "posta indesiderata",
                     "posta-indesiderata"],
            "sent": ["sent", "inviati", "inviata"],
            "drafts": ["drafts", "bozze", "draft"],
            "important": ["important", "importante"],
            "starred": ["starred", "speciali"],
            "archive": ["archive", "archivio", "all", "tutti"],
        },
        en={
            "inbox": ["inbox"],
            "trash": ["trash", "trashed"],
            "spam": ["spam", "junk"],
            "sent": ["sent"],
            "drafts": ["drafts", "draft"],
            "important": ["important"],
            "starred": ["starred"],
            "archive": ["archive", "all"],
        },
    )
    R(
        MAIL_TIME_WINDOW,
        "mapping",
        match_mode="word",
        it={
            "today": ["today"],
            "yesterday": ["yesterday"],
            "preset_week": ["last-settimana"],
            "preset_month": ["last-mese"],
            "preset_year": ["last-anno"],
            "day_unit": ["d", "giorno", "giorni"],
            "hour_unit": ["h", "ora", "ore"],
            "week_unit": ["w", "settimana", "settimane"],
            "month_unit": ["mo", "mese", "mesi", "m", "min"],
            "year_unit": ["y", "anno", "anni"],
            "relative_marker": ["last", "past", "minus", "ago"],
        },
        en={
            "today": ["today"],
            "yesterday": ["yesterday"],
            "preset_week": ["last-week"],
            "preset_month": ["last-month"],
            "preset_year": ["last-year"],
            "day_unit": ["d", "day", "days"],
            "hour_unit": ["h", "hour", "hours"],
            "week_unit": ["w", "week", "weeks"],
            "month_unit": ["mo", "month", "months", "m", "min"],
            "year_unit": ["y", "year", "years"],
            "relative_marker": ["last", "past", "minus", "ago"],
        },
    )
    R(
        MAIL_FOLDER_SPECIAL,
        "mapping",
        match_mode="substring",
        review_policy="manual",
        it={
            "junk": ["junk", "spam", "indesiderata", "spazzatura"],
            "trash": ["trash", "cestino", "eliminata"],
            "sent": ["sent", "inviata", "inviate"],
            "drafts": ["draft", "drafts", "bozze"],
        },
        en={
            "junk": ["junk", "spam"],
            "trash": ["trash"],
            "sent": ["sent"],
            "drafts": ["draft", "drafts"],
        },
    )
    R(
        PACKAGE_DIRECTION_ALIAS,
        "mapping",
        match_mode="word",
        review_policy="manual",
        it={
            "install": [
                "installa", "installare", "installazione", "metti su",
            ],
            "uninstall": [
                "disinstalla", "disinstallare", "disinstallazione",
                "rimuovi il programma", "rimuovere il programma",
                "togli il programma", "elimina il programma",
                "cancella il programma", "rimuovi l'applicazione",
                "togli l'applicazione", "elimina l'applicazione",
            ],
        },
        en={
            "install": ["install", "set up"],
            "uninstall": [
                "uninstall", "remove the program", "remove the app",
                "delete the program", "delete the app",
                "get rid of the program",
            ],
        },
    )
    R(
        CLUSTER_STOPWORD,
        "phrases",
        match_mode="word",
        it=[
            "il", "lo", "la", "i", "gli", "le", "un", "una", "uno",
            "di", "del", "della", "dei", "dello", "delle", "degli",
            "dimmi", "dimi", "mostrami", "mostra", "fammi", "fai",
            "quali", "sono", "che", "cosa", "cos", "c'e", "ce",
        ],
        en=[
            "the", "a", "an", "of", "for", "to", "in", "on", "show",
            "tell", "give", "list", "what", "which", "is", "are", "do",
            "does",
        ],
    )


def _all_seed_rows_ready() -> bool:
    for concept, kind in _KINDS.items():
        for language in ("it", "en"):
            resource = _dl.resource_for_language(
                concept, language, fallback=False, ready_only=True,
            )
            if (not resource or resource.get("kind") != kind
                    or (concept in _MANUAL
                        and resource.get("review_policy") != "manual")):
                return False
    return True


def _ensure_registered() -> None:
    """Register lazily and retry after tests/installations swap the DB."""

    global _registered_target
    target = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    if _registered_target == target:
        return
    register_all()
    refreshed = (str(_dl.DB_PATH), id(getattr(_dl, "_conn", None)))
    _registered_target = refreshed if _all_seed_rows_ready() else None


def ready_forms(concept: str) -> tuple[str, ...]:
    """Return the native-ready forms plus reviewed IT/EN baselines."""

    if _KINDS.get(concept) != "phrases":
        raise KeyError(f"unknown residual A-M phrase concept: {concept}")
    _ensure_registered()
    return tuple(_dl.native_ready_forms(
        concept,
        require_manual=concept in _MANUAL,
        include_reviewed_baselines=True,
    ))


def ready_mapping(concept: str) -> dict[str, list[str]]:
    """Return a native-ready mapping, enforcing manual identity policy."""

    if _KINDS.get(concept) != "mapping":
        raise KeyError(f"unknown residual A-M mapping concept: {concept}")
    _ensure_registered()
    return _dl.native_ready_mapping(
        concept,
        require_manual=concept in _MANUAL,
        include_reviewed_baselines=True,
    )


def ready_complete_mapping(concept: str) -> dict[str, list[str]]:
    """Return a complete manual mapping, or an empty fail-closed result.

    Mutating consumers cannot reconstruct a missing target-language bucket
    from the reviewed baselines: the exact active row must first contain the
    complete canonical key set with non-empty string lists.
    """

    expected = _COMPLETE_MAPPING_KEYS.get(concept)
    if expected is None:
        raise KeyError(f"unknown complete residual A-M mapping: {concept}")
    _ensure_registered()
    language = _dl.current_lang()
    resource = _dl.resource_for_language(
        concept, language, fallback=False, ready_only=True,
    )
    payload = resource.get("payload") if resource else None
    if (not resource or resource.get("kind") != "mapping"
            or resource.get("review_policy") != "manual"
            or not isinstance(payload, dict)
            or set(payload) != set(expected)
            or any(
                not isinstance(payload.get(key), list)
                or not payload[key]
                or not all(isinstance(form, str) and form.strip()
                           for form in payload[key])
                for key in expected
            )):
        return {}
    merged = _dl.native_ready_mapping(
        concept,
        require_manual=True,
        include_reviewed_baselines=True,
    )
    if set(merged) != set(expected):
        return {}
    return merged


def ready_patterns(concept: str):
    """Return native-ready compiled regexes, with manual policy as declared."""

    if _KINDS.get(concept) != "regex":
        raise KeyError(f"unknown residual A-M regex concept: {concept}")
    _ensure_registered()
    return tuple(_dl.native_ready_patterns(
        concept,
        require_manual=concept in _MANUAL,
        include_reviewed_baselines=True,
    ))


__all__ = [
    "AGENT_AFFINITY_STOPWORD",
    "AGENT_BINDING_WEAK",
    "AGENT_SIMPLE_CONJUNCTION",
    "CALENDAR_IDENTITY_ALIAS",
    "CLUSTER_STOPWORD",
    "GMAIL_FOLDER_ALIAS",
    "MAIL_FOLDER_SPECIAL",
    "MAIL_TIME_WINDOW",
    "MANIFEST_VERBOSITY",
    "NOTIFY_CONTINUATION",
    "PACKAGE_DIRECTION_ALIAS",
    "ready_complete_mapping",
    "ready_forms",
    "ready_mapping",
    "ready_patterns",
    "register_all",
]
