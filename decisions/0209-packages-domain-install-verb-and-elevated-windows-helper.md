---
id: 0209
title: Packages domain — the `install` verb, and an elevated helper on Windows
date: 2026-08-16
status: accepted
area: naming | executor | security | remote
related:
  - 0011
  - 0046
  - 0070
  - 0071
  - 0183
  - 0193
  - 0196
complements:
  - 0045
---

## Context

Metnos could not install software. The request «installa LibreHardwareMonitor
su pc-roberto» reached the planner, found no executor able to perform it, and
was refused — correctly, and since 2026-08-16 with a message that says so
plainly (`capability_missing`).

The feasibility study
(`internal/design/analysis_install_software_windows_16_8_2026.md`, same day)
established four things, all verified rather than assumed:

- The remote-executor transport is not the obstacle. Phase 7 is closed, the
  Rust client on `pcroberto` runs 0.2.25 and already executes **mutating**
  executors with device-aware undo (ADR 0183).
- The obstacle is privilege. The client's Scheduled Task runs with
  `RunLevel: Limited` under the logged-in user — a deliberate choice defended
  on 2026-07-03, because installing Metnos on a PC must not require an
  administrator. A non-elevated process cannot install machine-scope software.
- The architectural answer already exists in-house for Linux: ADR 0070's
  `admin → sudoer` split, where the deliberating component holds no privilege
  and the privileged component makes no decisions.
- `winget` is present (v1.29.280 verified) and declares the installer URL and
  its SHA-256 **before** installing, which is exactly what an approval card
  needs in order to show the user what they are authorizing.

The study left four decisions to Roberto. They were taken on 2026-08-16 and
this ADR records them.

## Decision

### D1 — The `packages` domain gains a verb, and the vocabulary gains `install`

`packages` is already a member of `OBJECTS`. It gets a second executor,
`install_packages`, and the closed `ACTIONS` vocabulary is extended with
**`install`**.

The §2.2 governance requires three criteria to hold together for a new token.
They are argued, not asserted:

- **Necessary.** The alternatives were weighed in the study and rejected by
  Roberto. `create_packages` would have used only existing tokens, but
  `create` means «bring a container or a persistent derivative into
  existence»; installing software modifies the registry, services, file
  associations and PATH of a machine. Calling that `create` would blur the
  boundary that makes `create` predictable elsewhere. Routing it through
  `admin` would have kept the vocabulary closed at the price of an
  `argv`-shaped contract, which is precisely the contract this operation must
  not have (D2).
- **General.** Installing software is not a Windows notion, nor a winget one.
  The same verb covers `apt`/`dnf` on Linux and any future package source; the
  provider is a backend detail, not a name.
- **Understandable by the average local model without explanation.**
  «install» is among the least ambiguous verbs in the technical lexicon of
  both project languages.

Vocabulary work follows the procedure written at the top of `runtime/vocab.py`:
append to `ACTIONS`, add the category, add the bilingual `ACTION_MAPPING`
entry with its semantic boundary, and classify the verb.

**Classification**: `install` joins `DESTRUCTIVE_VERBS` and
`COVERAGE_REQUIRED_VERBS`; it is **not** a producer. The consequence is
deliberate: an explicit install request must be carried to completion (§4.3),
and the honesty guards that already refuse to declare an unperformed mutation
apply to it unchanged.

### D2 — One executor, two directions

`install_packages` performs both directions through an explicit mode
parameter (`uninstall`), rather than through a separate `delete_packages`.

The reason is D3: uninstalling is not the inverse of installing, and giving
the two directions separate executor names would invite the runtime — and the
user — to treat them as a symmetric pair. One executor with a declared
direction keeps the asymmetry visible at the call site.

The argument contract is **vectorial by construction** (§2.1): a list of
package identifiers in, a list of results out. It is **not** an `argv`: the
identifier is resolved against the package source's catalogue, and the
approval card shows the URL and SHA-256 that the source declares for it.

`find_packages` is extended in the same domain. Today it is a `shutil.which`
PATH lookup, singular, and therefore answers a narrower question than its
name suggests. It becomes «is this component, program or application
installed on this machine?» on both Linux and Windows — consulting the
package database, not only the PATH — and vectorial like every other executor
(§2.1). Per §7.1 the current signature is replaced, not shimmed.

### D3 — Installing is not undoable, and Metnos must say so

`install_packages` declares `revertible = false`. There is no reverse pattern
and none will be invented: an installer modifies the environment
irreversibly, and a package that was already present in another version makes
the «inverse» a downgrade rather than a removal.

When a user asks to undo a turn that installed something, the answer is not a
silent refusal and not an automatic uninstall. Metnos states that this is not
an undo in the proper sense — the environment was irreversibly modified — and
requires the user to ask for the uninstallation **explicitly**. That request
is collected through a form (the existing runtime dialog), so the explicit
ask is one interaction rather than a new sentence the user must compose.

This is §2.8 applied to a case where the honest answer is uncomfortable: an
undo that silently uninstalls would be a false claim of reversibility.

### D4 — Who may install what, and where

Two rules, and they are about identity, not about packages:

- **On a user's own devices**: that user may install any package. The device
  belongs to them; the blast radius is theirs. **Only** on their own devices —
  see the amendment below.
- **On the server**: only an administrator of the instance. The server hosts
  every user's data and every service; an installation there is an
  instance-wide act.

The rule is enforced where authority already lives — the invocation
choke-point and the consent gate — not inside the executor, which must not be
the place where permission is decided.

#### Amendment (2026-08-17, Roberto) — administrator does not reach other people's machines

The original wording said what a user may do on their own devices and what
the server requires. It did not say what an administrator may do on **someone
else's** device, and silence there reads, by the usual convention, as «an
administrator may do anything». That is not the rule.

- **Nobody installs on a device they do not own — the administrator
  included.** Being an administrator of the instance is authority over the
  *instance*, not over the personal machine of another person. A package
  installed on someone's PC changes their machine, and the administrator role
  was never a grant of that.
- **On the server, only an administrator**, unchanged.

The two rules are the same principle read twice: authority follows what the
act actually touches. The server is shared, so it takes the shared role; a
personal device is not shared, so no role reaches it from outside.

Verified rather than assumed: the device candidate list is already filtered by
owner before placement (`agent_runtime` → `devices.owner_id_for_actor`), with
no administrator branch, and a request naming a device outside that list
raises `PlacementError` instead of falling back to the server. Rule one is
therefore structural today, and the implementation must not weaken it. Rule
two — the server — has no check yet and must be built (spec §6.4).

### D5 — Elevation: the helper, directly

The per-user-scope phase (`winget --scope user`, no new component) is
**skipped**. Its coverage is partial and unpredictable in a way the user
cannot understand, and «this package yes, that one no» is a promise that
breaks in use.

Metnos will install an elevated helper on Windows: a component installed
**once**, with an explicit consent prompt, that the non-elevated client asks
to perform operations. It is the Windows counterpart of `sudoer`.

Its non-negotiable properties, in the order that matters:

1. **A closed vocabulary of operations**, never a command string. The helper
   accepts «install this package id», not «run this». A `LocalSystem` service
   that executes arbitrary argv from a user process is a local privilege
   escalation with a friendly interface.
2. **An authenticated local channel**: a named pipe with an ACL bound to the
   owning user's SID. Not a TCP port, on any interface.
3. **Caller verification**: the helper checks the signature of what it is
   asked to do, as `sudoer` re-validates at fire time rather than trusting the
   decision it was handed.
4. **Its own audit**, separate from the client's.
5. **Clean removal**: uninstalling the helper removes the privilege, and the
   user must be able to do it without Metnos's cooperation.

The helper is a component of the product, not a script: it is the most
privileged software Metnos will ever place on someone else's machine, and it
is designed and reviewed as such.

## Alternatives considered

**`create_packages`, no new token.** The cheapest path, and the study's own
recommendation. Rejected by Roberto: the verb would have to carry a meaning
`create` does not have, and the vocabulary's value is that each token means
one thing. Adding a token is the honest cost of a genuinely new operation.

**Routing through `admin`.** `admin` is already the gateway for privileged
system operations and would have required no vocabulary change. Rejected
because `admin`'s contract is a proposed `argv` validated by an LLM call and a
canonical signature — deliberately general, and therefore deliberately
unable to constrain the operation to a catalogue entry with a declared hash.
`admin` stays what it is; installation gets a narrow contract instead.

**A separate `delete_packages` for uninstallation.** Symmetric and natural to
read. Rejected under D3: the symmetry is false, and the naming would enshrine
it.

**Per-user installs first (study's Phase 1).** Rejected under D5.

**UAC prompt per operation.** Requires a person at the screen at that moment;
on a non-interactive session the prompt never appears. Retained only as a
declared fallback when the helper is absent, never as the mechanism.

## Consequences

The `packages` domain becomes the third mutating domain reachable on a remote
device, after files and dirs. The verb count in the closed vocabulary goes
from 26 to 27, and every consumer of `ACTIONS` — naming grammar, GBNF, synt
stage 1, intent extractor, prefilter, proposer — sees the new token by
construction, because they all read `runtime/vocab.py`.

Two prices are paid knowingly:

- `find_packages` changes signature. Nothing in the catalogue consumes it
  today, so the cost is a re-sign and a manifest rewrite.
- The bilingual `ACTION_MAPPING` entry for `install` will be, like every other
  entry, `it`+`en` only. That is the debt measured the same day in
  `internal/design/analysis_regex_i18n_16_8_2026.md`; this ADR adds one more
  row to it rather than fixing it, and says so.

Work items, in order: vocabulary extension and its guards; `find_packages`
rewritten and extended to the package database on both platforms;
`install_packages` with its approval card, its permission rule and its
non-undo contract; the elevated Windows helper, which needs its own ADR for
the channel, the operation vocabulary and the installation flow.

The last item is not a matter of days, and this ADR does not pretend
otherwise.
