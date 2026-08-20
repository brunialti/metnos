---
id: 0211
title: Starting a registered package without accepting a command
date: 2026-08-20
status: accepted
area: security | remote | executor
related:
  - 0209
  - 0210
modifies:
  - 0210
---

## Context

Some executor capabilities genuinely depend on a user-facing process that is
installed but not always running. Starting such a process is distinct from
reading a privileged provider interface. The latter is governed by ADR 0212
and does not imply that an application process must be started.

Accepting a path, command line, executable name, or caller-controlled
arguments would turn the LocalSystem helper into an arbitrary execution
service. ADR 0210 explicitly forbids that interface. The existing public
executor vocabulary already contains `create_processes`, so adding another
planner verb would also be unnecessary.

## Decision

### D1 — Keep the public and package-operation vocabularies unchanged

`create_processes` remains the only public verb. The helper's `Operation`
enumeration remains exactly `query`, `install`, `uninstall`, and `version`.

Managed start uses a separate request shape on the authenticated local
channel. It has no operation field and contains only:

- the closed package source (`winget` initially);
- one exact `package_id`;
- one closed lifetime (`session` or `persistent`);
- an idempotency key and signature.

The signature has its own fixed domain separator, so it cannot be replayed as
a package-management request. The helper applies the same owner, signature,
replay, audit, and single-request checks used by ADR 0210.

### D2 — Resolve identity from authoritative installation metadata

The caller never sends a path. The helper resolves the exact package identity
from machine-owned Windows registration data and accepts a target only when:

1. the package identifier matches exactly;
2. the installer type has a supported resolver;
3. the registration exposes one executable target, or its registered package
   directory contains exactly one executable;
4. the canonical target is a regular `.exe` inside the canonical registered
   installation directory.

The first resolver covers machine-scope WinGet portable packages through the
registered `WinGetPackageIdentifier`, `WinGetInstallerType`,
`InstallLocation`, and `PortableTargetFullPath` values. Archive-style portable
packages omit the last value; for those, the resolver accepts only the unique
`.exe` below the canonical registered installation directory. Missing,
unsupported, or ambiguous metadata is a refusal. There is no package-name
heuristic and no application-specific table.

Additional installer types may gain independent resolvers later, under the
same output contract. Unsupported software is reported honestly rather than
guessed.

### D3 — Two explicit lifetimes

- `session` starts the registered target now and leaves it running until it
  exits or the computer restarts.
- `persistent` records a helper-owned startup task for that resolved target
  and starts it now.

The caller cannot choose the task name, action, account, trigger, arguments,
or privilege level. A deterministic task identity is derived from the package
identifier. This is different from the rejected ADR 0210 alternative where
the client controlled a scheduled task's arbitrary action.

If the exact target is already running, session start is successful without a
duplicate. Persistent start still verifies or updates the fixed startup task,
then avoids a duplicate process.

### D4 — Consent and internationalisation live above the privileged boundary

The executor asks explicitly whether the program should run until restart or
at every startup. Labels describe that consequence, not the Windows
mechanism. All user-visible text comes from the i18n catalogue. The helper
returns stable language-neutral error codes and never composes UI prose.

Consumer executors declare package dependencies in data, not in launcher
logic. The launcher remains generic and contains no list of known programs.

## Alternatives considered

**Add `activate` or `run` to the helper operation enumeration.** Rejected.
The public verb already exists, and preserving the closed package-management
vocabulary makes accidental general execution easier to detect.

**Let the client send a path or scheduled-task action.** Rejected. It is the
arbitrary elevated command interface ADR 0210 prohibits.

**Guess the executable from its name.** Rejected. Archive layouts change and
application-specific names would become a launcher table. The archive fallback
uses no name: it succeeds only when the registered package root contains one
executable, and otherwise refuses.

**Start and stop the provider around each reading.** Rejected. Hardware
providers need time to initialise; repeated cycling adds latency and unstable
measurements. The user chooses the lifetime once.

## Consequences

- The helper protocol gains a second, narrower signed request shape but no
  new package operation and no command-like field.
- The first delivery supports registered machine-scope WinGet portable
  packages. Other installer types fail closed until a resolver exists.
- `create_processes` becomes a real two-phase executor with explicit consent.
- New code documentation and comments are English; every new user-facing
  message is provided through the i18n catalogue.
