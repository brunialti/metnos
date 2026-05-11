---
id: 0037
title: Self-contained client binary; no toolchain required on the target
date: 2026-04-27
status: accepted
area: architecture
related:
  - 0046
---

## Context

The remote-executor architecture (ADR 0011) places a small daemon —
`metnos-client` — on each target device. The naive distribution of
such a binary requires runtime libraries on the target: on Windows
the Visual C++ Redistributable, on Linux a specific glibc version,
on macOS Homebrew dependencies. Each prerequisite is friction; each
piece of "please install X first" is a failure of onboarding.

For Metnos the failure mode is acute. A user on Telegram pairs a
new device with `/pair-device laptop-windows`, gets a download URL,
clicks. From that moment to "the laptop is paired" should take
minutes, not an afternoon of installing Visual Studio Build Tools.

The decision was needed on 27 April 2026 before the Rust client
implementation started (the W1 of the MVP roadmap of ADR 0046),
because target architecture and link-time choices are part of the
build configuration from day one.

## Decision

The `metnos-client` binary (and any other component distributed to
target devices) is fully self-contained. The user downloads one
file and runs it. No prerequisites.

**Per-platform requirements:**

- **Windows.** Target `x86_64-pc-windows-gnu` with `-C
  link-arg=-static-libgcc -C link-arg=-static-libstdc++` (or
  equivalents) to statically link libgcc / libstdc++ / winpthread.
  Alternative: `x86_64-pc-windows-gnullvm` once mature. The user
  receives a `.exe` (or a minimal `.msi`); double-click runs.
- **Linux.** Target `x86_64-unknown-linux-musl` for static binaries
  (no glibc symbol versioning). Preferred over the `gnu` target for
  distribution.
- **macOS.** Target `aarch64-apple-darwin` (and `x86_64-apple-darwin`
  for Intel). No Homebrew dependencies.

**Verification.** After every build, dependencies are inspected
(`objdump -p` on Windows, `ldd` on Linux, `otool -L` on macOS).
The binary may only require stock OS DLLs / dylibs (e.g. on Windows:
kernel32, ntdll, ws2_32, advapi32). It must never require libgcc,
libstdc++, msvcr120 or similar.

**Mingw (or similar cross-compilation toolchains)** stays on the
*builder* (`.33`). The user never sees mingw. The build pipeline is
allowed to use it; the distributable is not.

**Acceptable exceptions** (not user prerequisites, by design):
- `python-build-standalone` downloaded lazily on demand by the
  client. The user does not actively install it; the client fetches
  it from the server's mirror at first execution. This is the
  design (ADR 0046), not an exception in spirit.
- Wheels downloaded via the server's mirror cache. Same reasoning.

In short: anything requiring active installation by the user is a
failure of the onboarding experience.

## Alternatives considered

**Distribute a Python interpreter + venv directly.** Pro: standard
in the indie tooling ecosystem. Con: 60–100 MB per OS, slow to
download, slow to install, includes binaries the user does not need.
Rejected. (Detailed comparison in ADR 0046's research.)

**Require system Python on the target.** Pro: no Python in the
client. Con: fragmentation across versions; users who do not have
Python lose the onboarding; macOS system Python is unreliable;
Windows often has none. Rejected.

**Static binary via PyOxidizer / PyInstaller / Nuitka.** Pro:
self-contained. Con: binaries are 60–100 MB; PyOxidizer is a dead
project; PyInstaller produces large boot times; Nuitka has
compatibility quirks with C extensions. Rejected (analysis in ADR
0046).

**Require Visual C++ Redistributable on Windows.** Pro: typical
Windows app pattern. Con: the user has to consent to a separate
install; failure mode is a confusing error message that mentions
Microsoft, not Metnos. Rejected.

## Consequences

The Rust client (ADR 0046) is built with the static-linking
configuration as default in `scripts/build-client.sh`. The first
real Windows build (27 April evening) produced a 2.5 MB `.exe`
that requires only stock Windows DLLs, verified with `objdump -p`.
Onboarding is `/pair-device` → URL → download → run.

The discipline interacts with the OSS-self-hosted-first principle
(ADR 0016): the build infrastructure (cross-compilation) lives on
the server `.33`, not on a CI provider. The build artifacts come
from the same physical host that will serve them. Audit and supply
chain are local.

A specific operational note: on Linux the chosen target is
`x86_64-unknown-linux-musl`, not `gnu`. musl produces fully static
binaries that work across distributions without glibc-version
roulette. Slightly more memory at runtime, almost no portability
issues. Worth it.

The exception list (lazy-downloaded `python-build-standalone`,
mirror-served wheels) is not a relaxation of the rule but a
clarification: the user installs nothing actively. The client may
fetch additional artifacts on first run; that is the design, not
a prerequisite.
