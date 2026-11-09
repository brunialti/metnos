# SPDX-License-Identifier: MIT
"""One affinity rule for every executor, whoever wrote it (RM-0010).

Affinity words are what select a tool, so they are checked the same way for a
builtin, a synthesized or an imported executor:

1. two executors of different families (``verb_object``) must not share most
   of their words; format siblings of one family share them by design;
2. on a tool with a provider (``find_images_google_photos``) every tag needs a
   word of its own, not claimed by the native family, or the provider's name;
3. action words used by more than five tools of several objects tell tools
   apart no better than a verb does: they are derived from the catalog and
   joined to the reviewed lexicon list.

Each kind of executor meets the rule where it enters: the loader refuses a
synthesized or imported one, the release refuses an authored one.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

OVERLAP_THRESHOLD = 0.5
DERIVED_MIN_TOOLS = 5

_seen: dict[str, tuple[str, ...]] = {}
_derived: frozenset[str] | None = None


@dataclass(frozen=True)
class Finding:
    kind: str                # "overlap" | "provider_tag"
    name: str
    other: str               # overlapping executor, or the native family
    terms: tuple[str, ...]


def _family(name: str) -> str:
    return "_".join(name.split("_")[:2])


def _object(name: str) -> str:
    return name.split("_")[1] if "_" in name else name


def _provider(name: str) -> str | None:
    from vocab import PROVIDER_SKILLS
    for provider in sorted(PROVIDER_SKILLS, key=len, reverse=True):
        if name.endswith("_" + provider):
            return provider
    return None


def _tags(affinities: Mapping[str, Iterable[str]]) -> dict[str, set[str]]:
    return {name: {str(tag).strip().lower() for tag in (tags or ()) if str(tag).strip()}
            for name, tags in affinities.items()}


def derived_generic_tokens(affinities: Mapping[str, Iterable[str]]) -> frozenset[str]:
    """Action words claimed by more than five tools spanning several objects."""
    from prefilter import _localized_verb_table, tokenize

    actions = {token for form in _localized_verb_table() for token in tokenize(form)}
    claims: dict[str, set[str]] = defaultdict(set)
    for name, tags in _tags(affinities).items():
        for tag in tags:
            for token in tokenize(tag):
                claims[token].add(name)
    return frozenset(
        token for token, names in claims.items()
        if token in actions and len(names) > DERIVED_MIN_TOOLS
        and len({_object(name) for name in names}) > 1)


def remember_catalog(affinities: Mapping[str, Iterable[str]]) -> None:
    """Keep the affinities of the catalog this process has just loaded.

    Only a copy is taken here: the words are derived on first use, so the
    catalog load does not read the lexicon.
    """
    global _seen, _derived
    _seen = {name: tuple(tags or ()) for name, tags in affinities.items()}
    _derived = None


def derived_tokens() -> frozenset[str]:
    global _derived
    if _derived is None:
        _derived = derived_generic_tokens(_seen) if _seen else frozenset()
    return _derived


def findings(affinities: Mapping[str, Iterable[str]]) -> list[Finding]:
    """Every breach of the rule, for any executor, in a stable order."""
    from prefilter import generic_affinity_verbs, object_hint_mapping, stopwords, tokenize

    tags = _tags(affinities)
    names = sorted(tags)
    out: list[Finding] = []
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            if _family(left) == _family(right) or not tags[left] or not tags[right]:
                continue
            shared = tags[left] & tags[right]
            if len(shared) / len(tags[left] | tags[right]) >= OVERLAP_THRESHOLD:
                out.append(Finding("overlap", left, right, tuple(sorted(shared))))
    ignored = stopwords() | generic_affinity_verbs() | derived_generic_tokens(affinities)
    hints = object_hint_mapping()
    for name in names:
        provider = _provider(name)
        if provider is None:
            continue
        family = _family(name)
        # Words naming the object («foto», «photos») belong to the native
        # family by nature, also when the provider's name contains one.
        object_words = tokenize(_object(name)) | {
            token for word in hints.get(_object(name), ()) for token in tokenize(word)}
        claimed = object_words | {
            token for other in names
            if other != name and _family(other) == family and _provider(other) is None
            for tag in tags[other] for token in tokenize(tag)}
        own_name = {token for part in provider.split("_") for token in tokenize(part)} - object_words
        without_own_word = sorted(
            tag for tag in tags[name]
            if not tokenize(tag) & own_name and not tokenize(tag) - ignored - claimed)
        if without_own_word:
            out.append(Finding("provider_tag", name, family, tuple(without_own_word)))
    return out


# Proposals that need a word outside the closed vocabulary say so with this
# marker (the design guide §2.2 governance, `telos_lenses/_base.py`).
VOCABULARY_MARKER = "RICHIEDE estensione vocab"


def vocabulary_pressure(days: int = 30, *, now: float | None = None) -> dict:
    """F3: affinity refusals and new-word requests over the last ``days``.

    A settling vocabulary yields fewer refusals per new word as the catalog
    grows; a jump points at a word that entered too generic, and at the week
    to look at. Refusals count once per executor and rival.
    """
    import json
    import time
    from datetime import datetime, timezone

    import change_intents
    import loader

    since = (time.time() if now is None else now) - days * 86400
    stamp = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    refused: set[tuple[str, str]] = set()
    try:
        lines = loader._affinity_audit_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and str(entry.get("ts") or "") >= stamp:
            refused.add((str(entry.get("name")), str(entry.get("overlapping_with"))))
    requests = sum(
        1 for intent in change_intents.list_intents(limit=5000)
        if str(intent.discovered_at or "") >= stamp
        and VOCABULARY_MARKER in f"{intent.intent_rationale or ''} {intent.intent_body or ''}")
    return {"days": days, "refused": len(refused), "requests": requests}
