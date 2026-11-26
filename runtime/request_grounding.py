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

- which record of a group stays, by its age: the age word and the verb that
  governs it. A verb that removes or moves makes it the record acted upon, so
  the other one stays; a verb that keeps makes it the one that stays. The
  age is read only inside a form this reader knows entirely: every word of
  its clause, from the first to the last, plays a part the lexicon registers
  — the verb, the thing it acts on («the copies», «il file», «quelli»), the
  group a record is chosen from («di ogni coppia», «dei duplicati»), the
  date it is chosen by («con la data»), the place it goes to («in /tmp/x»,
  «nella cartella X», «lì»). A word that plays none of these, a number, or a
  link that opens a second thing between what the verb acts on and the age
  («the copies of the old photos», «all but the newest», «the copies next
  to the oldest», «newer than 2020», «older than 30 days») makes the age
  named but not read: the forms that except, replace or relate are an open
  class, the forms read are a closed one. One age named and not read is
  enough to read nothing: the plan is left as the planner wrote it, and the
  question before a removal says which record stays;
- a copy that is named, not asked for: the request has a verb that moves and
  every «copy» in it follows a definite determiner («the copy», «each copy»).

A manifest marks the arguments this applies to with `semantic_type`
(`kept_by_age`, `copy_mode`, `set_operator`): no tool is named here. Where the sentence says
something this reader cannot settle (two ages, an age in a form it does not
know), the planner's value is left as it is.

Where the request is really silent about what stays (no age, no verb that
keeps, no denial, and what it removes said in plain words: «delete the
copies»), a value the planner wrote for `kept_by_age` has no ground and is
taken out: the default the tool declares applies. One word this reader does
not know in the clause that removes («the previous copy», «the originals»)
is enough to leave the plan alone. The planner is shown the two
values of such an argument and nothing of what they mean, so what it writes
for a silent request is not a reading. Measured on «find the duplicate files
in X and delete the copies», four neutral copies of the catalog: the oldest
kept or nothing written in 4 plans of 4; with one more argument declared by
the same tool, the newest kept in 3 of 4; with a shorter description, in 4 of
4 (`misura_pianificatore_4`, 16/11/2026).
"""
from __future__ import annotations

import re

import detection_lexicon_seed_parsers as _lexicons

_SENTENCE_END = re.compile(r"[.;:!?\n]+")
# A path stands among the words as one mark: a place, not words of the request.
_PATH = chr(0xE000)      # a private-use character: never a word
# The marks that pause a sentence without ending it.
_PAUSES = ",()[]{}«»\"“”„—–"
_TOKEN = re.compile(rf"\w+|{_PATH}|[{re.escape(_PAUSES)}]")
_AGES = ("newest", "oldest")
# The part a word plays in a form this reader knows, and the concept that
# registers the words for it.
_PARTS = {
    "keep": "keep_verb", "act": "act_verb", "find": "find_verb",
    "article": "article", "each": "each_word", "quantity": "quantity_word",
    "record": "record_word", "copy": "copy_word", "group": "group_word",
    "of": "group_link", "to": "place_link", "place": "place_word",
    "there": "place_adverb", "denial": "negation",
    "restriction": "restriction_link",
}
_VERBS = frozenset({"keep", "act", "find"})
_HEADS = ("record", "copy", "group")
# A word that is none of the parts above, or only the name of a thing, can
# be the name of a place («nella cartella Immagini»).
_NAMEABLE = frozenset(_HEADS)


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


def _sentence_tokens(query: str) -> list[list[str]]:
    """Each sentence as its words, a path as one mark, the pauses kept."""
    from tool_grammar import _RE_FS_PATH_TOKEN

    def mark(found) -> str:
        # The pattern takes the comma or the full stop after a path with it.
        token = found.group(0)
        return f" {_PATH} " + token[len(token.rstrip(_PAUSES + ".;:!?")):]

    return [_TOKEN.findall(sentence.casefold())
            for sentence in _SENTENCE_END.split(_RE_FS_PATH_TOKEN.sub(mark, query))]


def _single(lexicon: dict, concept: str) -> set[str]:
    return {form[0] for form in _phrases(lexicon, "parser.grounding." + concept)}


def _age_mentions(lexicon: dict, tokens: list[str]) -> list[tuple[str, int, int]]:
    """(age, start, end) of every age the tokens name.

    A form with a degree wins over the plain word inside it («meno recente»
    is not «recente»). A plain word between an article or a link and a place
    or a product of the request («a new folder», «nel nuovo foglio») is the
    age of that, not one the request chooses by: it is left out.
    """
    leads = (_single(lexicon, "article") | _single(lexicon, "each_word")
             | _single(lexicon, "place_link"))
    places = _single(lexicon, "place_word")
    found: list[tuple[str, int, int]] = []
    covered: set[int] = set()
    for age in _AGES:
        for start, end in _occurrences(tokens, _phrases(lexicon, "parser.grounding." + age)):
            found.append((age, start, end))
            covered.update(range(start, end))
    for age in _AGES:
        for start, end in _occurrences(
                tokens, _phrases(lexicon, f"parser.grounding.{age}_plain")):
            of_a_place = (start > 0 and tokens[start - 1] in leads
                          and end < len(tokens) and tokens[end] in places)
            if start not in covered and not of_a_place:
                found.append((age, start, end))
    return found


def _stretches(lexicon: dict, tokens: list[str],
               mentions: list[tuple[str, int, int]]) -> list[tuple[bool, list]]:
    """The sentence as stretches of words between its pauses and its joining
    words («e», «then»). Each word is (the parts it can play, where it is);
    an age the sentence names is one word, and so is a registered link to a
    date or word for one. The flag says a joining word stands before the
    stretch: after a pause alone, the words may still belong to the verb."""
    parts = {part: _single(lexicon, concept) for part, concept in _PARTS.items()}
    breaks = _single(lexicon, "clause_break")
    plain = _single(lexicon, "newest_plain") | _single(lexicon, "oldest_plain")
    whole = {start: (end, frozenset({"age"})) for _age, start, end in mentions}
    for part, concept in (("dated", "date_link"), ("date", "date_word"),
                          ("ask", "request_lead")):
        for start, end in _occurrences(tokens, _phrases(lexicon, "parser.grounding." + concept)):
            whole.setdefault(start, (end, frozenset({part})))
    stretches: list[tuple[bool, list]] = []
    words: list = []
    joined = False
    position = 0
    while position <= len(tokens):
        token = tokens[position] if position < len(tokens) else ","
        if position in whole:
            end, kinds = whole[position]
            words.append((kinds, position))
            position = end
            continue
        if token in breaks or token in _PAUSES:
            if words:
                stretches.append((joined, words))
                words, joined = [], False
            joined = joined or token in breaks
        elif token == _PATH:
            words.append((frozenset({"path"}), position))
        elif token in plain:
            words.append((frozenset({"aged_place"}), position))
        else:
            kinds = {part for part, forms in parts.items() if token in forms}
            if kinds <= _NAMEABLE:
                kinds.add("name")
            words.append((frozenset(kinds), position))
        position += 1
    return stretches


def _step(kinds: list, at: set[int], *parts: str) -> set[int]:
    """After one word that plays one of the parts."""
    return {position + 1 for position in at
            if position < len(kinds) and kinds[position].intersection(parts)}


def _run(kinds: list, at: set[int], *parts: str) -> set[int]:
    """After any number of words that play one of the parts."""
    seen = set(at)
    edge = seen
    while edge:
        edge = _step(kinds, edge, *parts) - seen
        seen |= edge
    return seen


def _object(kinds: list, at: set[int]) -> set[int]:
    """Where the thing a verb acts on can end: «le copie», «all the old
    files», «il più recente», «quelli recenti», «il file con la data più
    vecchia», «tutti». One thing, with its age before or after its name and
    nothing else between the two."""
    bare = _run(kinds, at, "quantity")
    led = _run(kinds, _step(kinds, bare, "article", "each"), "quantity")
    named = _run(kinds, _step(kinds, bare | led, *_HEADS), *_HEADS)
    dated = _step(kinds, named, "dated")
    dated |= _step(kinds, dated, "article")
    return (_run(kinds, _step(kinds, at, "quantity"), "quantity")
            | _run(kinds, _step(kinds, led, "age"), *_HEADS)
            | _run(kinds, _step(kinds, _step(kinds, bare, "age"), *_HEADS), *_HEADS)
            | named | _step(kinds, named, "age")
            | _step(kinds, _step(kinds, dated, "date"), "age")
            | _step(kinds, _step(kinds, dated, "age"), "date"))


def _group(kinds: list, at: set[int]) -> set[int]:
    """«di ogni coppia», «dei duplicati», «for each file»: the group a
    record is chosen from. «Of the files», «di quelli» can be a comparison
    («newer than those») and is not one."""
    link = _step(kinds, at, "of")
    each = _step(kinds, link, "each")
    some = link | each | _step(kinds, link, "article")
    return (_step(kinds, each, *_HEADS)
            | _run(kinds, _step(kinds, some, "copy", "group"), "copy", "group"))


def _place(kinds: list, at: set[int]) -> set[int]:
    """«lì», «in /tmp/x», «nella cartella /tmp/x», «in quella cartella», «to
    a new folder»: where the records are or go. A name after the place word
    is read only where the place closes the clause (`_described`): before
    what the verb acts on, the word there may be one that excepts («nella
    cartella escluso il più recente»)."""
    link = _step(kinds, at, "to")
    led = link | _step(kinds, link, "article", "each")
    aged = led | _step(kinds, led, "aged_place")
    place = _step(kinds, aged | _step(kinds, aged, "name"), "place")
    return (_step(kinds, at, "there") | _step(kinds, led, "path")
            | place | _step(kinds, place, "path"))


def _described(kinds: list, at: set[int]) -> set[int]:
    """What is said of a place closes the clause: its name, where it is, how
    it was made («in una cartella X creata dentro quella stessa cartella»).
    It holds no age, denial, or verb of this reader: those would be said of
    something else, in a form this reader does not know. In particular a
    trailing denied clause must not be swallowed into this description."""
    closing = {position for position in range(len(kinds) + 1)
               if not any(kinds[later] & (_VERBS | {"age", "denial"})
                          for later in range(position, len(kinds)))}
    return {len(kinds)} if closing.intersection(at) else set()


def _around(kinds: list, at: set[int], described: bool = False) -> set[int]:
    """After any number of groups and places. After the thing a verb acts
    on, a place may be described."""
    seen = set(at)
    edge = seen
    while edge:
        places = _place(kinds, edge)
        if described:
            places |= _described(kinds, places)
        edge = (_group(kinds, edge) | places) - seen
        seen |= edge
    return seen


def _verbs(kinds: list) -> tuple[int, ...] | None:
    """Where the verbs are, when the words are from the first to the last a
    form this reader knows: how the request is put («puoi», «please»), a
    group or a place, then each verb with the one thing it acts on and the
    groups and places around that. None otherwise."""
    size = len(kinds)
    tried: dict[int, tuple[int, ...] | None] = {}

    def from_verb(position: int) -> tuple[int, ...] | None:
        if position == size:
            return ()
        if position not in tried:
            tried[position] = None
            if kinds[position] & _VERBS:
                before = _around(kinds, {position + 1})
                ends = before | _around(kinds, _object(kinds, before), described=True)
                for end in sorted(ends, reverse=True):
                    rest = from_verb(end)
                    if rest is not None:
                        tried[position] = (position, *rest)
                        break
        return tried[position]

    asked = _run(kinds, {0}, "ask")
    for start in sorted(_run(kinds, _around(kinds, asked), "ask")):
        if start < size and (found := from_verb(start)):
            return found
    return None


def _governed(lexicon: dict, tokens: list[str],
              mentions: list[tuple[str, int, int]]) -> dict[int, str]:
    """For each age the sentence names (by where it starts), the verb that
    governs it — only where the words around it are a form this reader knows
    entirely.

    A stretch after a pause with no verb of its own can complete the verb
    before it («sposta lì, per ogni coppia, il file …»). A stretch that comes
    after a verb of this reader standing in a form it does not know is not
    read: what governs that verb («non devi cancellare e spostare …») may
    govern this one too."""
    stretches = _stretches(lexicon, tokens, mentions)

    def kinds_of(words: list) -> list:
        return [kinds for kinds, _position in words]

    governed: dict[int, str] = {}
    sure = True
    index = 0
    while index < len(stretches):
        words = list(stretches[index][1])
        kinds = kinds_of(words)
        if any("denial" in kind for kind in kinds):
            # Only a registered restriction link can introduce an unrelated
            # operation («without opening documents»). A named file/place
            # alone cannot close «for these files do not, ...». The operation
            # must name its own object, contain no age or known action, and
            # not coordinate the following clause under the same restriction.
            separate = (len(kinds) > 2 and "restriction" in kinds[0]
                        and kinds[1] == {"name"}
                        and any(kind & (set(_HEADS) | {"place"}) for kind in kinds[2:])
                        and not any(kind & (_VERBS | {"age", "denial"})
                                    for kind in kinds[1:])
                        and not (index + 1 < len(stretches) and stretches[index + 1][0]))
            if not separate:
                return {}
        verbs = _verbs(kinds)
        index += 1
        while (verbs is not None and index < len(stretches)
               and not stretches[index][0]
               and not any(kinds & _VERBS for kinds in kinds_of(stretches[index][1]))):
            longer = words + stretches[index][1]
            more = _verbs(kinds_of(longer))
            if more is None:
                break
            words, verbs, index = longer, more, index + 1
        if verbs is not None and sure:
            for place, (kinds, position) in enumerate(words):
                verb = max((at for at in verbs if at < place), default=None)
                if "age" in kinds and verb is not None:
                    governed[position] = tokens[words[verb][1]]
        if any(kinds & {"keep", "act"} for kinds in kinds_of(words)):
            sure = sure and verbs is not None
        else:
            # Denied actions have already rejected the whole sentence above;
            # an inserted clause cannot make them affirmative again. Preserve the
            # ordinary reading of unrelated descriptions without a denial.
            sure = True
    return governed


def kept_by_age(query: str) -> str | None:
    """«oldest» or «newest»: the record of each group the request leaves in
    place. None when the request names no age, names both, or names one
    outside a form this reader knows entirely."""
    lexicon = _lexicons.load_family("grounding")
    if not query or lexicon is None:
        return None
    keeps, acts = _single(lexicon, "keep_verb"), _single(lexicon, "act_verb")
    stays: set[str] = set()
    for tokens in _sentence_tokens(query):
        mentions = _age_mentions(lexicon, tokens)
        if not mentions:
            continue
        # Negation belongs to the clause it qualifies. The closed grammar
        # rejects a denied verb/object and _governed carries that uncertainty
        # across coordinated actions. An unrelated clause («without opening
        # documents») does not negate a separately asserted move.
        governed = _governed(lexicon, tokens, mentions)
        for age, start, _end in mentions:
            verb = governed.get(start)
            if verb in keeps:
                stays.add(age)
            elif verb in acts:
                # The two values of the argument are the two ages of the lexicon.
                stays.add(next(other for other in _AGES if other != age))
            else:
                return None     # an age named and not read: nothing is read
    return stays.pop() if len(stays) == 1 else None


def _removal_said_plainly(lexicon: dict, query: str) -> bool:
    """Every clause that removes or moves is made of words that say nothing
    of which record: «delete the copies», «move them to a new folder»."""
    acts = _single(lexicon, "act_verb")
    asking = {word for form in _phrases(lexicon, "parser.grounding.request_lead")
              for word in form}
    plain = set().union(acts, asking, {_PATH}, *(_single(lexicon, concept) for concept in (
        "definite_determiner", "record_word", "copy_word", "place_word",
        "newest_plain", "oldest_plain", "article", "each_word",
        "quantity_word", "group_word", "group_link", "place_link",
        "place_adverb", "neutral_word")))
    breaks = _single(lexicon, "clause_break")
    seen = False
    for tokens in _sentence_tokens(query):
        clause: list[str] = []
        for token in [*tokens, ","]:
            if token not in breaks and token not in _PAUSES:
                clause.append(token)
                continue
            if acts.intersection(clause):
                if not plain.issuperset(clause):
                    return False
                seen = True
            clause = []
    return seen


def says_what_stays(query: str) -> bool:
    """False only for a request that is really silent on which record stays:
    it names no age, uses no verb that keeps, denies nothing, and what it
    removes or moves is said in plain words («delete the copies»). A word
    this reader does not know in that clause («the previous copy», «the
    originals», «the first one») may be the choice: the plan is left alone."""
    lexicon = _lexicons.load_family("grounding")
    if not query or lexicon is None:
        return True         # nothing to read with: the plan is left alone
    keeps, denials = _single(lexicon, "keep_verb"), _single(lexicon, "negation")
    for tokens in _sentence_tokens(query):
        if (keeps.intersection(tokens) or denials.intersection(tokens)
                or _age_mentions(lexicon, tokens)):
            return True
    return not _removal_said_plainly(lexicon, query)


def _has_default(schema, name: str) -> bool:
    spec = (schema.get("properties") or {}).get(name)
    required = schema.get("required")
    return (isinstance(spec, dict) and "default" in spec
            and name not in (required if isinstance(required, list) else ()))


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


def _schemas(catalog) -> dict:
    schemas = {}
    for entry in catalog or ():
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        schema = (entry.get("args_schema") or entry.get("args")) if isinstance(entry, dict) \
            else getattr(entry, "args_schema", None)
        if isinstance(name, str) and isinstance(schema, dict):
            schemas[name] = schema
    return schemas


def kept_note(steps, position: int, catalog) -> str:
    """One sentence for the question before a removal: which record of each
    group stays, when the rows about to be removed come from a step that
    chose them by age. A reading that went wrong is then seen before the yes.
    Empty when no step behind this one chose that way."""
    from messages import get as message

    schemas = _schemas(catalog)
    seen: set[int] = set()
    while 0 <= position < len(steps) and position not in seen:
        seen.add(position)
        step = steps[position]
        args = step.args if isinstance(getattr(step, "args", None), dict) else {}
        schema = schemas.get(getattr(step, "tool", None) or "")
        for name in _marked(schema, "kept_by_age") if schema else ():
            value = args.get(name, (schema["properties"][name] or {}).get("default"))
            if value in _AGES:
                return message("MSG_KEPT_BY_AGE_" + str(value).upper())
        source = args.get("from_step")
        if not isinstance(source, int) or isinstance(source, bool):
            break
        position = source - 1
    return ""


def apply_to_framework(framework, query: str, catalog):
    """Give the marked arguments of a plan what the request says."""
    steps = list(getattr(framework, "steps", None) or [])
    if not steps or not query:
        return framework
    schemas = _schemas(catalog)
    stays = named_only = said = disjunction = None
    for step in steps:
        schema = schemas.get(getattr(step, "tool", None) or "")
        if schema is None:
            continue
        args = dict(step.args or {})
        for name in _marked(schema, "kept_by_age"):
            stays = kept_by_age(query) if stays is None else stays
            if stays:
                args[name] = stays
            elif name in args and _has_default(schema, name):
                said = says_what_stays(query) if said is None else said
                if not said:
                    del args[name]      # no ground in the request: the default
        for name in _marked(schema, "copy_mode"):
            if args.get(name) is True:
                named_only = copy_only_named(query) if named_only is None else named_only
                if named_only:
                    del args[name]
        for name in _marked(schema, "set_operator"):
            if args.get(name) == "or" and _has_default(schema, name):
                if disjunction is None:
                    lexicon = _lexicons.load_family("grounding")
                    words = [word for sentence in _sentence_tokens(query) for word in sentence]
                    disjunction = lexicon is None or bool(_occurrences(
                        words, _phrases(lexicon, "parser.grounding.disjunction")))
                if not disjunction:
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
        writes=frozenset({"args.@kept_by_age", "args.@copy_mode", "args.@set_operator"}),
        reads=frozenset({"catalog", "step.tool", "args.@kept_by_age",
                         "args.@copy_mode", "args.@set_operator"}),
        rationale="an argument marked kept_by_age, copy_mode or set_operator takes what the "
                  "sentence says: which record of a group stays by its age; "
                  "a copy that is only named is not a request to copy; "
                  "a union requires a disjunction in the request",
        adr="0138"),)
