---
id: 0036
title: No third-party real names in docs, code, or examples
date: 2026-04-27
status: accepted
area: documentation
related:
  - 0035
---

## Context

The host + guest model (ADR 0035) introduced "guests" as an
explicit category — invited family members or trusted others. The
documentation of the system, the code's example data, the dialogues
and walk-through scenarios all needed examples of guests doing
things. The natural temptation in such examples is to use the real
names of family members ("Anna asks Metnos to schedule a meeting",
"Marco requests an invoice export").

That temptation has to be refused. The corpus is bilingual public
documentation reachable from `metnos.com`. Putting a real family
member's name into a public document is an intrusion on family
privacy that the project has no right to make. Even in private
memory files, real names create a habit that leaks: the same name
appears later in a doc draft, gets deployed, and only then somebody
notices.

## Decision

Real proper nouns of people other than Roberto are not used in
documentation, code, memory files, scaffolding, or example data.
The only admissible proper noun is "Roberto" (author and host). For
everyone else, generic terms are used.

Permitted forms:
- "guest", "ospite", "familiare invitato" (Italian), "trusted
  invitee", "actor", "sender";
- when multiple guests are needed: "guest A", "guest B" or "G1",
  "G2";
- in code examples: `actor='guest'`, `actor='ospite'`, never
  `actor='anna'` or any real name;
- in dialogue or scenarios: "the guest asks X" rather than "Anna
  asks X".

The rule is absolute. It applies in all surfaces — the public
corpus, the source code, the configuration files, the memory store,
even comments. There is no "private memory" carve-out, because
private memories influence drafts, and drafts deploy.

## Alternatives considered

**Use real names with an anonymization pass before deploy.** Pro:
more vivid examples during authoring. Con: anonymization passes
are forgettable; the failure mode is that one name slips through;
the cost of the slip is much greater than the writing-vividness
gain. Rejected.

**Use realistic invented names ("Carla", "Luca").** Pro: still
vivid, no real-person privacy issue. Con: real-sounding invented
names blur with reality in a small project where the household
context is known to readers; "Carla" looks like it could be a real
person; the rule should be unambiguous. Rejected — generic terms
are the cleanest discriminator.

**Use famous-person names ("Plato asks Metnos...").** Pro: clearly
non-real-context. Con: comic effect that distracts from the
example's purpose; varies by reader's familiarity with the name.
Rejected.

## Consequences

The corpus stays clean. Documentation examples use "the host", "a
guest", "actor A". Code examples in test fixtures use
`actor='guest'`. Memory files, even private ones, follow the same
rule.

The narrative cost is small. A scenario "the guest asks Metnos to
schedule a delivery on Friday" reads as well as "Marco asks Metnos
to schedule a delivery on Friday", and is more transferable: a
reader from a different household can map "guest" to their own
context, where they cannot map "Marco".

The interaction with ADR 0035 is direct: the host + guest model
provides the categorical labels, this rule provides the lexical
labels. Together they keep the design and its public face
consistent.

A specific implication for future testing data: when a synthetic
mailbox or contact list is needed for a test fixture, the names
inside are also generic (`guest@example.com`, `contact1@example.com`).
The discipline applies to test data the same as to docs.
