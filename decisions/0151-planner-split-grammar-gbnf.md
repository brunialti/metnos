---
id: 0151
title: PLANNER split call — selector + args grammar GBNF
date: 2026-05-19
status: implemented (opt-in)
area: runtime | planner | grammar | latency
related:
  - 0078  # HTTP API surface
  - 0099  # runtime perf (reasoning budget dyn)
  - 0102  # thinking-leak scrubber
  - 0133  # grammar-constrained tool_call
  - 0146  # LLM tier consolidation
---

## Context

Bench #H0a (19/5/2026 v3, vedi
[[project_bench_planner_slim_2026_05_19]]) ha validato lo slim (b) tool
schema + (c) section pruning portando lo step PLANNER da **67s → 15.7s
median** (4.3× speedup, accuracy 0.86 → 0.93). Ulteriore margine
identificato dalla decomposizione del costo:

```
prompt token: ~10.9k (system 7.5k + tools 1.6k + history 1.8k)
output token: ~218 (tool_call JSON)
think budget: 512 → effective thinking ~150-300 tok
```

L'observation chiave: il LLM produce per step DUE artefatti distinti che
soddisfano due esigenze diverse:
- **SELECTION**: scegliere UN tool dal pool (decisione, ~5-15 tok output).
- **ARGS FILLING**: riempire gli args del tool scelto (~50-200 tok output).

Entrambi richiedono il prompt giant (per "vedere" tutti i tool e le
istruzioni). Lo splitting in 2 call permette di:
1. Mostrare solo nomi+desc-1-frase al selector (~700 tok totali).
2. Mostrare solo lo schema args del tool scelto all'args filler (~500 tok).

Atteso: 10.9k tok → ~1.2k tok per call × 2 call = 2.4k tok totali
(compounded **-78%** vs SLIM+SMART single-call). Latenza attesa 1-3s
totali.

## Decision

Implementato come **opt-in via env `METNOS_PLANNER_SPLIT=1`**.

### Architettura

Due call sequenziali via `provider.chat_with_tools(..., grammar=<GBNF>)`
con grammar custom per ciascuna fase:

**Step 1 — SELECTOR**:
- `system`: prompt minimo (IT) "Scegli UN tool dalla lista per la query
  utente. Output: SOLO `{\"name\":\"<tool>\",\"arguments\":{}}`. Niente
  prosa." + lista `<name>: <desc-prima-frase-≤200ch>` per ogni tool del
  pool.
- Grammar GBNF: discriminated union dei nomi del pool +
  `arguments:{}` literal:
  ```
  root ::= "{" ws "\"name\"" colon name sep "\"arguments\"" colon "{}" ws "}"
  name ::= "\"find_files\"" | "\"find_dirs\"" | ...
  ```
  Include `final_answer` (synthetic, se step≥2) e
  `request_disambiguation_from_user` (synthetic, se step==1).
- `max_tokens=256`, `think=False` (grammar forza think=False, vedi ADR 0133).
- Output atteso: `{"name":"<tool>","arguments":{}}` parseato come
  `ToolCall`.

**Step 2 — ARGS FILLER**:
- `system`: prompt minimo con SOLO il `name + descrizione + schema args`
  del singolo tool scelto.
- Grammar GBNF: riusa `tool_grammar.generate_tool_grammar([chosen_tool])`
  con allow_final_answer/disambiguation se synthetic. Output forzato
  `{"name":"<chosen>","arguments":{...args full schema-valid...}}`.
- `max_tokens=4096`, `think=False`.

### Output shape (drop-in)

Wrapper `chat_with_tools_split(provider, system, user, tools, history, ...)`
ritorna `ToolUseResult` (stesso shape del provider monolitico):
- `tool_calls=[ToolCall(name=<chosen>, arguments=<filled>, call_id=...)]`
- `in_tokens=sum(selector + args)`, `out_tokens=sum(selector + args)`
- `latency_ms=sum(selector + args)`
- `text=""`, `thinking=""` (think=False imposto)

### Failure modes → fallback monolitico

`SplitFailure` sollevata su:
- `provider_unsupported`: solo `llamacpp` supportato (Anthropic frontier
  non beneficia dello split: latenza dominata da network round-trip).
- `empty_pool`: `tools=[]` non separabile.
- `selector_grammar_empty`/`args_grammar_empty`: edge case.
- `selector_no_tool`/`args_no_tool`: provider non emette tool_call (raro
  con grammar mode, indica payload malformato).
- `selector_off_pool`: chosen name non nel pool (impossibile con grammar
  ben costruita, paranoid check).
- `args_name_mismatch`: args filler ha cambiato `name` rispetto al
  selector (grammar bug).
- `args_validation_failed`: validate_tool_call rifiuta args (required
  missing).

Il caller in `agent_runtime.py` cattura `SplitFailure` e fa fallback
chiamando `provider.chat_with_tools(...)` monolitico con gli stessi
parametri. Nessun double-fail: il monolitico funziona indipendentemente.

### Telemetria

JSONL append-only su
`~/.local/share/metnos/planner_split_telemetry.jsonl`. Per ogni call:
`{ts, step, chosen_tool, sel_lat_ms, args_lat_ms, sel_in/out_tok,
args_in/out_tok, failure_reason}`. Helper `_split_telemetry_persist` in
`agent_runtime.py`, fail-safe (errori I/O silenziati).

## Rationale

### Perche' opt-in e non default ON

1. **Calibration pending (#H0e)**: la decisione "split vs monolithic"
   beneficera' di una threshold di confidence basata su rank distance
   del prefilter + lingua (DEFAULT_LANG) + classe verb. Vedi
   [[planner_split_calibration_design]] per il design 3-level (user
   file > library pre-baked > task scheduler v2 one-shot).
2. **Pre-deploy validation**: bench end-to-end ampio (20+ query × 3 run)
   da fare prima del default-on per confermare zero-regression accuracy
   su query mutating (delete/send/create/move/write/set).
3. **Provider-coupled**: solo `llamacpp` supportato (grammar GBNF) →
   default-on cambierebbe semantica per chi usa altri provider locali.

### Perche' grammar GBNF e non parsing tollerante

Lo split richiede che il selector emetta SOLO un name fra quelli del
pool. Senza grammar, il LLM potrebbe:
- Emettere prosa di reasoning visibile (defeats latency goal).
- Inventare un name (`find_papers_pdf` per "trova i pdf in /tmp").
- Restituire JSON con args inventati (defeats split design).

Grammar GBNF *forza* la struttura `{"name":"X","arguments":{}}` con
X ∈ enum del pool. Parsing tolerant in fallback non risolve
l'invenzione di nomi.

### Perche' history su entrambe le call

Sia selector sia args filler hanno bisogno di history per:
- Selector: capire se siamo al primo step (no history) o post-step
  (history determina se chiamare `final_answer` synthetic).
- Args filler: gli args dipendono dall'observation dello step precedente
  (es. `from_step=1` per pipe entries). Senza history, args filler non
  sa quale step referenziare.

Future optimization: history slim per il selector (solo last_step
summary), full per args. Non in v1.

## Misure smoke (19/5/2026 v3)

Standalone smoke `python3 runtime/planner_split.py` con pool=3 tools:

| query | chosen | selector | args | total |
|---|---|---:|---:|---:|
| "trova i file .py in /tmp" | find_files ✓ | 586ms | 699ms | 1285ms |
| "elenca directory in /opt" | find_dirs ✓ | 350ms | 507ms | 857ms |
| "che ora è" | get_now ✓ | 327ms | 472ms | 799ms |

End-to-end smoke `python3 /tmp/split_wire_single.py` con full catalog
(57 executor):

- Step 1 `find_files`: selector 1382ms + args 1103ms = **2485ms**
- Step 2 `final_answer`: selector 1733ms + args 1202ms = **2935ms**
- Totale LLM time: **~5.4s** per 2-step turn.

Confronto monolithic SLIM+SMART (bench #H0a): 15.7s/step × 2 = ~31s →
**~6× speedup compounded**.

## Consequences

### Positive

- Speedup atteso 5-8× compounded vs SLIM+SMART (latenza turn 30s→5s).
- Accuracy preservata: grammar GBNF garantisce sintassi corretta;
  bench #H0a ha mostrato accuracy MIGLIORE con prompt slim (0.86→0.93).
- Telemetria fine-grained per debug + tuning futuro calibration.
- Fallback graceful: SplitFailure → monolithic (no double-fail).
- Reverse-compatible: env=0 mantiene il path attuale identico.

### Negative

- Latenza in scenario worst-case (selector ok + args fail + fallback)
  = selector + args fail + monolithic ≈ 1.5x monolithic. Mitigato dal
  basso tasso atteso di SplitFailure (smoke 0/3 fail).
- Cache prompt (llamacpp `cache_prompt`) meno efficace: 2 prompt
  distinti vs 1 ripetuto. Marginal hit gia' basso (today_iso + history
  dinamici).
- Telemetry file grows append-only — TODO weekly cleanup (>30d).

### Neutral

- Provider non-llamacpp: split skipped (filter `is_provider_supported`).
  Frontier fallback (16/5/2026) NON usa split — corretto, network RTT
  domina.

## Wire-in details

File modificati:
- `runtime/planner_split.py` — riscrittura completa (227 → ~430 LoC):
  rimosso scaffold `_ToolCall(name, args)` non-compat, sostituito da
  `ToolUseResult`+`ToolCall` shared con `llm_provider`. Aggiunte
  `build_selector_grammar`, `SplitFailure`, `SplitTelemetry`,
  `is_provider_supported`, `_cli_smoke`.
- `runtime/agent_runtime.py` — wrap del singolo `provider.chat_with_tools`
  call (~line 4562) con try/SplitFailure/fallback. Aggiunto helper
  `_split_telemetry_persist`. Path identico se env=0.

Nessuna modifica a:
- `runtime/tool_grammar.py` (riusa `generate_tool_grammar`, `_emit_primitives`,
  `_extract_name`).
- `executors/**` (NO re-sign necessario).
- Schema manifest (NO modifiche).

## Calibration (deferred to #H0e)

Vedi [[planner_split_calibration_design]] per il design:
- `runtime/calibration_check.py::ensure_calibration(lang)` 3-level
  fallback (user file > `runtime/calibration_sets/<lang>.json` > task
  scheduler v2 one-shot).
- Library pre-baked `it.json` + `en.json` committate nel repo per
  baseline.
- Callback `calibrate_planner_split` in
  `scheduler_v2/builtin_callbacks.py`.
- README installazione GitHub sezione "Auto-calibration al boot".

Pre-requisiti per #H0e:
1. Wire-in stabile (questo ADR).
2. Bench corpus 20-30 query × 3 run con env=1 default-on (separato
   da #H0a).
3. Helper `_compute_calibration` (logprob analysis + threshold opt).
4. ADR dedicato (numero TBD post-0151).

## Open questions

- Cache prompt impact su llama-server `--cache-reuse 256` con 2 prompt
  diversi back-to-back. Misurare in bench corpus pre-default-on.
- Synthetic `request_disambiguation_from_user` vs `final_answer`: i
  prompt args filler per loro sono ottimali? Bench dedicato richiesto.
- Verb safety gate: gating del split per verbi mutating (delete/send/
  move/share/write/set/create). Bench #H0a ha mostrato accuracy ok
  anche su `send_messages` + `create_events` ma sample size = 1 run
  per cella. #H0e calibration affrontera' questa dimensione.

## Update v4 (19/5/2026 sera) — fix post-bench #H0c.2

Bench ampio 25q × 2 run × 2 mode = 100 calls (vs smoke 14q×4cfg×3run
del v3) ha mostrato:

- Speedup median: **1.72x** (off=94.6s, on=54.9s) — molto inferiore
  al **14-22×** del smoke. Causa: smoke beneficia cache prompt warm,
  bench ampio mix di query mutating con state dependency.
- Accuracy regression: off=96% (48/50), on=90% (45/50) = **-6 pp** in
  on-mode. 3 query regredite:
  1. "sposta i file .pdf vecchi più di 6 mesi in /tmp/old" (mutating)
  2. "cancella i file .tmp in /tmp" (mutating)
  3. "salva nota: spesa supermercato 35€" (write)

Indagine cause strutturali e fix:

### Fix 1 — Selector prompt pipeline-aware

`_build_selector_prompt` originario era minimo: lista name + desc breve,
nessuna guidance su come usare history. Su step≥2 il LLM riceveva
history dal provider ma non sapeva come interpretarla → ripeteva tool
dello step precedente (es. find_files due volte invece di find→move).

Fix: aggiunte regole §6 prescriptive nel prompt:
```
DEVI: se la conversation ha step precedenti, scegli il tool che FA
PROGREDIRE la pipeline (dopo find_files → move/delete/compress/send;
dopo get_inputs → l'azione vera; dopo read_messages →
describe/filter/move).
NON DEVI: ripetere lo stesso tool dello step precedente quando
l'osservation e' gia' `ok:true`.
```

Risolve Q1 regression (0/2 → 3/3 ok post-fix).

### Fix 2 — Soft-gate verb-canonical (B.5)

`agent_runtime._check_top_k_affinity_jaccard` originario usava solo
overlap di tokens raw. Su query lunga ("salva nota: spesa supermercato
35€") i token irrilevanti diluivano l'overlap sotto threshold 0.3,
permettendo a `request_new_executor` di passare nonostante write_files
fosse nel top-K.

Fix: lookup tramite `prefilter._VERB_TO_CANONICAL` (mappa "salva"→
"write", "cancella"→"delete"). Se la query contiene un verbo
canonical E il top-K ha un tool che inizia con quel verbo, ritorna
match forte score=1.0 PRIMA del calcolo overlap. Evita dilution.

### Fix 3 — Executor delete_files creato

Causa Q2 "cancella i file .tmp" → PLANNER sceglieva `delete_dirs`
perche' `delete_files` **non esisteva** nel catalogo. Gap del vocab.
Aggiunto `executors/delete_files/{manifest.toml, delete_files.py}` +
backend `runtime/backends/files/local.py::delete_files` (backup blob
§2.3 + reverse_pattern `restore_blob_backup` + safety §2.9 rifiuta
dir/system file/path fuori scope).

### Convergence test post-fix

3 query × 3 run con env=1 forced: **9/9 answer**. Errore=0 × 3 run
consecutivi.

### Implicazione per default-on flip

Bench ampio mostra:
- Speedup realistic ~1.72× (non 14-22× del smoke).
- Accuracy regression chiusa dai 3 fix strutturali sopra.
- Flip default-on `METNOS_PLANNER_SPLIT=1` ora technicamente sicuro
  ma il guadagno ridotto rende meno urgente. Decisione user pending:
  i 3 fix sono robusti su corpus + livello catalog gap chiuso.
