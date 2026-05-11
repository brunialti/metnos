---
id: 0071
title: Safety signatures — deterministic tools, SQLite schema, and v1 seed bootstrap
date: 2026-05-02
status: accepted
area: executor, runtime, security, naming
related:
  - 0002  # naming convention stringente
  - 0026  # wise tier quality floor
  - 0045  # closed naming vocabulary
  - 0058  # intent extractor LLM-based
  - 0067  # introvertiva MVP — events.turn_id refactor
  - 0069  # builtin + verb-unique fourth category
  - 0070  # admin → sudoer chain
complements:
  - 0070
modifies:
  - 0045  # adds the new object 'signatures' to OBJECTS in vocab.py
---

## Context

ADR 0070 specifies the chain `admin → (scheduler?) → sudoer` and refers to
«deterministic safety tools» that classify a candidate command as
forbidden, blacklisted, whitelisted, or unknown. This ADR defines those
tools concretely: their interfaces, their persistence, their seed
bootstrap, and the small extension to the closed vocabulary that makes
them first-class executors in the pool.

The design constraints, from the project memory
`metnos_shell_exec_analysis.md`:

1. The safety logic must live **outside** `admin` and `sudoer`, in
   reusable executors. The two builtin + verb-unique primitives are
   orchestrators, not containers of safety code.
2. The tools must be **deterministic** (no LLM, no heuristic). A
   signature in the blacklist is in the blacklist.
3. The persistence must be **idempotent under upgrade**: a new release
   ships an updated seed file, but existing user-curated entries are
   preserved.
4. The tools must be **invocable directly by the user** (e.g.
   «show me the blacklist», «add this signature»). They are visible to
   the PLANNER as ordinary handcrafted executors with verbs in the closed
   vocabulary.
5. A **fast LLM sanity check** is permitted but only as a second opinion
   on top of the deterministic tools, never replacing them.
6. The seed must be **rich enough** to spare the user from approving
   hundreds of innocuous commands during the first weeks of operation.

## Decision

### Vocabulary extension: new object `signatures`

`runtime/vocab.py::OBJECTS` is extended with one new plural object,
**`signatures`**, bringing the total from 11 to 12. The existing
specialised object `images` is unchanged. New qualifiers are added to
`QUALIFIERS`: `_blacklist`, `_whitelist`, `_graylist`, `_forbidden`,
`_seed`, `_sanity`, `_command`. All other qualifiers remain.

### Ten executors (handcrafted, in the regular pool)

All ten are written by hand in `/opt/myclaw/executors/<name>/`, signed
Ed25519, sandboxed via the standard `bwrap` profile derived from the
manifest. They are visible to the PLANNER and can be invoked by the
user directly.

| Executor | Action | Inputs | Outputs |
|---|---|---|---|
| `compute_signatures_command` | canonicalise an argv into a signature | `argv: list[str]` | `{signature, binary, target_kind, flags_canonical}` |
| `find_signatures_blacklist` | check signature against blacklist | `signature: str` | `{negate: bool, reason: str\|None}` |
| `find_signatures_whitelist` | check signature against whitelist/graylist | `signature: str` | `{allow: bool, age_class, uses: int, last_used_at}` |
| `find_signatures_forbidden` | check argv against forbidden paths | `argv: list[str]` | `{negate: bool, matched_path: str\|None}` |
| `find_signatures_graylist` | list graylist entries for review | (none) | `entries: list[dict]` |
| `find_signatures_seed_diff` | diff seed file vs DB | (none) | `entries: list[dict]` (added/modified/removed) |
| `find_signatures_promotion_candidates` | list graylist entries above promotion threshold | (none) | `entries: list[dict]` |
| `compute_signatures_seed_apply` | apply seed bootstrap idempotently | (none) | `{applied: int, skipped: int, seed_version: int}` |
| `write_signatures_blacklist` | add a blacklist entry | `signature: str, reason: str, severity: str` | `{ok: bool}` |
| `write_signatures_whitelist` | add a whitelist entry (manual) | `signature: str, reason: str` | `{ok: bool}` |
| `delete_signatures_blacklist` | remove a blacklist entry | `signature: str` | `{ok: bool}` |
| `delete_signatures_whitelist` | remove a whitelist/graylist entry | `signature: str` | `{ok: bool}` |
| `compute_signatures_sanity_check` | fast-LLM «smell» evaluation | `intent_text, argv, age_min, system_state` | `{smell: 'ok'\|'suspicious'\|'urgent_review', reason}` |
| `compute_signatures_reversibility` | classify reversibility of a command | `signature, argv` | `{class: 'reversible'\|'irreversible'\|'unknown', undo_hint}` |

(That is fourteen entries in the pool, of which thirteen deterministic
and one &mdash; `compute_signatures_sanity_check` &mdash; an LLM-fast wrapper.
The category line is drawn explicitly: thirteen are deterministic
guardians, one is a probabilistic adviser. Only the thirteen
deterministic ones are part of the ordering in `admin` and the
re-validation in `sudoer`; the sanity check is invoked only by `sudoer`
at fire time and only under the heuristic of ADR 0070.)

### Signature format

A signature is a colon-separated string with three parts:

```
binary:subcommand_or_flag_kind:target_kind
```

| Part | Examples |
|---|---|
| `binary` | `systemctl`, `rm`, `apt`, `ls`, `journalctl`, `dd` |
| `subcommand_or_flag_kind` | `status`, `restart`, `rf` (rm flag), `install`, `*` (any) |
| `target_kind` | `*`, `fs:user`, `fs:system`, `unit`, `pkg`, `block_device` |

Examples:

```
ls:*:fs:user                    → whitelist (any subcommand on user fs)
systemctl:status:*              → whitelist (status of any unit)
systemctl:restart:*             → graylist (sudo, reversible, ask first)
rm:rf:fs:user                   → graylist (irreversible on user paths)
rm:rf:/                         → blacklist, severity=forbidden
dd:*:block_device               → blacklist, severity=forbidden
```

The `target_kind` taxonomy is fixed in `runtime/safety/canonicalize.py`
and includes: `*`, `fs:user`, `fs:system`, `fs:tmp`, `unit`, `pkg`,
`block_device`, `network_iface`, `process_pid`. New target kinds require
a small ADR addendum.

### SQLite schema

`runtime/safety/storage.py` owns two tables in
`~/.local/state/metnos/safety.db`:

```sql
CREATE TABLE safety_signatures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signature     TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL CHECK (kind IN
                  ('whitelist','blacklist','graylist','forbidden')),
    severity      TEXT CHECK (severity IN
                  ('forbidden','irreversible','dangerous','reversible')),
    source        TEXT NOT NULL CHECK (source IN
                  ('seed','user','auto-promoted')),
    uses          INTEGER NOT NULL DEFAULT 0,
    last_used_at  TEXT,
    weight        REAL NOT NULL DEFAULT 1.0,
    created_at    TEXT NOT NULL DEFAULT (
                      strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by    TEXT,
    reason        TEXT,
    seed_version  INTEGER
);

CREATE INDEX idx_safety_kind   ON safety_signatures(kind);
CREATE INDEX idx_safety_source ON safety_signatures(source);
CREATE INDEX idx_safety_used   ON safety_signatures(last_used_at);

CREATE TABLE safety_meta (
    seed_version  INTEGER PRIMARY KEY,
    applied_at    TEXT NOT NULL DEFAULT (
                      strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    applied_count INTEGER,
    skipped_count INTEGER
);
```

Three fields deserve attention.

- **`source`** distinguishes seeded entries (`seed`) from user-curated
  ones (`user`) and from Synt-promoted ones (`auto-promoted`). The
  bootstrap never overwrites `source='user'`.
- **`severity`** distinguishes truly non-negotiable entries
  (`forbidden`, Law 1, immutable across releases) from user-derogable
  blacklist entries.
- **`seed_version`** is set on entries originating from the seed file.
  Bootstrap upgrades compare this against the file version.

### Seed file `runtime/safety_seeds/v1.toml`

Versioned with the codebase, ~200 whitelist + ~40 blacklist entries.
Excerpt of structure:

```toml
version = 1

[[signatures]]
sig      = "ls:*:fs:user"
kind     = "whitelist"
severity = "reversible"
reason   = "list user-space files, no side effect"

[[signatures]]
sig      = "rm:rf:/"
kind     = "blacklist"
severity = "forbidden"
reason   = "Law 1: irrecoverable system state"
```

### Bootstrap algorithm

Run by `compute_signatures_seed_apply` and also by the `admin` builtin
on first boot:

```
1. Read seed file, parse version V_seed.
2. Query MAX(seed_version) from safety_meta = V_db.
3. If V_seed <= V_db: nothing to do, return {applied:0, skipped:0}.
4. For each entry in seed:
     - if entry.signature already exists with source='user': skip.
     - else: INSERT OR REPLACE with source='seed', seed_version=V_seed.
5. Write a row in safety_meta(V_seed, applied_count, skipped_count).
```

Idempotent across reruns of the same version. Respects user curation.
Auditable via `safety_meta`.

### Order of invocation in `admin` (recall from ADR 0070)

```
compute_signatures_command(argv)               → signature
find_signatures_forbidden(argv)                → STOP if negate (Law 1)
find_signatures_blacklist(signature)           → STOP if negate
find_signatures_whitelist(signature)           → if allow, silent OK
                                                  if miss, show card
compute_signatures_reversibility(sig, argv)    → tag for the card
```

### Re-validation in `sudoer` at fire time

```
find_signatures_forbidden(argv)                → STOP if negate
find_signatures_blacklist(signature)           → STOP if negate
[heuristic] activate sanity check?
  if yes: compute_signatures_sanity_check(...)  → STOP if urgent_review
[exec]
```

### Sanity check (LLM fast)

`compute_signatures_sanity_check` is the only non-deterministic tool. It
wraps a `tier=fast` LLM call (qwen3:8b, think=false) with a fixed prompt
that asks: «given the original intent, the canonical argv, the time
elapsed since planning, and a brief snapshot of system state, does this
execution still smell right?». Output is JSON-schema-guided with three
kinds: `ok`, `suspicious`, `urgent_review`.

It is invoked only when it adds value: chains delayed by ≥5 minutes, or
commands classified as `irreversible`/`unknown` reversibility.

It can never authorise what the deterministic tools have blocked. Its
authority is purely additive: it can add a block, never lift one.

## Alternatives considered

**(a) Embed safety logic inside `admin` and `sudoer` directly.** Rejected:
the safety logic is reusable (the user can directly query the blacklist),
testable in isolation, and audit-friendly when externalised. Embedding
would couple two privileged primitives to a body of policy that should
be a first-class concern.

**(b) Use a single LLM call to classify safety, no deterministic tools.**
Rejected (see ADR 0026, the wise-tier quality floor): safety in privileged
operations is exactly the domain where LLM non-determinism is least
acceptable. The fast LLM is allowed only as a second opinion.

**(c) Per-command full signature (no wildcards).** Rejected: the
whitelist would explode (one entry per `systemctl:status:<unit>`). The
chosen middle ground &mdash; wildcard at the safe end, explicit at the
dangerous end &mdash; covers most cases with few entries.

**(d) Empty seed at t0, build everything via user curation.** Rejected:
the user would face hundreds of approval cards in the first weeks. The
seed of ~200 whitelist + ~40 blacklist is the minimum to make the system
usable from day one.

## Consequences

**What this opens.** A clean, auditable, extensible safety layer for
shell orchestration. Each new privileged operation has a deterministic
gate, a place in the SQLite store, and an audit trail.

**What this closes.** The temptation to write «just one big function in
admin that does everything»: by the time it has six branches, it is
more readable as a chain of small executors anyway.

**Work items spawned.**
- `runtime/vocab.py`: add `signatures` to `OBJECTS`; add new qualifiers.
- `runtime/safety/canonicalize.py`, `storage.py`, `seed_bootstrap.py`,
  `sanity_check.py`, `secret_slot.py` (~700 LOC total).
- `runtime/safety_seeds/v1.toml` (~250 entries; ~2–3 hours of curated
  authorship).
- Fourteen executor manifests + Python files in
  `/opt/myclaw/executors/` (~800 LOC).
- A unit test suite covering canonicalisation, the bootstrap idempotency
  property, the source='user' preservation rule, the wildcard matching
  in find/whitelist queries.

**What becomes more expensive.** Releasing a new version with an updated
seed is now a two-step decision: bump `version` in the TOML, document
the diff. (`find_signatures_seed_diff` makes this trivial.)

**What becomes easier.** Adding a new safety rule, blacklisting a
command Roberto regrets approving, exporting the whole policy for
inspection &mdash; all become single-tool operations.
