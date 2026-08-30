"""RM-0005 seed extension for deterministic argument extraction."""
from __future__ import annotations

import detection_lexicon as _dl


def register_all() -> None:
    register = _dl.register
    register(
        "args.date_offset", "mapping", match_mode="word",
        it={
            "0": ["oggi"], "-1": ["ieri"], "1": ["domani"],
            "2": ["dopodomani"], "-2": ["altroieri"],
        },
        en={
            "0": ["today"], "-1": ["yesterday"], "1": ["tomorrow"],
            "2": ["day after tomorrow"], "-2": ["day before yesterday"],
        },
    )
    register(
        "args.time_window", "mapping", match_mode="word",
        it={
            "this-week": ["questa settimana"],
            "last-week": ["settimana scorsa"],
            "next-week": ["settimana prossima"],
            "this-month": ["questo mese"],
            "last-7d": ["ultimi 7 giorni"],
            "last-24h": ["ultime 24 ore", "ultime ore"],
        },
        en={
            "this-week": ["this week"],
            "last-week": ["last week"],
            "next-week": ["next week"],
            "this-month": ["this month"],
            "last-7d": ["last 7 days"],
            "last-24h": ["last 24 hours", "last hours"],
        },
    )
    register(
        "args.relative_window_prefix", "phrases", match_mode="word",
        it=["ultimo", "ultima", "ultimi", "ultime"], en=["last"],
    )
    register(
        "args.relative_window_unit", "mapping", match_mode="word",
        it={"d": ["giorno", "giorni"], "h": ["ora", "ore"]},
        en={"d": ["day", "days"], "h": ["hour", "hours"]},
    )
    register(
        "args.home_marker", "phrases", match_mode="word",
        it=["home", "la home", "nella home", "in home"],
        en=["home", "the home"],
    )
    register(
        "args.flag_description_noise", "phrases", match_mode="word",
        it=[
            "vero", "falso", "default", "solo", "tutte", "tutti",
            "ritorna", "valore", "campo", "email", "mail", "messaggi",
            "file", "una", "uno", "con", "non", "per", "del", "della",
            "delle", "dello", "degli", "dalla", "dalle", "dallo", "dagli",
            "nella", "nelle", "nello", "negli", "sulla", "sulle", "sullo",
            "anche", "come", "sono", "questo", "questa", "quando", "dove",
        ],
        en=[
            "true", "false", "default", "only", "all", "return", "returns",
            "value", "field", "email", "emails", "mail", "messages", "file",
            "files", "the", "with", "not", "for",
        ],
    )
    register(
        "args.file_noun", "phrases", match_mode="word",
        review_policy="manual",
        it=["file", "documento", "documenti"],
        en=["file", "files", "document", "documents"],
    )
    register(
        "args.file_extension_clause", "regex", match_mode="word",
        review_policy="manual",
        it=[
            r"\b(?:file|documento|documenti)\s+"
            r"(?:di\s+tipo\s+)?([A-Za-z0-9]{2,5})\b",
        ],
        en=[
            r"\b(?:file|files|document|documents)\s+"
            r"(?:of\s+type\s+)?([A-Za-z0-9]{2,5})\b",
        ],
    )
