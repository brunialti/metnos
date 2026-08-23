---
id: 0221
title: Typed Windows packaged-app activation in the owner's session
date: 2026-08-23
status: accepted
area: executors | windows client | undo
related:
  - 0209
  - 0210
  - 0211
  - 0217
modifies:
  - 0218
---

## Context

The canonical `run` boundary of ADR 0218 worked end to end, but the real
request to start Windows Notepad stopped after discovery. WinGet returned an
inventory identity shaped as `MSIX\<PackageFullName>`, while the elevated
helper accepts only registered portable-package identities. Passing the raw
inventory string to the helper would be wrong twice: it is not that protocol's
identity family, and a service running as SYSTEM cannot place a packaged app
in the owner's visible desktop session.

An application-name table, an executable-path fallback, or a shell command
would solve one demonstration while weakening the general boundary. Windows
already exposes authoritative package identity, application identity and
activation APIs, so the design had to preserve those types through discovery,
launch, verification and undo.

## Decision

`find_packages` may adapt a structurally valid WinGet `MSIX` inventory result
to the transport identity `appx:<PackageFullName>`. This adapter never decides
that a package is launchable. The Windows client is the authority: it validates
the suffix with `VerifyPackageFullName`, opens the current user's registered
package with `OpenPackageInfoByFullName`, enumerates application identifiers
with `GetPackageApplicationIds`, and fails closed unless exactly one
application identity exists.

The client activates that AUMID through
`IApplicationActivationManager::ActivateApplication`. Activation therefore
runs in the interactive user's session, not in the elevated helper or a SYSTEM
desktop. No path, command line, arguments, guessed executable or product name
crosses this boundary. The portable-package helper path remains unchanged and
the two identity families are dispatched by their typed prefixes.

The activation receipt binds the package identity, AUMID, activation boundary,
the PID returned by Windows and its creation time. It also records the bounded
set of package processes that existed before activation, as exact PID and
creation-time pairs. Reverse derives the post-activation process cohort from
Windows package identity; for full-trust packaged applications whose launcher
hands execution to another process, the only fallback is an executable image
inside the immutable registered package root returned by Windows. It excludes
every pre-existing identity and terminates only verified processes born after
the boundary, repeating the bounded scan to follow a handoff.

Disappearance of the activation PID alone is not proof of restoration. If the
client cannot identify and stop at least one eligible process, it returns a
causal failure; the executor accepts success only with an explicit positive
restoration attestation. AppX activation advertises only session lifetime.
Persistent startup is not offered until Windows startup registrations have an
equally exact, user-owned identity and verified inverse.

## Alternatives considered

Routing every MSIX identity through the privileged helper was rejected because
package installation authority is not interactive-session activation
authority. Maintaining a Notepad/Calculator/application table was rejected as
application-specific hardcoding. Searching WindowsApps for an executable and
running it was rejected because a package path is not an AUMID and bypasses
the package activation contract. Treating every package with several AUMIDs as
launchable by choosing the first was rejected because ordering is not identity
proof.

## Consequences

Any single-entry packaged application discovered through authoritative Windows
metadata can use the same flow. Ambiguous packages remain discoverable but are
not mutated. Portable and AppX applications share the public `run_processes`
contract while retaining separate OS-native authorities. Client 0.2.57 proved
real packaged-app activation. A later live observation showed that the first
PID can hand execution to another package process, invalidating the original
0.2.58 stop attestation. Client 0.2.62 closes that general gap with cohort
tracking; live turn `5b1cc75b42aa4aeb` and reverse invocation
`inv-18ce87c4bcce8dcbde1f61c5` returned `stopped=true`, with the application
window observed closed by the user.
