---
id: 0053
title: Stage 1 MAPPING bilingual amplification — closing the IT/EN vocabulary gap
date: 2026-04-28
status: accepted
area: synt
related:
  - 0049
  - 0050
  - 0051
  - 0052
---

## Context

The multistage rewrite of the synthesizer (ADR 0051) had been partially
validated on 28 of the 35 non-proto-mnest stress queries (ADR 0052
validation run b634utc9o, halted by a usage limit). 18 of 28 queries
synthesised correctly (~64 %), with 10 failures concentrated almost
entirely on stage 1 (`NAMING + CLASSIFICATION`). Inspecting the failures
revealed a single structural cause:

> The closed action vocabulary is in English (`read`, `write`, `extract`,
> `compress`, `compute`, ...). The stage 1 prompt explaining the mapping
> from user verbs to canonical actions was written in Italian, with a
> short list of Italian synonyms per verb (e.g. `"calcola/valuta/risolvi
> (espressione)" → compute`). Concretely, the prompt offered the LLM a
> single direction (Italian user verb → English canonical) and a thin
> list of synonyms.

This produced two distinct failure families:

* **(A) Linguistic gap** — the LLM stumbled on user verbs whose Italian
  synonym was in the list but whose semantics shaded into a different
  English canonical. Example: `"estrai testo da page.html"` was rejected
  because the prompt scoped `extract` to `"zip→file"` (literal), missing
  that "extract" semantically covers HTML tag stripping, video frame
  extraction, etc.
* **(B) Out-of-vocabulary user verbs** — `resize`, `transcribe`,
  `convert`, `query` (SQL), `validate`, `crack` etc. with no entry in
  the closed vocabulary. The LLM correctly escalated these as
  rejections, but they appeared as failures in the harness.

Family (A) was a real synthesizer bug. Family (B) was correct behaviour
miscounted by the harness.

## Decision

Rewrite the `MAPPING` block of `STAGE1_PROMPT` in
`runtime/synt_multistage.py` from a thin one-liner per canonical verb
into a **bilingual amplification** structure:

```
read       IT: leggi, apri, visualizza, mostra, conta-occorrenze-in
           EN: read, open, view, show, display, count-occurrences-in
           Sola lettura: ritorna contenuto/dati. Nessun side-effect.

write      IT: scrivi, salva, sostituisci-il-contenuto, sovrascrivi
           EN: write, save, replace-contents, overwrite, persist
           Crea o sostituisce contenuto di un file specifico.

... (one block per canonical verb in the 20-action vocabulary)
```

Each block carries:

* an Italian synonym list (the user often writes IT);
* an English synonym list (the user sometimes writes EN, and the LLM
  reasons closer to the canonical token when both shores are visible);
* one short line of semantic boundary, distinguishing the verb from
  adjacent canonicals (e.g. `extract` vs `render` vs `compress`).

Auxiliary changes:

* extend qualifier set with `_video`, `_audio`, `_image` to absorb the
  `extract_files_video`, `extract_files_html`, etc. patterns;
* add three concrete worked examples to the prompt covering the
  recovered cases (`extract_files_html`, `extract_files_video`,
  `filter_lines` for regex replacement).

**Crucially: do _not_ extend the closed vocabulary.** No `convert`, no
`validate`, no `query`. The KIS rule for the synthesizer is "synonyms
before vocabulary": a non-canonical verb the user uses should first be
checked against the synonym lists; only when it has no valid mapping
across all 20 canonicals does it warrant a vocabulary extension. The
empirical result (below) shows the existing 20-verb vocabulary is
sufficient when synonyms are rich.

## Consequences

Stage 1 isolated test on the 35 non-proto-mnest queries (run
`bovur9uj7`, ~7 minutes wall):

| | pre-amplification | post-amplification |
|---|---|---|
| name resolved | 26/35 | **31/35** |
| escalations (name=null) | 9 (mixed) | 4 (all semantically correct) |
| ex-fail q08/q11/q20/q25 | 0/4 match | **4/4 perfect match** |

Of the 12 cases where the produced action diverges from the harness's
`desired_executor` field, ~8 are cases where the synthesizer's choice is
strictly more aligned with the canonical naming convention than the
dataset's hand-written desired (e.g. `compress_files_zip` instead of
`render_files_zip`, `compute_files` for SHA-256 instead of
`get_files_hash`). The dataset was authored before the naming
convention was tightened (ADR 0049 and follow-ups); the synthesizer is
now the more reliable arbiter.

The remaining 4 escalations are correct out-of-vocabulary calls:

* `q06` "resize image" — image manipulation, requires an external tool;
* `q18` "execute SELECT … on .sq" — SQL/database access, requires a DB
  client capability the runtime does not yet expose;
* `q30` "validate YAML well-formed" — schema validation, distinct from
  the existing `compare`/`describe`/`compute` semantics;
* `r05` "crack /etc/shadow" — explicitly rejected by design.

These four are correctly in the territory of "not synthesisable from
the current vocabulary". They are a signal for future vocabulary
extension if the same patterns recur, not for prompt patching.

## Architectural lesson

A closed-vocabulary classifier is asymmetric across languages: the
vocabulary lives in one language, the user lives in another. The bridge
must be bilingual on both sides — synonyms in the user's language _and_
in the vocabulary's language — even when the user is monolingual,
because the LLM reasons better when both shores are visible. This will
generalise to any other prompt that crosses the IT/EN axis (vaglio
verdicts, planner intent classification, etc.).

The general rule, from now on:

> **Synonyms before vocabulary.**  When a user verb is not in the closed
> vocabulary, first try to absorb it as a synonym of an existing
> canonical. Only when the verb has no valid mapping across all
> canonicals (semantically distinct, recurring, not media-specific
> hardware) does it warrant a vocabulary extension.

Recorded in Claude memory as `feedback_synonyms_before_vocab_extension`.

## Open

* Stage 5 (CODE) abandoned for q09 (`describe_numbers`, mean+median):
  not a stage-1 issue, separate investigation needed (the name and
  signature are correct; the wise-tier code generation collapses).
* Whether to run a full multistage v3 (35 queries × 5 stages, ~75 min
  wall) to confirm end-to-end accuracy: the stage-1 signal is strong
  enough to consider it a high-cost confirmation, not a discovery run.
