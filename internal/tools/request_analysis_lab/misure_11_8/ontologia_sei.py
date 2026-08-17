#!/usr/bin/env python3
"""ONTOLOGY rewritten in the Metnos §6 prescriptive form.

House form, copied from `runtime/prompts/en/agentic_sites_action.j2`:

    YOU MUST: <imperative verb + action>.
    YOU MUST NOT: <imperative verb + forbidden action>.
    OK: <positive example, one line>.
    ERROR: <negative example, one line>.

§6 caps a rule at 4-5 lines, forbids prose justification and hedges. The measured
house rule "§6 prompts carry placeholders only" (Tutor work, 103 -> 106/134)
means OK and ERROR describe the SHAPE of a correct or incorrect act and never
quote a sample request; the house example obeys this.

Version 2. The first draft was rejected by an adversarial reading on both fronts,
and the reasons are the finding, not an accident of drafting. Applying §6 to a
block written as scoped paragraphs pushed the text three ways at once:

  1. It IMPORTED THREE TIE-BREAKS. Needing a prohibition and a negative example
     per rule, the draft reached for the sharpest available contrasts -- and the
     sharpest ones live in `TIE_BREAK_REFINEMENTS`: "enumeration -> get/persons",
     "never get/dirs", "not set/images". In this arm TIE_BREAK is REMOVED, so the
     draft would have measured form plus three reinstated tie-breaks. That alone
     would have voided the comparison.
  2. It DESTROYED THE PARAGRAPH SCOPING. The source scopes `read`, `get`, `find`
     and `list` with the literal words "For filesystem entities:", and states
     `filter` inside the mail paragraph. §6 has no heading device, so atomic
     rules made all four global and put "selection by a criterion is find" in
     direct contradiction with "selecting a subset is filter". The scope is
     restored here with the source's own words, at the cost of compound MUST
     lines: §6 atomicity and the source's scoping cannot both be satisfied.
  3. It INVENTED DISCRIMINANTS to fill mandatory slots: "records that merely
     describe people", a prohibition on `list` under a criterion, "an operation
     the grammar prohibits" in place of "negated or excluded". Where the source
     states no contrast, the prohibition here only mirrors the obligation.

Also corrected from the draft: `processes` denotes machine-health snapshots
again, `packages` denotes installed software again, "aggregate inspection" is not
narrowed to "size", the download rule caps a second WRITE and not the record
count, `numbers`/`places` include rather than exclusively own their cases, the
definition of `describe` sits in the obligation instead of the prohibition, and
the framing sentence is gone.

Measured: 17 rules, 6037 characters against 2253 and 953 words against 321, so
2.68x by character and 2.97x by word. Block size correlates -0.10 with measured
load, so length is not evidence either way.

Read-only on production: module attribute replacement, frozen file untouched.
"""
from __future__ import annotations

ONTOLOGIA_SEI = """\
Product ontology refinements (technical behavior, independent of input
language):

YOU MUST: treat a direct information question as a requested operation.
YOU MUST NOT: require an imperative surface verb before emitting a request.
OK: emit a request record for an interrogative clause that asks for data.
ERROR: emit a non-request record because no imperative verb is present.

YOU MUST: route a request for raw or complete content to read; describe covers
only condensation, synthesis, or aggregation.
YOU MUST NOT: route a request for raw or complete content to describe.
OK: emit read when the content itself is asked for.
ERROR: emit describe when the content itself is asked for.

YOU MUST: route identity enrollment and biometric face-registry membership to
persons: creating or updating a membership is set/persons, deleting one is
delete/persons, reading a known person's information is read/persons, taking a
registry snapshot is get/persons.
YOU MUST NOT: route membership of that registry to another object.
OK: emit get/persons when a snapshot of the registry is taken.
ERROR: emit a non-persons object for membership of the identity registry.

YOU MUST: route ordinary structured business records to entries.
YOU MUST NOT: route ordinary structured business records to persons.
OK: emit entries for ordinary structured business records.
ERROR: emit persons for ordinary structured business records.

YOU MUST: route calendar commitments to events: reading one's own commitments
is read/events, discovering candidate availability by a criterion is
find/events, creating a booking is create/events.
YOU MUST NOT: route a calendar commitment to another object.
OK: emit find/events when candidate availability is discovered by a criterion.
ERROR: emit an object other than events for a calendar commitment.

YOU MUST: treat mailbox folders as part of messages, route moving a message
into another mailbox to move/messages, and route selecting a subset that leaves
source data intact to filter.
YOU MUST NOT: route that move to set, and do not emit delete unless data is
actually removed from the source of truth.
OK: emit move/messages for a transfer between mailboxes.
ERROR: emit set for a transfer between mailboxes.

YOU MUST: for filesystem entities, route the content of a known file or path to
read/files and the attributes of a known file to get/files.
YOU MUST NOT: exchange those two routes.
OK: emit get/files when the attributes of a known file are asked for.
ERROR: emit read/files when the attributes of a known file are asked for.

YOU MUST: for filesystem entities, route existence, pattern search, a count of
matching elements, or selection by a criterion to find, and enumeration of the
members of a known container to list.
YOU MUST NOT: route those cases to another verb.
OK: emit list when the members of a known container are enumerated.
ERROR: emit a verb other than find for a pattern search.

YOU MUST: for filesystem entities, route aggregate inspection of directories to
find/dirs in this ontology.
YOU MUST NOT: route aggregate inspection of directories to another route.
OK: emit find/dirs for aggregate inspection of a directory.
ERROR: emit a route other than find/dirs for aggregate inspection of a
directory.

YOU MUST: emit a requested download as its own request when the clause also
names a destination path.
YOU MUST NOT: create a second write action for that destination, unless writing
or saving is independently requested.
OK: emit a second write record when saving is independently commanded.
ERROR: emit a second write action for a destination named in the same clause.

YOU MUST: route visual search, visual description or classification, and OCR of
image pixels to images, and route generic filesystem operations and file
metadata on an image to files.
YOU MUST NOT: route a generic filesystem operation or file metadata on an image
to images.
OK: emit images when visual content itself is analyzed.
ERROR: emit images for a generic filesystem operation on an image file.

YOU MUST: route metadata enrichment attached to photo files to get/files.
YOU MUST NOT: route metadata enrichment attached to photo files elsewhere.
OK: emit get/files for descriptive metadata attached to a photo file.
ERROR: emit a route other than get/files for descriptive metadata attached to a
photo file.

YOU MUST: treat packages as installed software and route an existence or
installation-state check to find/packages.
YOU MUST NOT: route an installation-state check to another object.
OK: emit find/packages for an installation-state check.
ERROR: emit an object other than packages for an installation-state check.

YOU MUST: treat processes as running processes and machine-health snapshots,
route process existence to find/processes, and route a whole machine or process
snapshot to get/processes.
YOU MUST NOT: route a whole machine or process snapshot to find/processes.
OK: emit get/processes for a whole machine or process snapshot.
ERROR: emit find/processes for a whole machine or process snapshot.

YOU MUST: treat numbers as including clock and time scalar answers, and places
as including the user's own location and geographic place lookup.
YOU MUST NOT: exclude a scalar time answer from numbers or a place lookup from
places.
OK: emit numbers for a scalar time answer.
ERROR: emit an object other than numbers for a scalar time answer.

YOU MUST: emit a predicate inside a relative or passive clause as a record that
describes the requested entity.
YOU MUST NOT: emit it as an additional operation.
OK: emit a describing record for a predicate in a relative clause.
ERROR: emit an additional operation for a predicate in a relative clause.

YOU MUST: emit a negated or excluded operation as forbid, including when it
appears as an infinitive or an attached pronoun.
YOU MUST NOT: emit a negated or excluded operation as a request.
OK: emit forbid for an operation the user negates or excludes.
ERROR: emit a request for an operation the user negates or excludes.
"""


def install(module) -> bool:
    """Swap the block in a loaded bench module. Refuses a silent no-op."""
    if len(module.ONTOLOGY_REFINEMENTS) < 2000:
        raise AssertionError("ONTOLOGY_REFINEMENTS not in its expected state")
    module.ONTOLOGY_REFINEMENTS = ONTOLOGIA_SEI
    return True
