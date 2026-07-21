# Metnos Executor Standard

**Identifier:** `metnos.executor/1.0`  
**Status:** normative  
**Scope:** every planner-visible executor, regardless of origin or transport

## 1. Purpose

This document is the foundational contract for Metnos executors. It defines
what the planner may assume, what an executor must declare, and what evidence is
required before a result can be reported as successful.

The standard is intentionally independent from implementation style. Product
membership, origin, and transport are separate axes: an executor may be
Metnos-builtin or third-party; handcrafted or synthesized; in-process, local,
remote, or backed by a future MCP transport. These choices must not change the
planner-facing contract or enlarge the executor's authority. In particular, a
provider qualifier such as `_github` does not imply imported provenance.

The keywords **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are
normative.

### 1.1 Authority order

When sources disagree, use this order:

1. this versioned standard and its adopting decision;
2. the runtime authorities explicitly referenced here, such as
   `runtime/vocab.py`, `runtime/naming_grammar.py`, placement, capability, and
   reverse-pattern registries;
3. accepted domain decisions that add stricter requirements;
4. design notes, handoffs, examples, and historical manifests.

An inconsistency between the standard and a runtime authority is a blocking
architecture defect. It MUST be resolved at the source of truth, never by a
local exception in an executor or transport adapter.

## 2. Conformance and migration

There are three conformance states:

| State | Meaning | Planner visibility |
|---|---|---|
| `candidate` | Draft under generation, translation, signing, or admission | no |
| `legacy` | Existing executor not yet assessed against this standard | temporarily yes |
| `conformant` | Declares `executor_standard = "metnos.executor/1.0"` and passes all applicable gates | yes |

Every new executor MUST become `conformant` before it becomes active. A
candidate MUST NOT be made visible merely because its code runs once.

Existing executors are not rewritten in a single migration. They remain
`legacy` until they are deliberately refactored. Refactoring an existing
executor means:

1. preserve its planner-facing purpose or version the incompatible change;
2. remove duplicated domain logic in favor of shared runtime helpers;
3. satisfy every applicable requirement and test gate in this document;
4. add the standard declaration only after the checks pass;
5. re-sign the manifest and run the relevant regression and E2E tests.

The absence of a declaration is a temporary migration allowance, not an
alternative standard. New generators and authoring templates MUST emit the
current standard identifier. Admission MUST reject unknown standard versions.
A manifest MUST NOT claim partial conformance: once the declaration is present,
every applicable active-profile gate is blocking.

The required declaration is:

```toml
manifest_format = "1.0"
executor_standard = "metnos.executor/1.0"
```

## 3. Public contract

### 3.1 Identity and purpose

- The name MUST follow the canonical `verb_object[_qualifier[_descriptor]]`
  grammar and the closed vocabulary in `runtime/vocab.py`.
- The executor MUST have one narrow, independently testable purpose. Provider,
  protocol, device, and model names MUST NOT create parallel public verbs when
  they can be represented as backends or placement.
- Descriptions MUST state `SCOPO`, `PATTERN`, `NON`, and `OUT` within the runtime
  rendering budget and MUST remain understandable to the configured local
  planner tier.
- `SCOPO`, `PATTERN`, `NON`, and `OUT` are stable protocol anchors in every
  language; the text following each anchor is localized.

### 3.2 Input

- The standard begins after natural-language interpretation. Users MUST NOT be
  required to know executor names, argument names, enum values, ordering rules,
  or a canonical command template. Planner and deterministic normalization
  layers translate varied natural phrasing into the typed executor input.
- Affinity terms, examples, and argument descriptions are semantic guidance,
  not a phrase whitelist. An executor family MUST NOT turn them into a hidden
  command language or reject an otherwise unambiguous request merely because
  it uses a different wording.
- The public input MUST be a JSON object described by `[args]`.
- Every required argument MUST exist in `args.properties`; unknown arguments
  MUST be rejected or removed before execution according to the runtime policy.
- Collection operations MUST be vectorial. Zero and one item are normal cases,
  not separate executor names. Homogeneous iteration belongs inside the
  executor; conditional cross-step iteration belongs to the planner.
- Common natural-language near-misses MUST be normalized by shared helpers when
  the correction is unambiguous. Domain code MUST NOT grow site-, provider-, or
  query-specific patches for general coercion problems.
- Runtime-owned arguments MUST declare `runtime_resolved = true`, remain absent
  from `required`, and never be requested from the model or user. The `_` prefix
  MAY be used for opaque control values, but it is not an authority boundary:
  ownership is defined exclusively by `runtime_resolved` and enforced by the
  grammar, proposer, coercion, and runtime resolver layers.
- Raw credentials, approval tokens, and unrelated personal context MUST NOT be
  placed in planner-visible arguments.

### 3.3 Output and honest failure

Every terminal result MUST be a JSON object with `ok: bool`.

A producer SHOULD return `entries`; a mutating or transforming executor SHOULD
return `results`. Scalar and dialog results MAY use a purpose-specific field,
but their shape MUST be declared in `[output].schema_inline`.

The following rules are mandatory:

- `ok = true` requires the declared postcondition to be observed, not inferred
  from the absence of an exception.
- A complete failure MUST return `ok = false`, a language-neutral
  `error_class`, a stable `error_code` where one exists, and an i18n-rendered
  user message. Raw provider or subprocess text belongs only in redacted
  diagnostic detail.
- Mixed outcomes MUST expose successful items and failed items and MUST mark the
  result as partial. They MUST NOT silently discard failures.
- Any cap, pagination stop, timeout budget, or incomplete source coverage MUST
  be visible through fields such as `truncated`, `used`, `available_total`, or a
  domain-equivalent declared field.
- An empty valid result is different from an execution failure. The executor
  MUST preserve that distinction.

## 4. Authority and effects

- The manifest MUST declare all required capabilities using names from the
  canonical runtime registry. Filesystem, network,
  credentials, privileged operations, platforms, and placement MUST be derived
  from declared data, not executor-name allowlists or ambient process access.
- Remote-provider authority MUST use `provider:access`. Its `hint` MUST be a
  provider-skill binding from `runtime/vocab.py::PROVIDER_SKILLS`. A backend
  selector MAY restrict it with the closed clause
  `when = { arg = "client", values = ["provider"] }`; malformed, undeclared,
  unreachable, or non-matching conditions grant no authority.
- Executor names, provider suffixes, argument values, schema defaults, and
  provenance MUST NOT grant network or credential access to a conforming
  executor. They may remain temporary compatibility signals only for an
  explicitly legacy executor.
- A backend or transport MAY reduce authority but MUST NOT enlarge it.
- Consent and credential mandates are external authority gates. An executor,
  including an intelligent one, MUST NOT approve itself or reinterpret a denied
  action as a narrower success.
- Read-only, idempotent, and mutating behavior MUST be distinguishable by the
  runtime from the canonical verb and declared metadata.
- If `revertible = true`, the manifest MUST name a supported reverse pattern and
  the implementation MUST return the evidence required to execute and verify
  that reverse operation. Otherwise it MUST declare non-reversibility honestly.
- Remote execution MUST preserve target identity. Failure of the selected
  device or service MUST NOT silently move the action to another host.

## 5. Direct and intelligent execution

Direct execution is the default. An executor MAY use internal intelligence only
when its goal is narrow, its action space is bounded, progress can be observed,
and its postcondition can be verified.

An intelligent executor:

- MUST keep exactly the same public input and output contract as a direct
  implementation of the capability;
- MUST run deterministic resolvers before a model fallback;
- MUST constrain the model to actions enumerated by the executor;
- MUST enforce finite attempts, elapsed time, observation size, and history;
- MUST treat page, document, provider, and model text as untrusted data;
- MUST re-observe after action and fail honestly when the postcondition is not
  met;
- MUST produce an explicit handoff when progress requires new human information
  or authority.

Conformance constrains mandate, authority, budgets, allowed effects, and the
shape and verification of the result. It MUST NOT prescribe one fixed internal
path when several safe paths can satisfy the same postcondition. Deterministic
resolvers establish known facts and guardrails; bounded intelligence retains
freedom to select and repair the path inside them.

The model may choose a path inside the mandate. It may not change the mandate,
invent a new capability, or report an unverified success.

## 6. Transport independence

Local process, signed remote invocation, and MCP are transport adapters below
the executor contract. The planner MUST NOT receive transport-specific control
arguments such as an MCP server name, raw tool name, arbitrary JSON-RPC payload,
device queue identifier, or subprocess command.

An MCP tool can therefore be admitted only as:

1. a backend of an existing conforming executor;
2. a narrow conforming proxy executor;
3. a quarantined capability not exposed to the planner.

Changing transport MUST NOT change purpose, input semantics, authority, error
meaning, or success criteria.

### 6.1 Central execution policy

Execution scheduling is a runtime authority, not executor-local behavior. A
manifest MAY declare an `[execution]` table with the closed fields
`effect`, `parallelism_class`, `resource_class`, `concurrency_key`, and
`equivalence_gate`. Absence of the table has exactly these semantics:

```toml
[execution]
effect           = "unknown"
parallelism_class = 0
resource_class   = "default"
concurrency_key  = "none"
equivalence_gate = "unverified"
```

`parallelism_class > 0` is an opt-in claim, never an inference from an
executor name, verb, capability, origin, or read-only appearance. Classes are
`0` (caller thread only), `1` (moderate), `2` (high), and `3` (maximum bounded
by detected hardware and runtime ceilings). A positive class requires a known,
non-interactive effect, `equivalence_gate = "verified"`,
at least one hermetic birth test with `equivalence_runs = 2..8`, signed manifest
review, and behavioral evidence from the admission gates. The class is a
portable resource request, not a literal thread count; only the central runtime
maps it to a hardware-aware budget. The declaration itself is not proof. The
runtime MUST retain a global kill switch and MUST degrade an unknown,
incomplete, or invalid policy to class `0`.

`read_only` is the simplest admissible effect, not a permanent restriction.
The closed effect vocabulary also includes `create_only`, `reversible`, and
`mutating`. Any positive-class executor with an effect other than `read_only`
MUST declare a non-`none` `concurrency_key`; the runtime MUST resolve that key
from trusted invocation context and serialize calls sharing the same identity.
Missing identity degrades that invocation to the caller-thread path. Concurrent
mutation admission additionally requires hermetic setup/teardown, idempotency
or collision evidence appropriate to the domain, and verified postconditions.

A `candidate` or `synthesized` executor MUST remain serial and unverified. An
LLM may propose resource characteristics and equivalence cases, but it MUST NOT
write or promote the scheduling authority fields. Promotion tooling renders
those fields from a runtime-owned template only after independent gates pass.

The central scheduler MAY apply bounded queues, resource limits, timing
metrics, and backpressure to every invocation. It MUST NOT change arguments,
result shape, planner order, source coverage, retries, placement, authority,
undo, or success criteria. Interactive and dependency-linked steps remain
serial. Mutating steps may enter the pool only with the effect-specific
isolation and admission evidence defined above.

An executor MUST NOT create a competing top-level thread pool to obtain
cross-executor concurrency. Bounded internal concurrency over homogeneous
items remains permitted when it is part of the executor implementation and its
public semantics, budgets, provenance, and failure accounting are preserved.

## 7. Internationalization and interaction

- Planner-visible descriptions and argument descriptions MUST use language
  maps, never flat language-specific strings.
- Shipped conforming executors MUST provide Italian and English. Runtime-created
  candidates MAY carry a source language while quarantined, but MUST complete
  the normal translation gate before activation.
- Identifiers, enum values, `error_class`, and `error_code` are language-neutral.
- Every user-facing label, prompt, failure, warning, and handoff MUST be resolved
  through the i18n repository. Provider text MUST not be presented as a trusted
  localized explanation.
- The normal user experience MUST describe purpose, data, account, mandate,
  effect, target, and reversibility. Implementation protocols belong in a
  technical detail view, not in the natural-language command.

## 8. Lifecycle, provenance, and observability

- Code and manifest MUST be signed before activation. The digest, identity,
  schema, authority, tests, provenance, and lifecycle state form one reviewed
  unit.
- Product membership, origin, and transport MUST remain distinguishable in
  catalog and audit data. `builtin` is product membership; `handcrafted`,
  `synthesized`, and `imported` are origins; `in-process`, local subprocess,
  remote device, and MCP are transports. Implementations MAY retain a legacy
  combined field during migration, but normative prose and new metadata MUST
  not conflate these axes.
- A first-party provider bundle maintained by Metnos MUST be classified by its
  actual authorship. The GitHub bundle is builtin with handcrafted origin and
  MUST NOT carry `imported_from` provenance.
- Timeouts, cancellation, retries, and concurrency limits MUST be bounded.
  Retrying a mutating action requires idempotency evidence or an explicit
  recovery protocol.
- Audit records MUST preserve the causal outcome and target without storing raw
  secrets. A transport-level response alone is not proof of domain success.
- A catalog or schema change MUST invalidate affected caches and require normal
  admission; dynamic discovery never grants automatic planner visibility.

## 9. Required gates

A conforming executor MUST pass all applicable gates below:

1. canonical naming validation;
2. manifest parse, strict structural lint, schema checks, and standard-version
   validation;
3. signature and code-digest verification;
4. sandbox, capability, credentials, placement, and platform validation;
5. unit tests for success, empty result, invalid input, dependency failure, and
   partial/vector behavior where applicable;
6. mutation tests for postcondition, repeat behavior, interruption, and reverse
   operation where applicable;
7. intelligent-executor tests for proposal rejection, budget exhaustion,
   untrusted observation, handoff, and failed postcondition where applicable;
8. remote or broker equivalence tests where a non-local transport is used;
9. IT/EN prompt and message checks;
10. orchestration tests using materially different natural paraphrases, not
    only the manifest wording or a canonical CLI-like phrase;
11. relevant regression suite and at least one end-to-end scenario.

Mechanically checkable rules belong in `runtime/executor_standard.py`,
`runtime/manifest_lint.py`, the loader, templates, and admission. Semantic
claims that code cannot prove MUST be backed by tests and review; they MUST NOT
be downgraded to a boolean manifest assertion.

Before signing or re-signing a conforming executor, run:

```bash
python3 runtime/executor_standard.py executors/<name>/manifest.toml
python3 runtime/test_runner.py executors/<name>/manifest.toml
```

For migration planning only, the full legacy inventory is produced with:

```bash
python3 runtime/executor_standard.py --report-legacy executors
```

The report is diagnostic. It does not upgrade an executor and its findings MUST
NOT be fixed mechanically without checking the public behavior and applicable
domain tests.

## 10. Change discipline

Changes to this standard require a versioned architectural decision, updates to
the validator and authoring templates, and an impact report over both conforming
and legacy executors. A stricter check starts in report mode for legacy entries
and blocking mode for new or explicitly conforming entries.

The standard is the authority. Domain specifications may add constraints, but
they MUST NOT weaken or contradict it.
