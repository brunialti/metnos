---
id: 0135
title: Grammar pool extensions (final_answer synthetic + truncated recovery + from_step gating)
date: 2026-05-15
status: accepted
area: grammar | runtime | planner
related:
  - 0133  # constrained grammar tool_call GBNF
complements:
  - 0133
---

## Context

ADR 0133 ha introdotto la grammar GBNF discriminated-union che forza il
PLANNER LLM a emettere `{"name":..., "arguments":...}` ben formato.
Convergenza 50%→100%, latency 70s→41s. Tre problemi emersi dall'uso
in produzione:

1. **No final_answer naturale**: grammar mode rifiuta il branch
   "no tool_call → text final" perche' lo schema force richiede
   sempre un tool_call. Risultato: il LLM post-pipeline
   `read_X → describe_entries(ok)` non puo' emettere un final
   testuale; fallback su describe_entries duplicato → `auto_final_on_duplicate`
   → final scarno "read_X: completato (N elementi). <titolo>".
   Battery e2e pre-fix 6/9 PASS; turn 15/5 ROCm/mail/time tutti FAIL.

2. **JSON truncated da llama.cpp**: in grammar-mode llama-server
   termina prematuramente (EOS prima della chiusura `}`), lasciando
   `{"name": "list_tasks"` non parsabile. `json.loads` falliva →
   tool_calls vuoto → text finale (raw JSON visibile all'utente).
   Bug riprodotto 4 turn consecutivi.

3. **filter_entries al primo step**: `_UNIVERSAL_HELPERS` iniettava
   sempre filter/sort/compute/classify/group/describe_entries nel
   pool. Tutti richiedono `from_step` su lista preesistente. Al
   primo step il LLM li sceglieva con `from_step=1` riferendo step
   inesistente → fail → loop_break. Esempio live: "fissa appuntamento
   mercoledi mattina dopo le 9" → filter_entries x4 → loop.

## Decision

Tre estensioni alla grammar pipeline 0133:

### (A) `final_answer` synthetic tool

`runtime/tool_grammar.py::generate_tool_grammar(allow_final_answer=False)`:

```
pairFinalAnswer ::= "\"final_answer\"" sep "\"arguments\"" colon
                   ("{" ws "\"message\"" colon jsonStr ws "}")
```

Aggiunto alla discriminated union quando `allow_final_answer=True`.
`validate_tool_call(allow_final_answer=True)` accetta required=["message"]
type string.

Runtime wiring `agent_runtime.py`:
- `allow_final_answer=(step_num >= 2)`: lo step 1 forza esecuzione
  di un producer (no early-exit prematuro su query banali).
- Subito dopo decodifica del tool_call: `if chosen_name ==
  "final_answer": log.final_message = arguments.message; return log`.

### (B) Parser truncated recovery

`runtime/llm_provider.py::_parse_tool_call_tolerant`:
1. (a) `json.loads(t)` strict (path esistente)
2. (a.bis) **NEW**: se `t.startswith("{")` ma json fail → regex
   `"name"\s*:\s*"([id])"` estrae nome. Best-effort `arguments`
   parziale con regex `"arguments"\s*:\s*(\{.*?\})\s*(?:\}|$)`.
   Default `args={}` per tool con `required=[]`.
3. (b) Gemma 4 `<|tool_call>` template (path esistente)

General-purpose §7.3: copre qualsiasi tool name, non whitelist.

### (C) Pool gating al primo step

`agent_runtime.py` nuovo frozenset `_FROM_STEP_HELPERS = {
filter_entries, sort_entries, compute_entries, classify_entries,
group_entries, describe_entries }`. Al primo step (`step_num == 1`):
- non iniettati dagli universal helpers
- filtrati dai candidates anche se il prefilter li ha inclusi

`undo_last_turn` resta sempre disponibile (azione utente diretta
indipendente da observation precedenti).

## Consequences

- Battery e2e 22/22 PASS (era 6/9 pre-fix, 19/22 post-fix solo A+C,
  22/22 con tutti i tre).
- 51+12=63 unit test grammar (44 originali + 7 synthetic + 12 parser
  recovery). Tutti PASS.
- Bug pre-grammar (parsing fragile, prosa al posto di tool_call)
  resta risolto: grammar GBNF copre il 100% delle scelte tool;
  recovery copre solo l'edge case "EOS prematuro" del provider
  llama-server.
- Pool gating §4.2 generalizzato: il pattern "from_step su step 1"
  non e' piu' raggiungibile dal LLM per i 6 *_entries helper.

## Notes

- final_answer synthetic NON e' un executor del catalog: il runtime
  intercetta il name speciale e chiude il turno. Niente subprocess,
  niente sign required.
- Il fix C e' general-purpose §7.3: applicabile a qualsiasi helper
  futuro che richieda `from_step` (aggiungi al frozenset).
- Cap_expand prepend skip: in `TurnLog.write()`, se l'ultimo step e'
  `final_answer`, skip dei notice di truncation. Il LLM ha gia'
  formulato un final consapevole, prependere ridonda.
