---
id: 0212
title: Signed, typed providers for privileged read-only data
date: 2026-08-20
status: accepted
area: security | remote | executor
related:
  - 0210
  - 0211
modifies:
  - 0211
---

## Context

An installed program is not necessarily a service, and a running GUI is not
necessarily a data interface. LibreHardwareMonitor 0.9.6 illustrates the
distinction: its library can read sensors through PawnIO, while its process
does not expose the WMI interface previously assumed by `get_processes`.
The ordinary remote executor is intentionally unprivileged and therefore
cannot open PawnIO directly.

Letting invocation arguments send a DLL path, script, type, method, or argument
list to the LocalSystem helper would be arbitrary privileged execution. Starting a GUI
and hoping that data appears would be unreliable and would mix process
lifetime with data acquisition.

## Decision

A signed executor manifest may declare a managed dependency in `provider`
mode. The declaration contains only an abstract key, an exact registered
package identity, one closed provider interface, the paired executor selector
arguments that request a bounded slice, and a declarative implementation profile. The current
profile contains only a direct-child assembly file name and its entry type.
Both are author-signed manifest data, never planner or user arguments.

When both selectors contain a non-empty closed selection, the server verifies
the author signature and current code digest, derives a one-invocation grant
that binds the canonical domains and measurement types, and signs it with the
installation author key. The remote client accepts
only an exact match between the verified invocation, the verified manifest,
and that grant.

The privileged helper verifies both the paired-client signature and the
server grant. It then runs one standard interface host. No package identity
selects a code branch. The profile cannot provide a path, command, script,
method, property, or free argument: the helper owns all interface operations.
Package registration is read from machine-owned metadata and the named
assembly must be a canonical direct child of that exact registered root.
Archive members installed by the package manager may retain Windows'
download provenance marker.  After the root and direct child have been
validated, the provider host may use the framework's local marked-assembly
loader; it does not remove the marker or modify the installed package.
Rust's Windows canonicalizer returns the extended-length spelling used for
the containment proof.  That canonical value remains authoritative; only
after the proof may the host respell a local drive path for an older managed
runtime that cannot consume the extended-length prefix.  Network, device and
relative paths are not eligible for that handoff.
The hardware-sensor interface enables only the domains and measurement types
requested by its consumer. A CPU-temperature read does not initialise GPU or
storage discovery, and provider initialisation is attempted once rather than
retried implicitly. Its host drains bounded stdout and diagnostics concurrently;
a timeout reports the last completed provider stage without accepting partial data.

Provider output is size-bounded, schema-checked, sorted deterministically, and
passed to the executor through one bounded environment value. The executor
names only its abstract dependency key. A failed provider read may fall back
to ordinary unprivileged probes, but it never causes a process start, retry
loop, installation, or hidden mutation.

The ordinary installation UI remains the only package installation path. The
person selects a package identity there and never writes a manifest or an
adapter. Installation and generic process start do not require a provider
profile at all. When Metnos must also read application data, a package or
consumer maintainer distributes the small signed compatibility profile as
data. A new package that implements an existing standard provider interface
therefore requires no new Metnos code. Software without a supported
machine-readable interface is reported as incompatible rather than guessed
from its GUI.

## Consequences

Installation, process activation, and data access remain three separate
contracts. Existing process-mode dependencies keep the explicit
session/persistent choice from ADR 0211. Provider-mode dependencies are lazy,
read-only, and transparent when successful.

Adding another implementation of an existing interface is data-only. A truly
new semantic interface requires one reviewed standard interface variant, not
an adapter per product. It does not require branches in the runtime or
executor, and it does not add a public Metnos verb. User-visible failure text
uses the i18n catalogue and names the provider contract rather than one product.

## Alternatives considered

**Run the installed application first.** Rejected because process presence
does not prove a usable data channel.

**Loosen the PawnIO device ACL.** Rejected because every process on the device
would gain direct hardware-driver access.

**Let the executor load the library.** Rejected because the executor is
deliberately unprivileged and sandboxed.

**Accept a reflection recipe.** Rejected because selectable methods and
properties would be privileged code by another name. The profile may select
an implementation, while the helper keeps the operations fixed.

**Build one temperature-specific runtime branch.** Rejected because future
read-only providers need the same signed acquisition boundary.
