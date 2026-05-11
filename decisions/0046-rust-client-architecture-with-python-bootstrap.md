---
id: 0046
title: Rust client + python-build-standalone + uv as the remote-executor architecture
date: 2026-04-27
status: accepted
area: architecture
related:
  - 0011
  - 0037
---

## Context

ADR 0011 had decided the *what* of remote execution — an
asymmetric client/server architecture with `metnos-client` on
each device. The *how* of building that client was open. The
client must:

- run on Windows, Linux, macOS;
- install minimally (small footprint, few dependencies);
- install lazily (just-on-demand at first use);
- install incrementally (add capabilities on request, not as a
  monolith);
- execute Python (the executors are Python).

The tension is in the last requirement. A small Rust binary that
also runs Python is not trivial — there are several historical
approaches and most have failed for one reason or another. A deep
research session was launched on 27 April 2026 to map the current
state of the art before any code was written.

## Decision

The chosen architecture: **Rust launcher (5–10 MB) +
`python-build-standalone` (Astral) downloaded lazily + uv as a
subprocess for wheels and venv management**.

This preserves all five constraints. Decisions ratified the same
evening:

**1. Wheel distribution: PEP 503 mirror server-side from day one
(not direct PyPI).**
- Endpoints on `.33`: `/agent/pypi/simple/{package}/`,
  `/agent/pypi/files/{package}/{filename}`,
  `/agent/runtime/cpython-...tar.zst`,
  `/agent/client/metnos-client-...`.
- Hash-keyed cache in `/var/lib/metnos/wheel-cache/` (no eviction
  in MVP).
- Two-level hash verification: server (at cache time) + client
  (at install time).
- Why: server controls the supply chain, audit unified, no
  user-side network configuration, works in corporate networks
  that block `pypi.org`, second device requesting the same wheel
  gets it instantly from cache.
- Cost: ~200 lines of Python on the server (PEP 503 mirror plus
  cache plus audit), +3 days in W4–5 of the roadmap.
- Hash pinning in executor manifests remains mandatory.

**2. uv as a subprocess for the MVP** (option A vs a Rust
rewrite).
- The mirror moves the cost-benefit toward A: uv accepts
  `UV_INDEX_URL` custom and does the work.
- Cost: two binaries distributed (~7 MB client + ~16 MB uv
  compressed = ~25 MB total).
- A targeted Rust rewrite (~500–1000 lines) is deferred to v2
  when the wheel superset for the 27 seeds stabilizes and a
  smaller binary becomes worth the work.

**3. Device identity client-side.**
- At first launch the client generates Ed25519 locally. Private
  key never leaves the device.
- Pairing via an ephemeral Telegram token (TTL 10 min) plus
  `RegisterDevice(token, public_key)`.
- Storage MVP: `%LOCALAPPDATA%\metnos\key` (Windows),
  `~/.local/share/metnos/key` (Linux). v2: TPM / Secure Enclave /
  keyring.
- Challenge-response is effectively free from the mTLS handshake.

**4. Versioning of `python-build-standalone`**: minor pin
(`3.13.x`), auto-bump patch in CI.

**Other decisions closed the same evening (residual five):**

- **Multi-device naming**: name chosen at pairing
  (`/pair-device laptop-windows`); canonical identifier
  everywhere; numerical `device_id` internal; `/rename-device old
  new` for corrections.
- **Approval UX with the "where" always visible**:
  `Sto per: pkg_install("git") su laptop-windows. [Approva]
  [Rifiuta]`. Server-side reads `su .33`. Multi-step: one line
  per action with its own target.
- **Offline buffering minimal**: server down = client stops, with
  notification on next attempt. Exception that costs little:
  buffer locally for log + telemetry + results of in-flight
  executions. Spool in `%LOCALAPPDATA%\Metnos\spool\` /
  `~/.local/share/metnos/spool/`. Persistent queue of new actions
  is *not* MVP (deferred to phase-7 robustness).
- **State + secrets server-only**: on the device, only the Ed25519
  key, the read-only `python-build-standalone` cache, the
  hash-keyed wheel cache, and the spool. No local SQLite. App
  state (mnestoma, scratchpad, history) lives only on the server.
  OAuth / API secrets live only on the server, injected into the
  subprocess's environment at execution time over mTLS, never
  persisted to disk. Stolen device ≠ leaked secrets.
- **Build and distribution: everything on `.33`**. No GitHub
  Actions. `scripts/build-client.sh <version>` cross-builds for
  each target, signs with the server's Ed25519 key, copies into
  `/var/lib/metnos/client/<version>/<target>/`, updates the
  manifest. Same pattern as `deploy.sh` for the site. macOS
  tier-2 (manual build on a Mac when needed).

The MVP roadmap (5 weeks, immutable):
1. **W1–2 Linux MVP** — Rust client + mTLS to `.33` +
   python-build-standalone via mirror + bwrap sandbox.
2. **W3 Windows base** — Job Object sandbox +
   python-build-standalone Windows tarball.
3. **W4 Pairing + auto-update** — Telegram `/pair-device`,
   `self_update` from signed releases.
4. **W5 Cache + telemetry** — wheel + executor cache hash-keyed,
   GC, structured logs, heartbeat.
5. **W6+ Hardening** — AppContainer Windows, Azure Artifact
   Signing $9.99/month if needed.

## Alternatives considered

**PyOxidizer.** Frozen since January 2023; its author declared the
project "uncertain, possibly dead". Astral inherited
`python-build-standalone` (which is what the chosen schema relies
on) but not the embedding magic. Rejected.

**RustPython.** Does not load CPython wheels (numpy, cryptography,
pydantic v2, lxml, pyarrow). Fragments the executor pool. Rejected.

**PyO3 with statically linked `libpython`.** Cross-compile on
Windows is painful, ABI fragility, loses the "small client"
property. Rejected.

**PyInstaller / Nuitka / shiv / pex.** Betray lazy and
incremental: 60–100 MB binaries per OS. Rejected.

**WASM (wasi-python).** Wheel-WASI ecosystem embryonic,
performance poor. Re-evaluate in 2027+. Rejected for MVP.

**Direct PyPI access from each device** (no server-side mirror).
Pro: no mirror code. Con: no supply chain control, broken in
corporate networks, repeated downloads on multiple devices.
Rejected.

**uv rewrite in pure Rust** for MVP. Pro: smaller binary. Con: 500–1000
lines of Rust to maintain; the wheel set is not yet stable; v2
work. Rejected for MVP, kept as v2 candidate.

## Consequences

The first remote device was paired E2E on the evening of 27
April: Roberto's Windows PC against `.33`, cross-build via mingw,
`.exe` of 2.5 MB statically linked (DLL stock only — see ADR
0037), server-side mirror operational, real `RegisterDevice`
ceremony succeeded. Zero exotic installations on the PC.

The decision plumbs into multiple existing rules:

- ADR 0011 (asymmetric client/server) gets the technical body.
- ADR 0034 (routing policy) has a real device profile to score.
- ADR 0037 (self-contained binary) is the link-time discipline
  that produces the 2.5 MB `.exe`.
- ADR 0016 (OSS self-hosted) appears as the build infrastructure
  (cross-compile on `.33` rather than CI provider) and as the
  wheel mirror.

Open after the decision (deferred):
- concurrency of parallel executors per device;
- explicit unpair flow;
- health telemetry detail;
- cert pinning at CA vs leaf level;
- delta updates for the client binary.

Macro-topics for phase 7 still open:
- multi-user (today single-user, see ADR 0035 for the host+guest
  shape);
- end-to-end efficacy measurement (latency, LLM cost, routing
  hit-rate);
- formalized failure modes (LLM / executor / sandbox / channel
  down: graceful vs hard-fail);
- disconnect-proof (Internet / Telegram / Headscale / `.33` loss
  scenarios).
