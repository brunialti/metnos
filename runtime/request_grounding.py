# SPDX-License-Identifier: MIT
"""What a request says, where a planner only has to copy it (16/11/2026).

Two arguments were filled by the planner from the nearest word instead of
from the sentence. Measured on the duplicate search, four neutral copies of
the catalog:

- «move the most recent copy of each pair» → `keep="newest"`, 12 plans of 12:
  the file to keep is the oldest. The adjective was put into the argument
  whichever side it described. With longer words («the file with the most
  recent date») the same planner chose right.
- «move the copy …», «move the duplicate …» → `copy=true`, 4 of 4 each: the
  noun was read as the verb. A clearer description of the argument changed
  nothing.

Both are read here from the sentence, with registered words and no model:

- which record of a group stays, by its age: the age word and the nearest
  verb before it in the same sentence. A verb that removes or moves makes it
  the record acted upon, so the other one stays; a verb that keeps makes it
  the one that stays. «Older than 30 days» compares with a quantity of time
  and chooses nothing, «the oldest of each pair» chooses;
- a copy that is named, not asked for: the request has a verb that moves and
  every «copy» in it follows a definite determiner («the copy», «each copy»).

A manifest marks the arguments this applies to with `semantic_type`
(`kept_by_age`, `copy_mode`): no tool is named here. Where the sentence does
not say, or says two things, the planner's value is left as it is.
"""
from __future__ import annotations

import re

import detection_lexicon_seed_parsers as _lexicons

_SENTENCE_END = re.compile(r"[.;:!?\n]+")


def _words(sentence: str) -> list[str]:
    return re.findall(r"\w+", sentence.casefold())


def _phrases(lexicon: dict, concept: str) -> list[tuple[str, ...]]:
    """Registered forms as word tuples, the longest first."""
    forms = {tuple(_words(str(form))) for form in lexicon.get(concept) or ()}
    return sorted((form for form in forms if form), key=len, reverse=True)


def _occurrences(words: list[str], phrases: list[tuple[str, ...]]) -> list[tuple[int, int]]:
    """(start, end) of every registered form, never one inside another."""
    found: list[tuple[int, int]] = []
    position = 0
    while position < len(words):
        size = next((len(form) for form in phrases
                     if tuple(words[position:position + len(form)]) == form), 0)
        if size:
            found.append((position, position + size))
            position += size
        else:
            position += 1
    return found


def kept_by_age(query: str) -> str | None:
    """«oldest» or «newest»: the record of each group the request leaves in
    place. None when the request names no age that way, or names both."""
    lexicon = _lexicons.load_family("grounding")
    if not query or lexicon is None:
        return None
    # The two values of the argument are the two concepts of the lexicon.
    ages = {age: _phrases(lexicon, "parser.grounding." + age)
            for age in ("newest", "oldest")}
    keeps = {form[0] for form in _phrases(lexicon, "parser.grounding.keep_verb")}
    acts = {form[0] for form in _phrases(lexicon, "parser.grounding.act_verb")}
    compares = {form[0] for form in
                _phrases(lexicon, "parser.grounding.comparison_connector")}
    units = _time_units()
    stays: set[str] = set()
    for sentence in _SENTENCE_END.split(query):
        words = _words(sentence)
        for age, forms in ages.items():
            for start, end in _occurrences(words, forms):
                if (end < len(words) and words[end] in compares
                        and any(word.isdigit() or word in units
                                for word in words[end + 1:end + 4])):
                    continue          # a threshold, not a choice
                verb = next((word for word in reversed(words[:start])
                             if word in keeps or word in acts), None)
                if verb is None:
                    continue
                stays.add(age if verb in keeps
                          else next(other for other in ages if other != age))
    return stays.pop() if len(stays) == 1 else None


def _time_units() -> set[str]:
    """The registered words for a unit of time («days», «mesi»)."""
    family = _lexicons.load_family("time_resolver") or {}
    units = family.get("parser.time.unit") or {}
    return {str(form).casefold() for forms in units.values() for form in forms
            if len(str(form)) > 1}


def copy_only_named(query: str) -> bool:
    """The request moves something and every «copy» in it is a thing it
    names: nothing asks to copy."""
    lexicon = _lexicons.load_family("grounding")
    if not query or lexicon is None:
        return False
    copies = {form[0] for form in _phrases(lexicon, "parser.grounding.copy_word")}
    moves = {form[0] for form in _phrases(lexicon, "parser.grounding.move_verb")}
    determiners = {form[0] for form in
                   _phrases(lexicon, "parser.grounding.definite_determiner")}
    # A folder called «copie» is a place, not a word of the request.
    from tool_grammar import _RE_FS_PATH_TOKEN

    words = _words(_RE_FS_PATH_TOKEN.sub(" ", query))
    named = [position for position, word in enumerate(words) if word in copies]
    asked = {form[0] for form in _phrases(lexicon, "parser.grounding.copy_verb")}
    if not named or not moves.intersection(words) or asked.intersection(words):
        return False
    # «la copia», «ogni singola copia»: the determiner, or one word after it.
    return all(determiners.intersection(words[max(0, position - 2):position])
               for position in named)


def _marked(schema, kind: str) -> list[str]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    return [name for name, spec in (properties or {}).items()
            if isinstance(spec, dict) and spec.get("semantic_type") == kind]


def apply_to_framework(framework, query: str, catalog):
    """Give the marked arguments of a plan what the request says."""
    steps = list(getattr(framework, "steps", None) or [])
    if not steps or not query:
        return framework
    schemas = {}
    for entry in catalog or ():
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        schema = (entry.get("args_schema") or entry.get("args")) if isinstance(entry, dict) \
            else getattr(entry, "args_schema", None)
        if isinstance(name, str) and isinstance(schema, dict):
            schemas[name] = schema
    stays = named_only = None
    for step in steps:
        schema = schemas.get(getattr(step, "tool", None) or "")
        if schema is None:
            continue
        args = dict(step.args or {})
        for name in _marked(schema, "kept_by_age"):
            stays = kept_by_age(query) if stays is None else stays
            if stays and args.get(name) != stays:
                args[name] = stays
        for name in _marked(schema, "copy_mode"):
            if args.get(name) is True:
                named_only = copy_only_named(query) if named_only is None else named_only
                if named_only:
                    del args[name]
        if args != (step.args or {}):
            step.args = args
    return framework


def guards(guard_type) -> tuple:
    """The entry of this module in the planner's guard pipeline."""
    return (guard_type(
        "ground_request_words",
        lambda framework, _intent, query, catalog: apply_to_framework(
            framework, query, catalog),
        scope="per-clause",
        writes=frozenset({"args.@kept_by_age", "args.@copy_mode"}),
        reads=frozenset({"catalog", "step.tool", "args.@kept_by_age",
                         "args.@copy_mode"}),
        rationale="an argument marked kept_by_age or copy_mode takes what the "
                  "sentence says: which record of a group stays by its age; "
                  "a copy that is only named is not a request to copy",
        adr="0138"),)
