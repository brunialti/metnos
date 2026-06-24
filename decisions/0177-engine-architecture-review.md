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
 │     (planner ReAct :5998, ~3000 LOC — NON morto: path foto-upload + resume-dialog, S7)
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
| `runtime/agent_runtime.py` | 9396 | contiene run_turn + intake + ~3000 LOC «legacy» (NON morte: foto+resume, S7) |
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

### S7 — Legacy planner ReAct: **NON è morto** (correzione 23/6) ⚠
~3000 LOC in agent_runtime. **La premessa iniziale «codice morto» era ERRATA** (verificato 23/6, agente M1). Il gate `METNOS_PLANNER_LEGACY=0` (riga 5985, indent 8) è DENTRO il blocco `if not _ref_images_for_prompt and not resume_with_scratchpad:` (5628, indent 4); il corpo «legacy» (5998, indent 4) gira DOPO quel blocco. Quando ci sono **foto allegate** (`reference_images` → l'engine v3 disabilitato via `_bypass_for_uploads`, 5889) o un **resume-dialog** (`resume_with_scratchpad`), il blocco 5628 — gate incluso — è saltato e il controllo cade nel «legacy» (5998), che è quindi il path **VIVO** per: (a) routing foto-upload (iniezione step `@uploaded` 6516-6558 + blocco prompt foto 5582-5595 → `find_images_indices`); (b) resume scratchpad (6383-6429). `engine/` non ha equivalente (zero `@uploaded`/`reference_image`/`resume_with_scratchpad`). Test live `test_run_turn_reference_images` passa attraverso questo codice. → **Non rimuovibile come «codice morto»**; serve prima il porting sull'engine v3 (NB: `_try_engine_v2` è solo il nome della funzione dispatch; l'engine attivo è v3). (§7.1 vale ancora, ma il debito è «migra poi rimuovi», non «cancella».)

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
| T7 | **Migrare upload+resume sull'engine v3, POI rimuovere il legacy** (~3000 LOC). NON è una cancellazione: il legacy è vivo (S7). (NB: `_try_engine_v2` è solo il nome legacy della funzione dispatch; l'engine attivo è v3, non c'è un «v2» separato.) | S7 |
| T8 | **Migrare i lessici a `detection_lexicon`** a regressione-zero, incrementale. | S8 |

---

## 4.bis Deep-analysis legacy planner + sicurezza (23/6, 3 agenti + verifica diretta)

Analisi a fondo del blocco legacy `run_turn` (5349→9356 = **4008 LOC**, di cui ~3359 legacy) in vista dell'assorbimento (sessione dedicata). Esiti:

**S9 (NUOVO) — il path engine di PRODUZIONE girava SENZA vaglio. ✓ CHIUSO 23/6.**
`_try_engine_v2` (agent_runtime ~5126) non passava `vaglio_judge` a `dispatch.run_turn` → `Executor.vaglio=None` → il check (executor ~1646) era inerte; il legacy invece chiama `judge()` su ogni step. Impatto verificato: BASSO ma reale — il *giudice* col default non blocca quasi nulla, la *shell-guard* è inapplicabile (0 `shell_exec`, 0 `shell=True` negli executor), MA la **guardia forbidden-path** (`~/.ssh`, `/etc/shadow`, `.aws/credentials`, `/boot`) non era coperta da nessun altro strato (la sandbox bubblewrap è no-op: `bwrap` assente). **Fix** (commit `6c6b43d`): `Executor.vaglio_guard` deterministico eseguito **PRE-invoke** (previene, non blocca-a-valle come il post-step), wirato in `_try_engine_v2` via `vaglio.guard_check`. Solo la guardia, NON il giudice (rischio regressione path-traversal). 4 test.

**Dead-code nel legacy (CONFERMATO in contraddittorio).** `ModeRouter.select()` è un no-op → `is_multistep` sempre True in prod → **11 rami `if not is_multistep:` morti** (8047,8062,8115,8190,8262,8298,8499,8521,8562,8589,9118); fase **seed-step URL** (6431-6504) morta-in-contesto (guardia 6446 sempre False nei 2 path). Unica fonte di `mode≠local` = CLI `--mode`. `ModeRouter` rimosso (commit `77add9d`); gli 11 rami spariranno con l'assorbimento (vivono dentro il blocco da eliminare).

**Mappa assorbimento (M1 vero).** Core minimo reale dei 2 path vivi ≈ **600-800 LOC (~20%)** delle 3359. **Upload: facile** — l'engine ha GIÀ il consumer-match `reference_image`→`reference_images` (executor ~342, test verde); serve solo seedare lo step-0 `@uploaded` + un boost nel pool. **Resume: più invasivo** (l'engine non riparte da scratchpad: `RunResult` nasce vuoto, executor ~1217) MA gran parte del resume reale è già scavalcata dall'orchestratore deterministico (`_orchestrate_implicit_actions`, orchestration ~993) → da MISURARE quanto cade nel loop-legacy-resume (oggi i log non lo strumentano: 0 marker). Estensione minima stimata: `seed_state` param su dispatch/executor/proposer (~90-120 LOC nuove) → elimina ~3300 LOC. Rischi: (1) proposer-awareness del resume; (2) hint foto deterministico vs ranking telos; (3) precedence con il gate-resume esistente.

**Fattorizzazione DRY (23/6).** Vittorie pulite fatte: `default_event_client` (4 copie→SoT `backends.events`), `_sha256_short` i18n (copia→import). **Trappole DRY evitate** (duplicazione VOLUTA, NON unire): i 3 set verbi-mutating (`DESTRUCTIVE_VERBS`/`_MUTATING_VERBS`/`MUTATING_VERBS`, membership diversa per scopo diverso); `i18n._sha256_full` (forma `sha256:<hex>`) ≠ copia locale (hex nudo). Deferito (richiede re-sign batch): A4 metadata-troncamento §2.7 in ~13 executor → `executor_helpers.set_truncation`. Già-fattorizzati (nessuna azione): proposer Metis, guard struttura, `_VERB_TO_CANONICAL`.

## 5. Piano di migrazione incrementale (ordinato per ROI/rischio)

> Principio: ogni passo è indipendente, gated (suite 2828/0 + routing 29/29 v3), e committabile da solo. Nessun big-bang.

1. **M0 — Sicurezza prima (cheap, alto valore)**: T4 (test idempotenza guard) + esplicitare l'ordine-contratto della pipeline guard (T3 parte test). Chiude il rischio silenzioso S3 senza toccare comportamento.
2. **M1 — RIFRAME (23/6): NON è pulizia morta.** Il «legacy» è VIVO (S7: foto-upload + resume-dialog). M1 reale = **(a)** portare sull'engine **v3** l'iniezione `@uploaded` + il blocco prompt foto, **(b)** la pre-popolazione scratchpad/step-offset del resume, **(c)** togliere `_bypass_for_uploads` lasciando l'engine v3 gestire entrambi, **(d)** e2e foto→`find_images_indices` + resume-dialog via engine v3; SOLO ALLORA il gate `METNOS_PLANNER_LEGACY` diventa incondizionato e il blocco 5998→9247 è eliminabile. (NB: l'engine è v3 — `_try_engine_v2` è solo il nome della funzione; nessuna «v2» da inseguire.) **Non** zero-rischio. (De-dup render-degenere→synth, S5, resta separato e a basso rischio.)
3. **M2 — Unifica sintesi finale**: T5 (Finalizer unico). Sana «final_message=1» e l'i18n non garantito.
4. **M3 — Consolida guard**: T3 (fondere i 3 «produttore mancante», resolver-registry).
5. **M4 — Vincola la generazione**: T2 (grammar-on-args, spike + misura: quanti guard si possono spegnere). Il passo più incerto → prototipo dietro flag, misurato su banco.
6. **M5 — Confine planning**: T1 (decomposer come pre-stadio; decisione D1 informata da M4).
7. **M6 — Slim executor**: T6 (resolver-registry, separazione esecuzione/finalizzazione).
8. **M7 — Debito lessici**: T8 (migrazione a detection_lexicon, a lotti per concept).

### 5.bis M1-UPLOAD — FATTO (23/6, engine v3, gate verde)

Il sotto-passo **(a)+(c)+(d)** di M1 limitato alle **foto-allegate** è chiuso e LIVE in prod (engine v3 default). Il **resume-dialog (b)** resta aperto (sotto).

**Meccanismo `seed_state` (generale, riusabile per il resume).** L'engine accetta un parametro `seed_state`: una lista di `StepRun` pre-esistenti iniettati come history a 0-offset PRIMA del primo step reale, così `from_step=1` li raggiunge. Niente di foto-specifico nel motore (§7.3).
- `engine/executor.py`: `Executor(seed_steps=...)`; `run()` pre-popola `result.steps`; **seed-wiring** — il primo step reale che può CONSUMARE il seed (consumer-match `reference_images`, o entries-consumer) e a cui il proposer non ha dato una sorgente USABILE (no `from_step`; arg-consumer assente/vuoto/placeholder-non-risolvibile `${step0…}`) → `from_step=1` deterministico (droppa il placeholder rotto). Le foto VINCONO su un `query_text` del proposer (parità ADR 0092). Local, framework non mutato (idempotenza hit-cache §S3).
- `engine/dispatch.py`: `run_turn(seed_state=…)` → boost `find_images_indices`/`find_persons_indices` nel pool + **salta L0/L1 + niente cache** con seed (turno context-specific, parità col legacy che skippava il fast_path).
- `agent_runtime.py`: `_try_engine_v2(reference_images=…)` costruisce il seed `@uploaded`; **branch dedicato** prima del PLANNER legacy instrada le foto all'engine (gate `METNOS_ENGINE_UPLOADS`, default 1; =0 → bypass→legacy per A/B). Handler engine→TurnLog estratto in `_finalize_engine_result` (riuso main-path + upload-branch, byte-invariato).

**Scoperta chiave (e2e reale).** Il proposer Mētis, ignaro del seed, emette `find_images_indices(reference_images="${step0.entries.*.path}")` — placeholder 0-index che NON risolve (stepref è 1-index `${step1…}`). Il seed-wiring lo riconosce come «sorgente non usabile» e lo ricuce a `from_step=1`. Senza questo, l'arg corretto restava `None` (salvato solo dal fallback `entries` dell'executor — outcome ok ma arg sbagliato).

**Verifiche.** Suite **2863/0**, routing **29/29** (v3, env prod). Unit nuovo `tests/test_engine_seed_uploads.py` (8 casi: wiring, precedenza-su-query_text, placeholder `${step0}`/`${step1}`, idempotenza, regressione no-seed). e2e in-proc determinismo 3/3 + legacy-fallback (UPLOADS=0) 1/1. **e2e HTTP REALE prod**: upload multipart → engine v3 → `find_images_indices(reference_images=[foto])` sull'indice 31k → 4 match reali, «1 foto simili nel tuo album». Turn log confermato (step0 `@uploaded` + step1 ref wired).

### 5.ter «SEMINA DI TURNO» — entità unificata + resume migrato (23/6, no «passi indietro»)

Roberto: «nel resume approfitta per integrare e migliorare. non un resume ma una NUOVA ENTITÀ. no passi indietro.» → invece di portare 1:1 il `resume_with_scratchpad` legacy, il `seed_state` di M1-upload è generalizzato a **stato-pregresso di turno a due nature** (`StepRun.kind`):
- **`input`** — seed CONSUMABILE (foto `@uploaded`): il 1° step reale lo usa via `from_step=1`. (M1-upload, già vivo.)
- **`done`** — seed GIÀ ESEGUITO in un turno precedente (continuazione dialogo): il proposer NON lo ri-emette, gli step a valle lo referenziano via `from_step`.
- **`live`** — default, ogni step eseguito ORA (callsite byte-invarianti).

**Tre meccanismi nuovi** (commit `b286493`):
1. **Proposer-aware** (`engine_proposer.j2` IT+EN + `_render_prior_steps`): sezione «FATTO FINORA» SOTTO il marker `STATIC-END` (fuori dalla prefix-cache → SYSTEM byte-identico verificato `sys1==sys2`, §11 intatta) elenca gli step `done` e istruisce «pianifica solo il resto». `propose(prior_steps=())` ai 4 callsite + grammar-multi.
2. **Guardia dedup deterministica** (`Executor.run`): se il proposer (LLM) ri-emette un produttore già `done`, lo SALTA. Match per **NOME-TOOL** (non shape-args): il proposer del turno di ripresa rigenera lo stesso producer con chiavi-arg diverse (`time_window`→`time_windows`+`size`) — è la stessa ri-esecuzione. Rete di sicurezza §7.9: evita doppia latenza e ri-esecuzione di side-effect. L'engine non aveva ALCUN dedup di step (verificato).
3. **Marker-filtering** del seed resume: `get_inputs`/`get_approval`/`@uploaded` NON entrano nel seed `done` (non sono produttori; occupavano un indice rompendo `${stepN}` — bug e2e `${step2.summary}`→get_inputs). `step_idx` rinumerato contiguo.

**Resume migrato** (commit `f738a0f`): `resume_with_scratchpad` (get_inputs mid-pipeline, l'UNICO resume rimasto sul legacy — il gate get_approval già ri-esegue via `pre_approved_gate`→engine) instradato all'ENGINE via `_try_engine_v2(resume_steps=...)` → seed `done`. Branch gated `METNOS_ENGINE_RESUME` (default 1; =0→legacy A/B), fallback su engine-None. **GENERALIZZA oltre `_ACTION_TEMPLATES`** (che copriva solo `create_events`): e2e provati = continuazione `create_events` (prenota slot scelto) E `send_messages` (riassunto mail lette, che il legacy deterministico NON copriva), dedup verificato (il producer fatto non ri-gira). Suite **2876/0**, routing **29/29**, e2e prod-config + determinismo 3/3.

**`_ACTION_TEMPLATES` RITIRATO** (commit `f029c78`, −275 LOC): lo shortcut deterministico `_orchestrate_implicit_actions` (ADR 0129, solo `(create,events)`) è rimosso ora che l'engine copre il caso generale. e2e provati PRIMA della rimozione: create_events+notify COMPOUND (il caso ADR 0129) + send_messages (che lo shortcut NON copriva), determinismo 3/3. `resume_planner_with_dialog_values` instrada SEMPRE a `run_turn(resume_with_scratchpad)`→engine. Il detector `detect_implicit_actions` (intent-side) resta. Rimossi anche 4 helper morti + import `detection_lexicon` morto.

**Resta (M1 completo → eliminazione legacy).** (1) Strumentare i resume in prod per confermare 0 cadute nel loop-legacy (oggi il fallback è gated, non misurato). (2) Solo allora: gate `METNOS_PLANNER_LEGACY` incondizionato + rimozione blocco legacy (~3300 LOC) + 11 rami `if not is_multistep:` + `_bypass_for_uploads` + il path `resume_with_scratchpad` in run_turn.

---

## 6. Decisioni aperte per Roberto (servono prima di M4+)

- **D1 — ESEGUITO 2026-06-24 → decomposer ELIMINATO, engine-only permanente.** Roberto: «favorevole a unificare e rinunciare al decomposer per maggior qualità; se si mostra inutile DEVE essere eliminato e il codice ripulito». Il bake `METNOS_DECOMPOSER=0` (22-24/6) fu **revertito il 22/6** perché esponeva una regressione §2.8 dell'engine (extract→create: 0 entries → create saltato → `final_message` mentiva «creato il foglio»). **Ri-valutato il 24/6 DOPO il fix onestà `a139dcd`**: l'engine è ora §2.8-onesto sia su successo (file creato, claim vero, verificato e2e 3/3) sia su extract→0 (dice «azione NON eseguita», non mente); prod girò engine-only ~1 giorno con 0 lamentele §2.8. **Eliminato**: il PATH `decompose_query` (entry in `agent_runtime` ~143 LOC + `compound_decomposer.decompose_query` + 3 helper privati, −343 LOC nel modulo) + `finalize_decomposed_plan` (dispatch) + flag `METNOS_DECOMPOSER` + drop-in prod + `test_decomposer_path.py` + `bench/measure_paths.py`. **TENUTI** gli helper condivisi del modulo (`PRODUCER_VERBS`, `derive_tool_name`, `split_query_chunks`, `derive_extract_fields`, `_send_has_explicit_recipient`) — usati dai guard dell'engine. Path compound ora UNICO (S1 chiuso). Tradeoff accettato: cold-start novel ~20s (ammortizzato da cache L0/L1). Vedi [[project-decomposer-retirement-bake]].
- **D2 — Grammar-on-args**: §11 dice «grammar-on-verbs accantonata (enforce+align bastano)». Riapro lo spike SOLO sugli ARGS (M4, misurato) o resto sul correggi-a-valle?
- **D3 — Profondità**: faccio tutto il piano M0→M7 o mi fermo a M0–M3 (sicurezza + pulizia + sintesi, basso rischio) e ridiscutiamo M4+?

---

## Riferimenti

- [[project-engine-architecture-review]] (brief + scaffolding), [[project-compound-planning-refactor]] (sottoinsieme + P0/P1 fatti).
- ADR 0174 (cache-discipline guard su hit), 0175 (engine v3 compound), 0176 (mail-read completeness).
- Prod = `f0b96f2`; gate suite 2828/0, routing 29/29 (v3).
