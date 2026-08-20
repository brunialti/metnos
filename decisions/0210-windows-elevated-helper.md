---
id: 0210
title: The Windows elevated helper — channel, vocabulary, consent, removal
date: 2026-08-17
status: accepted
area: security | remote | executor
related:
  - 0011
  - 0046
  - 0070
  - 0183
  - 0184
  - 0193
  - 0209
complements:
  - 0209
modified_by:
  - 0211
---

## Context

> **Amended by ADR 0211.** The package-operation enumeration remains closed.
> A separate signed request shape can start one registered package without
> accepting a command, path, or caller-controlled argument.

ADR 0209 (D5) decided that installing software on Windows requires an
elevated helper, and stated its non-negotiable properties. It deliberately
stopped there: the channel, the operation vocabulary, the installation flow
and the removal flow were left to a dedicated ADR, because this is the most
privileged software Metnos will ever place on someone else's machine, and an
error here is not a defect — it is a local privilege escalation.

Roberto approved the helper on 2026-08-17. This ADR fixes the four open
points, and is the precondition the implementation spec
(`internal/design/spec_install_packages.md` §7.1) requires before any code.

### The starting constraint, unchanged

The Windows client runs as a Scheduled Task «At logon» with
`RunLevel: Limited`, by choice: installing Metnos on a PC must not require an
administrator. That choice stays. The helper is a **second component**,
installed separately and with its own explicit consent.

### Why the Linux twin is not a template

`runtime/system/sudoer.py` (ADR 0070) is the executive component that holds
privilege and takes no decisions. Its interface is an **argv**, validated by
`admin` and re-validated by the sudoer at fire time against the deterministic
safety tools.

That interface is sound where it runs: the owner's own server, against the
owner's own blacklist, with a human in the loop for the delayed chains. It is
**not** sound as a template for the helper. The helper runs on a machine that
is not the deciding party's, under an unattended session, and a component
holding `LocalSystem` that accepts an argv from a user process is a privilege
escalation with a friendly interface — the exact shape ADR 0209 D5 ruled out.

The helper is therefore the sudoer's counterpart **in role** (it executes and
does not decide) and strictly narrower **in interface**. Where sudoer
re-validates a command, the helper never receives one.

## Decision

### D1 — A closed vocabulary of operations, never a command

The helper exposes exactly three operations:

| Operation | Arguments | Meaning |
|---|---|---|
| `query` | `source`, `package_id` | is this package installed, and at what version |
| `install` | `source`, `package_id`, `version?` | install that package |
| `uninstall` | `source`, `package_id` | remove that package |

`source` is a **closed enumeration** (`winget` at first delivery).
`package_id` is matched against a strict charset before anything else; a
value that does not match is refused without being passed anywhere.

DEVI: costruire la riga di comando **dentro** l'aiutante, dai soli argomenti
tipizzati dell'operazione.
NON DEVI: accettare dal chiamante una riga di comando, un argv, opzioni
libere o un percorso di file.
OK: `install(source="winget", package_id="Foo.Bar")`.
ERRORE: `install(command="winget install --override ...")`.

The rule is not cosmetic. Package managers expose flags whose whole purpose
is to forward arbitrary arguments to the underlying installer, or to write
outside the managed location. Accepting caller-supplied flags — even one —
reopens arbitrary elevated execution through a door labelled «install». The
helper builds its own argv from an allowlist and forwards nothing.

### D2 — An authenticated local channel, and the squatting defence

The channel is a **named pipe** with an ACL bound to the owning user's SID.
Never a TCP port, on any interface, including loopback.

Two verifications, in both directions, because a pipe name is not a secret:

1. **Helper → client.** The helper obtains the client's process id from the
   connection, resolves its token, and refuses any caller whose SID is not
   the consented owner. Membership in a group is not accepted in place of the
   SID.
2. **Client → helper.** The helper creates the pipe with
   `FILE_FLAG_FIRST_PIPE_INSTANCE`, and the client resolves the pipe server's
   process and verifies it is the signed helper running as `LocalSystem`
   before writing anything into it.

Without (2), any unprivileged process that wins the race can create the pipe
first and collect whatever the client sends — a known and cheap attack. The
helper is not the only party that must authenticate.

### D3 — Re-validation at fire time, and no replay

The helper does not trust that the request «was already approved». At the
moment of execution it verifies, in order:

1. the request carries a valid signature from the paired installation;
2. the calling SID is the consented owner (D2.1);
3. `source` is in the enumeration and `package_id` matches the charset;
4. the idempotency key has not already been consumed.

Point 4 is not bureaucracy: without it a captured request can be replayed,
and «install» is not idempotent in its effects on a machine. The consumed
keys are kept by the helper, not by the client.

### D4 — Consent once, explicit, and not smuggled in

The helper is installed by its own signed installer, which raises UAC **once**
and states in plain words what the machine is granting and to whom. The
consent is recorded by the helper.

NON DEVI: installare l'aiutante durante l'installazione del client, o come
effetto collaterale di una richiesta d'installazione.
The initial installation never happens as a side effect and still requires
the explicit Windows elevation described above. Later builds from the same
paired installation may update without another prompt, but only from the
server-signed helper release, with the pinned server key, component, target,
version and artifact hash all revalidated by the helper itself.

Update discovery is lazy and always on: before any helper operation the client
performs a local version handshake. Network and download work occurs only when
the client is newer. The helper updates and restarts before the real operation
is sent, so no mutating request is retried. An unused, aligned helper does no
periodic network polling.

### D5 — Its own audit

The helper keeps its own append-only audit, separate from the client's, and
writes it as `LocalSystem` to a location the owner can read and the client
cannot rewrite. Every operation is recorded with its outcome, including the
refusals — a refusal that leaves no trace is indistinguishable from an attack
that was never noticed.

### D6 — Removal without Metnos's cooperation

The helper registers a standard entry under «Installed apps» and uninstalls
from there, with no involvement of Metnos, the client, or a network. Removal
stops the service, deletes the pipe and revokes the pairing key.

Removing the helper must not damage the client: install requests then fail
with the declared honest message (spec §7.4) — what is missing, and how to
install it — never with a generic error, and never with an attempt to elevate
in the background.

### D7 — The helper serves one owner

The consent recorded at D4 binds the helper to one owner SID. A second user
on the same machine does not inherit it; they consent separately or they do
not install.

This keeps the permission rule of ADR 0209 D4, as amended on 2026-08-17,
true at the level that actually holds the privilege rather than only in the
planner: nobody installs on a device they do not own, the instance
administrator included. The helper enforces that at its own boundary — it
serves the SID that consented, and an instance role means nothing to it. Two
independent refusals, at the planner and at the privilege, for one rule.

### D8 — Linux gains no new privileged component

`install_packages` covers Linux and Windows (ADR 0209). On Linux, a
system-wide installation goes through the existing `admin` → `sudoer` chain
(ADR 0070), which already holds privilege on the server under the owner's own
safety store. No second privileged component is introduced there, and the
first delivery on Linux stays per-user (spec §9).

## Alternatives considered

**Port the sudoer interface (argv + re-validation).** Rejected: see Context.
The re-validation that makes it sound on the owner's server does not transfer
to an unattended machine, and the interface itself is the escalation.

**A UAC prompt per operation.** Rejected in ADR 0209 D5, and reaffirmed: on a
non-interactive session the prompt never appears and the turn hangs. Retained
only as the declared fallback when the helper is absent.

**Elevate the client.** Rejected: the process that receives executors from
the network would become administrator. It is the fastest solution and the
worst one.

**Let the client create a scheduled task with `RunLevel: Highest`.** Rejected,
and named explicitly because it is the tempting shortcut that looks like it
avoids a new component: the client would control the task's action, which is
arbitrary elevated argv under another name. It is elevating the client,
written differently.

## Consequences

- Part D of `internal/design/spec_install_packages.md` is unblocked, under
  the four points fixed here.
- The helper is Windows-only, and Windows-specific by construction: the pipe,
  the SID and the ARP entry have no meaning elsewhere.
- `install_packages` must state, in its manifest and in its failure, that
  installing outside the user's scope on Windows requires the helper — and
  what to do when it is absent (spec §7.4).
- The public documentation and the Tutor gain a component that a user can be
  asked to install: both must describe what it grants, and how to remove it,
  before the capability is announced (§9.1).
