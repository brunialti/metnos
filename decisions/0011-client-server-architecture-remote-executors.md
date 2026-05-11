---
id: 0011
title: Client/server architecture for remote executors (metnos-server + metnos-client)
date: 2026-04-24
status: accepted
area: architecture
related:
  - 0007
  - 0046
---

## Context

The Architettura chapter 4 had introduced "remote executors" as a
direction, but the topology was abstract: where exactly does the code
run? When the topology was fixed on `.33` as the home server (ADR
0007), the question got concrete. Some user requests are intrinsically
about the *device*: filesystem on a laptop, packages on a Windows PC,
hardware on a Mac. These can never be satisfied from `.33` alone.

At the same time, *all* the policy decisions — vaglio, telos
matching, approval gating, mnestoma access — must remain centralized.
Distributing policy across devices creates a multi-headed system that
can disagree with itself; centralizing policy preserves the single
authoritative trail of "who decided what, when, and why".

The decision was taken on 24 April 2026 to formalize an asymmetric
client/server architecture before any further microdesign work
(pairing, sandbox, the Rust client) would be locked.

## Decision

Two components, sharply asymmetric.

**`metnos-server`** lives on `.33`. It owns the gateway, the vaglio,
the mnestoma, the central audit log, and is the canonical source of
*who, what, when*. All signed manifests live in
`workspace/executors/<name>/`. The server emits signed invocations
toward clients through the Headscale overlay (ADR 0007). It controls
device pairing, distribution of remote-executor binaries, and
revocations.

**`metnos-client`** is a small daemon on the target device — Windows
Service (auto-start, user account), `systemd --user` on Linux, or
equivalent on macOS. It maintains a long-lived mTLS connection inside
the Headscale overlay back to `.33`. It receives calls from the
server, verifies the signature and manifest of the executor against
its local cache, runs in the strongest sandbox the platform offers,
and returns the result. It writes the device-side line of the audit
log, correlated by `trace_id` with the server-side line.

The client **never acts on its own initiative**. No command is
generated client-side. It executes, traces, returns. This rule is
load-bearing: it eliminates a whole category of attacks (a
compromised client that issues self-commands) and aligns with the
"single locus of policy" principle.

Trust model: the client is subordinate to the server. Pairing of a
device is a signed ceremony from a channel already trusted (Telegram
DM). Without valid pairing, the client receives no token. Tokens are
single-invocation (mTLS plus signed token per call): a leaked token
does not grant continuous access. Revocation: a DM command removes
the device from the paired list; future tokens are refused.

Sandbox strength is platform-asymmetric: bubblewrap + landlock +
seccomp + namespace mount on Linux (strong, parity with `.33`),
sandbox-exec + entitlements on macOS (medium), AppContainer + Job
Object + NTFS ACL on Windows (weak). The weak sandbox on Windows is
compensated by double profile verification (server signature +
client check before each operation), mandatory before/after
reversibility patterns, idempotency requirement, and double audit.

## Alternatives considered

**Federate the runtime, no central server.** Each device runs a full
Metnos and they sync. Pro: any single device can keep working
offline. Con: distributed policy, eventual disagreement, mnestoma
fragmentation, cryptographic complexity (multi-master), audit becomes
a reconstruction problem. The "one authoritative voice" property of
Metnos collapses. Rejected.

**Server-only, no remote execution.** Pro: simplest. Con: a whole
class of user requests becomes unanswerable ("install VLC on my
laptop", "what files are on this PC"). The capability gap is large
and recurrent. Rejected.

**Server-side proxy with SSH/WinRM/agent.** Pro: no custom client
binary. Con: heavyweight prerequisites on every device (SSH server
exposed, credentials, brittle on Windows), no platform-specific
sandbox, no audit on the device side, no signed manifest verification
client-side. Rejected.

**Client decides what to run** (active client). Pro: less server
load. Con: the rule "every action passes through one policy point" is
broken; the asymmetric trust model collapses. Rejected.

## Consequences

Three documents are downstream of this decision. `pairing.html` (UNTRUSTED
today) must be rewritten to extend the DM-pairing of senders to the
device-pairing ceremony. `sandbox.html` (UNTRUSTED today) must include
the three platform-specific tables. A new `metnos_client.html` joins
the canonical microdesign as the fourth v1.1 doc after executor /
mnest / mnestoma, bilingual from origin.

Roadmap: the first real remote executor (phase 5+) is not written
before pairing, sandbox, and `metnos_client.html` are fixed. The
implementation language and binary architecture of the client are
the subject of phase-7 topic 1, settled in ADR 0046 (Rust launcher
+ python-build-standalone + uv).

Four design decisions were left explicitly open at the time of this
ADR and were closed later (ADR 0046):

1. Distribution and bootstrap (signed package + one-liner).
2. Auto-update of the client (server-signed).
3. Cache of remote executors on the client (push vs pull).
4. Failure modes (offline client, compromised client, offline server).
