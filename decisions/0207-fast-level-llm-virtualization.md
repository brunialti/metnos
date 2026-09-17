---
id: 0207
title: Fast-level LLM virtualization
date: 2026-08-05
status: accepted
area: runtime | llm | installation | ui
related:
  - 0025
  - 0146
supersedes:
  - 0025
---

## Context

The LLM census found three fast-level contracts: tiny decisions, procedural
fast operations, and long faithful fast operations. They currently use the
same local Qwen binding and the same decoding policy. The established
`middle` and `wise` roles remain separate operational tiers: they are not
aliases of fast levels and retain their existing workloads.

Planning, divergent editorial work, and an explicit remote escalation remain
genuinely different contracts.  Per-call temperature, thinking, and reasoning
budget overrides are not acceptable: they make a configured tier unsafe to
change.

## Decision

Metnos exposes five tiers:

| Tier or level | Contract |
|---|---|
| `fast.micro` | bounded labels, tiny JSON, and short decisions |
| `fast.procedural` | deterministic extraction, classification, and structured judgments |
| `fast.fidelity` | faithful transformations on larger context, including translation and semantic verification |
| `middle` | deterministic intermediate transforms and judgments |
| `wise` | deliberate planning and synthesis |
| `creative` | divergent proposal and editorial generation |
| `frontier` | explicit maximum-capability escalation |

`micro`, `procedural`, and `fidelity` are levels of the legacy `fast` tier,
not public model names and not a quality ordering.  A workload in
`runtime/llm_workloads.py` resolves to a tier request; the router receives the
level together with `fast`.  The compatibility facade is:

```python
get_llm("fast", level="fidelity")
```

The caller still selects only a logical workload.  Provider, model, endpoint,
temperature, thinking, and reasoning budget belong to the router.  Output
ceiling, deadline, grammar, and tool schema remain operation constraints.

`DEFAULT_FAST_LEVELS` contains a complete default policy for all three levels.
At adoption they are intentionally identical.  The instance may override one
level under `[fast.level.<name>]`; it inherits the binding from `[fast]` unless
the override supplies another binding. `middle` and `wise` retain deterministic
`temperature=0` policies. `creative` has `temperature=0.35` and, until an
administrator configures `[creative]`, inherits the physical binding of
`wise`. `frontier` stays optional and never degrades to a local tier.

## Consequences

- Existing code can request `get_llm("fast", level=...)` without depending on
  the serving engine.
- Production workloads keep their established `middle` and `wise` assignments.
- A future separate fast-level binding changes configuration, not callers.
- The Models UI shows the three fast levels as independently editable cards.
- A grammar does not alter a generation profile; it remains a structural
  operation constraint.

## Verification

Tests cover the closed tier vocabulary, the three fast-level defaults,
level-specific configuration, Models-page round trips, installer output, and
the absence of production per-call decoding-policy overrides.

## 17 September 2026 — lifecycle ownership (candidate)

The host lifecycle boundary is `virt.resources.ModelResource`: a generic claim,
binding facts and bounded readiness operation. LRE validates admitted facts
before and after calling it; provider-specific endpoint and launcher decisions
remain in Virt. The existing local vision lifecycle is the first adapter.
Externally supervised and remote services retain their current lifecycle.

Dynamic replicas require stable public model identity and endpoint, measured
resource profiles, shared reservations including pending launches, bounded
routing and drain-before-stop. They are not implemented or enabled by this
boundary refactor. In particular, an available endpoint is not proof of GPU
memory headroom while other processes use the same device.
