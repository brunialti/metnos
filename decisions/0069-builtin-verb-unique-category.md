---
id: 0069
title: Builtin + verb-unique — fourth category of executors for privileged primitives
date: 2026-05-02
status: accepted
area: executor, runtime, naming
related:
  - 0002  # naming convention stringente
  - 0009  # executor / mnest / mnestoma ontology
  - 0013  # builtin executors as third category
  - 0045  # closed naming vocabulary
  - 0066  # synth executors in user data dir
complements:
  - 0013  # estende la categoria builtin con un sottoinsieme privilegiati
---

> Modificata da ADR 0088 (4/5/2026): l'invariante 2 («not visible to the
> PLANNER») non e' piu' assoluta. Singoli verb-unique builtin possono
> optare per `EXPOSE_TO_PLANNER=True` purche' dichiarino anche
> `MANIFEST_VIRTUAL`. Il caso `admin` adotta la nuova soglia. Il
> sudoer resta `EXPOSE_TO_PLANNER=False`.


## Context

Until now Metnos recognised three categories of executor: handcrafted in
the repo pool (`/opt/myclaw/executors/`, ADR 0002 + 0045), synthesized by
the Synt and persisted in the user data dir (`~/.local/share/metnos/executors/`,
ADR 0066), and builtin runtime modules that ship with the system but are
not part of the closed vocabulary as ordinary tools (ADR 0013, examples:
the scheduler, the mnestoma ager, the synt cascade itself).

Two new components &mdash; the shell-orchestration `admin` and the privileged
executor `sudoer` &mdash; have been designed during the 2 May 2026 sessions on
shell execution capability (see project memory `metnos_shell_exec_analysis.md`).
They share three properties that none of the existing three categories
captures cleanly:

1. They expose actions that the planner LLM **must not** be able to compose
   freely. Their power is asymmetric: a wrong call can compromise the host.
2. Their verbs (`admin`, `sudoer`) are not part of the closed vocabulary and
   must not enter it: putting them there would mean exposing them to synth
   pipeline at stage 1, defeating the safety boundary.
3. They are invoked only by the system dispatcher itself, never directly by
   the user via NL request and never proposed by the planner as part of a
   ReAct plan.

Without a formal category, these properties would have to be re-stated for
each new privileged primitive, accumulating prose in many ADRs. Better to
fix the category now and let future privileged primitives cite it.

## Decision

We introduce a fourth executor category named **`builtin + verb-unique`**.
It is a strict subclass of *builtin* (ADR 0013) with five additional
invariants that the loader enforces at boot.

### Position in the taxonomy

| Category | Vocabulary | Synthesisable | Visible to PLANNER |
|---|---|---|---|
| handcrafted | closed (23 verbs) | no | yes |
| synth | closed (23 verbs) | yes | yes |
| builtin | closed or extended | no | yes (via tool wrapper) |
| **builtin + verb-unique** | **private, outside vocabulary** | **no** | **no** |

### Five invariants

A builtin + verb-unique executor MUST satisfy all five of the following
properties, checked by `runtime/loader.py` at module load:

1. **Unique verb outside the closed vocabulary.** Its verb does not appear
   in `runtime/vocab.py::ACTIONS`. No other executor (any category) may
   share the verb. The loader rejects collisions with a fatal error at
   boot.

2. **Not visible to the PLANNER.** The manifest declares
   `not_in_vocab = true` and `expose_to_planner = false`. The prefilter
   (ADR 0058 + 0063) excludes these entries from `rank_with_intent`. The
   tool list emitted to the LLM at PLANNER turn does not contain them.

3. **Invocable only by a whitelisted caller.** The loader records, for each
   verb-unique builtin, the list of authorised invokers (typically the
   system dispatcher and possibly other verb-unique builtins that compose
   it in chains). Any invocation from outside the whitelist fails with
   an explicit `PermissionError` at runtime, never silently.

4. **Not synthesisable, not even by derivation.** The Synt pipeline (ADR
   0051) reads the taxonomy at boot and excludes verb-unique verbs from
   stage 1 (NAMING) and stage 5 (CODE). Neither a fresh synth proposal
   nor a derivative of an existing one may target a verb-unique slot.

5. **Mandatory structured audit.** Every invocation writes a record to
   `events.turn_id` (ADR 0067) with: `caller_id`, `intent_text` (when
   originating from a chain), `argv_or_payload`, `outcome`,
   `caller_authorised` (boolean for invariant 3 verification). For
   ordinary builtins audit is the default; for verb-unique it is a
   precondition of existence.

### Manifest schema additions

The TOML manifest of a builtin + verb-unique executor declares two new
top-level fields:

```toml
not_in_vocab       = true
expose_to_planner  = false
authorised_callers = ["runtime.dispatcher", "builtin.admin"]  # whitelist
```

### Loader enforcement

`runtime/loader.py` runs the following checks at boot, in order:

1. parse manifest, detect `not_in_vocab=true`;
2. check the verb is absent from `vocab.py::ACTIONS`;
3. check no other executor (any category) shares the verb;
4. check the manifest declares a non-empty `authorised_callers`;
5. register the verb-unique entry in a separate map
   `runtime.loader.VERB_UNIQUE_REGISTRY` (not in the regular catalog
   exposed to the PLANNER);
6. when a call attempts to invoke a verb-unique entry, look up the caller
   identity and validate it against the whitelist; refuse otherwise with
   `PermissionError`.

Any of these checks failing is a **fatal boot error**: the system refuses
to start with an inconsistent verb-unique configuration.

## Alternatives considered

**(a) Extend ADR 0013 (builtin) without a new sub-category.** Just declare
that some builtins are «privileged» via an ad-hoc flag. Rejected because
the privileged status carries five invariants, not one flag, and these
invariants will be cited by every future privileged primitive: better to
formalise once.

**(b) Add the privileged verbs to the closed vocabulary as «reserved».**
Rejected because reserved verbs in the public vocabulary would still be
visible to stage 1 of the Synt pipeline, the prefilter, and the planner,
unless we then sprinkle exceptions everywhere. Keeping them outside the
vocabulary entirely is the cleaner boundary.

**(c) Make every privileged operation a manually-locked handcrafted
executor.** Rejected because handcrafted executors are part of the public
vocabulary by construction (ADR 0045): hiding them via per-executor
flags would split that category into two, again replicating the
verb-unique distinction without the formal name.

## Consequences

**What this opens.** A clean home for `admin`, `sudoer`, and future
privileged primitives (`key_manager`, `secret_slot`, `ager_curator`,
`introvertiva_engine`). Each new member cites this ADR, states which of
the five invariants are non-trivial in its case, and adds nothing to
the surface visible to the planner or the synth pipeline.

**What this closes.** The temptation to expose privileged operations
through clever prompts: the verbs simply do not exist for the planner
to call.

**Work items spawned.**
- ADR 0070 (apply the category to the `admin` / `sudoer` chain).
- `runtime/loader.py` extension to enforce the five invariants
  (estimated ~80 lines plus tests).
- A small unit test suite in `tests/runtime/test_verb_unique.py` covering
  the boot-time rejection of: collision with public vocabulary, missing
  `authorised_callers`, double registration, invocation by an
  unauthorised caller.

**What becomes more expensive.** Adding a privileged primitive now costs
an ADR (citing 0069) plus a manifest with five mandatory fields, instead
of «just a Python file». This is the intended friction: privileged power
should require a deliberate act, not casual addition.

**What becomes easier.** Auditing the surface of privileged power: a
single registry (`VERB_UNIQUE_REGISTRY`) lists every privileged entry
in the system, with its whitelist of callers and its purpose. No need
to grep across modules.
