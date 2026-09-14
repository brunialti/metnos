---
id: 0218
title: Canonical run verb, web-open boundary, and manifest-driven program resolution
date: 2026-08-23
status: accepted
area: vocabulary | routing | executors | i18n
related:
  - 0002
  - 0018
  - 0193
  - 0209
  - 0211
  - 0217
supersedes:
  - 0211-D1-public-naming
---

## Context

The natural requests `avvia Blocco note` and `start Notepad` were classified as
`open/sites`. The planner was not free to express the intended operation:
`run` did not exist in the closed action vocabulary, while `open` was the only
available start-like public action and means a persistent browser session.
The resulting web search was therefore a vocabulary defect, not a missing
application-name special case.

The existing `create_processes` name also described object creation rather
than the user's action. ADR 0211 correctly constrained the privileged helper
to registered package identities, but its claim that this was already the
canonical public verb was false. Preserving that name would keep the language
model and the product ontology misaligned.

## Decision

`run` is the canonical action for starting already-installed software and
producing a process. `run_processes` replaces `create_processes`; there is no
compatibility alias before 1.0. `open` remains exclusively the action for a
persistent browser session in the `sites` domain. `install` changes installed
software, and `admin` remains the separate privileged arbitrary-command
boundary. The privileged helper protocol and its registered-package-only
security contract do not change.

The canonical action identity and its editorial IT/EN seed live in
`ACTION_MAPPING`; operational surfaces live in the versioned detection
resource `vocab.action_surfaces`, and semantic boundaries in the versioned
i18n keys `VOCAB_ACTION_*_BOUNDARY`. Italian `avvia`/`avviare` and English
`run`/`start`/`launch` map to `run`; site-opening surfaces stay under `open`.
The prefilter and intent prompt read the active localized resources, with a
coverage report and catalogued fallback. There is no query, program, language,
or executor-name branch in the routing engine. This RM-0005 alignment was
implemented on 2026-08-23 without changing the canonical `run != open`
boundary decided here.

A user may supply a localized Unicode display name. `find_packages` validates
human software names by general character classes, queries the device package
source, and emits `resolved_id` only for an exact or unique match. It never
uses a product-name table. `run_processes` consumes that identity through the
manifest declarations `from_entries_key = "resolved_id"` and
`from_entries_complete = true`; incomplete or ambiguous vector projection
fails closed rather than starting a partial subset.

Composition is also declared. The signed `[planning].companions` relation
makes the resolver visible beside `run_processes`; the general
`[planning].object_aliases` declaration states that its input identity also
belongs to canonical `packages`, so either valid intent object ranks the same
executor. Managed dependency remediation selects the unique launcher through the signed
`managed-package-start` capability hint. Runtime code contains no launcher
executor name.

`run` is coverage-required and mutating because it changes process or startup
state. It is not the implicit mutating default for a bare mention of a process:
the user must express the action. Per-execution undo remains governed by ADR
0217: a newly created session process is reversible, reuse is no-effect, and
persistent startup is irreversible.

## Consequences

- Natural Italian and English requests can express program launch without
  colliding with web sessions.
- Any installed program supported by authoritative package metadata follows
  the same path; no application-specific mapping is introduced.
- Ambiguous display names remain readable search results but cannot flow into
  a mutating launch.
- Future launcher implementations may be renamed or replaced without changing
  managed-dependency runtime code, provided exactly one signed executor owns
  the role capability.
- The executor catalog, vocabulary documentation, tests, and signatures move
  atomically to `run_processes`.
- The privileged helper remains limited by ADR 0211 to registered portable
  packages. ADR 0221 adds a separate typed AppX family resolved and activated
  by the current-user Windows client, without an application-specific table.

## Rejected alternatives

A literal check for `avvia`, `Blocco note`, or `Notepad` was rejected as
language- and application-specific hardcoding. Mapping `avvia` to `open` with
an object exception was rejected because it leaves one token with two public
meanings and requires domain branches. Accepting a path, command line, script,
or guessed executable was rejected by the ADR 0210/0211 authority boundary.
Starting the shortest ambiguous name match was rejected because ranking is not
identity proof.

## Desktop closure and action coverage (2026-09-14)

`set_processes(state="closed")` closes registered desktop Win32 applications
through the same opaque identity source used for launch. It does not accept
executable names, paths, wildcards or arbitrary commands. AppX and portable
package closure are not provided by this executor.

The device first observes exact PID/creation-time identities and asks the user
to choose normal closure, forced termination (with an unsaved-data warning),
or cancellation. The runtime binds the choice to those identities and that
device. Normal closure never escalates to forced termination. A process left
running, including a tray application or save prompt, is not a successful
closure; success requires a fresh absence check. Closing an application is
not reliably undoable, even when it held no documents.

The terminal executor's localized `final_message_hint`, when accompanied by
a typed per-execution effect receipt, takes precedence over a pre-execution
answer template. An observed already-closed state must not become a claim
that the runtime closed the application. The shared effect counter treats
`_undo.outcome="no_effect"` as zero mutations even when the desired-state
check returned successful items. This rule is independent of application,
executor name and language; ordinary read-only templates are unchanged.

Required-action coverage checks both canonical verb and object, including
signed planning aliases. `set_preferences` cannot satisfy `set_processes`.
Single-action intents receive the same lexical repair as compound actions
when their original verb/object has no executable candidate; the localized
canonical vocabulary remains the sole source of action surfaces.

New first-party sources enter the normal changed-only release admission
through Producer/Birth, then a verified store reread. They need no invented
previous signature. Authoring materialization remains a closed-tree capture,
not admission; authenticated-current readers still require signed evidence.
The generated documentation explicitly describes source metadata, whereas
the live instance catalog shows only admitted contracts.
