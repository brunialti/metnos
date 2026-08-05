# LLM invocation census

Status: accepted by ADR 0207, 2026-08-05. The static inventory contains 56
active text-LLM source expressions in 33 production files. It counts wrappers
and retries as expressions, not as independent workload policies. Two dormant
text paths and the separate VLM family are outside that active count.

## Result in plain language

All active calls fit six logical contracts. Three are deterministic variants
of the same `fast` tier and therefore become levels rather than separate
tiers:

| Router request | Logical work | Current default policy |
|---|---|---|
| `fast.micro` | routing, labels, tiny JSON, map digests | temperature 0; no thinking |
| `fast.procedural` | extraction, classification, structured judgement | temperature 0; no thinking |
| `fast.fidelity` | translation, grounded composition, semantic verification | temperature 0; no thinking |
| `wise` | planning, code and test synthesis | temperature 0; no thinking in the current local deployment |
| `creative` | Telos, editorial proposals, descriptive refactoring | temperature 0.35; no thinking |
| `frontier` | expressly authorized remote escalation | provider-owned default policy |

The three fast-level defaults are deliberately equal today because they share
the same Qwen endpoint. The level is still passed to the router, recorded in
the workload contract, and may receive a separate binding or policy later.
It is not passed to the model.

## Workload inventory

The registry contains 39 declared workloads. Two small dialog behaviors reuse
the intent-extraction callback but retain their own registry identities;
`final.compose` and `tutor.relation` remain the two registry follow-ups from
the source inventory. The active registry mapping is:

| Contract | Workloads |
|---|---|
| micro | intent extraction/routing/filler, site reductions, image reranking, Tutor mode, small entry description/classification, routine planning |
| procedural | entry classification/extraction, bills, Vaglio, Alignment, admin translation, manifest normalization, folder/URL classification, procedural SYNt, frontier tool loop |
| fidelity | entry description, i18n/detection translation, skill description, semantic SYNt verification, Tutor composition, grammar planning |
| wise | deliberate planning and SYNt generation, birth tests, multistage synthesis |
| creative | Telos, promotion commentary, manifest refactoring, SYNt description |
| frontier | consult_frontier |

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
