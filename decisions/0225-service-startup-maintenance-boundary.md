---
id: 0225
title: Separate installed-service startup from certification
date: 2026-09-09
status: accepted
area: runtime | installation | maintenance
modifies:
  - 0224
---

Service-local verifier implemented and exercised in production;
HTTP/configuration/control changes tested, deployment tracked separately.
Modifies the whole-host startup coupling of ADR 0224, not executor admission.

## Context

An ordinary administrative Python security update changed historical executable
bytes. Comparing them with an installation-time attestation stopped all gated
services. An independent aggregate systemd observation mismatch compounded the
failure. A successful source review or CI run did not prove service availability.
The operator explicitly required normal maintenance and configuration to remain
possible without silently weakening executor admission or confinement.

## Decision

- `check`/`launch` for an installed gated service authenticate the required
  signed history and exact immutable file inventory, then its own executable,
  root-owned unit and declared systemd restrictions. They do not compare old
  OS-tool hashes or an aggregate observation of unrelated services, and do not
  require historical code to equal the updated verifier's compiled source root.
- Current OS tools remain root-trusted and measured for authentication. Service
  target identity, UID/GID, capabilities, protected paths and explicit unit policy
  are not optional. The actual managed Python is executed after privilege drop;
  ambient Python overrides and user site packages are excluded.
- `check-all` and administrative certification retain complete evidence and the
  current source-review requirement. Startup creates no new authority or receipt.
- The HTTP runtime may expose its existing authenticated maintenance handlers
  when Birth or prompt startup fails. Execution, publication and automatic work
  remain unavailable, and health states this explicitly. An unsafe core or broken
  authentication substrate is not made safe by this fallback.
- SMTP defaults are resolved per mail invocation, consistently with mounted
  credentials. Bad mail settings do not prevent opening administration.
- Service display and lifecycle commands use the same selected catalog scope.
  This does not broaden the operator's accepted commands.

## Alternatives considered

Reissuing the complete installation proof after every OS update would retain
the original coupling and prevent repair when that proof already fails. Removing
startup verification entirely would discard useful code and confinement checks.
Service-local authentication keeps those checks while treating installation
history as historical evidence, not a permanent freeze of the operating system.

## Limits and consequences

This is not a general bypass for modifying signed units, immutable releases or
executor code. Those updates still need a bounded authorized maintenance path.
The existing readiness quarantine relation and incomplete administrative update
commands need separate treatment; this decision does not claim they are fixed.
Boot prerequisites must be recreated by the OS before services start. A live
start and an enabled target are not a completed reboot test.

Static documentation publication is independent of the local signed Tutor
database. `deploy.sh --static-only` checks generated pages and the public
inventory before upload; it does not rebuild Tutor or load its signing key.
The default deployment still builds and verifies Tutor before upload. The
deployment root is the script's checkout, not a fixed installation directory.

Regression coverage includes service-local rejection of altered targets, unit
fragments, drop-ins and policy, selected-state reread, preserved full certification,
the managed interpreter, authenticated maintenance routes and per-invocation SMTP.
Recovery acceptance requires normal HTTP, a real Telegram daemon and a harmless
authenticated request that actually executes an admitted executor.
