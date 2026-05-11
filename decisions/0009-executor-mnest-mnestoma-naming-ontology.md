---
id: 0009
title: Rename of the executor/mnest/mnestoma ontology (ex neuron/synapse)
date: 2026-04-23
status: accepted
area: ontology
related:
  - 0008
---

## Context

Through April 2026 the project's vocabulary for its self-synthesized
components had been "neurons" and the relations between them "synapses".
Those words had served well in the first dialogues — they suggested
plasticity and emergence — but as the actual referent crystallized
(a sandboxed Python script with a TOML manifest, an Ed25519 signature,
a rigid I/O contract, fabricated centrally and approved by a human)
the cognitive metaphor began to mislead. A neuron does not get
fabricated; an executor does. Neurons learn from inside; executors
have whatever capability they were wired with and never improve.

Two long night sessions on 22 and 23 April produced the rename and a
larger ontological cleanup. Roberto's binding constraint during these
sessions was explicit: *"if Metnos becomes too complex the user will
not use it"*. The work was therefore not just renaming, it was the
attempt to express the whole design as a few principles whose
consequences are readable, rather than a list of mechanisms the
reader has to memorize.

## Decision

Three rename axes plus one taxonomy clarification, all canonical.

**Neuron → executor.** Captures "executive capacity, not skill", uses
clean CS vocabulary, drops the misleading cognitive promise. Canonical
definition: *an executor is a unit of executive capacity, not of
competence; invoked, it produces a measurable effect, otherwise it is
inert; intelligence, if any, lives in the synthesizer that built it
and in the human who approved it.*

**Synapse → mnest** (and a family). From the Greek root μνήμη /
Semon's "engram", aligned with the network-medicine vocabulary
(genome, proteome, connectome). The atomic edge between two executors
is a `mnest`; a community-detected cluster of densely connected
executors is a `traccia mnestica` (mnestic trace, Barabási's "disease
module" applied here); the totality of mnests in an installation is
the `mnestoma` (`mnestome` in English with the *-ome* suffix); a
snapshot at time T is a `mnestshot`. Beyond co-call counters, mnests
also carry **proto-mnest** entries: registered attempts that found no
terminus, i.e. *capabilities the system tried to use and could not*.
Proto-mnests are the engine of capability proactivity: when N
recurrent proto-mnests cluster, the synt proposes a new executor.

**Maintenance subsystem → ager.** The batch cycle that ages, recycles
or deprecates stagnant mnests and executors. Ontological name (it
describes the function), not action-name. Considered alternatives —
`autofago` (too biological), `equalizer` (too technical), `cleaner` /
`remover` (too flat) — were rejected.

**Flat executor taxonomy.** A single ontological type — executor.
Origins differ (`builtin` seed, `synthesized` from scratch, `fused`
from trace pattern, `imported`), each with its own aging / fusion /
gate policy, but no special class for seeds. A builtin can evolve in
the future without an architectural exception.

The decision is consolidated in the v1.1 canonical triad
`executor.html` / `mnest.html` / `mnestoma.html` (IT) and
`mnestome.html` (EN), bilingual from day one, replacing
`neuron.html` and `synapse.html` (now untrusted, see ADR 0008). The
glossary maps the old terms to the new ones; the dialogue *Sugli
executor e sulla memoria distribuita* is the discursive validation.

## Alternatives considered

**Keep the cognitive metaphor (neuron / synapse).** Pro: continuity
with the early corpus; the metaphor was intelligible to a
non-technical reader. Con: it overpromises. A neuron implies
plasticity and learning that an executor does not have, and lying to
the reader about the nature of the artifact is the worst kind of
documentation. Rejected.

**Rename to plain CS terms (`tool`, `function`, `module`, `edge`).**
Pro: zero conceptual cost. Con: erases the network-medicine
foundation that *gave* the second axis (mnestoma as memory of the
installation). The decision to keep `mnest` family is not cosmetic;
without it, the system loses a vocabulary for proactive capability
growth. Rejected.

**Apply rename only to internal code and keep the doc vocabulary.**
Pro: less work. Con: produces drift between code and corpus that
multiplies over time; readers who follow the docs would build a
mental model the code does not honor. Rejected.

## Consequences

The corpus grew a v1.1 canonical triad and the 22 prior microdesign
documents were marked UNTRUSTED (ADR 0008). Subsequent rewrites of
gateway / sandbox / pairing / approval_ux must respect the new
vocabulary. Code-level rename of the internal symbols (e.g.
`Neuron`, `Synapse`) is a separate batch, deferred to a dedicated
session — it is mechanical and low-risk, but spreads across many
files.

The flat taxonomy enables an unanticipated benefit: synt-generated
and hand-written executors share the same machinery. Manifest schema,
signature, sandbox profile, birth tests, audit shape — one path. A
later evolution (e.g. a builtin that gains a probabilistic component)
does not require a new entity, only a new policy on the same
ontological type.

The proto-mnest field (memory of attempted-and-failed combinations)
is the structural place where the cascade of synthesis (ADR 0010)
lands: when a proto-mnest cluster crosses threshold the synt has a
concrete generation target, not an imagined one.
