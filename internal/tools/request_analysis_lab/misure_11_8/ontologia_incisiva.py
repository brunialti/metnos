#!/usr/bin/env python3
"""ONTOLOGY rewritten for incisiveness: same facts, sharper form.

Incisiveness is the one axis never varied so far. We have measured length
(-41% cost 3 queries), order (a rewrite by dimensions cost 1), added rules
(-2 and -3, twice) and a removed block (+1). None of those changed HOW a
retained rule is written.

Every choice below is traceable to a measurement made on 10-11/8:

  1. Coverage is identical. All 33 atomic facts of the original survive; the
     held-out blind spots (identity registry, mailbox moves) live in items 2
     and 4, so cutting them would be measuring the wrong thing.
  2. Length is not compressed. Correlation between block size and measured
     load was -0.10, and the one real compression attempt scored 33/40 and ran
     slower. Only ceremony goes, never a fact.
  3. Every binding ends in the explicit `verb`/`object` pair. The original left
     the object implicit across whole paragraphs ("Creating/updating membership
     is set, deleting membership is delete"), so the model had to carry
     `persons` across three clauses. Eleven such inferences are now written out.
  4. The notation is declared once. The original used `find/dirs` and
     `get/processes` without ever saying that the slash separates the two
     fields to emit. Declaring it costs one clause and pays for every later
     block that uses the same notation.
  5. The discriminating word comes first. Numbered items became domain heads
     (Identity, Calendar, Mail, Filesystem, Visual, System, Clauses): the model
     looks up a domain, not an ordinal. GLOSS, the block with the highest load
     per character measured (+7 for 445 chars), leads every sentence with the
     field being decided.
  6. Contrast is kept where the contrast IS the decision (read vs describe,
     move vs set, filter vs delete, find vs get on processes) and dropped where
     it only defended against a temptation. The negation law was falsified on
     11/8, so this is not a rule about negation: it is a rule about whether the
     sentence carries a decision.

The first draft failed its own equivalence test. An adversarial reading against
the assembled prompt caught nine content changes hiding inside what was meant to
be a form change; all nine are reverted here, and they are worth recording
because they are the failure modes of this kind of rewrite:

  * "is not an additional operation" had become "is not a second record", which
    INVERTS the contract: BASE line 580 orders one record for every predicate
    mistakable for an operation, and line 585 gives a relative clause the role
    `description`. The rewrite was telling the model to suppress a required
    record.
  * "does not create a second write action ... unless writing/saving" had become
    "is not a second record ... unless saving", losing both the named verb and
    the lexical trigger "write".
  * The heading claimed every binding is written as a `verb`/`object` pair. Six
    sentences in the block have no object, so the block contradicted its own
    heading. Only the notation gloss survives.
  * "selection by a criterion is find" had gained /files, which routes "how many
    directories" away from dirs; the object stays implicit, as in the original.
  * "process existence" had gained "one named", and "whole machine snapshot" had
    become "process-table": both imported from neighbouring blocks.
  * Domain heads had swallowed two restrictive qualifiers, turning "Identity
    enrollment ... are persons" into the unconditioned "enrollment is persons".
  * "in this ontology" and ", not describe" had been dropped as ceremony. They
    are not: each marks a decision against the model's prior.
  * "is forbid" had become "takes role forbid". Defensible, and measured
    separately: a correction is not a form change.
  * numbers and places had ended up under a "System:" head that does not
    describe a clock or a place.

Read-only on production: the frozen bench file is never edited, this module
replaces a module attribute at runtime.
"""
from __future__ import annotations

ONTOLOGIA_INCISIVA = """\
Product ontology refinements (technical behavior, independent of input
language; `verb`/`object` names the two fields):

A direct information question is a requested operation even with no imperative
surface verb. A request for raw or complete content is read, not describe;
describe is only condensation, synthesis, or aggregation.

Identity: identity enrollment and biometric face-registry membership are
persons. Creating or updating a membership is set/persons, deleting one is
delete/persons, reading a known person's information is read/persons, and a
registry snapshot is get/persons. Ordinary structured business records are
entries, never persons.

Calendar: calendar commitments are events. Reading one's own commitments is
read/events, discovering candidate availability by a criterion is find/events,
and creating a booking is create/events.

Mail: mailbox folders are part of messages. Moving a message into another
mailbox is move/messages, never set. Selecting a subset while the source
survives is filter; delete is only actual removal from the source of truth.

Filesystem: the content of a known file or path is read/files, and the
attributes of a known file are get/files. Existence, pattern search, a count of
matching elements, or selection by a criterion is find. Enumerating the members
of a known container is list. Aggregate inspection of directories is find/dirs
in this ontology. A destination path named alongside a requested download does
not create a second write action unless writing or saving is independently
requested.

Visual: images covers visual search, visual description or classification, and
OCR of image pixels. Generic filesystem operations and file metadata on an image
use files, and metadata enrichment attached to photo files is get/files.

System: packages are installed software, and an existence or installation-state
check is find/packages. processes are running processes and machine-health
snapshots: process existence is find/processes, while a whole machine or
process snapshot is get/processes.

numbers covers clock and time scalar answers. places covers the user's own
location and geographic place lookup.

A predicate inside a relative or passive clause describes the requested entity
and is not an additional operation. A negated or excluded operation is forbid,
including when it appears as an infinitive or an attached pronoun.
"""


def install(module) -> bool:
    """Swap the block in a loaded bench module. Refuses a silent no-op."""
    if len(module.ONTOLOGY_REFINEMENTS) < 2000:
        raise AssertionError("ONTOLOGY_REFINEMENTS not in its expected state")
    module.ONTOLOGY_REFINEMENTS = ONTOLOGIA_INCISIVA
    return True
