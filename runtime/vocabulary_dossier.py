# SPDX-License-Identifier: MIT
"""The case file of a request for a new vocabulary word (RM-0010 F5).

A generated proposal that needs a word outside the closed vocabulary says so
with the governance marker (the design guide §2.2). This module prepares, without a
model, what the person needs to decide: for each requested word whether the
vocabulary already has it, a verb synonym or an object word covers it, the
naming rules forbid it, or it is really new; for a new word how many
proposals ask for it and how many tools' descriptions already use it.

The decision stays human and is recorded with its reason. The case file is
computed when shown, so it always reflects the current vocabulary.
"""
from __future__ import annotations

import re
from typing import Iterable

from affinity_integrity import VOCABULARY_MARKER

# Words the naming rules of the design guide exclude, so the case file can say so:
# a vector executor *is* the batch version (§2.1, §7.7); `fetch` was retired
# for `get_urls` (§2.2). The closed vocabulary itself is not changed here.
FORBIDDEN_BY_NAMING_RULES = frozenset({"batch", "fetch"})

_QUOTED = re.compile(r"['\"`‘’“”«»]([A-Za-z][A-Za-z0-9_-]{1,29})['\"`‘’“”«»]")
_WINDOW = 300


def _texts(intent) -> list[str]:
    """The proposer's prose: the rationale and the body's text values, never its keys."""
    body = getattr(intent, "intent_body", None) or {}
    values = body.values() if isinstance(body, dict) else [body]
    return [str(getattr(intent, "intent_rationale", "") or "")] + [
        value for value in values if isinstance(value, str)]


def proposer_text(intent) -> str | None:
    """What the proposer wrote after the marker, or None without a request."""
    for text in _texts(intent):
        at = text.casefold().find(VOCABULARY_MARKER.casefold())
        if at >= 0:
            return " ".join(text[at:at + _WINDOW].split())
    return None


def requested_words(intent) -> list[str]:
    """Words the proposer quoted after the marker, in order, once each."""
    text = proposer_text(intent)
    if text is None:
        return []
    return list(dict.fromkeys(match.lower() for match in _QUOTED.findall(text)))


def classify(word: str, affinities: dict[str, Iterable[str]] | None = None) -> dict:
    """One requested word against the vocabulary, its synonyms and the catalog."""
    import vocab
    from prefilter import object_hint_mapping, tokenize

    for kind, values in (("action", vocab.ACTIONS), ("object", vocab.OBJECTS),
                         ("qualifier", vocab.QUALIFIERS), ("system", vocab.SYSTEM_VERBS)):
        if word in values:
            return {"word": word, "verdict": "exists", "term": word, "kind": kind}
    if word in FORBIDDEN_BY_NAMING_RULES:
        return {"word": word, "verdict": "forbidden"}
    for verb, mapping in vocab.ACTION_MAPPING.items():
        forms = {str(form).lower() for language in ("it", "en") for form in mapping.get(language, ())}
        if word in forms:
            return {"word": word, "verdict": "synonym", "term": verb}
    for obj, words in object_hint_mapping().items():
        if word in {str(form).lower() for form in words}:
            return {"word": word, "verdict": "object_word", "term": obj}
    users = sorted(name for name, tags in (affinities or {}).items()
                   if any(word in tokenize(str(tag)) for tag in tags or ()))
    return {"word": word, "verdict": "new", "tools": len(users),
            "objects": len({name.split("_")[1] for name in users if "_" in name})}


def dossier(intent, others: Iterable = (), affinities: dict | None = None) -> dict | None:
    """The case file of ``intent``, or None when it asks for no new word.

    ``others`` are the proposals to count requests of the same word over;
    ``affinities`` the loaded catalog's, to count tools already using it.
    """
    text = proposer_text(intent)
    if text is None:
        return None
    if affinities is None:
        import affinity_integrity
        affinities = affinity_integrity.remembered_affinities()
    asked: dict[str, int] = {}
    for other in others:
        for word in requested_words(other):
            asked[word] = asked.get(word, 0) + 1
    words = []
    for word in requested_words(intent):
        entry = classify(word, affinities)
        if entry["verdict"] == "new":
            entry["requests"] = max(asked.get(word, 0), 1)
        words.append(entry)
    return {"words": words, "proposer": text}
