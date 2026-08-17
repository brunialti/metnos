"""Small locale-neutral BCP47 normalizer used by the offline candidate."""
from __future__ import annotations

import re


_ALNUM = re.compile(r"^[A-Za-z0-9]{1,8}$")
_ALPHA = re.compile(r"^[A-Za-z]+$")


def normalize_language_tag(value: str) -> str:
    """Validate and canonicalize a structural BCP47 tag, without an allowlist."""
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("language tag must be a nonempty BCP47 string")
    parts = value.split("-")
    if any(not _ALNUM.fullmatch(part) for part in parts):
        raise ValueError("malformed BCP47 language tag")
    if parts[0].casefold() == "x":
        if len(parts) < 2:
            raise ValueError("private-use BCP47 tag requires a subtag")
        return "-".join(part.casefold() for part in parts)

    language = parts[0]
    if not _ALPHA.fullmatch(language) or len(language) not in {2, 3, 4, 5, 6, 7, 8}:
        raise ValueError("malformed BCP47 primary language")
    normalized = [language.casefold()]
    offset = 1
    if len(language) in {2, 3}:
        extlangs = 0
        while offset < len(parts) and len(parts[offset]) == 3 and _ALPHA.fullmatch(parts[offset]) and extlangs < 3:
            normalized.append(parts[offset].casefold())
            offset += 1
            extlangs += 1
    if offset < len(parts) and len(parts[offset]) == 4 and _ALPHA.fullmatch(parts[offset]):
        normalized.append(parts[offset].title())
        offset += 1
    if offset < len(parts) and (
        len(parts[offset]) == 2 and _ALPHA.fullmatch(parts[offset])
        or len(parts[offset]) == 3 and parts[offset].isdigit()
    ):
        normalized.append(parts[offset].upper())
        offset += 1
    variants: set[str] = set()
    while offset < len(parts) and (
        5 <= len(parts[offset]) <= 8
        or len(parts[offset]) == 4 and parts[offset][0].isdigit()
    ):
        variant = parts[offset].casefold()
        if variant in variants:
            raise ValueError("duplicate BCP47 variant")
        variants.add(variant)
        normalized.append(variant)
        offset += 1

    singletons: set[str] = set()
    while offset < len(parts) and len(parts[offset]) == 1 and parts[offset].casefold() != "x":
        singleton = parts[offset].casefold()
        if singleton in singletons:
            raise ValueError("duplicate BCP47 extension singleton")
        singletons.add(singleton)
        normalized.append(singleton)
        offset += 1
        start = offset
        while offset < len(parts) and 2 <= len(parts[offset]) <= 8:
            normalized.append(parts[offset].casefold())
            offset += 1
        if offset == start:
            raise ValueError("BCP47 extension requires a subtag")
    if offset < len(parts) and parts[offset].casefold() == "x":
        normalized.append("x")
        offset += 1
        if offset == len(parts):
            raise ValueError("private-use BCP47 suffix requires a subtag")
        normalized.extend(part.casefold() for part in parts[offset:])
        offset = len(parts)
    if offset != len(parts):
        raise ValueError("malformed BCP47 language tag")
    return "-".join(normalized)
