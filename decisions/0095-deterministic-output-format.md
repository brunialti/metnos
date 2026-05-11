---
id: 0095
title: Deterministic output formatting helpers
date: 2026-05-07
status: accepted
area: runtime, ux
related:
  - 0090  # get_inputs (declarative UI engine — channel adapter contract)
  - 0094  # fast path (same family: deterministic > LLM)
complements:
  - 0094
---


## Context

Live turn 7/5/2026 12:22 ("stato sistema") produced this output:

```
📊 Stato server
  Carico: 0.98 / 0.94 / 0.47 (uptime 164h)
  RAM: 38.4% (46/121 GB), swap 8%
  ...
Top processi:
  python                   cpu=100.0%  mem= 0.0%
  llama-server             cpu= 3.8%  mem=11.1%
  ...
```

Roberto's feedback: «cosa vuol dire `Carico: 0.98 / 0.94 / 0.47`?». The
slash composition is ambiguous to a casual reader. The "top processi"
block is a list of identical 3-attribute records — a natural table — but
rendered as fixed-width text bullets. Below that, an LLM rephrasing via
`describe_entries` produced the same data, more readable, but creating
a *double rendering* problem (deterministic + LLM-rephrased of the same
content).

The underlying issue is that several executors and orchestration helpers
emit ad-hoc string formatting (slashes, fixed-width, custom prefixes). No
shared vocabulary. Each LLM-rephrasing pass tries to compensate for the
raw output's poor structure but adds latency and cost.

CLAUDE.md §7.9 says deterministic code > LLM when equipotent. Pretty
output for tabular data is exactly that: deterministic.

Until per-manifest formatting standards are defined, we need a
**central deterministic formatter** that channel adapters consume
uniformly.

## Decision

Introduce `runtime/output_format.py`: small module of pure helpers
that emit channel-agnostic markdown. Channel adapters (`channels/daemon.py`
for Telegram, `http_routes_agent.py` for HTTP chat, voice adapter for
TTS) translate the markdown to their target format.

### Rules

- **KV (single value)**: `**label**: value [unit]`. Bold label,
  unit inline. `%` sticks to the value (`38.4%`); word units have a
  space (`47.7 GB`, `120 sec`).
- **KV group (correlated values)**: titled block + bullets per pair.
  Forbid the slash composition for ambiguous numeric triplets
  (`0.98 / 0.94 / 0.47`). Use explicit labels: `1m: 0.98, 5m: 0.94,
  15m: 0.47` or per-line bullets.
- **List → bullets** when items are short text/path/URL. **List →
  table** when records are homogeneous and have ≥3 comparable
  attributes (the canonical example: top-N processes with name + CPU%
  + MEM%). Threshold: ≥3 records AND ≥3 attributes → table.
- **Section** for substantial bodies: `### Title\n\nbody`. `format_section`.
- **TL;DR** of one italic line at the top of outputs >5 lines.
  `format_tldr` → `_Riepilogo: ..._`.
- **Cap-expand / offerte utente** in their own block, separated from
  content by an HR (`---`). `format_offer(title, body)`.
- **Numeric quantities always have units inline** (no naked numbers).
- **Channel-agnostic markdown only** — no HTML in the executor or
  orchestration layer. Channel adapter does the translation.
- No LLM call in the formatter (§7.9 deterministic > LLM).

### Module surface

```python
format_kv(label, value, unit=None) -> str
format_kv_group(title, pairs: [(label, value, unit_or_None)]) -> str
format_list(title, items, bullet="•", cap=None) -> str
format_table(headers, rows, title=None, align=[...]) -> str
format_section(title, body) -> str
format_tldr(line) -> str
format_separator() -> str
format_offer(title, body) -> str
```

### Initial application

`runtime/orchestration.py`:
- `_fmt_health_block` — load average rendered as `1m X, 5m Y, 15m Z` (no
  slashes); each metric heading bolded.
- `_fmt_entries_block` — get_processes records (cpu_pct + mem_pct + name)
  emitted as `format_table` instead of fixed-width bullet text.

### Until per-manifest standards exist

Executor manifests today don't declare output formatting. Until we add a
schema field for that, the orchestration helpers in `orchestration.py`
remain the central rendering layer. Adding new applications means
calling `format_*` from existing helpers, not new manifest fields.

## Consequences

Positive:
- Roberto's two ambiguity complaints addressed in the same change
  (load slashes + processes table).
- Removes drive for double-rendering: the deterministic output is now
  good enough that `describe_entries` is reserved for genuine semantic
  summarisation, not visual cleanup of bad text.
- Single locus for formatting evolution. Consistency across HTTP/Telegram/
  voice via channel adapter contract.
- Zero LLM cost for visual quality.

Open / future:
- Per-manifest formatting standard (TBD): when a manifest declares
  `[output.format]` (e.g. `kind="table"`, `columns=[...]`), the runtime
  formatter applies it without orchestration changes. Out of scope here;
  this ADR establishes the helper layer that the future schema will use.
- Channel adapters today render markdown by passthrough or plain-strip;
  Telegram could exploit `<b>`/`<i>` mapping for bold/italics but that's
  a per-channel optimisation, not blocking.
- `describe_entries` may still rephrase deterministic output when
  semantic summarisation IS the user's intent ("riassumimi questa lista")
  vs "mostrami questa lista". Distinction belongs in the planner's
  intent extraction, not in the formatter.
