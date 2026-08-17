"""Complete RFC 5646/IANA grandfathered language-tag authority table.

The records are data, not language-specific control flow.  ``preferred`` is
the IANA Preferred-Value when one exists; otherwise ``canonical`` preserves
the registered grandfathered spelling.
"""
from __future__ import annotations

from dataclasses import dataclass


AUTHORITY = "IANA Language Subtag Registry: grandfathered records; RFC 5646 section 2.2.8"


@dataclass(frozen=True, slots=True)
class GrandfatheredTag:
    canonical: str
    preferred: str | None


GRANDFATHERED_TAGS = (
    GrandfatheredTag("art-lojban", "jbo"),
    GrandfatheredTag("cel-gaulish", None),
    GrandfatheredTag("en-GB-oed", "en-GB-oxendict"),
    GrandfatheredTag("i-ami", "ami"),
    GrandfatheredTag("i-bnn", "bnn"),
    GrandfatheredTag("i-default", None),
    GrandfatheredTag("i-enochian", None),
    GrandfatheredTag("i-hak", "hak"),
    GrandfatheredTag("i-klingon", "tlh"),
    GrandfatheredTag("i-lux", "lb"),
    GrandfatheredTag("i-mingo", None),
    GrandfatheredTag("i-navajo", "nv"),
    GrandfatheredTag("i-pwn", "pwn"),
    GrandfatheredTag("i-tao", "tao"),
    GrandfatheredTag("i-tay", "tay"),
    GrandfatheredTag("i-tsu", "tsu"),
    GrandfatheredTag("no-bok", "nb"),
    GrandfatheredTag("no-nyn", "nn"),
    GrandfatheredTag("sgn-BE-FR", "sfb"),
    GrandfatheredTag("sgn-BE-NL", "vgt"),
    GrandfatheredTag("sgn-CH-DE", "sgg"),
    GrandfatheredTag("zh-guoyu", "cmn"),
    GrandfatheredTag("zh-hakka", "hak"),
    GrandfatheredTag("zh-min", None),
    GrandfatheredTag("zh-min-nan", "nan"),
    GrandfatheredTag("zh-xiang", "hsn"),
)

BY_CASEFOLDED_TAG = {item.canonical.casefold(): item for item in GRANDFATHERED_TAGS}

