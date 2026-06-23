# ADR 0177 — Analisi architetturale del motore di turno (as-is → smells → target)

- **Stato**: PROPOSTA / da concordare con Roberto PRIMA di rifattorizzare.
- **Data**: 2026-06-22.
- **Scope**: TUTTA la pipeline di `run_turn`, non solo il compound planning (quello è il SOTTOINSIEME trattato in [[project-compound-planning-refactor]]).
- **Input**: misure e analisi 3-agenti 22/6 (coverage/latenza, inventario guard) + mappatura 5 sotto-sistemi (intake, cache, executor, sintesi, trasversali) via agenti 22/6 sera. Numeri verificati a mano (LOC, def, ADR).
- **Metodo onestà**: i fatti misurati sono marcati ✓; le ipotesi/smell non ancora provati sono marcati ⚠ e NON vanno trattati come verità finché non testati.

---

## 1. Mappa as-is — la spina dorsale

`runtime/agent_runtime.py::run_turn` (riga 5342, su un file di **9396 LOC**) è il cuore. Sotto-sistemi:

```
run_turn()
 ├─ [1] INTAKE / short-circuit  (first-match-wins, ~12 decider sequenziali)
 │     credential-extract → admin-cmd → strato-3 escalation → fast-path L0
 │     → seed-step URL → scheduling/ricorrenza → resume-scratchpad
 ├─ [2] PLANNING — DUE PATH che si sovrappongono
 │     (a) DECOMPOSER deterministico  compound_decomposer.decompose_query (:5759, ≥2 verbi)
 │     (b) ENGINE LLM                 _try_engine_v2 → engine/dispatch.run_turn (:5884)
 │     (legacy ReAct planner :5977, default OFF, ~3000 LOC morte-ma-presenti)
 ├─ [3] PROPOSER          proposer.py / proposer_metis.py / proposer_v3.py
 │     + intent_extractor.py + prefilter.py + routing_pool.py
 ├─ [4] CACHE             L0 fastpath.py (fastpaths.sqlite) · L1 autopath.py (autopath.sqlite)
 ├─ [5] GUARD/FINALIZE    dispatch.py — 11 guard + orchestratore (righe 257–1336)
 ├─ [6] ESECUZIONE        executor.py::Executor.run  (SHARED da tutti i path)
 ├─ [7] SINTESI FINALE    terminator / synth LLM / describe / output_policy / zero-result
 └─ [8] TRASVERSALI       i18n · messages · detection_lexicon · reverse_patterns
                          · platform_policy · telemetria/cost
```

### LOC verificati (✓)

| File | LOC | Note |
|---|---|---|
| `runtime/agent_runtime.py` | 9396 | contiene run_turn + intake + ~3000 LOC legacy morte |
| `runtime/engine/dispatch.py` | 1916 | 11 guard (257–1336) + orchestratore + integrazione cache |
| `runtime/engine/executor.py` | 1674 | Executor.run = ~454 LOC, 9 responsabilità |
| `runtime/engine/fastpath.py` | 735 | L0 |
| `runtime/engine/autopath.py` | 584 | L1 |
| `runtime/engine/proposer.py` | 531 | SimpleProposer base |
| `runtime/engine/proposer_metis.py` | 532 | MetisProposer (prod) |
| `runtime/engine/proposer_v3.py` | 79 | drop-in subclass pura ✓ (zero contaminazione v2) |
| `runtime/compound_decomposer.py` | 614 | path deterministico |
| `runtime/intent_extractor.py` | 191 | LLM-based |

### Dati di esercizio (✓, misurati 22/6)

- **Coverage planning**: DECOMPOSER copre **12%** dei compound, ENGINE **88%**.
- **Latenza planning** (isolata, cache esclusa): decomposer **~1.5ms** vs engine **~17.4s** (mediana: intent + pool + propose wise + guard).
- **Latenza end-to-end** (`run_turn` reale, query «leggi eventi…estrai…crea foglio»):

  | Scenario | Turno completo |
  |---|---|
  | Decomposer (sempre) | ~1.2 s · `final_message="1"` (bug) |
  | Engine **cold** (cache-miss, 1ª volta/novel) | ~16–24 s · messaggio corretto |
  | Engine **warm** (cache-hit L0/L1) | ~1.1–1.6 s · messaggio corretto |

  **Chiave**: il costo engine è **solo cold-start**. La cache L0/L1 (SQLite su disco, aging 30gg/grace 14gg/LRU 500, pruned solo dal reaper notturno — NON per-richiesta) lo azzera dalla 2ª occorrenza; non viene erosa dal traffico (ogni hit rinfresca `last_used`). Il decomposer è di fatto un **mitigatore di cold-start** per il 12% di forme che gestisce.
- **Guard**: **11 funzioni** in dispatch (righe 257–1336), ≈ 60–70% del file; **12 correttori atterrati in 5 giorni**, ognuno su un caso-banco.

---

## 2. Smell architetturali (ordinati per gravità)

### S1 — Due path di planning che si sovrappongono e divergono ✓
Decomposer ed engine decompongono entrambi un compound in step (StepSpec, `from_step`, `derive_tool_name`, clausola extract): **duplicazione reale**. Fino a P1 (commit `f0b96f2`) il decomposer **saltava** i guard → i due path divergevano sulla stessa query. Sintomo storico: il bug `extract_entries.fields` viveva nel DECOMPOSER ma banco/unit test coprivano solo l'ENGINE (blind-spot di misura). P1 ha instradato il decomposer per `_apply_deterministic_structure_guards`; resta aperta la decisione di fondo (D1).

### S2 — La batteria di guard è il vero planner compound ✓⚠
✓ 11 guard (righe 257–1336) ri-derivano deterministicamente OGGETTO, VERBO, ORDINE e ARGS che il proposer LLM *dovrebbe* emettere. ⚠ Lettura: lo strato deterministico **compensa un proposer debole sotto MTP** invece di vincolarne la generazione. Tre guard «produttore mancante» si sovrappongono per livello: `_enforce_missing_clauses` (verb-level), `_enforce_missing_objects` (object-level), `_align_foreign_producers_v3`. ⚠ La pipeline è **ordine-dipendente** (load-bearing: `_ensure_extract_clause` PRIMA di `_conform_to_intent_order`; `_fill_clause_args` DOPO ordine stabile) ma l'ordine non è documentato come contratto.

### S3 — Idempotenza dei guard sugli hit cache asserita, non provata ⚠
ADR 0174: i guard girano su OGNI hit L0/L1 (dispatch ~1629, ~1677) prima dell'esecuzione. Il commento (~1626) dichiara «idempotente + no-op su mono» come **asserzione**. Non risulta un test mirato `guard(guard(fw)) == guard(fw)`. Rischio: un piano cachato che attraversa i guard a ogni hit potrebbe mutare in modo non-fixed-point e divergere dalla forma cachata. **Gravità alta perché silenzioso.**

### S4 — Executor.run è un orchestratore pesante (9 responsabilità in 454 LOC) ✓⚠
✓ Una funzione fa: loop step + branching/skip + risoluzione di **9 tipi di placeholder in cascata** (from_step → stepref → filler → runtime → backend → self_recipient → calendar → query-canonical → scope) + invoke + remediation/vaglio + rendering finale + synth LLM fallback + error handling. ⚠ I resolver sono cablati separatamente (try-except isolati, non una catena visibile); l'ordine è critico ma non dichiarato.

### S5 — Cinque percorsi divergenti producono il messaggio finale ✓⚠
✓ Il `final_message` può nascere da: (1) terminator zero-result deterministico → `MSG_NO_RESULTS` i18n; (2) template del proposer (`_render_final_message`); (3) synth LLM (`_synthesize_final_from_steps`); (4) `describe_entries` builtin; (5) `output_policy` ranked/gallery. ⚠ Nessuna fonte unica → mutazione del messaggio sparsa, i18n non garantito (il template letterale del proposer NON passa da `MSG_*`), e il bug «final_message=1» (decomposer write-injection → result terso → synth rende il conteggio) è un sintomo di questa frammentazione. ⚠ Il blocco render-degenere→synth è duplicato in due punti (executor ~1255–1268 e ~1662–1673).

### S6 — Intake a 12 decider first-match-wins senza contratto d'ordine ⚠
Mappati ~12 meccanismi di short-circuit (admin, strato-3, fast-path, seed-step, scheduling, resume, decomposer, engine). ⚠ Ordine-dipendenza non documentata; branch sovrapposti (scheduling-parse vs tasks-marker per skippare il decomposer; resume-scratchpad vs seed-step pre-popolano entrambi lo scratchpad); `log.write()` pre-return ripetuto ~8× senza helper; flag `METNOS_*` sparsi senza registry. NB: parte di questi smell sono ipotesi dell'agente, da confermare leggendo il codice prima di agire.

### S7 — Legacy planner ReAct morto-ma-presente ✓
~3000 LOC in agent_runtime, `METNOS_PLANNER_LEGACY=0` di default (riga 5977). Viola §7.1 (no backward-compat in dev). Peso morto su un file già di 9396 LOC.

### S8 — Debito lessici §7.3 (hardcoding IT+EN inline) ✓
Lessici di detezione ancora hardcoded fuori da `detection_lexicon`: `prefilter._VERB_TO_CANONICAL`/`_OBJECT_HINTS`/stopwords/estensioni; `compound_decomposer._FIELD_STOP`/`_FIELD_CUT_PREP`/`_FORMAT_HINTS`; resolver vari. Non traducibili per lingue nuove; regressione §7.3 reintrodotta in `c6269de`. `detection_lexicon` + seed esistono e funzionano: la migrazione è incrementale e a regressione-zero.

---

## 3. Principio di fondo (la tensione da risolvere)

§7.9: «codice deterministico > LLM se equipotente, equiefficace o se LLM troppo complesso». Il motore oggi vive una **tensione**: planner deterministico (decomposer) accanto a proposer LLM + 11 guard deterministici che lo correggono a valle. La domanda non è «determinismo o LLM» ma **dove mettere il confine**:

- **Vincolare la GENERAZIONE** (grammar-on-args GBNF: l'LLM emette gli arg required e l'oggetto/verbo corretto) → riduce i guard a valle.
- **Correggere a VALLE** (lo stato attuale: 11 guard) → robusto ma cresce per accumulo (un guard per caso-banco).

Il target propone una **linea**: la generazione vincolata cattura ciò che è esprimibile come grammatica (forma, arg required, oggetto); i guard restano SOLO per ciò che richiede contesto-turno (store-field-refs, ordine derivato dall'intent compound). Vedi D2.

---

## 4. Target to-be (proposta)

**Invarianti da preservare**: determinismo del routing (seed fisso §11), i guard come rete di sicurezza §7.3, onestà §2.8, output i18n §11.

| # | Target | Risolve |
|---|---|---|
| T1 | **Un solo path di planning compound** con il decomposer come PRE-STADIO che alimenta i guard (NON un secondo planner). Confine pulito: decomposer = fast-path strutturale; guard+finalize = stadio unico condiviso. | S1 |
| T2 | **Grammar-on-args** (GBNF) per vincolare la generazione del proposer a emettere arg required + oggetto/verbo del pool. I guard residui SOLO per logica contesto-turno. | S2 |
| T3 | **Consolidare i 3 guard «produttore mancante»** in uno, e dichiarare l'ordine della pipeline come contratto esplicito + test di sequenza. | S2 |
| T4 | **Test di idempotenza dei guard** sugli hit cache (`guard(guard(fw))==guard(fw)` su un corpus di piani reali) PRIMA di qualsiasi refactor cache. | S3 |
| T5 | **Una sola fonte del messaggio finale**: un Finalizer con strategia esplicita (zero-result → template → synth → describe → policy), de-duplicato, i18n-garantito. | S5 |
| T6 | **Slimmare Executor.run**: estrarre la catena resolver in un registry ordinato e dichiarato; separare esecuzione da finalizzazione. | S4 |
| T7 | **Rimuovere il legacy planner** (~3000 LOC) — §7.1. | S7 |
| T8 | **Migrare i lessici a `detection_lexicon`** a regressione-zero, incrementale. | S8 |

---

## 5. Piano di migrazione incrementale (ordinato per ROI/rischio)

> Principio: ogni passo è indipendente, gated (suite 2828/0 + routing 29/29 v3), e committabile da solo. Nessun big-bang.

1. **M0 — Sicurezza prima (cheap, alto valore)**: T4 (test idempotenza guard) + esplicitare l'ordine-contratto della pipeline guard (T3 parte test). Chiude il rischio silenzioso S3 senza toccare comportamento.
2. **M1 — Pulizia morta**: T7 (rimuovi legacy planner) + de-duplica il blocco render-degenere→synth (S5 parziale). Riduce 9396→~6000 LOC agent_runtime, zero rischio comportamentale.
3. **M2 — Unifica sintesi finale**: T5 (Finalizer unico). Sana «final_message=1» e l'i18n non garantito.
4. **M3 — Consolida guard**: T3 (fondere i 3 «produttore mancante», resolver-registry).
5. **M4 — Vincola la generazione**: T2 (grammar-on-args, spike + misura: quanti guard si possono spegnere). Il passo più incerto → prototipo dietro flag, misurato su banco.
6. **M5 — Confine planning**: T1 (decomposer come pre-stadio; decisione D1 informata da M4).
7. **M6 — Slim executor**: T6 (resolver-registry, separazione esecuzione/finalizzazione).
8. **M7 — Debito lessici**: T8 (migrazione a detection_lexicon, a lotti per concept).

---

## 6. Decisioni aperte per Roberto (servono prima di M4+)

- **D1 — DECISO 2026-06-22 → RITIRA il decomposer (ipotesi 2).** Roberto: «favorevole a unificare e rinunciare al decomposer per maggior qualità; se si mostra inutile DEVE essere eliminato e il codice ripulito». Motivazione: la latenza engine è solo cold-start (ammortizzata da cache robusta), §7.9 non protegge il decomposer (non equipotente: output peggiore), e si elimina la classe di divergenze alla radice. **Bake avviato** con `METNOS_DECOMPOSER=0` (flag `agent_runtime.py:5756` + drop-in prod, reversibile). **Trigger 2026-06-29**: se inutile su traffico reale → cancella `compound_decomposer.py` (614 LOC) + flag + dedup, engine-only permanente. Vedi [[project-decomposer-retirement-bake]].
- **D2 — Grammar-on-args**: §11 dice «grammar-on-verbs accantonata (enforce+align bastano)». Riapro lo spike SOLO sugli ARGS (M4, misurato) o resto sul correggi-a-valle?
- **D3 — Profondità**: faccio tutto il piano M0→M7 o mi fermo a M0–M3 (sicurezza + pulizia + sintesi, basso rischio) e ridiscutiamo M4+?

---

## Riferimenti

- [[project-engine-architecture-review]] (brief + scaffolding), [[project-compound-planning-refactor]] (sottoinsieme + P0/P1 fatti).
- ADR 0174 (cache-discipline guard su hit), 0175 (engine v3 compound), 0176 (mail-read completeness).
- Prod = `f0b96f2`; gate suite 2828/0, routing 29/29 (v3).
