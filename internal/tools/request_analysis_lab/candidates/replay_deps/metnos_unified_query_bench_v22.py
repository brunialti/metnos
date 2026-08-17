#!/usr/bin/env python3
"""Non-invasive A/B bench for a unified structured request analyzer.

Nothing in this file is imported by production.  The candidate performs verb
normalization and intent extraction in one constrained LLM call.  It emits
token references and canonical ontology values, never a rewritten query.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path("/opt/metnos")
sys.path.insert(0, str(ROOT / "runtime"))

from intent_extractor import extract_intent  # noqa: E402
from llm_router import LLMRouter, tier_endpoint  # noqa: E402
from llm_workloads import tier_for  # noqa: E402
from vocab import (ACTIONS, OBJECTS, QUALIFIERS, qualifier_compatible,
                   render_boundaries)  # noqa: E402


ROLE_VALUES = ("request", "forbid", "condition", "description", "quote")
RESOURCE_SCOPE_VALUES = (
    "single_known", "collection_by_criterion", "whole_domain",
    "held_result", "new_resource", "not_applicable",
)
SINK_MODE_VALUES = ("none", "explicit_persistence")
CARRIER_ROLE_VALUES = (
    "none", "source_container", "transport_channel", "representation",
)
SINK_RELATION_VALUES = (
    "none", "primary_output", "secondary_persistence",
)
EFFECT_VALUES = (
    "content_read", "discovery", "existence_check", "snapshot",
    "metadata_lookup", "create_artifact", "update_artifact", "delete",
    "move", "select_subset", "label_assignment", "collection_grouping",
    "transform", "human_delivery", "access_grant", "attachment_reference",
    "biometric_identity_lookup", "schedule", "other",
)


SEMANTIC_HEAD_INSTRUCTION = """\
Analyze predicates in a request written in ANY language. This pass is strictly
linguistic-semantic and independent of product routing, tools, objects, and
arguments. Return one record for every predicate that could otherwise be
mistaken for an operation, in source order.

`semantic_action` is the closest canonical action to the predicate's own
lexical or idiomatic meaning after resolving inflection and attached pronouns.
Do not replace it because a different operation could process its arguments.
`role` distinguishes a user request from a prohibited, conditional,
descriptive, or quoted predicate. `predicate_anchor_token_id` is one numbered
source token anchoring the predicate. Output JSON only. Never rewrite or
translate the request.
"""


def _catalog_output_artifact_qualifiers() -> tuple[str, ...]:
    """Derive output-only format modes from the technical catalog snapshot."""
    try:
        rows = json.loads(
            (ROOT / "tests/benchmarks/catalog_snapshot.json").read_text())
        names = {str(row.get("name") or "") for row in rows}
        created = {name.removeprefix("create_files_") for name in names
                   if name.startswith("create_files_")}
        writable = {name.removeprefix("write_files_") for name in names
                    if name.startswith("write_files_")}
        return tuple(sorted((created & writable) & set(QUALIFIERS)))
    except Exception:
        return ()


OUTPUT_ARTIFACT_QUALIFIERS = _catalog_output_artifact_qualifiers()


def _technical_catalog_indexes() -> tuple[set[tuple[str, str]], dict[str, set[str]], set[str]]:
    """Generate action/object and argument indexes from technical contracts."""
    pairs: set[tuple[str, str]] = set()
    action_objects: dict[str, set[str]] = {}
    argument_names: set[str] = set()
    try:
        rows = json.loads(
            (ROOT / "tests/benchmarks/catalog_snapshot.json").read_text())
        for row in rows:
            name = str(row.get("name") or "")
            parts = name.split("_")
            if len(parts) >= 2 and parts[0] in ACTIONS and parts[1] in OBJECTS:
                pair = (parts[0], parts[1])
                pairs.add(pair)
                action_objects.setdefault(parts[0], set()).add(parts[1])
            props = ((row.get("args_schema") or {}).get("properties") or {})
            argument_names.update(str(value).casefold() for value in props)
    except Exception:
        pass
    try:
        from tool_grammar import _UNIVERSAL_HELPERS
        universal = {name.removesuffix("_entries") for name in _UNIVERSAL_HELPERS
                     if name.endswith("_entries")}
    except Exception:
        universal = set()
    return pairs, action_objects, argument_names | universal


CATALOG_ACTION_OBJECT_PAIRS, CATALOG_OBJECTS_BY_ACTION, _CATALOG_AUX = (
    _technical_catalog_indexes()
)
CATALOG_ACTIONS_BY_OBJECT: dict[str, set[str]] = {}
for _catalog_action, _catalog_object in CATALOG_ACTION_OBJECT_PAIRS:
    CATALOG_ACTIONS_BY_OBJECT.setdefault(_catalog_object, set()).add(
        _catalog_action)
try:
    from tool_grammar import _UNIVERSAL_HELPERS as _UH
    UNIVERSAL_ACTIONS = {
        name.removesuffix("_entries") for name in _UH
        if name.endswith("_entries")
    }
except Exception:
    UNIVERSAL_ACTIONS = set()
CATALOG_ARGUMENT_NAMES = _CATALOG_AUX - UNIVERSAL_ACTIONS


def _lexically_related(left: str, right: str, *, prefix: int = 5) -> bool:
    """Language-neutral similarity over canonical English technical labels."""
    a = re.sub(r"[^a-z]", "", (left or "").casefold())
    b = re.sub(r"[^a-z]", "", (right or "").casefold())
    if not a or not b:
        return False
    common = 0
    for av, bv in zip(a, b):
        if av != bv:
            break
        common += 1
    return common >= prefix and common >= min(len(a), len(b), prefix)


OBJECT_CONTRACT = """\
Object is the technical entity the operation acts on, not a grammatical word.
Use files for generic filesystem I/O, including ordinary image/text files;
use images only when visual content itself is searched or analyzed. Use dirs
when the directory/container is the unit being measured, created, deleted or
enumerated. Use entries for structured records already in memory or in an
explicit internal record store. Use messages for mail/chat content or an
outbound human message; events for calendar records; persons for the enrolled
identity/face registry; contacts for an address book; urls only for an explicit
web resource/source; processes for operating-system process or machine-health
snapshots. Choose the closest remaining ontology name by its ordinary technical
meaning. Never infer an object from a pronoun alone: resolve its antecedent.
"""


ONTOLOGY_REFINEMENTS = """\
Product ontology refinements (technical behavior, independent of input
language):

1. A direct information question is a requested operation even when it has no
imperative surface verb. A request for raw/full content is read, not describe;
describe is only condensation, synthesis, or aggregation.
2. Identity enrollment and biometric face-registry membership belong to
persons. Creating/updating membership is set, deleting membership is delete,
reading a known person's information is read, and taking a registry snapshot is
get. Ordinary structured business records belong to entries, not persons.
3. Calendar commitments belong to events. Reading one's commitments is read;
discovering candidate availability by a criterion is find; creating a booking
is create.
4. Mailbox folders are part of messages. Moving a message into another mailbox
is move, not set. Selecting a subset without destroying source data is filter;
delete is only actual removal from the source of truth.
5. For filesystem entities: content of a known file/path is read; attributes of
a known file are get; existence, pattern search, count of matching elements, or
selection by a criterion is find; enumerating members of a known container is
list. Aggregate inspection of directories uses find/dirs in this ontology.
A destination path mentioned with a requested download does not create a
second write action unless writing/saving is independently requested.
6. Images is used for visual search, visual description/classification, and OCR
reading of image pixels. Generic filesystem operations and file metadata on an
image use files. Metadata enrichment attached to photo files uses get/files.
7. packages denotes installed software; existence/installation-state checks are
find/packages. processes denotes running processes and machine-health snapshots;
process existence is find/processes, while a whole machine/process snapshot is
get/processes. numbers includes clock/time scalar answers. places includes the
user's own location and geographic place lookup.
8. A predicate in a relative/passive clause describes the requested entity and
is not an additional operation. A negated or excluded operation is forbid, even
when expressed by an infinitive or attached pronoun.
"""


TIE_BREAK_REFINEMENTS = """\
Mandatory canonical tie-breaks:

- A person/guest's enrollment, registration, or membership in an identity
registry is persons even when the registry itself is not named. A question
about the requester's own identity is read/persons. Enumeration of registered
people is get/persons; list/persons is not a canonical intent.
- A request to transfer existing messages between mailboxes is move/messages.
- Reading/downloading the content represented by an already named file is
read/files. Merely naming a local destination/container in the same clause does
not change that predicate to write; a separate save/write predicate would be a
separate request record.
- Selecting, keeping, excluding, or discarding members according to a property
is filter. It is neither delete nor forbid because source data remains intact.
Role forbid is only for a prohibited operation, not for a negative selection
criterion.
- Directory/container aggregate size is find/dirs in the Metnos intent
ontology, never get/dirs.
- Adding or retrieving descriptive metadata for photo files is get/files,
unless the request explicitly changes pixel content. It is not set/images.
"""


STRUCTURE_REFINEMENTS = """\
`predicate_anchor_token_id` identifies one source token anchoring the predicate,
never its patient, object, filters, paths, destinations, or dates. It is only a
stable graph identifier; canonical meaning lives in `verb` and `object`.

Acquiring/downloading content is read even when the same clause names where the
result should land. Writing is a separate intent only when creation, saving, or
replacement is independently commanded. Reading text encoded in image pixels,
including screenshots, is read/images. A requested operation that filters out
members is request+filter; it is not a prohibited operation.

A yes/no check that software is installed, present, or available on the machine
is find/packages. A check that one named process, service, or daemon is actively
running is find/processes. Use get/processes only for a snapshot/list of process
or machine-health data. Do not infer active execution from mere software
presence.
"""


COMPOUND_REFINEMENTS = """\
Pipeline and routability refinements:

- `input_from_predicate_anchor_id` is a graph edge, not a record ordinal. Use 0
  when the predicate has an explicit patient or no data dependency. Otherwise
  use the `predicate_anchor_token_id` of the earlier predicate whose result
  supplies this predicate's elided patient. Never put a record number there.
- A whole-domain operating-system process observation, including ranking or
  selecting current processes by resource use, is get/processes. Only the
  running-state/existence check for one named process is find/processes.
- A command to produce a report/document artifact is write/files. Searching
  within a mailbox remains messages; an explicit internal structured-record
  store is entries. Persisting extracted records to that store is a separate
  write/entries request.
- `group` partitions/combines a held collection; `classify` assigns a category
  or label to each member. Tasks represent scheduled automated work; events
  represent calendar commitments and reminders.
- A transfer to a human recipient or human communication channel is
  send/messages regardless of the payload type. An explicit HTTP(S) source is
  urls regardless of the downloaded representation. Public-web discovery is
  find/urls; it is neither read/urls nor discovery of the subject-matter object.
- Generic movement, compression, persistence, attachment lookup, metadata
  lookup, and access sharing of an existing image file use files. Use images
  only when the operation acts on visual content itself. Recognizing enrolled
  identities in visual content is get/persons.
- `object_qualifier` is normally none. Set it only when a requested persistent
  output artifact requires a distinct canonical technical route; a requested
  spreadsheet artifact uses the canonical spreadsheet qualifier. Never copy a
  source filename extension or input representation into this field.
"""


STRICT_COMPOUND_REFINEMENTS = """\
Before emitting JSON, enforce every technical invariant below. These define
canonical product operations and override literal surface-verb similarity:

1. A resource-ranked or filtered observation of the current process table is
   get/processes. find/processes is reserved for the existence/running-state of
   one named process.
2. Assigning a category/label to every member is classify, including when the
   category key is a date or place. group only partitions/combines data without
   assigning labels.
3. Delivery of any payload to a person or communication channel is
   send/messages. Granting continuing access to an existing resource is
   share/files. These operations are distinguished by effect, not recipient.
4. Reading/downloading from an explicit HTTP(S) source is read/urls. Searching
   the public web is find/urls regardless of the subject being sought.
5. A named existing local attachment is get/files. Generic persistence,
   movement, compression, metadata inspection, and sharing of image files use
   files; pixel/visual analysis alone uses images. Biometric identity lookup in
   visual content is get/persons, not classification.
6. An explicit persistent structured-record store is a distinct write/entries
   sink. If extraction produces records for that sink, emit both extract/entries
   and write/entries even when expressed in one grammatical clause.
7. Creating a new specialized artifact uses create, while updating an existing
   one uses write. Use an output artifact qualifier only for that newly created
   or updated artifact; all source formats have qualifier none.

Check that no emitted request violates these seven invariants.
"""


ANCHOR_AND_SINK_REFINEMENTS = """\
Structural precedence rules:

- Emit exactly one increasing predicate anchor per record. The anchor is a
  source-token identifier, not a copied span and not a record ordinal.
- If the user's predicate semantically names one canonical action explicitly,
  preserve that action. Do not replace classify with group, or share with send,
  merely because an argument could also support the latter action. Apply a
  product boundary override only where the technical ontology explicitly
  requires one, such as a process-table snapshot.
- `materialize_to` records a persistent sink expressed inside the same predicate
  when there is no separate save/write predicate anchor. Use the canonical sink
  object, otherwise none. Extraction directed into the structured-record store
  therefore has materialize_to=entries. If saving/writing has its own predicate,
  emit that predicate instead and leave materialize_to=none.
"""


VERB_LAYER_REFINEMENTS = """\
Keep lexical-semantic normalization separate from product routing:

- Each `semantic_heads` record is generated before product routing. Its
  `semantic_action` is the canonical action that best preserves the predicate's
  own meaning, independent of its arguments and of executor conventions. It is
  an enum, never a rewritten word. Inflection, clitics, idiom and language are
  normalized here.
- `verb` is the final Metnos operation. `verb_resolution` is direct when the
  predicate already has that canonical meaning, generalized for an idiom or
  language-level semantic generalization, and technical_override only when an
  explicit product boundary requires `verb` to differ from `semantic_verb`.
- With direct or generalized resolution, semantic_action and verb MUST be equal.
  Do not claim a technical override merely because another operation could also
  process the arguments. Resource-ranked process-table observation is a real
  technical override (find semantics -> get operation); categorization and
  access-sharing are not overrides.

First complete the ordered `semantic_heads` array using only predicate meaning.
Then complete `predicates` with exactly the same anchors and roles, consulting
the already emitted semantic head before product ontology. Do not revise the
semantic decision while routing.
"""


PRECOMPUTED_HEAD_REFINEMENTS = """\
The input includes `precomputed_semantic_heads` produced by an isolated
language-semantic pass. Copy those heads exactly into the output; do not
reinterpret their actions, roles, anchors, count, or order. Route each copied
head into its product `verb` and `object`. A difference between semantic action
and product verb is allowed only as technical_override. In particular:

- acquiring content from a URL routes to read/urls even if the semantic head is
  get or read;
- creating/saving file content routes to write/files, except that a newly
  requested specialized artifact routes to create with its artifact qualifier;
- referencing an already named existing item for attachment routes to get/files
  and does not create or rewrite that item;
- process-table resource observation routes to get/processes;
- direct categorize and access-grant heads remain classify and share.
"""


LEMMA_LAYER_REFINEMENTS = """\
Use a morphology-first structured head, not a rewritten query:

- In `semantic_heads`, emit the predicate's short dictionary-form lemma in the
  SAME language as its source token. Remove inflection and attached/reflexive
  pronouns. Do not translate, paraphrase, choose a product action, copy the
  patient, or rewrite the clause. One lexical lemma is preferred; a genuine
  phrasal predicate may use at most three words.
- Complete all heads before product routing. Then use each lemma together with
  the untouched original request to choose the final canonical action/object.
  The lemma is morphological evidence, never executable text and never a
  replacement for the original request.
- Context decides the technical operation: a display/show lemma can become
  read, render, or get depending on what is shown; an attachment lemma referring
  to a named existing file becomes get/files; a process search lemma can become
  get/processes for a whole-table snapshot. Conversely, a direct classify,
  share, create, or compress lemma must not be replaced merely because its
  arguments support another operation.
"""


EFFECT_LAYER_REFINEMENTS = """\
For each morphology head, also classify the predicate's real-world `effect`
independently of the final product verb:

- existence_check asks whether one named entity exists or is active; snapshot
  observes a domain/table or ranks its current members; metadata_lookup reads
  attributes of known entities;
- create_artifact makes a new persistent artifact; update_artifact saves or
  changes its content; attachment_reference only references an already existing
  item for attachment and must not create/update it;
- human_delivery transfers a copy through a human communication channel;
  access_grant changes continuing access without transferring ownership/data;
- label_assignment assigns a category to members; collection_grouping only
  partitions/combines them; select_subset reduces a held collection.

The remaining effect names have their ordinary technical meaning. This effect
is a structured semantic fact, not a surface-term mapping and not free text.
Final canonical routing must agree with it.
"""


MORPH_SEMANTIC_REFINEMENTS = """\
After emitting each morphology `lemma`, emit `semantic_action`: the closest
canonical action to the lemma's own ordinary predicate meaning before product
routing. Arguments may disambiguate an idiom, but must not replace an explicit
classify, share, compress, or create meaning just because another executor can
handle the patient. The later product `verb` may differ only for a documented
technical boundary. Thus the head provides three independent signals: source
lemma, language-level semantic action, and real-world effect.
"""


GLOSS_REFINEMENTS = """\
Immediately after each same-language lemma, emit `semantic_gloss_en`: one
common English dictionary verb preserving the lemma's lexical meaning before
product routing. This is a pivot label, not a rewritten request and not a
surface-term dictionary. Resolve false friends from the untouched local clause.
A genuine phrasal predicate may use at most three words. Complete lemma and
gloss before choosing semantic_action, effect, verb, or object.
"""


SCOPE_REFINEMENTS = """\
For each semantic head also emit `resource_scope`, independently of product
routing. single_known means one already identified entity; collection_by_criterion
means multiple source-of-truth members selected or ranked by a criterion;
whole_domain means an unfiltered domain/table snapshot; held_result means the
patient comes from an earlier predicate; new_resource means this predicate
creates it; not_applicable is for predicates without a resource patient. This
is semantic cardinality/state, not a guess based on a product tool name.
"""


SINK_AND_IDENTITY_REFINEMENTS = """\
For every product predicate emit `sink_mode`. Use explicit_persistence only
when the user independently requests committing this predicate's result to a
persistent destination expressed in the same predicate. Intermediate typed
data that merely feeds a later predicate is not a sink, even if its canonical
object is entries. With sink_mode=none, materialize_to MUST be none.

Use effect biometric_identity_lookup when the predicate recognizes or
identifies enrolled people in visual content. This is different from assigning
labels/categories to images: it is a lookup in the identity registry and its
technical product result belongs to persons.
"""


GROUNDED_SINK_REFINEMENTS = """\
Ground every implicit persistence edge in the untouched source:

- `sink_anchor_token_id` is 0 when `sink_mode` is none. When sink_mode is
  explicit_persistence it MUST identify a source token that independently
  names the persistent destination requested by the user.
- A predicate token, its patient, an output format/artifact, a downstream
  consumer, or merely intermediate typed data is not destination evidence.
  Do not infer persistence from the product object's type.
- `materialize_to` describes only an additional destination effect. Creating
  or updating the predicate's own persistent artifact is already represented
  by its verb/object and therefore has no materialize_to edge.
"""


TYPED_ARGUMENT_GRAPH_REFINEMENTS = """\
Phase 1 must build a grounded predicate/argument graph before Phase 2 product
routing. This graph is semantic and language-independent; never rewrite the
request or use source-language trigger lists.

Identity and edges:
- Assign predicate_id exactly 1,2,3,... in source order. It is the graph node
  identity and is distinct from predicate_anchor_token_id, which only points to
  the source predicate token.
- source_predicate_id is 0 or the predicate_id of an EARLIER predicate whose
  result supplies this predicate's elided patient. It is never a token number,
  record count chosen as a patient, or current/future id.

Grounded typed spans use inclusive token ids. Use start=end=0 for no explicit
span; otherwise 1 <= start <= end. A span copies no text into the output.
- patient span: the entity/content acted on or requested. patient_object is its
  semantic domain even when the patient is elided and only source_predicate_id
  is present.
- carrier span: an explicit source container, transport channel, or physical
  representation. carrier_role says which relation holds. The carrier is not
  automatically the patient. Searching an item inside a source-of-truth domain
  acts through that carrier; acquiring content from an explicit URL likewise
  uses the URL carrier.
- sink span: an explicit destination. primary_output means the predicate itself
  creates/updates that destination (save to a file/path) and MUST NOT create an
  extra materialize_to edge. secondary_persistence means the predicate also
  commits its result to a separate store without a dedicated save/write
  predicate; only this relation creates sink_mode=explicit_persistence.

Domain preservation:
- A held result keeps its semantic patient domain across filter/delete/sort/
  classify unless the operation explicitly changes representation. Scheduled
  automation records remain tasks; do not collapse them to generic entries.
- In discovery, the sought item and its source container may differ. For
  example a search inside a message source remains a messages operation even
  if the sought business item would otherwise be an entry.
- Raw/full content is content_read. Metadata_lookup is only attributes about a
  known entity; the words denoting raw content belong in the patient span, not
  in a metadata effect.

Complete every semantic head, including all grounded roles, before emitting
any Phase 2 predicate. Phase 2 must copy predicate_id, anchor, role and graph
edge exactly. Product object selection may consult the typed patient, carrier,
source and sink roles but must not revise Phase 1.
"""


TAGGED_ARGUMENT_GRAPH_REFINEMENTS = """\
Phase 1 must build a grounded predicate/argument graph before Phase 2 product
routing. This graph is semantic and language-independent; never rewrite the
request or use source-language trigger lists.

Identity and edges:
- Assign predicate_id exactly 1,2,3,... in source order. It is the graph node
  identity and is distinct from predicate_anchor_token_id, which only points to
  the source predicate token.
- source_predicate_id is 0 or the predicate_id of an EARLIER predicate whose
  result supplies this predicate's elided patient. It is never a token number,
  record count chosen as a patient, or current/future id.

Grounded typed spans use inclusive token ids. A patient uses start=end=0 only
when it is elided or absent; otherwise 1 <= start <= end. The carrier and sink
are tagged unions, so choose exactly one variant and emit only that variant's
fields:
- carrier {kind:none} when there is no explicit carrier; otherwise choose
  source_container, transport_channel, or representation and include a
  non-zero source span plus its canonical object.
- sink {kind:none} when there is no explicit destination; otherwise choose
  primary_output or secondary_persistence and include a non-zero source span
  plus its canonical object.
- primary_output means the predicate itself creates, saves, moves, or updates
  that destination. It is already the predicate's object and never adds an
  operation.
- secondary_persistence means THIS SAME predicate additionally commits its
  result to a separate persistent store. It is the only sink variant that adds
  a derived write operation.
- If saving/writing is an independently expressed predicate, the upstream
  predicate MUST have {kind:none}; put primary_output on the dedicated
  save/write predicate and connect it with source_predicate_id. Never encode
  the same persistence both as an upstream secondary sink and a downstream
  predicate.

Domain preservation:
- A held result keeps its semantic patient domain across filter/delete/sort/
  classify unless the operation explicitly changes representation. Scheduled
  automation records remain tasks; do not collapse them to generic entries.
- In discovery, the sought item and its source container may differ. Searching
  an item inside a source-of-truth domain acts through the source_container;
  acquiring content from an explicit URL likewise acts through the URL carrier.
- Raw/full content is content_read. Metadata_lookup is only attributes about a
  known entity; the words denoting raw content belong in the patient span.
- Use effect biometric_identity_lookup when recognizing enrolled identities in
  visual content; the product result belongs to persons.

Complete every semantic head, including all grounded roles, before emitting
any Phase 2 predicate. Phase 2 copies predicate_id, anchor, role and graph edge,
then emits only verb, verb_resolution, object, and object_qualifier. It MUST NOT
repeat, reinterpret, or redundantly encode carrier/sink metadata. Product
selection may consult Phase 1 but may not revise it.
"""

# Same graph contract for the ablation that removes the noisy effect facet.
# This is derived mechanically so the two variants differ only in that facet,
# not through a second hand-maintained prompt.
TAGGED_ARGUMENT_GRAPH_LITE_REFINEMENTS = TAGGED_ARGUMENT_GRAPH_REFINEMENTS.replace(
    "- Use effect biometric_identity_lookup when recognizing enrolled identities in\n"
    "  visual content; the product result belongs to persons.\n", "")

TAGGED_ARGUMENT_GRAPH_V24_REFINEMENTS = (
    TAGGED_ARGUMENT_GRAPH_LITE_REFINEMENTS.replace(
        "Grounded typed spans use inclusive token ids. A patient uses start=end=0 only\n"
        "when it is elided or absent; otherwise 1 <= start <= end. The carrier and sink\n"
        "are tagged unions, so choose exactly one variant and emit only that variant's\n"
        "fields:\n",
        "Grounded roles are tagged unions. Choose exactly one variant and emit only its\n"
        "fields. A patient is {kind:none} only when no semantic patient exists;\n"
        "{kind:explicit,start_token_id,end_token_id,object} when source tokens name it;\n"
        "{kind:elided,object} only when source_predicate_id supplies it; or\n"
        "{kind:implicit,object} only when context supplies an unstated patient and there\n"
        "is no source edge. Never use elided or implicit when patient words are present.\n"
        "All explicit spans are inclusive and strictly positive. Carrier and sink use\n"
        "the same tagged-union discipline:\n"))


BASE_INSTRUCTION = """\
Analyze a user request written in ANY language. This is semantic normalization,
not text rewriting. The input is JSON containing the exact original request and
its whitespace tokens numbered from 1.

Return one record for every predicate that could otherwise be mistaken for an
operation. Preserve their order. A record role is:
- request: an operation the user asks the assistant to execute;
- forbid: another operation the user explicitly commands not to perform;
- condition: a state or condition controlling another requested operation;
- description: a predicate describing data, history, state, or a relative clause;
- quote: predicate-like text inside a quoted/literal value.

For request records, normalize the predicate directly to one canonical action
and object from the output schema. Resolve inflection, idiom, attached pronouns,
ellipsis and cross-clause references from meaning and context. Set
`predicate_anchor_token_id` to one numbered source token anchoring that
predicate. Set `input_from_predicate_anchor_id` to an earlier predicate anchor
supplying an elided patient, otherwise 0. For non-request records use the closest canonical action/object when clear,
otherwise `none`; they are never executable intents.

Do not translate, rewrite, summarize, copy literals, add implicit operations,
or turn passive/descriptive/conditional/quoted predicates into requests.
An operation whose own purpose is to select, keep, exclude, or discard data is
a request, not a forbid record. Forbid applies only when the grammar prohibits
performing a separate operation. Do not treat auxiliaries, articles,
prepositions, pronouns or conjunctions as independent predicates. Output JSON
only.

Canonical action ontology (technical definitions, not surface-word mappings):
{boundaries}

{object_contract}
"""


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def schema(prompt_variant: str = "v20") -> dict:
    result = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "semantic_heads": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "predicate_anchor_token_id": {"type": "integer"},
                        "role": {"type": "string", "enum": list(ROLE_VALUES)},
                        "lemma": {"type": "string", "minLength": 1,
                                  "maxLength": 80},
                        "semantic_gloss_en": {
                            "type": "string", "minLength": 1, "maxLength": 80,
                        },
                        "semantic_action": {
                            "type": "string", "enum": ["none", *ACTIONS],
                        },
                        "effect": {"type": "string",
                                   "enum": list(EFFECT_VALUES)},
                        "resource_scope": {
                            "type": "string",
                            "enum": list(RESOURCE_SCOPE_VALUES),
                        },
                    },
                    "required": [
                        "predicate_anchor_token_id", "role", "lemma",
                        "semantic_gloss_en", "semantic_action", "effect",
                        "resource_scope",
                    ],
                },
            },
            "predicates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "predicate_anchor_token_id": {"type": "integer"},
                        "role": {"type": "string", "enum": list(ROLE_VALUES)},
                        "verb": {
                            "type": "string", "enum": ["none", *ACTIONS],
                        },
                        "verb_resolution": {
                            "type": "string",
                            "enum": ["direct", "generalized", "technical_override"],
                        },
                        "object": {
                            "type": "string", "enum": ["none", *OBJECTS],
                        },
                        "object_qualifier": {
                            "type": "string",
                            "enum": ["none", *OUTPUT_ARTIFACT_QUALIFIERS],
                        },
                        "materialize_to": {
                            "type": "string", "enum": ["none", *OBJECTS],
                        },
                        "sink_mode": {
                            "type": "string", "enum": list(SINK_MODE_VALUES),
                        },
                        "input_from_predicate_anchor_id": {"type": "integer"},
                    },
                    "required": [
                        "predicate_anchor_token_id", "role", "verb",
                        "verb_resolution", "object",
                        "object_qualifier", "materialize_to",
                        "sink_mode",
                        "input_from_predicate_anchor_id",
                    ],
                },
            },
        },
        "required": ["semantic_heads", "predicates"],
    }
    if prompt_variant == "v21":
        predicate = result["properties"]["predicates"]["items"]
        predicate["properties"]["sink_anchor_token_id"] = {
            "type": "integer",
        }
        predicate["required"].append("sink_anchor_token_id")
    elif prompt_variant == "v22":
        head = result["properties"]["semantic_heads"]["items"]
        old_head_properties = head["properties"]
        head["properties"] = {
            "predicate_id": {"type": "integer"},
            **old_head_properties,
            "patient_start_token_id": {"type": "integer"},
            "patient_end_token_id": {"type": "integer"},
            "patient_object": {
                "type": "string", "enum": ["none", *OBJECTS],
            },
            "carrier_start_token_id": {"type": "integer"},
            "carrier_end_token_id": {"type": "integer"},
            "carrier_object": {
                "type": "string", "enum": ["none", *OBJECTS],
            },
            "carrier_role": {
                "type": "string", "enum": list(CARRIER_ROLE_VALUES),
            },
            "source_predicate_id": {"type": "integer"},
            "sink_start_token_id": {"type": "integer"},
            "sink_end_token_id": {"type": "integer"},
            "sink_object": {
                "type": "string", "enum": ["none", *OBJECTS],
            },
            "sink_relation": {
                "type": "string", "enum": list(SINK_RELATION_VALUES),
            },
        }
        head["required"] = list(head["properties"])

        predicate = result["properties"]["predicates"]["items"]
        old_predicate_properties = predicate["properties"]
        old_predicate_properties.pop("input_from_predicate_anchor_id")
        predicate["properties"] = {
            "predicate_id": {"type": "integer"},
            **old_predicate_properties,
            "input_from_predicate_id": {"type": "integer"},
        }
        predicate["required"] = list(predicate["properties"])
    elif prompt_variant in ("v23", "v23lite", "v24"):
        # The grounded optional roles are real discriminated unions.  In
        # particular, the `none` alternatives cannot carry a stale object or
        # relation and active alternatives cannot omit their grounding span.
        def tagged_role(active_kinds: tuple[str, ...]) -> dict:
            variants = [{
                "type": "object",
                "additionalProperties": False,
                "properties": {"kind": {"const": "none"}},
                "required": ["kind"],
            }]
            for kind in active_kinds:
                variants.append({
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"const": kind},
                        "start_token_id": {"type": "integer", "minimum": 1},
                        "end_token_id": {"type": "integer", "minimum": 1},
                        "object": {
                            "type": "string", "enum": list(OBJECTS),
                        },
                    },
                    "required": [
                        "kind", "start_token_id", "end_token_id", "object",
                    ],
                })
            return {"oneOf": variants}

        head = result["properties"]["semantic_heads"]["items"]
        old_head_properties = head["properties"]
        if prompt_variant in ("v23lite", "v24"):
            old_head_properties.pop("effect")
        head["properties"] = {
            "predicate_id": {"type": "integer"},
            **old_head_properties,
            "patient_start_token_id": {"type": "integer"},
            "patient_end_token_id": {"type": "integer"},
            "patient_object": {
                "type": "string", "enum": ["none", *OBJECTS],
            },
            "carrier": tagged_role((
                "source_container", "transport_channel", "representation",
            )),
            "source_predicate_id": {"type": "integer"},
            "sink": tagged_role((
                "primary_output", "secondary_persistence",
            )),
        }
        if prompt_variant == "v24":
            head["properties"].pop("patient_start_token_id")
            head["properties"].pop("patient_end_token_id")
            head["properties"].pop("patient_object")
            patient_variants = [{
                "type": "object", "additionalProperties": False,
                "properties": {"kind": {"const": "none"}},
                "required": ["kind"],
            }]
            for patient_kind in ("elided", "implicit"):
                patient_variants.append({
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "kind": {"const": patient_kind},
                        "object": {"type": "string", "enum": list(OBJECTS)},
                    },
                    "required": ["kind", "object"],
                })
            patient_variants.append({
                "type": "object", "additionalProperties": False,
                "properties": {
                    "kind": {"const": "explicit"},
                    "start_token_id": {"type": "integer", "minimum": 1},
                    "end_token_id": {"type": "integer", "minimum": 1},
                    "object": {"type": "string", "enum": list(OBJECTS)},
                },
                "required": [
                    "kind", "start_token_id", "end_token_id", "object",
                ],
            })
            head["properties"]["patient"] = {"oneOf": patient_variants}
        head["required"] = list(head["properties"])

        predicate = result["properties"]["predicates"]["items"]
        old_predicate_properties = predicate["properties"]
        old_predicate_properties.pop("input_from_predicate_anchor_id")
        old_predicate_properties.pop("materialize_to")
        old_predicate_properties.pop("sink_mode")
        predicate["properties"] = {
            "predicate_id": {"type": "integer"},
            **old_predicate_properties,
            "input_from_predicate_id": {"type": "integer"},
        }
        predicate["required"] = list(predicate["properties"])
    return result


def semantic_head_schema() -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "semantic_heads": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "predicate_anchor_token_id": {"type": "integer"},
                        "role": {"type": "string", "enum": list(ROLE_VALUES)},
                        "semantic_action": {
                            "type": "string", "enum": ["none", *ACTIONS],
                        },
                    },
                    "required": [
                        "predicate_anchor_token_id", "role", "semantic_action",
                    ],
                },
            },
        },
        "required": ["semantic_heads"],
    }


LATENCIES: dict[str, list[float]] = {
    "current": [], "semantic": [], "folded": [],
}


def current_call(system: str, user: str, *, max_tokens: int = 320, **kw) -> str:
    started = time.perf_counter()
    args = {"max_tokens": max_tokens, "request_timeout_s": 90}
    if kw.get("grammar") is not None:
        args["grammar"] = kw["grammar"]
    result = LLMRouter().provider(tier_for("intent.extract")).chat(
        system, user, **args)
    LATENCIES["current"].append((time.perf_counter() - started) * 1000)
    return (getattr(result, "text", result) or "").strip()


def tokenize(query: str) -> list[str]:
    # Deliberately language-neutral and lossless at the token level. Offsets are
    # not needed by this POC; production would retain start/end offsets too.
    return re.findall(r"\S+", query or "", re.UNICODE)


def semantic_head_call(query: str) -> tuple[list[dict], dict]:
    tokens = tokenize(query)
    payload = json.dumps(
        {"original_request": query,
         "tokens": [{"id": i, "text": token}
                    for i, token in enumerate(tokens, 1)]},
        ensure_ascii=False,
    )
    body = {
        "model": "local", "temperature": 0, "seed": 42,
        "max_tokens": 600,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "semantic_heads",
                "schema": semantic_head_schema(), "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": SEMANTIC_HEAD_INSTRUCTION},
            {"role": "user", "content": payload},
        ],
    }
    endpoint = tier_endpoint(tier_for("intent.extract")).rstrip("/")
    req = urllib.request.Request(
        endpoint + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        response = json.load(urllib.request.urlopen(req, timeout=90))
        raw = response["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        heads = parsed.get("semantic_heads")
        valid = isinstance(heads, list)
        reason = "" if valid else "semantic_shape"
    except Exception as exc:
        raw = f"{type(exc).__name__}: {exc}"
        heads, valid, reason = [], False, "semantic_transport_or_json"
    elapsed = (time.perf_counter() - started) * 1000
    LATENCIES["semantic"].append(elapsed)
    return heads, {"valid": valid, "reason": reason, "raw": raw,
                   "latency_ms": elapsed}


def folded_call(query: str, *, prompt_variant: str = "v0",
                precomputed_heads: list[dict] | None = None) -> tuple[dict, dict]:
    tokens = tokenize(query)
    payload_user = json.dumps(
        {"original_request": query,
         "tokens": [{"id": i, "text": token}
                    for i, token in enumerate(tokens, 1)],
         **({"precomputed_semantic_heads": precomputed_heads}
            if precomputed_heads is not None else {})},
        ensure_ascii=False,
    )
    instruction = BASE_INSTRUCTION.format(
        boundaries=render_boundaries("en"), object_contract=OBJECT_CONTRACT)
    if prompt_variant in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += """\
\nValidation policy: emit every independently requested operation exactly once.
An operation embedded only as the patient/state of another predicate is not an
extra request. A condition may require later planning, but is not an independent
intent unless the user explicitly commands its verification. The canonical
intent stays semantic; do not change it merely to imitate a tool name.
"""
    if prompt_variant in ("v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += "\n" + ONTOLOGY_REFINEMENTS
    if prompt_variant in ("v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += "\n" + TIE_BREAK_REFINEMENTS
    if prompt_variant in ("v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += "\n" + STRUCTURE_REFINEMENTS
    if prompt_variant in ("v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += "\n" + COMPOUND_REFINEMENTS
    if prompt_variant in ("v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += "\n" + STRICT_COMPOUND_REFINEMENTS
    if prompt_variant in ("v9", "v10", "v11", "v12", "v13", "v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += "\n" + ANCHOR_AND_SINK_REFINEMENTS
    if prompt_variant in ("v10", "v11", "v12"):
        instruction += "\n" + VERB_LAYER_REFINEMENTS
    if prompt_variant == "v12":
        instruction += "\n" + PRECOMPUTED_HEAD_REFINEMENTS
    if prompt_variant in ("v13", "v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += "\n" + LEMMA_LAYER_REFINEMENTS
    if prompt_variant in ("v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23"):
        instruction += "\n" + EFFECT_LAYER_REFINEMENTS
    if prompt_variant in ("v15", "v17", "v19", "v20", "v21", "v22", "v23"):
        instruction += "\n" + MORPH_SEMANTIC_REFINEMENTS
    if prompt_variant in ("v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += "\n" + GLOSS_REFINEMENTS
    if prompt_variant in ("v19", "v20", "v21", "v22", "v23", "v23lite", "v24"):
        instruction += "\n" + SCOPE_REFINEMENTS
    if prompt_variant in ("v20", "v21", "v22"):
        instruction += "\n" + SINK_AND_IDENTITY_REFINEMENTS
    if prompt_variant == "v21":
        instruction += "\n" + GROUNDED_SINK_REFINEMENTS
    if prompt_variant == "v22":
        instruction += "\n" + TYPED_ARGUMENT_GRAPH_REFINEMENTS
    if prompt_variant == "v23":
        instruction += "\n" + TAGGED_ARGUMENT_GRAPH_REFINEMENTS
    if prompt_variant == "v23lite":
        instruction += "\n" + TAGGED_ARGUMENT_GRAPH_LITE_REFINEMENTS
    if prompt_variant == "v24":
        instruction += "\n" + TAGGED_ARGUMENT_GRAPH_V24_REFINEMENTS
    body = {
        "model": "local",
        "temperature": 0,
        "seed": 42,
        "max_tokens": 2600 if prompt_variant in ("v22", "v23", "v23lite", "v24") else (
            1500 if prompt_variant in ("v20", "v21") else 900),
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "unified_request_analysis",
                "schema": schema(prompt_variant),
                "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": payload_user},
        ],
    }
    endpoint = tier_endpoint(tier_for("intent.extract")).rstrip("/")
    req = urllib.request.Request(
        endpoint + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    normalizations: list[str] = []
    try:
        response = json.load(urllib.request.urlopen(req, timeout=90))
        raw = response["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        if prompt_variant == "v22":
            ok, reason = validate_frame_v22(parsed, tokens)
        elif prompt_variant == "v23":
            ok, reason = validate_frame_v23(parsed, tokens)
        elif prompt_variant == "v23lite":
            ok, reason = validate_frame_v23(parsed, tokens, prompt_variant="v23lite")
        elif prompt_variant == "v24":
            parsed, normalizations = canonicalize_v24_frame(parsed)
            ok, reason = validate_frame_v23(parsed, tokens, prompt_variant="v24")
        else:
            ok, reason = validate_frame(
                parsed, tokens, precomputed_heads,
                require_sink_anchor=prompt_variant == "v21",
            )
    except Exception as exc:
        raw = f"{type(exc).__name__}: {exc}"
        parsed, ok, reason = {}, False, "transport_or_json"
    elapsed = (time.perf_counter() - started) * 1000
    LATENCIES["folded"].append(elapsed)
    return parsed, {"valid": ok, "reason": reason, "raw": raw,
                    "tokens": tokens, "latency_ms": elapsed,
                    "normalizations": normalizations}


def validate_frame_v22(frame: dict, tokens: list[str]) -> tuple[bool, str]:
    """Validate the v22 predicate-id graph and grounded typed roles."""
    token_count = len(tokens)
    if not isinstance(frame, dict) or set(frame) != {"semantic_heads", "predicates"}:
        return False, "top_shape"
    heads = frame.get("semantic_heads")
    predicates = frame.get("predicates")
    if (not isinstance(heads, list) or not isinstance(predicates, list)
            or len(heads) != len(predicates)):
        return False, "phase_alignment"
    expected_head_keys = set(
        schema("v22")["properties"]["semantic_heads"]["items"]["required"])
    expected_predicate_keys = set(
        schema("v22")["properties"]["predicates"]["items"]["required"])

    def valid_span(start: object, end: object) -> bool:
        if not isinstance(start, int) or not isinstance(end, int):
            return False
        return ((start == 0 and end == 0)
                or (1 <= start <= end <= token_count))

    last_anchor = 0
    for index, (head, item) in enumerate(zip(heads, predicates), 1):
        if not isinstance(head, dict) or set(head) != expected_head_keys:
            return False, f"semantic_{index}_shape"
        if not isinstance(item, dict) or set(item) != expected_predicate_keys:
            return False, f"predicate_{index}_shape"
        if head.get("predicate_id") != index or item.get("predicate_id") != index:
            return False, f"predicate_{index}_identity"
        anchor = head.get("predicate_anchor_token_id")
        if (not isinstance(anchor, int) or not 1 <= anchor <= token_count
                or anchor <= last_anchor
                or item.get("predicate_anchor_token_id") != anchor):
            return False, f"predicate_{index}_anchor"
        if re.search(r"https?://|@|\d|^(?:[.~]?/|[A-Za-z]:\\|\\\\)",
                     tokens[anchor - 1]):
            return False, f"predicate_{index}_literal_anchor"
        role = head.get("role")
        if role not in ROLE_VALUES or item.get("role") != role:
            return False, f"predicate_{index}_role"
        lemma = head.get("lemma")
        gloss = head.get("semantic_gloss_en")
        if (not isinstance(lemma, str) or not lemma.strip()
                or len(lemma) > 80 or len(lemma.split()) > 3
                or re.search(r"https?://|@|\d|[/\\]", lemma)):
            return False, f"semantic_{index}_lemma"
        if (not isinstance(gloss, str) or not gloss.strip()
                or len(gloss) > 80 or len(gloss.split()) > 3
                or not re.fullmatch(r"[A-Za-z][A-Za-z -]*", gloss.strip())):
            return False, f"semantic_{index}_gloss"
        if (head.get("semantic_action") not in ("none", *ACTIONS)
                or head.get("effect") not in EFFECT_VALUES
                or head.get("resource_scope") not in RESOURCE_SCOPE_VALUES):
            return False, f"semantic_{index}_enum"

        patient_span = valid_span(
            head.get("patient_start_token_id"), head.get("patient_end_token_id"))
        carrier_span = valid_span(
            head.get("carrier_start_token_id"), head.get("carrier_end_token_id"))
        sink_span = valid_span(
            head.get("sink_start_token_id"), head.get("sink_end_token_id"))
        if not patient_span:
            return False, f"semantic_{index}_patient_span"
        if not carrier_span:
            return False, f"semantic_{index}_carrier_span"
        if not sink_span:
            return False, f"semantic_{index}_sink_span"
        patient_obj = head.get("patient_object")
        carrier_obj = head.get("carrier_object")
        sink_obj = head.get("sink_object")
        if patient_obj not in ("none", *OBJECTS):
            return False, f"semantic_{index}_patient_object"
        if carrier_obj not in ("none", *OBJECTS):
            return False, f"semantic_{index}_carrier_object"
        if sink_obj not in ("none", *OBJECTS):
            return False, f"semantic_{index}_sink_object"
        carrier_zero = head["carrier_start_token_id"] == 0
        carrier_role = head.get("carrier_role")
        if (carrier_role not in CARRIER_ROLE_VALUES
                or (carrier_role == "none") != carrier_zero
                or (carrier_obj == "none") != carrier_zero):
            return False, f"semantic_{index}_carrier_alignment"
        sink_zero = head["sink_start_token_id"] == 0
        sink_relation = head.get("sink_relation")
        if (sink_relation not in SINK_RELATION_VALUES
                or (sink_relation == "none") != sink_zero
                or (sink_obj == "none") != sink_zero):
            return False, f"semantic_{index}_sink_alignment"
        source_id = head.get("source_predicate_id")
        if (not isinstance(source_id, int) or source_id < 0
                or source_id >= index
                or item.get("input_from_predicate_id") != source_id):
            return False, f"predicate_{index}_source_edge"

        if item.get("verb") not in ("none", *ACTIONS):
            return False, f"predicate_{index}_verb"
        if item.get("object") not in ("none", *OBJECTS):
            return False, f"predicate_{index}_object"
        if item.get("verb_resolution") not in (
                "direct", "generalized", "technical_override"):
            return False, f"predicate_{index}_resolution"
        qualifier = item.get("object_qualifier")
        if (qualifier not in ("none", *OUTPUT_ARTIFACT_QUALIFIERS)
                or (qualifier != "none"
                    and not qualifier_compatible(qualifier, item.get("object")))):
            return False, f"predicate_{index}_qualifier"
        materialize_to = item.get("materialize_to")
        sink_mode = item.get("sink_mode")
        expected_secondary = sink_relation == "secondary_persistence"
        if expected_secondary:
            if sink_mode != "explicit_persistence" or materialize_to != sink_obj:
                return False, f"predicate_{index}_secondary_sink"
        elif sink_mode != "none" or materialize_to != "none":
            return False, f"predicate_{index}_primary_or_no_sink"
        if role == "request":
            if item.get("verb") == "none" or item.get("object") == "none":
                return False, f"predicate_{index}_incomplete_request"
        elif item.get("verb") != "none" or item.get("object") != "none":
            return False, f"predicate_{index}_nonrequest_executable"
        last_anchor = anchor
    return True, ""


def canonicalize_v22_frame_to_v23(frame: dict) -> dict:
    """Project a v22 frame into the non-redundant v23 representation.

    This is a language-neutral structural migration, used for offline replay of
    already captured frames.  An ungrounded optional role becomes the `none`
    alternative; sink information is never copied into Phase 2.
    """
    migrated = json.loads(json.dumps(frame))
    heads = migrated.get("semantic_heads") or []
    predicates = migrated.get("predicates") or []
    for head in heads:
        carrier_kind = head.pop("carrier_role", "none")
        carrier_start = head.pop("carrier_start_token_id", 0)
        carrier_end = head.pop("carrier_end_token_id", 0)
        carrier_object = head.pop("carrier_object", "none")
        if (carrier_kind != "none" and carrier_start and carrier_end
                and carrier_object != "none"):
            head["carrier"] = {
                "kind": carrier_kind,
                "start_token_id": carrier_start,
                "end_token_id": carrier_end,
                "object": carrier_object,
            }
        else:
            head["carrier"] = {"kind": "none"}

        sink_kind = head.pop("sink_relation", "none")
        sink_start = head.pop("sink_start_token_id", 0)
        sink_end = head.pop("sink_end_token_id", 0)
        sink_object = head.pop("sink_object", "none")
        if (sink_kind != "none" and sink_start and sink_end
                and sink_object != "none"):
            head["sink"] = {
                "kind": sink_kind,
                "start_token_id": sink_start,
                "end_token_id": sink_end,
                "object": sink_object,
            }
        else:
            head["sink"] = {"kind": "none"}

    for item in predicates:
        item.pop("materialize_to", None)
        item.pop("sink_mode", None)

    # A dedicated downstream persistence predicate is the sole representation
    # of that operation.  Remove a duplicated upstream secondary edge using
    # graph identity and canonical objects only, never input-language text.
    by_id = {item.get("predicate_id"): item for item in predicates}
    for head in heads:
        sink = head.get("sink") or {}
        if sink.get("kind") != "secondary_persistence":
            continue
        predicate_id = head.get("predicate_id")
        sink_object = sink.get("object")
        duplicate = any(
            item.get("input_from_predicate_id") == predicate_id
            and item.get("verb") in ("write", "create", "move")
            and item.get("object") == sink_object
            for item in predicates
            if item.get("predicate_id", 0) > predicate_id
        )
        if duplicate:
            head["sink"] = {"kind": "none"}
    return migrated


def canonicalize_v24_frame(frame: dict) -> tuple[dict, list[str]]:
    """Put tagged patients in graph-normal form without lexical inference."""
    normalized = json.loads(json.dumps(frame))
    changes: list[str] = []
    for head in normalized.get("semantic_heads") or []:
        patient = head.get("patient")
        if not isinstance(patient, dict):
            continue
        source_id = head.get("source_predicate_id")
        kind = patient.get("kind")
        if kind == "elided" and source_id == 0:
            patient["kind"] = "implicit"
            changes.append(f"patient_{head.get('predicate_id')}_elided_to_implicit")
        elif kind == "implicit" and isinstance(source_id, int) and source_id > 0:
            patient["kind"] = "elided"
            changes.append(f"patient_{head.get('predicate_id')}_implicit_to_elided")
    return normalized, changes


def validate_frame_v23(frame: dict, tokens: list[str],
                       *, prompt_variant: str = "v23") -> tuple[bool, str]:
    """Validate v23's predicate-id graph and discriminated grounded roles."""
    token_count = len(tokens)
    if not isinstance(frame, dict) or set(frame) != {"semantic_heads", "predicates"}:
        return False, "top_shape"
    heads = frame.get("semantic_heads")
    predicates = frame.get("predicates")
    if (not isinstance(heads, list) or not isinstance(predicates, list)
            or len(heads) != len(predicates)):
        return False, "phase_alignment"
    expected_head_keys = set(
        schema(prompt_variant)["properties"]["semantic_heads"]["items"]["required"])
    expected_predicate_keys = set(
        schema(prompt_variant)["properties"]["predicates"]["items"]["required"])

    def valid_span(start: object, end: object, *, allow_zero: bool) -> bool:
        if not isinstance(start, int) or not isinstance(end, int):
            return False
        if allow_zero and start == 0 and end == 0:
            return True
        return 1 <= start <= end <= token_count

    def tagged_role(value: object, active: tuple[str, ...]) -> tuple[bool, str, str]:
        if not isinstance(value, dict):
            return False, "", "none"
        kind = value.get("kind")
        if kind == "none":
            return set(value) == {"kind"}, kind, "none"
        if (kind not in active
                or set(value) != {"kind", "start_token_id", "end_token_id", "object"}
                or not valid_span(value.get("start_token_id"),
                                  value.get("end_token_id"), allow_zero=False)
                or value.get("object") not in OBJECTS):
            return False, str(kind or ""), str(value.get("object") or "none")
        return True, kind, value["object"]

    last_anchor = 0
    for index, (head, item) in enumerate(zip(heads, predicates), 1):
        if not isinstance(head, dict) or set(head) != expected_head_keys:
            return False, f"semantic_{index}_shape"
        if not isinstance(item, dict) or set(item) != expected_predicate_keys:
            return False, f"predicate_{index}_shape"
        if head.get("predicate_id") != index or item.get("predicate_id") != index:
            return False, f"predicate_{index}_identity"
        anchor = head.get("predicate_anchor_token_id")
        if (not isinstance(anchor, int) or not 1 <= anchor <= token_count
                or anchor <= last_anchor
                or item.get("predicate_anchor_token_id") != anchor):
            return False, f"predicate_{index}_anchor"
        if re.search(r"https?://|@|\d|^(?:[.~]?/|[A-Za-z]:\\|\\\\)",
                     tokens[anchor - 1]):
            return False, f"predicate_{index}_literal_anchor"
        role = head.get("role")
        if role not in ROLE_VALUES or item.get("role") != role:
            return False, f"predicate_{index}_role"
        lemma = head.get("lemma")
        gloss = head.get("semantic_gloss_en")
        if (not isinstance(lemma, str) or not lemma.strip()
                or len(lemma) > 80 or len(lemma.split()) > 3
                or re.search(r"https?://|@|\d|[/\\]", lemma)):
            return False, f"semantic_{index}_lemma"
        if (not isinstance(gloss, str) or not gloss.strip()
                or len(gloss) > 80 or len(gloss.split()) > 3
                or not re.fullmatch(r"[A-Za-z][A-Za-z -]*", gloss.strip())):
            return False, f"semantic_{index}_gloss"
        effect_valid = ("effect" not in head if prompt_variant in ("v23lite", "v24")
                        else head.get("effect") in EFFECT_VALUES)
        if (head.get("semantic_action") not in ("none", *ACTIONS)
                or not effect_valid
                or head.get("resource_scope") not in RESOURCE_SCOPE_VALUES):
            return False, f"semantic_{index}_enum"
        if prompt_variant == "v24":
            patient = head.get("patient")
            if not isinstance(patient, dict):
                return False, f"semantic_{index}_patient"
            patient_kind = patient.get("kind")
            if patient_kind == "none":
                patient_ok = set(patient) == {"kind"}
            elif patient_kind in ("elided", "implicit"):
                patient_ok = (set(patient) == {"kind", "object"}
                              and patient.get("object") in OBJECTS)
            elif patient_kind == "explicit":
                patient_ok = (
                    set(patient) == {
                        "kind", "start_token_id", "end_token_id", "object",
                    }
                    and patient.get("object") in OBJECTS
                    and valid_span(patient.get("start_token_id"),
                                   patient.get("end_token_id"), allow_zero=False))
            else:
                patient_ok = False
            if not patient_ok:
                return False, f"semantic_{index}_patient"
        else:
            patient_kind = None
            if not valid_span(head.get("patient_start_token_id"),
                              head.get("patient_end_token_id"), allow_zero=True):
                return False, f"semantic_{index}_patient_span"
            if head.get("patient_object") not in ("none", *OBJECTS):
                return False, f"semantic_{index}_patient_object"
        carrier_ok, _, _ = tagged_role(
            head.get("carrier"), ("source_container", "transport_channel", "representation"))
        if not carrier_ok:
            return False, f"semantic_{index}_carrier"
        sink_ok, sink_kind, sink_object = tagged_role(
            head.get("sink"), ("primary_output", "secondary_persistence"))
        if not sink_ok:
            return False, f"semantic_{index}_sink"
        source_id = head.get("source_predicate_id")
        if (not isinstance(source_id, int) or source_id < 0
                or source_id >= index
                or item.get("input_from_predicate_id") != source_id):
            return False, f"predicate_{index}_source_edge"
        if prompt_variant == "v24":
            if ((patient_kind == "elided") != (source_id > 0)
                    and patient_kind in ("elided", "implicit", "none")):
                return False, f"semantic_{index}_patient_source_alignment"
        if item.get("verb") not in ("none", *ACTIONS):
            return False, f"predicate_{index}_verb"
        if item.get("object") not in ("none", *OBJECTS):
            return False, f"predicate_{index}_object"
        if item.get("verb_resolution") not in (
                "direct", "generalized", "technical_override"):
            return False, f"predicate_{index}_resolution"
        qualifier = item.get("object_qualifier")
        if (qualifier not in ("none", *OUTPUT_ARTIFACT_QUALIFIERS)
                or (qualifier != "none"
                    and not qualifier_compatible(qualifier, item.get("object")))):
            return False, f"predicate_{index}_qualifier"
        if role == "request":
            if item.get("verb") == "none" or item.get("object") == "none":
                return False, f"predicate_{index}_incomplete_request"
        elif item.get("verb") != "none" or item.get("object") != "none":
            return False, f"predicate_{index}_nonrequest_executable"

        # Reject duplicate representations of the same requested persistence
        # edge.  This is graph consistency over typed ids/objects, not lexical
        # repair and therefore remains language independent.
        if sink_kind == "secondary_persistence":
            duplicate = any(
                later.get("input_from_predicate_id") == index
                and later.get("verb") in ("write", "create", "move")
                and later.get("object") == sink_object
                for later in predicates[index:]
            )
            if duplicate:
                return False, f"semantic_{index}_duplicate_sink"
        last_anchor = anchor
    return True, ""


def validate_frame(frame: dict, tokens: list[str],
                   expected_heads: list[dict] | None = None,
                   *, require_sink_anchor: bool = False) -> tuple[bool, str]:
    token_count = len(tokens)
    if not isinstance(frame, dict) or set(frame) != {"semantic_heads", "predicates"}:
        return False, "top_shape"
    semantic_heads = frame.get("semantic_heads")
    predicates = frame.get("predicates")
    if not isinstance(semantic_heads, list) or not isinstance(predicates, list):
        return False, "predicates_shape"
    if len(semantic_heads) != len(predicates):
        return False, "semantic_predicate_count"
    if expected_heads is not None and semantic_heads != expected_heads:
        return False, "precomputed_semantic_heads_changed"
    semantic_by_anchor: dict[int, tuple[str, str, str, str, str, str]] = {}
    semantic_last_anchor = 0
    for index, head in enumerate(semantic_heads, 1):
        if not isinstance(head, dict):
            return False, f"semantic_{index}_shape"
        anchor = head.get("predicate_anchor_token_id")
        if (not isinstance(anchor, int) or anchor < 1 or anchor > token_count
                or anchor <= semantic_last_anchor):
            return False, f"semantic_{index}_anchor"
        if head.get("role") not in ROLE_VALUES:
            return False, f"semantic_{index}_role"
        lemma = head.get("lemma")
        if (not isinstance(lemma, str) or not lemma.strip()
                or len(lemma) > 80 or len(lemma.split()) > 3
                or re.search(r"https?://|@|\d|[/\\]", lemma)):
            return False, f"semantic_{index}_lemma"
        gloss = head.get("semantic_gloss_en")
        if (not isinstance(gloss, str) or not gloss.strip()
                or len(gloss) > 80 or len(gloss.split()) > 3
                or not re.fullmatch(r"[A-Za-z][A-Za-z -]*", gloss.strip())):
            return False, f"semantic_{index}_gloss"
        effect = head.get("effect")
        if effect not in EFFECT_VALUES:
            return False, f"semantic_{index}_effect"
        semantic_action = head.get("semantic_action")
        if semantic_action not in ("none", *ACTIONS):
            return False, f"semantic_{index}_action"
        resource_scope = head.get("resource_scope")
        if resource_scope not in RESOURCE_SCOPE_VALUES:
            return False, f"semantic_{index}_resource_scope"
        semantic_by_anchor[anchor] = (
            head["role"], lemma.strip(), gloss.strip().casefold(),
            semantic_action, effect, resource_scope,
        )
        semantic_last_anchor = anchor
    earlier_anchors: set[int] = set()
    last_anchor = 0
    for index, item in enumerate(predicates, 1):
        if not isinstance(item, dict):
            return False, f"predicate_{index}_shape"
        anchor = item.get("predicate_anchor_token_id")
        if (not isinstance(anchor, int) or anchor < 1 or anchor > token_count
                or anchor <= last_anchor):
            return False, f"predicate_{index}_anchor"
        selected = tokens[anchor - 1]
        if re.search(r"https?://|@|\d|^(?:[.~]?/|[A-Za-z]:\\|\\\\)", selected):
            return False, f"predicate_{index}_literal_anchor"
        if item.get("role") not in ROLE_VALUES:
            return False, f"predicate_{index}_role"
        semantic = semantic_by_anchor.get(anchor)
        if semantic is None or semantic[0] != item.get("role"):
            return False, f"predicate_{index}_semantic_alignment"
        if item.get("verb") not in ("none", *ACTIONS):
            return False, f"predicate_{index}_verb"
        resolution = item.get("verb_resolution")
        if resolution not in ("direct", "generalized", "technical_override"):
            return False, f"predicate_{index}_verb_resolution"
        if item.get("object") not in ("none", *OBJECTS):
            return False, f"predicate_{index}_object"
        qualifier = item.get("object_qualifier")
        if qualifier not in ("none", *OUTPUT_ARTIFACT_QUALIFIERS):
            return False, f"predicate_{index}_qualifier"
        if (qualifier != "none"
                and not qualifier_compatible(qualifier, item.get("object"))):
            return False, f"predicate_{index}_qualifier_object"
        materialize_to = item.get("materialize_to")
        if materialize_to not in ("none", *OBJECTS):
            return False, f"predicate_{index}_materialize_to"
        sink_mode = item.get("sink_mode")
        if sink_mode not in SINK_MODE_VALUES:
            return False, f"predicate_{index}_sink_mode"
        if ((sink_mode == "none") != (materialize_to == "none")):
            return False, f"predicate_{index}_sink_alignment"
        if require_sink_anchor:
            sink_anchor = item.get("sink_anchor_token_id")
            if not isinstance(sink_anchor, int):
                return False, f"predicate_{index}_sink_anchor_type"
            if sink_mode == "none":
                if sink_anchor != 0:
                    return False, f"predicate_{index}_sink_anchor_without_sink"
            elif (sink_anchor < 1 or sink_anchor > token_count
                  or sink_anchor == anchor):
                return False, f"predicate_{index}_sink_anchor"
        input_from = item.get("input_from_predicate_anchor_id")
        if (not isinstance(input_from, int) or input_from < 0
                or (input_from != 0 and input_from not in earlier_anchors)):
            return False, f"predicate_{index}_input_from"
        if (item.get("role") == "request"
                and (item.get("verb") == "none"
                     or item.get("object") == "none")):
            return False, f"predicate_{index}_incomplete_request"
        earlier_anchors.add(anchor)
        last_anchor = anchor
    return True, ""


def current_signature(intent: dict | None):
    if not intent:
        return None
    actions = intent.get("actions") or []
    if actions:
        return [f"{a.get('verb')}/{a.get('object')}" for a in actions]
    return f"{intent.get('verb')}/{intent.get('object')}"


def folded_signature_v22(frame: dict):
    heads = frame.get("semantic_heads") or []
    predicates = frame.get("predicates") or []
    heads_by_id = {head.get("predicate_id"): head for head in heads}
    predicates_by_id = {
        item.get("predicate_id"): item for item in predicates
    }

    def inherited_patient_object(head: dict) -> str | None:
        patient = head.get("patient")
        obj = (patient.get("object") if isinstance(patient, dict)
               else head.get("patient_object"))
        if obj and obj != "none":
            return obj
        source_id = head.get("source_predicate_id") or 0
        seen: set[int] = set()
        while source_id and source_id not in seen:
            seen.add(source_id)
            source = heads_by_id.get(source_id) or {}
            source_patient = source.get("patient")
            obj = (source_patient.get("object")
                   if isinstance(source_patient, dict)
                   else source.get("patient_object"))
            if obj and obj != "none":
                return obj
            source_id = source.get("source_predicate_id") or 0
        return None

    values: list[str] = []
    requested = [item for item in predicates if item.get("role") == "request"]
    for item_index, item in enumerate(requested):
        predicate_id = item.get("predicate_id")
        head = heads_by_id.get(predicate_id) or {}
        gloss = (head.get("semantic_gloss_en") or "").casefold()
        semantic_action = head.get("semantic_action")
        effect = head.get("effect")
        resource_scope = head.get("resource_scope")
        patient_obj = inherited_patient_object(head)
        carrier = head.get("carrier")
        if isinstance(carrier, dict):
            carrier_obj = carrier.get("object", "none")
            carrier_role = carrier.get("kind", "none")
        else:
            carrier_obj = head.get("carrier_object")
            carrier_role = head.get("carrier_role")
        sink_role = head.get("sink")
        if isinstance(sink_role, dict):
            sink_obj = sink_role.get("object", "none")
            sink_relation = sink_role.get("kind", "none")
        else:
            sink_obj = head.get("sink_object")
            sink_relation = head.get("sink_relation")
        source_id = head.get("source_predicate_id") or 0
        upstream = predicates_by_id.get(source_id)
        upstream_obj = upstream.get("object") if upstream else None

        verb = item.get("verb")
        obj = item.get("object")
        qualifier = item.get("object_qualifier")
        gloss_action = gloss if gloss in ACTIONS else None
        matched_argument = next(
            (name for name in CATALOG_ARGUMENT_NAMES
             if _lexically_related(gloss, name)), None)

        # Grounded roles precede noisy auxiliary labels. They are semantic
        # structure, not source-language triggers.
        if (carrier_role == "source_container"
                and carrier_obj not in (None, "none")
                and verb in ("read", "find", "get", "list")):
            obj, qualifier = carrier_obj, "none"
        elif (sink_relation == "primary_output"
              and sink_obj not in (None, "none")
              and verb in ("write", "create", "move")):
            obj = sink_obj
        elif (patient_obj and verb in (
                "read", "find", "get", "list", "filter", "sort", "group",
                "classify", "delete", "move", "change", "compress")):
            obj = patient_obj

        if (matched_argument and not gloss_action
                and obj in ("files", "images")
                and verb not in ("read", "find")):
            verb, obj, qualifier = "get", "files", "none"
        elif effect == "snapshot" and obj == "processes":
            verb = "get"
        elif (effect == "existence_check" and obj == "processes"
              and resource_scope == "single_known"):
            verb = "find"
        elif (resource_scope in ("collection_by_criterion", "whole_domain")
              and verb not in UNIVERSAL_ACTIONS
              and (verb, obj) not in CATALOG_ACTION_OBJECT_PAIRS
              and len(CATALOG_ACTIONS_BY_OBJECT.get(obj, ())) == 1):
            verb = next(iter(CATALOG_ACTIONS_BY_OBJECT[obj]))
        elif (effect == "metadata_lookup" and obj in ("files", "images")
              and not (semantic_action == verb == "read")):
            verb, obj, qualifier = "get", "files", "none"
        elif obj == "events" and effect in ("create_artifact", "schedule"):
            verb = "create"
        elif (effect == "attachment_reference"
              and semantic_action not in ("read", "find")):
            verb, obj, qualifier = "get", "files", "none"
        elif effect == "biometric_identity_lookup":
            verb, obj, qualifier = "get", "persons", "none"
        elif ((semantic_action == "create" or effect == "create_artifact")
              and qualifier != "none"):
            verb = "create"
        elif effect == "access_grant" or semantic_action == "share":
            verb, obj, qualifier = "share", "files", "none"
        elif (effect == "human_delivery" and semantic_action != "share"):
            verb, obj, qualifier = "send", "messages", "none"
        elif semantic_action == verb:
            pass
        elif gloss_action and gloss_action != verb:
            target_obj = None
            if (gloss_action in UNIVERSAL_ACTIONS
                    or (gloss_action, obj) in CATALOG_ACTION_OBJECT_PAIRS):
                target_obj = obj
            elif (upstream_obj
                  and (gloss_action, upstream_obj)
                  in CATALOG_ACTION_OBJECT_PAIRS):
                target_obj = upstream_obj
            if target_obj is not None:
                verb, obj = gloss_action, target_obj
                qualifier = (qualifier if target_obj == item.get("object")
                             else "none")

        base_obj = obj
        rendered_obj = obj if qualifier == "none" else f"{obj}_{qualifier}"
        values.append(f"{verb}/{rendered_obj}")
        sink = (sink_obj if sink_relation == "secondary_persistence"
                else "none")
        explicit_sink = any(
            later.get("verb") == "write"
            and later.get("object") == sink
            and later.get("input_from_predicate_id") == predicate_id
            for later in requested[item_index + 1:])
        if (sink != "none" and not explicit_sink
                and not (verb in ("write", "create") and base_obj == sink)):
            values.append(f"write/{sink}")
    if not values:
        return None
    return values if len(values) > 1 else values[0]


def folded_signature(frame: dict):
    if any("predicate_id" in head for head in frame.get("semantic_heads") or []):
        return folded_signature_v22(frame)
    requested = [item for item in (frame.get("predicates") or [])
                 if item.get("role") == "request"]
    head_facets = {
        item.get("predicate_anchor_token_id"): (
            (item.get("semantic_gloss_en") or "").casefold(),
            item.get("semantic_action"), item.get("effect"),
            item.get("resource_scope"),
        )
        for item in (frame.get("semantic_heads") or [])
        if item.get("role") == "request"
    }
    values = []
    records_by_anchor = {
        item.get("predicate_anchor_token_id"): item for item in requested
    }
    for item_index, item in enumerate(requested):
        anchor = item.get("predicate_anchor_token_id")
        gloss, semantic_action, effect, resource_scope = head_facets.get(
            anchor, ("", None, None, None))
        verb = item.get("verb")
        obj = item.get("object")
        qualifier = item.get("object_qualifier")

        gloss_action = gloss if gloss in ACTIONS else None
        matched_argument = next(
            (name for name in CATALOG_ARGUMENT_NAMES
             if _lexically_related(gloss, name)),
            None,
        )
        upstream = records_by_anchor.get(
            item.get("input_from_predicate_anchor_id"))
        upstream_obj = upstream.get("object") if upstream else None

        # Product canonicalization from language-neutral facets and live
        # catalog contracts. More specific technical boundaries precede gloss.
        if (matched_argument and not gloss_action
                and obj in ("files", "images")
                and verb not in ("read", "find")):
            # Predicate denotes an argument relation (e.g. a file supplied to
            # another operation), so materialize the known resource, don't
            # create/rewrite it. The matched term comes from catalog arg names.
            verb, obj, qualifier = "get", "files", "none"
        elif effect == "snapshot" and obj == "processes":
            verb = "get"
        elif (effect == "existence_check" and obj == "processes"
              and resource_scope == "single_known"):
            verb = "find"
        elif (resource_scope in ("collection_by_criterion", "whole_domain")
              and verb not in UNIVERSAL_ACTIONS
              and (verb, obj) not in CATALOG_ACTION_OBJECT_PAIRS
              and len(CATALOG_ACTIONS_BY_OBJECT.get(obj, ())) == 1):
            verb = next(iter(CATALOG_ACTIONS_BY_OBJECT[obj]))
        elif effect == "metadata_lookup" and obj in ("files", "images"):
            verb, obj, qualifier = "get", "files", "none"
        elif obj == "events" and effect in ("create_artifact", "schedule"):
            verb = "create"
        elif (effect == "attachment_reference"
              and semantic_action not in ("read", "find")):
            verb, obj, qualifier = "get", "files", "none"
        elif effect == "biometric_identity_lookup":
            verb, obj, qualifier = "get", "persons", "none"
        elif ((semantic_action == "create" or effect == "create_artifact")
              and qualifier != "none"):
            verb = "create"
        elif (effect == "access_grant" or semantic_action == "share"):
            verb, obj, qualifier = "share", "files", "none"
        elif (effect == "human_delivery" and semantic_action != "share"):
            verb, obj, qualifier = "send", "messages", "none"
        elif (verb not in UNIVERSAL_ACTIONS
              and (verb, obj) not in CATALOG_ACTION_OBJECT_PAIRS
              and len(CATALOG_ACTIONS_BY_OBJECT.get(obj, ())) == 1):
            # If a semantic pair is not a product route and the signed catalog
            # exposes exactly one operation for that object, the technical
            # projection is unambiguous (for example the process domain is a
            # snapshot-only capability today).  This is catalog evidence, not
            # an input-language or per-query rule.
            verb = next(iter(CATALOG_ACTIONS_BY_OBJECT[obj]))
        elif semantic_action == verb:
            # Two independently emitted decisions agree; an exact canonical
            # gloss is a third vote when available. Preserve that consensus
            # instead of allowing a contradictory auxiliary effect label to
            # rewrite it. Explicit technical effects above still take priority.
            pass
        elif gloss_action and gloss_action != verb:
            target_obj = None
            if (gloss_action in UNIVERSAL_ACTIONS
                    or (gloss_action, obj) in CATALOG_ACTION_OBJECT_PAIRS):
                target_obj = obj
            elif (upstream_obj
                  and (gloss_action, upstream_obj)
                  in CATALOG_ACTION_OBJECT_PAIRS):
                target_obj = upstream_obj
            if target_obj is not None:
                verb, obj = gloss_action, target_obj
                qualifier = (qualifier if target_obj == item.get("object")
                             else "none")
        elif semantic_action == "share" or effect == "access_grant":
            verb, obj, qualifier = "share", "files", "none"
        elif (semantic_action == "send"
              or (effect == "human_delivery" and semantic_action != "share")):
            verb, obj, qualifier = "send", "messages", "none"
        elif semantic_action == "classify" or effect == "label_assignment":
            verb = "classify"
        elif semantic_action == "group" or effect == "collection_grouping":
            verb = "group"
        elif semantic_action == "compress":
            verb, obj, qualifier = "compress", "files", "none"
        base_obj = obj
        if qualifier and qualifier != "none":
            obj = f"{obj}_{qualifier}"
        values.append(f"{verb}/{obj}")
        sink = (item.get("materialize_to")
                if item.get("sink_mode") == "explicit_persistence"
                else "none")
        explicit_sink = any(
            later.get("verb") == "write"
            and later.get("object") == sink
            and later.get("input_from_predicate_anchor_id") == anchor
            for later in requested[item_index + 1:]
        )
        if (sink and sink != "none" and not explicit_sink
                and not (verb in ("write", "create") and base_obj == sink)):
            values.append(f"write/{sink}")
    if not values:
        return None
    return values if len(values) > 1 else values[0]


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))] if ordered else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=("mono", "compound", "all"),
                    default="mono")
    ap.add_argument("--variant", choices=("v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v17", "v19", "v20", "v21", "v22", "v23", "v23lite", "v24"),
                    default="v0")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--indices", default="",
                    help="comma-separated 1-based case numbers after dataset selection")
    ap.add_argument("--skip-current", action="store_true")
    ap.add_argument("--show-all", action="store_true")
    ap.add_argument("--output", default="",
                    help="optional JSON record path (defaults to /tmp by variant/dataset)")
    args = ap.parse_args()

    bench = ROOT / "tests" / "benchmarks"
    mono = load_module("intent_accuracy_gold_uq", bench / "intent_accuracy_bench.py")
    compound = load_module("intent_compound_gold_uq", bench / "intent_compound_bench.py")
    cases: list[tuple[str, str, object]] = []
    if args.dataset in ("mono", "all"):
        cases.extend(("mono", q, exp) for q, exp in mono.GOLD + mono.EDGE_GOLD)
    if args.dataset in ("compound", "all"):
        cases.extend(("compound", q, exp)
                     for q, exp in compound.COMPOUND_GOLD + compound.COMPOUND_XL_GOLD)
    if args.limit:
        cases = cases[:args.limit]
    if args.indices:
        wanted = {int(value) for value in args.indices.split(",") if value.strip()}
        cases = [case for index, case in enumerate(cases, 1) if index in wanted]

    current_ok = folded_ok = invalid = regressions = improvements = 0
    records = []
    for idx, (group, query, expected) in enumerate(cases, 1):
        current = None if args.skip_current else current_signature(
            extract_intent(query, current_call) or {})
        semantic_meta = None
        precomputed_heads = None
        if args.variant == "v12":
            precomputed_heads, semantic_meta = semantic_head_call(query)
        frame, meta = folded_call(
            query, prompt_variant=args.variant,
            precomputed_heads=precomputed_heads,
        )
        if semantic_meta is not None and not semantic_meta["valid"]:
            meta = {**meta, "valid": False,
                    "reason": semantic_meta["reason"],
                    "semantic_raw": semantic_meta["raw"]}
        folded = folded_signature(frame) if meta["valid"] else None
        is_current_ok = current == expected if not args.skip_current else False
        is_folded_ok = folded == expected
        current_ok += is_current_ok
        folded_ok += is_folded_ok
        invalid += not meta["valid"]
        regressions += is_current_ok and not is_folded_ok
        improvements += (not is_current_ok) and is_folded_ok
        records.append({"group": group, "query": query, "expected": expected,
                        "current": current, "folded": folded,
                        "frame": frame, "meta": meta,
                        "semantic_meta": semantic_meta})
        if args.show_all or not is_folded_ok or current != folded or not meta["valid"]:
            print(f"[{idx:03d}] {'OK' if is_folded_ok else 'XX'} {query}")
            print(f"      OLD {current}  NEW {folded}  EXP {expected}")
            if not meta["valid"]:
                print(f"      INVALID {meta['reason']}: {meta['raw'][:300]}")

    total = len(cases)
    print("\nSUMMARY")
    print(f"variant={args.variant} dataset={args.dataset} cases={total}")
    if not args.skip_current:
        print(f"current={current_ok}/{total}")
    print(f"folded={folded_ok}/{total} invalid={invalid} "
          f"regressions={regressions} improvements={improvements}")
    for name, values in LATENCIES.items():
        if values:
            print(f"{name}_calls={len(values)} p50={statistics.median(values):.0f}ms "
                  f"p95={percentile(values, .95):.0f}ms total={sum(values):.0f}ms")
    out = (Path(args.output) if args.output else
           Path(f"/tmp/metnos_unified_query_{args.variant}_{args.dataset}.json"))
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    print(f"records={out}")
    if args.skip_current:
        return 0 if folded_ok == total and invalid == 0 else 1
    return 0 if (folded_ok >= current_ok and regressions == 0 and invalid == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
