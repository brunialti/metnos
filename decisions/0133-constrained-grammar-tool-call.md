---
id: 0133
title: Constrained generation via GBNF per tool_call (anti-thinking-loop)
date: 2026-05-14
status: accepted
area: runtime
related:
  - 0114  # admission policy 4 layers (questa estende con grammar-gate)
  - 0123  # importer skill agentskills.io (provider qualifier nel filter)
  - 0129  # intent-implicit (dialog get_inputs piu' complesso post-pattern)
complements:
  - 0099  # runtime perf (compatibile: grammar e' kwarg dedicato)
---

## Context

Il PLANNER (Gemma 4 26B think=true via llama-server `b540-5755a100c`) sul protocollo
tool_call nativo soft-constrained mostrava 3 patologie ricorrenti su query non-banali:

1. **Thinking loop** — il modello entra in fase di reasoning lunga,
   torna a emettere prosa al posto di tool_call strutturato → PLANNER
   termina senza azione.
2. **Mix-match name/args** — emette `{"name":"get_inputs", "arguments":
   {kind:"choice", from_step:1, ...}}` (struttura di `filter_entries`,
   non di `get_inputs`).
3. **Escape-hatch arbitrari** — sceglie `request_new_executor` o
   `request_location_from_user` su query con tool canonical ovvio
   ("crea cartella /tmp/x" → `request_location_from_user`).

Bench iniziale (n=10 query rappresentative, mix simple/medium/complex/ambiguous):
- convergenza 50–70%
- regression cluster su `complex_propose_silvia` (propose+notify pipeline)
  → loop_break dopo 3× `get_inputs` malformato

La query critica e' la convergence proof del refactor ADR 0129:
"proponi 3 orari ... dopo la scelta mandami una email con la scelta".
Sotto soft-constrained protocol il PLANNER non chiudeva la pipeline in 100% dei tentativi.

Vincoli:
- §7.3 soluzioni generali, non hardcoded
- §7.9 deterministico > LLM
- §7.1 no back-compat (pre-1.0)
- nessuna dipendenza nuova (no jsonschema-tools esterni)

## Decision

Adottata la **constrained generation via GBNF** (llama.cpp) sul protocollo
tool_call con quattro layer:

1. **Grammar generator deterministico** (`runtime/tool_grammar.py::generate_tool_grammar`)
   - input: pool `tools_for_step` (Executor object o dict OpenAI)
   - output: stringa GBNF che vincola il JSON `{"name":..., "arguments":...}`
     a un solo tool del pool con args conformi al suo `args_schema`
   - **Discriminated union**: `root ::= "{" "name": (pairTool1 | pairTool2 | ...) "}"`
     dove `pairToolN ::= "\"toolN\"" sep "\"arguments\"" colon (argsToolN)`.
     Lega `name` a `args` per tool — il LLM non puo' mixare la struttura
     di un tool con il nome di un altro.
   - **Schema-aware recursive (B2)**: per `array of object` e nested `object`
     con `properties` tipizzate, emit sub-rule `{tool}ObjD{depth}I{idx}`
     (camelCase, prefix tool per univocita' cross-tool). Cap
     `_MAX_RECURSION_DEPTH = 4` poi fallback `jsonObject`.
   - **Naming camelCase**: workaround bug llama-server `b540-5755a100c` —
     rule names con underscore sono ignorati silenziosamente dal parser GBNF.
     `_sanitize_rule_name` converte tool_name e key in PascalCase.
   - **Dependency closure**: solo le primitives (`jsonStr`, `jsonNum`, ...)
     effettivamente referenziate sono emesse. Le primitives unused interferiscono
     col matching (bug llama-server osservato in convergence test).

2. **Provider wiring** (`runtime/llm_provider.py::LlamaCppProvider.chat_with_tools`)
   - nuovo kwarg `grammar: str | None`. Quando passato:
     - bypass del campo `tools` (llama-server rifiuta `grammar + tools`)
     - bypass del chat_template Gemma `<|tool_call>call:NAME(args)<tool_call|>` —
       history riscritta in messaggi assistant/user testuali, niente `role=tool`
     - `enable_thinking=False`, `reasoning_budget=0`
     - parser tolerant `_parse_tool_call_tolerant` accetta JSON puro
       o formato Gemma 4 — preserva back-compat soft-constrained mode

3. **Pool filter** (`runtime/tool_grammar.py::filter_pool_for_grammar`)
   - funzione pura testabile, input `tools + user_query + proximity_markers`,
     output `(pool_filtrato, [esclusi])`
   - escape-hatch esclusi contestualmente quando MANCA il marker semantico:
     - `request_new_executor` se >=3 canonical (escape globale)
     - `request_location_from_user` senza marker prossimita'
     - `undo_last_turn` senza marker undo ("annulla", "rollback", ...)
     - `<verb>_<obj>_<provider_suffix>` senza marker provider
       (lookup `_PROVIDER_SUFFIX_MARKERS = {"_google_workspace": ("google", "drive", "gmail", ...)}`)
   - match **word-boundary regex** (`\bmarker\b`) per evitare falsi
     positivi tipo `qua` ⊆ `qualcosa` o `vicino` ⊆ `vicinato`
   - safety: se filter azzera il pool, ripristina originale

4. **Post-decode validator** (Strategia 3, `runtime/tool_grammar.py::validate_tool_call`)
   - top-level required-only check: l'executor stesso ha messaggi piu'
     specifici sul deep schema mismatch
   - su fail: tool NON eseguito (no subprocess), error iniettato in
     `history_for_llm` come `role=tool` content → il LLM al prossimo step
     vede il messaggio e corregge args
   - `consecutive_blocked` previene loop > LOOP_BREAK_THRESHOLD

5. **Opt-in via env**: `METNOS_GRAMMAR=1` nel drop-in
   `~/.config/systemd/user/metnos-http.service.d/grammar.conf`. Default off
   per ora — promotion a default dopo soak settimanale.

### Bench (10 query rappresentative, n=1 per query)

| versione | convergenza | first_tool_match | mean latency | note |
|---|---|---|---|---|
| baseline (no grammar) | 50% | n/a | 70s | thinking-loop su 5/10 |
| grammar v1 (B2 base) | 80% | 89% | 25s | bug duplicate sub-rule cross-tool |
| grammar v3 (post fix) | 80% | 100% | 28s | location escape su 1/10, complex regr 1/10 |
| grammar v4 (discriminated) | 80% | 100% | 35s | provider google_workspace su 1/10 |
| grammar v5 (provider filter) | 90% | 100% | 35s | undo escape su 1/10 |
| grammar v6 (undo filter) | **100%** | **100%** | 42s | tutti 10/10 ✓ |
| grammar v7 (refactor estratto) | **100%** | **100%** | 41s | regression-test |

## Alternatives considered

**A. Fine-tuning Gemma 4 su corpus tool_call Metnos**
Risolve thinking-loop con weights specifici. Costi: dataset 1k+ esempi,
training infrastructure, regression su altre capacita'. Tempo: ~1 mese.
Rejected: troppo invasivo per il quality-floor attuale.

**B. Switch a provider frontier (Sonnet 4.6) per il PLANNER**
Sonnet tool-use protocol e' molto piu' stabile. Costi: ~$0.30/turn vs
$0.001 locale, dipendenza esterna, latency rete. Rejected: §10.3 self-hosted default.

**C. Soft constraints via system prompt heavy**
Aggiungere "DEVI emettere SOLO JSON, NIENTE prosa" + few-shot 6×. Costi:
prompt 25K → 35K token, latency +30%, regression random su query rare.
Rejected dopo bench: tagliava convergenza 50% → 70%, non sufficiente.

**D. Retry loop con fallback Sonnet**
Locale finche' funziona, fallback frontier su loop_break. Costi: hidden
dependency frontier, latency p95 esplode. Rejected: layer "magico" non
testabile.

**E. Constrained generation via JSON Schema (xgrammar / outlines)**
Stessa idea ma via libreria esterna. Costi: dipendenza nuova, JSON Schema
non e' identico a GBNF (un sottoinsieme), modelli supportati limitati.
Rejected: GBNF e' supportato nativo da llama.cpp, no nuove dipendenze.

**F. GBNF nativo llama.cpp (questa decisione)**
Scelta: zero dipendenze esterne, controllo totale sulla grammar generata,
deterministico §7.9, applicabile a qualsiasi pool senza training.

## Consequences

**Easier:**
- Pipeline propose+notify ADR 0129 robusta 100%
- Convergenza bench 50% → 100% (n=10 corpus rappresentativo)
- Latency mean dimezzata vs baseline (70s → 41s) — thinking-loop eliminato
- Test pool filter deterministico (43 unit test verdi)
- Generalizzazione provider qualifier (lookup table, no hardcoding)

**More expensive:**
- Schema TOML degli executor diventa source of truth piu' stretto:
  manifest incompleto = grammar fallback su `jsonObject` → grammar non
  protegge → regression. Workflow: ogni nuovo executor con nested
  object/array DEVE dichiarare `[args.properties.<prop>.items.properties.*]`.
  Esempio: `get_inputs.dialog.items` ora ha 8 sub-properties dichiarate.
- llama-server bug workaround (camelCase rules, no underscore, no unused
  rules) — fragile a regression upstream. Mitigato da test
  `test_no_underscore_in_rule_names` + `test_primitives_only_referenced_emitted`.

**Doors closed:**
- Difficile usare grammar+tools combinato (llama-server rifiuta) → la
  grammar e' attiva SOLO in grammar-mode. Niente fallback "grammar
  rilassata su top-level + tools per il resto".
- Models non-llama.cpp (Ollama nativo, Anthropic, OpenAI) NON usano
  grammar — passa `grammar=None` automatico. ADR resta locale per ora.

**Doors opened:**
- Estendere `_PROVIDER_SUFFIX_MARKERS` a futuri provider importati
  (telegram_bot, notion, ...) → lookup table O(1).
- Iterare grammar generator su feature 17.0+: pattern object con
  `oneOf` discriminato (futuro: union types per tool flessibili).
- Promotion grammar a default dopo soak (`METNOS_GRAMMAR=1` su tutti i
  drop-in dopo 7gg verdi).

**Work items spawned:**
- Soak settimanale: monitorare turn /admin/turns + alert su loop_break.
- Doc HTML for-dummies (Phase F8): `docs/it/architecture/grammar.html`
  + EN bridge — TODO.
- F6 Strategia E (loop-detect safety net) — opzionale, attualmente
  bench 0 errori.

---

**Riferimenti**

- Generator: `runtime/tool_grammar.py` (B2 recursive + discriminated union)
- Provider wiring: `runtime/llm_provider.py::LlamaCppProvider.chat_with_tools`
- Pool filter: `runtime/tool_grammar.py::filter_pool_for_grammar`
- Validator: `runtime/tool_grammar.py::validate_tool_call`
- Test: `tests/runtime/engine/test_tool_grammar.py` (43 unit test)
- Bench: `runtime/bench_grammar.py` (10 query × 2 modes)
- Env: `METNOS_GRAMMAR=1` in `~/.config/systemd/user/metnos-http.service.d/grammar.conf`
- Bug llama-server: `b540-5755a100c` underscore rules ignorate, unused rules interferiscono
