---
id: 0034
title: Three-level routing policy for executor placement (server vs device)
date: 2026-04-27
status: accepted
area: runtime
related:
  - 0011
  - 0046
---

## Context

Once remote executors became real (ADR 0011 and the Rust-client
research of ADR 0046), the question "where does this executor run"
needed a structured answer. The naive default — "always on `.33`" —
fails when the target is a resource on a specific device (a path
on a Windows laptop, a package manager, a specific GPU). The
opposite naive default — "always on the device that last spoke" —
fails when the user simply wants a search executed where bandwidth
is best.

The space of policy considerations is large: data locality, latency
budgets, trust levels in a future multi-user setup, capacity
saturation, battery state, failure history, learned use patterns.
Adding all of them at once would build a complex routing engine
before the first device pairing experience exists. The discipline
of simplicity (ADR 0017) and the KIS framing on 27 April 2026
constrained the design to a deterministic, three-level structure
with linear scoring, and a small set of additional policies that
can be added one at a time when their need is concrete.

## Decision

Three levels, evaluated in order. The first level that yields a
choice wins.

**Level 1 — absolute affinity.** Deterministic, never overridden.
- 1.a *Target on a device's local resource* → that device. The
  manifest declares `targets: [filesystem | network | hardware |
  ...]`; if the target binds the operation to a device's local
  state, that device.
- 1.b *Manifest scope* → `scope: server | device | any`.
  `server-only` and `device-only` are binding.
- 1.c *Explicit user override* → bypasses everything for that
  action.
- 1.d *Availability gate* → the device target requires a fresh
  heartbeat (<60 s). Open question: when offline, persistent queue
  or error?

**Level 2 — workload classification.** Only when level 1 did not
decide. The manifest declares `class: net | cpu | io_fs | mem |
mixed | llm_local`.
- `net` → device with the best bandwidth and stability (default
  `.33`).
- `cpu` → most powerful device (default `.33`, Strix Halo).
- `io_fs` → bound to filesystem locality.
- `llm_local` → where the configured provider runs.
- `mixed` → server by default.

The device profile is exposed at pairing and refreshed by heartbeat:
`cpu_bench_single`, `cpu_bench_multi`, `ram_free`,
`net_throughput`, `net_latency_to_server`, `has_gpu`, `gpu_model`,
`current_load`. Scoring is a 4×6 linear table — no machine
learning, no optimizer.

**Level 3 — tiebreaker.** When two or more devices remain.
- 3.a *Cache locality* → prefer the device that already has
  `python-build-standalone` plus the manifest in cache.
- 3.b *Current load* → the least loaded.
- 3.c *Default* → the server `.33`.

**Style constraint.** KIS. Linearity, simplicity, modifiability. No
over-engineering. The 4×6 scoring table is the upper bound of
tolerated complexity in MVP.

**Seven candidate policies, queued and added one at a time.** Each
is opt-in and can be feature-flagged off:
1. Privacy / data locality — sensitive data does not leave the
   owner device. Wins over efficiency. Open: how is the data tagged
   (manual tag, path-based rule, ML)?
2. Perceived latency — interactive favors fast first-byte over
   throughput.
3. Trust level — future multi-user. Personal laptop > VPS > device
   of other users.
4. Capacity / anti-saturation — per-device quota, queue when
   saturated.
5. Energy / battery — laptop below 20% deprioritized for
   non-urgent.
6. Failure history — three consecutive failures on `deviceX` →
   force another device. Anti-flapping.
7. Learned use patterns — "morning cluster on laptop, evening on
   desktop". Adaptive affinity, fuel for the introvertive cascade.

The seven are *not* implemented all at once; they are added when
their need is concrete.

## Alternatives considered

**Single-level ML router.** Pro: maximum theoretical optimality.
Con: requires telemetry that does not exist; black-box decisions
hide design choices; debugging the routing becomes a research
problem. Rejected.

**Two-level only (target + default).** Pro: simpler. Con: the
classification step is the place where workload-aware decisions
live; without it, GPU-bound or bandwidth-bound work routes wrong.
Rejected.

**More than three levels.** Pro: more nuance. Con: the levels stop
being readable; the order of evaluation becomes hard to remember;
the simplicity rule fails. Rejected.

**Implement all seven candidate policies day one.** Pro: complete
coverage. Con: most of them have no concrete need yet; each adds
configuration surface; debugging which policy fired becomes hard.
Rejected.

## Consequences

The runtime gains a `Router` module that consumes the manifest's
`targets`, `scope`, `class`, plus the heartbeat-fed device profile,
and returns a single `target_host` per executor invocation. Logs
record which level decided.

The discipline pairs with ADR 0011 (asymmetric client/server
trust): the router's decision sets where the server *sends* the
signed invocation; the client only ever executes what it received.
The decision is single-sided, by design.

The seven candidate policies are tracked in this ADR and the
companion memory; when a candidate becomes a real need, its
implementation is documented as an extension, not a new ADR. The
modular shape of the router (each policy is a function over the
candidate device set) is what makes adding them cheap.

Open from this decision, deferred to phase-7 robustness:
- offline buffering of pending invocations for a device that
  reconnects later;
- exact heartbeat staleness threshold (60s today, calibrate);
- sensitive-data tagging mechanism for policy 1 above.
