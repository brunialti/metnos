---
id: 0107
title: Vaglio short-circuit per executor safe-by-construction
date: 2026-05-07
status: accepted
area: runtime, vaglio, performance
related:
  - 0069  # admin/sudoer architecture
  - 0088  # admin esposto al PLANNER
---

## Context

Il `vaglio` e' chiamato per ogni step. Default v1.1 = giudice rule-based
(~µs). Quando `JUDGE_KIND=llm-v1` e' attivo (futuro v1.2 / opt-in oggi),
ogni step paga ~1500-3000 ms per la chiamata LLM giudice. Tipicamente
60-80% degli step sono read-only/pure-compute (`read_files`, `find_urls`,
`get_processes`, `filter_entries`, ...) — la guardia binaria li lascia
gia' passare e il giudice e' noise.

## Decision

Aggiungere uno **short-circuit deterministico** dopo la guardia: se
l'action (verbo iniziale dal naming `azione_oggetto[_qualifier]`)
appartiene a un set chiuso di verbi safe-by-construction, vaglio approva
direttamente con `judge_kind="safe-verb-shortcut"` e `score=1.0`. Niente
chiamata al giudice.

Whitelist `vocab.SAFE_VERBS` (read-only / pure-compute / output-only):

```python
SAFE_VERBS = frozenset({
    "read", "find", "get", "list", "filter",
    "describe", "classify", "compute", "compare",
    "sort", "group",
})
```

Esclusi (residui passano dal giudice completo): `write, move, delete,
send, create, change, extract, render, set, compress, order`.

## Implementation

In `runtime/vaglio.py::judge`, fra la fase 1 (guardia) e la fase 2
(giudice):

```python
action = _action_of(executor_name)  # split prima del primo "_"
if action in SAFE_VERBS:
    return Verdict(approved=True, judge_kind="safe-verb-shortcut",
                   score=1.0, ...)
```

Determinismo §7.9: lookup in `frozenset` + split string. Zero LLM, zero
I/O. Latenza < 1 µs per step.

La guardia (forbidden paths, comandi shell distruttivi) e' ESEGUITA PRIMA
del shortcut: leggere `~/.ssh/id_rsa` con `read_files` (verbo safe) viene
ancora bloccato dalla guardia.

## Consequences

  + ~3-5 s risparmiati per step su safe verbs quando giudice LLM attivo.
  + Riduce drasticamente carico tier middle in v1.2.
  + Il rule-based v1.1 risparmia ~µs (marginale ma allineato).
  - Aggiungere un nuovo verbo richiede classificazione esplicita
    (vocab.SAFE_VERBS vs no): non c'e' default sicuro.

## Test plan

`runtime/tests/test_vaglio_safe_verb_shortcut.py` (8 test): action
extraction, ogni safe-verb shortcut, ogni destructive verb non-shortcut,
guardia che precede lo shortcut, set membership. Tutti pass.
