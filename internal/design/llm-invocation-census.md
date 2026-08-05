# LLM invocation census

Status: accepted by ADR 0207, 2026-08-05, **with the amendment recorded
below**. The static inventory contains 56 active text-LLM source expressions
in 33 production files. It counts wrappers and retries as expressions, not as
independent workload policies. Two dormant text paths and the separate VLM
family are outside that active count.

> **Amendment, 2026-08-05.** This census proposed dissolving `middle` into
> `fast.procedural` and moving faithful transforms to `fast.fidelity`; its
> original table had no `middle` row at all. The ADR did not accept that part:
> its Decision keeps `middle` and `wise` as separate operational tiers that
> "retain their existing workloads". `runtime/llm_workloads.py` implements the
> ADR, so the mapping below is the amended one. Where the two disagree, the
> registry is the source of truth.

## Result in plain language

Every active call fits a logical contract. `fast` carries three deterministic
levels; `middle` and `wise` remain separate roles:

| Router request | Logical work | Current default policy |
|---|---|---|
| `fast.micro` | routing, labels, tiny JSON, map digests | temperature 0; no thinking |
| `fast.procedural` | declared, no production workload today | temperature 0; no thinking |
| `fast.fidelity` | declared, no production workload today | temperature 0; no thinking |
| `middle` | extraction, classification, structured judgement | temperature 0; no thinking |
| `wise` | translation, grounded composition, semantic verification, planning, code and test synthesis | temperature 0; no thinking in the current local deployment |
| `creative` | Telos, editorial proposals, descriptive refactoring | temperature 0.35; no thinking |
| `frontier` | expressly authorized remote escalation | provider-owned default policy |

The three fast-level defaults are deliberately equal today because they share
the same Qwen endpoint. The level is still passed to the router, recorded in
the workload contract, and may receive a separate binding or policy later.
It is not passed to the model.

`fast.procedural` and `fast.fidelity` therefore exist as configurable levels
without a production consumer: an administrator who binds them changes nothing
until a workload is moved onto them. That is a deliberate, visible cost of
keeping the level vocabulary in place, and the Models page states it on the
card rather than leaving an operator to discover it.

## Workload inventory

The registry contains 39 declared workloads. Two small dialog behaviors reuse
the intent-extraction callback but retain their own registry identities;
`final.compose` and `tutor.relation` remain the two registry follow-ups from
the source inventory. The active registry mapping is:

| Tier | Workloads |
|---|---|
| `fast.micro` | intent extraction/routing/filler, site reductions, image reranking, Tutor mode, small entry description/classification |
| `fast.procedural`, `fast.fidelity` | none |
| `middle` | entry classification/extraction, bills, Vaglio, Alignment, admin translation, manifest normalization, folder/URL classification, procedural SYNt, frontier tool loop |
| `wise` | entry description, i18n/detection translation, skill description, semantic SYNt verification, Tutor composition, grammar planning, routine and deliberate planning, SYNt generation, birth tests, multistage synthesis |
| `creative` | Telos, promotion commentary, manifest refactoring, SYNt description |
| `frontier` | consult_frontier |

`max_tokens`, deadlines, grammar and tool schemas vary materially within each
row. They stay operation-owned. Provider, model, endpoint, temperature,
thinking and reasoning budget do not: they belong to the resolved router
request.

## Residual hardening

The active production gateway has no per-call generation-policy overrides.
The dormant direct NLU client and an un-wired sanity-check callback remain
separate hardening work: they must be either registered through the router or
removed before activation. VLM is independently configured in `vlm_tiers.toml`
and is not a text-LLM tier.
