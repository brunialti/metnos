---
id: 0102
title: Thinking-leak scrubber + Z.bis/Z.quater disambiguation
date: 2026-05-07
status: accepted
area: runtime, planner
related:
  - 0098  # web crawl parallel + (Z)/(Z.bis)
  - 0101  # crawler error_class + soft-fail Z.ter/Z.quater
complements:
  - 0098
  - 0101
---


## Context

Turn live 7/5/2026 18:22 — query «Leggi e confronta
https://en.wikipedia.org/wiki/Metis_(mythology) e
https://invalid-xyz.invalid/». Il `final_message` consegnato all'utente
conteneva il pensiero interno del PLANNER come testo finale, estratto:

```
Per quanto riguarda https://en.wikipedia.org/wiki/Metis_(mythology),
la pagina descrive...

Actually, I'll check if I should use describe_entries to be more
"Metnos-like".
Rule: "DEVI: chiamare describe_entries ... oppure direttamente
final_answer".
Given the user wants a "comparison", and one is dead, a direct
answer is best.
...
Wait, I'll check if I can use describe_entries to get a better
summary.
Actually, I'll
```

Causa root: Gemma 4 26B think=true a volte emette il proprio
reasoning interno nel canale `text` invece che nel canale `thinking`
separato, specialmente sotto ambiguita' di scelta. La regola (Z.bis)
del PLANNER prompt diceva «DEVI describe_entries(...) OPPURE
direttamente final_answer», offrendo due alternative — il modello
ragionava ad alta voce sulla scelta nel canale text.

In parallelo, (Z.quater) soffriva dello stesso problema: «usa il
contenuto degli entries successful per la final_answer» suggeriva
sintesi diretta, che il modello non e' abituato a produrre senza
l'aiuto di describe_entries quando body_text >2000 char.


## Decision

Due interventi concorrenti, entrambi deterministici (§7.9).


### (a) Disambiguazione (Z.bis) e (Z.quater) nel planner prompt

`runtime/prompts/it/planner.j2`. Eliminata l'opzione «oppure
direttamente final_answer». Pattern unificato per entrambe:

```
DEVI: chiamare describe_entries(from_step=N, style="by_relevance",
     context=<domanda originale dell'utente>) come PROSSIMO step.
     Lo step successivo sara' final_answer formulato dal `summary`
     dell'observation di describe_entries.
NON DEVI: emettere final_answer direttamente saltando describe_entries —
     il modello fatica a sintetizzare body_text >2000 char senza
     l'aiuto di describe_entries, e tende a includere il proprio
     reasoning come testo finale (thinking-leak).
```

Niente shim, niente compat path — la regola e' modificata in place
(§7.1 no backward compat in dev).


### (b) Scrubber deterministico anti thinking-leak in `TurnLog.write()`

`runtime/agent_runtime.py`. Nuova funzione `_scrub_thinking_leak(text)`
posizionata vicino a `_scrub_credentials` (gia' esistente — pattern
omogeneo).

Strategia: regex `_THINKING_LEAK_RE` su righe **standalone** (anchor
`^` su ogni riga, line-by-line). Trigger pattern (case-insensitive):

```
Wait | Actually | Let me | I'll | I will | Hmm | Looking at |
One detail: | Final Answer[ construction]: | Wait,? I('|)ll |
Wait,? I should | Now I'll | Actually,? I'll | So,? the answer |
Let me think | I should | Rule:  | Given
```

Le righe matched vengono scartate; le restanti vengono ricongiunte e
collapsed (max 2 newline consecutive).

Vincoli (§2.8 no silent failure):
- Substring legittime in mezzo a paragrafi reali sono **preservate**
  (es. «Il documento dice: 'Wait, this is important'» rimane).
- Funzione idempotente: re-applicarla e' no-op.
- Input non-stringa o vuoto torna invariato.

Invocazione: in cima a `TurnLog.write()`, ramo `final_kind == "answer"`,
PRIMA di qualsiasi prepend (truncation notice, health block,
hallucination notice) e PRIMA dell'append di `_elapsed: Xs_`. Cosi'
nessuna riga aggiunta dal runtime puo' essere accidentalmente
scartata.

Un solo punto di scrubbing — tutti i ~30 path che assegnano
`log.final_message = ...` confluiscono in `write()`.


## Consequences

- Convergenza: 12/12 unit test scrubber PASS, 55/55 smoke invariants
  PASS, regression invariata.
- Latency: scrubber e' regex line-by-line, costo <0.5ms su final_message
  tipico.
- Determinismo: zero LLM call per pulire — match regex esplicito su
  trigger ortogonali.
- Estendibilita': nuovi pattern di leak osservati su altri provider/
  modelli si aggiungono al `_THINKING_LEAK_RE` senza toccare il flusso
  di run_turn.


## Alternatives considered

1. **Filtrare nel provider layer** — accoppia il scrubber al routing
   LLM, ma perderebbe i casi describe_entries/auto_final/cap_steps in
   cui il leak puo' arrivare anche da source path diversi.
2. **Forzare il modello via stop-sequence** — fragile cross-provider,
   non copre i casi in cui il leak e' inframmezzato a contenuto vero.
3. **Rewrite del prompt eliminando ogni ambiguita'** — coperto da (a)
   ma da solo non basta: anche prompt prescrittivi possono triggerare
   leak su query complesse. Lo scrubber (b) e' net runtime-safety.


## References

- CLAUDE.md §6 stile prompt prescrittivo, §7.1 no backward compat,
  §7.9 codice deterministico > LLM, §2.8 no silent failure.
- ADR 0101 Z.ter/Z.quater (riferimento per stile soft-fail).
- Bug live 7/5/2026 18:22 turn «Metis mythology comparison».
- Test: `runtime/tests/test_thinking_leak_scrubber.py` (12/12).
