# Mētis wiring consolidation (pre-rebuild audit)

> READ-ONLY snapshot 28/5/2026. Doppia implementazione Praxis Engine in-flight: `runtime/_legacy/*` (vecchio) ↔ `runtime/engine/*` (nuovo). Lo scopo è consolidare il **wiring** prima di rebuildare moduli. Niente refactor in questo doc.

---

## §1. Wiring map (per entry-point)

### Entry-point principale: `agent_runtime.run_turn` (chiamato da HTTP `/agent/turn`, telegram daemon, smoke, bench)

```
run_turn(query, …)
 ├─ [L0 legacy] try_fast_path(query)               # _legacy/fast_path.py — pattern deterministici (get_now, get_location, undo). Niente sqlite.
 │    └─ MISS → try_canonical_match(query)         # runtime/canonical_matcher.py BGE. Env: METNOS_CANONICAL_QUERY (toml)
 │         └─ MISS → _try_multi_tool_path_playback # _legacy/multi_tool_paths.py. DB: multi_tool_paths.sqlite. Env: METNOS_MULTI_TOOL_FAST_PATH (toml)
 │
 ├─ if (METNOS_ENGINE_V2=1, default) AND not _bypass_for_uploads:
 │    _try_engine_v2(query, catalog) → engine.dispatch.run_turn(...)
 │     ├─ prefilter.rank_with_intent (pool reduction K=12, METNOS_ENGINE_POOL_SIZE)
 │     ├─ L0 engine.fastpath.lookup       (METNOS_FASTPATH=1)  → DB: fastpaths.sqlite
 │     ├─ L1 engine.autopath.lookup       (METNOS_AUTOPATH=1)  → DB: autopath.sqlite
 │     ├─ L3 proposer.propose             (METNOS_ENGINE=simple|metis|frontier)
 │     │    └─ excluded = autopath.excluded_framework_hashes(intent)  → autopath.sqlite
 │     ├─ L2 validator.check               (METNOS_VALIDATOR=1, opt-in)  → catalog memoria
 │     ├─ executor.run(framework, …)       → invoke_executor callback (catalog reale)
 │     ├─ autopath.record_observation(…)   → autopath.sqlite (observations)
 │     ├─ on error: recovery.recover → executor.run(framework_alt)
 │     └─ on persistent error: terminator.explain → DB: aporiae.sqlite (engine version)
 │
 ├─ elif (METNOS_PRAXIS=1, default): _try_praxis_cascade  # path "morto" perchè engine v2 always-on
 │    └─ praxis_executor.try_praxis_path
 │         └─ on success → praxis.get_store().record_observation → praxis.sqlite (legacy)
 │         └─ on success → praxis.get_store().record_feedback("ok") synthetic auto-promote
 │
 └─ else: planner legacy (METNOS_PLANNER_LEGACY=1, default=0 → ritorna error)

returnvalue: TurnLog (steps, final_message, final_kind)
```

### Entry-point: `turn_feedback.append_feedback` (chiamato da bottoni ✓/✗/↻ chat HTTP/Telegram)

```
append_feedback(turn_id, action ∈ {ok,error,repeat})
 ├─ feedback_demote (ager) → JSONL
 └─ from praxis import get_store          # SHIM → _legacy.praxis  (DB: praxis.sqlite)
     └─ get_store().record_feedback(turn_id, verdict)
         → cerca observations WHERE turn_id=? in praxis.sqlite
         → MISS (perché engine v2 scrive in autopath.sqlite!) → no-op
```

**BUG WIRING #1**: feedback ✓/✗/↻ NON raggiunge `autopath.sqlite`. Path osservazione (engine v2) e path feedback (legacy shim) sono su DB diversi.

### Entry-point: scheduler v2 callbacks (`builtin_callbacks.py`)

```
praxis_template_refresh → jobs/praxis_template_refresh.py → praxis.sqlite (legacy)
praxis_cluster_merge    → jobs/praxis_cluster_merge.py    → praxis.sqlite (legacy)
```

Nessun job opera su `autopath.sqlite`. Engine v2 non ha jobs di manutenzione.

### Entry-point: `http_routes_admin.admin_aporiae`

```
GET /admin/aporiae → _legacy.aporia.get_store() → DB: aporiae.sqlite (legacy)
```

Engine v2 ha proprio `engine/terminator._record_lacuna` ma scrive su **stessa** `aporiae.sqlite` (schema possibilmente divergente, da verificare).

### Failure path summary

| Layer | Raise/timeout | Empty result |
|-------|---------------|--------------|
| `_try_engine_v2` | log.warning, return None → fallthrough a praxis legacy (mai raggiunto) |
| `engine.dispatch` propose fail | terminator.explain → answer "cannot help" |
| `engine.dispatch` exec error | recovery → 1 retry → terminator |
| `_try_praxis_cascade` | log.warning, return None → fallthrough a planner legacy (=error se METNOS_PLANNER_LEGACY=0) |
| feedback hook | log.warning, swallow (silenzioso) |

---

## §2. Doppie implementazioni — overlap

| Funzione | _legacy/file | engine/file | Status |
|----------|--------------|-------------|--------|
| Cache skill cosine+hash | `praxis.py::PraxisStore` (sqlite praxis.sqlite) | `autopath.py` (sqlite autopath.sqlite) | **conflict** — entrambi richiamabili, DB separati |
| Skill auto-promote | `praxis._maybe_promote` (env MIN_OBS_PRODUCER/MUTATING) | `autopath._promote_skill` (env METNOS_AUTOPATH_MIN_OBS) | **conflict** — soglie + DB diversi |
| LLM Proposer | `praxis_propose.propose_framework` (271 LOC + GRAMMAR_FRAMEWORK) | `proposer.SimpleProposer` / `proposer_metis.MetisProposer` | **partial** — engine.proposer importa ancora `praxis_propose.GRAMMAR_FRAMEWORK` |
| Executor deterministico | `praxis_executor.execute_framework` (746 LOC) | `engine/executor.py::Executor.run` (759 LOC) | **conflict** — entrambi ri-implementano filler/from_step/runtime placeholder |
| Cascata orchestrator | `praxis_executor.try_praxis_path` | `engine/dispatch.run_turn` | **used** (engine v2 default) / **dormant** (praxis cascata mai raggiunta a METNOS_ENGINE_V2=1) |
| Feedback hook | `praxis.record_feedback` | `autopath.record_feedback` | **conflict** — solo legacy ha caller (`turn_feedback.py`); engine version DORMANT |
| Cluster/embed | `praxis_cluster.py` (404 LOC, BGE-M3) | `engine/cluster.py` (107 LOC) | **conflict** — due implementazioni cosine/BGE separate |
| Aporia lacune | `_legacy/aporia.py` + `aporiae.sqlite` | `engine/terminator.py::_record_lacuna` + `aporiae.sqlite` | **conflict** — stesso DB, schemi possibilmente non identici |
| Pronoia classify_fail | `_legacy/pronoia.py` (391 LOC) + `pronoia_classify_fail.py` (151 LOC) | (assente in engine) | **dormant** — zero production callers (solo legacy tests) |
| Fast-path pattern | `_legacy/fast_path.py` (`get_now`, undo, location patterns) | `engine/fastpath.py` (user-approved cache) | **used both** — semantiche diverse, NON in conflitto |

**Punti di ambiguità da risolvere**:
1. `praxis.sqlite` vs `autopath.sqlite` → scegliere autopath, migrare skill+obs.
2. `engine.proposer.SimpleProposer` importa `praxis_propose.GRAMMAR_FRAMEWORK` → estrarre GRAMMAR in `engine/grammar.py`.
3. `praxis_cluster.py` vs `engine/cluster.py` → tenere engine version (BGE-M3 più snella).
4. `pronoia*.py` → archiviare (zero callers, mai promosso in engine v2).
5. `aporia.py` registry vs `terminator._record_lacuna` → unificare su engine writer + admin route legge.

---

## §3. Root cause: Praxis vuota

**Stato osservato**:
- `~/.local/share/metnos/praxis.sqlite`: skills=0, obs=0, anti=0 (DB con DDL ma ZERO righe).
- `~/.local/share/metnos/autopath.sqlite`: skills=0, obs=336, obs_with_verdict=0, anti=0.
- `~/.local/share/metnos/praxis.legacy_1779807058.sqlite` (backup 25/5): skills=23, obs=57.

**Path osservazione → promozione (engine v2)**:
1. `_try_engine_v2` → `dispatch.run_turn` → `autopath.record_observation(turn_id, intent, framework, query, latency_ms)` → INSERT in `autopath.sqlite::observations` (verdict=NULL).
2. Promozione richiede `record_feedback(turn_id, "ok")` → cerca observation by turn_id, UPDATE verdict='ok', poi `_promote_skill` se `n_ok >= METNOS_AUTOPATH_MIN_OBS` (default 1).

**Break confermato**: `runtime/turn_feedback.py:289` importa `from praxis import get_store` (= shim → `_legacy.praxis`) e chiama `.record_feedback(turn_id, verdict)` su `praxis.sqlite`. Quel DB è VUOTO (nessuna observation), quindi `record_feedback` esce con `{"ok": False, "reason": "no_observation"}` silenzioso. **MAI** chiamato `engine.autopath.record_feedback` in produzione (`grep` conferma: solo legacy tests).

Inoltre `agent_runtime.py:5933-5956` (sintetico auto-promote "ok" se tutti gli step ok+no_abort) è dentro il ramo `_praxis_res != None` — engine v2 NON entra mai in quel ramo perché `_engine_v2_res != None` short-circuita prima (return a riga 5897).

**Verdict**: 0 skills perché (a) `autopath.record_feedback` zero callers; (b) auto-promote sintetico legato al ramo legacy mai eseguito; (c) `praxis.sqlite` è DB orfano scritto da legacy mai usato.

**Intent_sig hash mismatch?** Verificato: `engine.autopath._compute_intent_sig` usa `sha256(f"{v}|{o}")[:16]` (keywords NON nell'hash, solo in `sig` leggibile). `_legacy.praxis` ha logica più complessa (può includere keywords). Quindi anche se backporting feedback fosse fatto, hash di obs (engine) e hash di feedback (legacy) sarebbero diversi. **Doppio break**: wiring + hash drift.

**Migrazione da `praxis.legacy_1779807058.sqlite` → `autopath.sqlite`**: nessuno script esiste. 23 skill perse perchè backup mai re-importato. Proposta: `jobs/migrate_legacy_skills.py` con UNION schema (drop colonne legacy `keywords_csv`, `source`, `version`, `born_from`; mantenere `intent_sig`, `intent_hash`, `cluster_id`, `framework_json`, `framework_hash`, `status`, `uses`, `ok_count`).

**Coverage 0% su 95q realistiche è REALE**: confermato wiring bug, non lacuna semantic. Anche se intent extraction perfetta, niente skill in cache → ogni query passa per `proposer.propose` (LLM full).

---

## §4. LOC reali (discount test/bench/dead)

Totale grezzo: 8036 LOC (5050 legacy + 2986 engine).

| File | LOC | Classe |
|------|-----|--------|
| `_legacy/praxis.py` | 1196 | **in-prod via shim** (turn_feedback + scheduler jobs) |
| `_legacy/multi_tool_paths.py` | 1041 | **in-prod via shim** (agent_runtime) |
| `_legacy/praxis_executor.py` | 746 | dormant (ramo cascata mai raggiunto a engine v2=1) |
| `_legacy/fast_path.py` | 452 | **in-prod** (try_fast_path L0) |
| `_legacy/praxis_cluster.py` | 404 | in-prod (jobs scheduler) |
| `_legacy/pronoia.py` | 391 | **dormant** (zero callers prod) |
| `_legacy/praxis_propose.py` | 271 | partial (engine.proposer importa GRAMMAR_FRAMEWORK) |
| `_legacy/aporia.py` | 260 | in-prod (admin route) |
| `_legacy/pronoia_classify_fail.py` | 151 | **dormant** |
| `_legacy/praxis_constants.py` | 130 | in-prod (constants used by jobs) |
| `_legacy/tests/` | 484 | test (skip) |
| `engine/executor.py` | 759 | **in-prod via dispatch** |
| `engine/autopath.py` | 382 | in-prod ma feedback hook morto |
| `engine/proposer_metis.py` | 268 | active solo se METNOS_ENGINE=metis |
| `engine/proposer.py` | 243 | **in-prod default** (SimpleProposer) |
| `engine/dispatch.py` | 224 | **in-prod entry** |
| `engine/fastpath.py` | 210 | in-prod L0 (separato da legacy fast_path) |
| `engine/terminator_metis.py` | 141 | METNOS_ENGINE=metis |
| `engine/terminator.py` | 144 | in-prod |
| `engine/types.py` | 129 | shared |
| `engine/validator.py` | 121 | opt-in METNOS_VALIDATOR |
| `engine/recovery.py` | 109 | in-prod |
| `engine/cluster.py` | 107 | in-prod |
| `engine/recovery_metis.py` | 94 | metis only |
| `engine/__init__.py` | 59 | shared |

**Discount**:
- Test/legacy tests: 484 LOC → escludere.
- Dormant (pronoia, pronoia_classify_fail, praxis_executor non raggiunto): 1288 LOC.
- METNOS_ENGINE=metis (proposer_metis, terminator_metis, recovery_metis attivi solo opt-in): 503 LOC.

**LOC realmente in produzione (METNOS_ENGINE=simple default)**:
- Legacy in-prod: ~3600 (praxis 1196 + multi_tool 1041 + fast_path 452 + cluster 404 + aporia 260 + constants 130 + propose ~135).
- Engine in-prod: ~2487 (executor 759 + autopath 382 + proposer 243 + dispatch 224 + fastpath 210 + terminator 144 + types 129 + recovery 109 + cluster 107 + validator 121 + init 59).

**Totale REALE in-prod**: ~**6100 LOC** (non 8000). Gonfiatura ~24% da test/dormant/opt-in.

---

## §5. Wiring inconsistencies (sorted by impact)

1. **CRITICO — feedback ✓ scrive su DB sbagliato** (§3 root cause). Path: `turn_feedback.py:289` → `_legacy.praxis` su `praxis.sqlite` mentre observations vivono in `autopath.sqlite`.
2. **CRITICO — auto-promote sintetico mai eseguito** in `agent_runtime.py:5947-5956`: ramo legacy `_praxis_res`, engine v2 sempre short-circuita prima. Codice morto.
3. **ALTO — `engine.proposer.SimpleProposer` import da legacy** (`from praxis_propose import GRAMMAR_FRAMEWORK`): engine dipende da modulo "deprecato".
4. **ALTO — schema drift `intent_hash`**: legacy include keywords/varianti, engine usa solo `verb|object`. Migrazione cross-DB richiede ricalcolo.
5. **ALTO — `aporiae.sqlite` scritto da due writer** con schemi potenzialmente diversi: `_legacy/aporia.py::record` vs `engine/terminator.py::_record_lacuna`. Admin route legge solo legacy schema.
6. **MEDIO — `praxis_cluster.py` e `engine/cluster.py` due embedding pipeline** (entrambi BGE-M3 ONNX). Jobs scheduler usano legacy cluster, engine dispatch usa engine cluster. Cluster_id NON portabili fra i due.
7. **MEDIO — naming collision `fast_path`**: `runtime/fast_path.py` (shim→legacy patterns) ≠ `runtime/engine/fastpath.py` (user-approved cache). `agent_runtime:49` importa primo, dispatch importa secondo. Confusione per chiunque legga.
8. **MEDIO — `METNOS_PRAXIS=1` flag attivo ma cascata mai raggiungibile** (engine v2 ha precedenza con default `=1`). Dead config.
9. **BASSO — pronoia file installati ma zero callers**: 542 LOC che non fanno nulla in prod.
10. **BASSO — scheduler jobs operano su DB legacy** (`praxis_cluster_merge`, `praxis_template_refresh`) ma le skill vive sono in autopath. Jobs effettivamente no-op.

---

## §6. Piano consolidamento wiring (PRE-rebuild)

> Ordinato per dipendenza. Smoke: `python -m runtime.smoke` post-step. Zero modifiche schema engine, zero modifiche LLM prompts.

| # | Step | File toccati | Smoke | Tempo |
|---|------|--------------|-------|-------|
| 1 | **Fix feedback wiring (#1)**: `turn_feedback.py` chiama `engine.autopath.record_feedback` se `METNOS_ENGINE_V2=1`, fallback `_legacy.praxis.record_feedback`. | `runtime/turn_feedback.py` (1 import, 5 righe) | feedback chat → verifica obs.verdict='ok' in autopath.sqlite | 30min |
| 2 | **Sposta auto-promote sintetico nel ramo engine v2**: dopo `return log` engine v2 success (riga 5897), call `engine.autopath.record_feedback(turn_id, "ok")` se step ok+no_abort. | `runtime/agent_runtime.py` (10 righe) | turn semplice ripetuto 2× → skill creata in autopath | 45min |
| 3 | **Backport intent_hash compat**: scegli un solo formula (engine version, `sha256(v|o)[:16]`). Riscrive `_legacy/praxis._compute_intent_hash` per allinearsi (solo se lega ancora qualcosa post-step 1+2). | `runtime/_legacy/praxis.py` (5 righe) | smoke + verifica match cross-version | 1h |
| 4 | **Migration script legacy backup → autopath**: `jobs/migrate_legacy_skills.py` UNION schema (drop colonne legacy). Rerun manuale. | new file 80 LOC | dry-run + commit → verifica skills_count >0 in autopath | 2h |
| 5 | **Estrai GRAMMAR_FRAMEWORK da legacy**: nuovo `engine/grammar.py`, import nel proposer engine + nel legacy. Rimuove dipendenza engine→legacy. | `engine/grammar.py` (new ~30 LOC), `engine/proposer.py` (1 import), `_legacy/praxis_propose.py` (re-export) | smoke proposer | 30min |
| 6 | **Archivia pronoia** (`mv runtime/pronoia.py runtime/_archive/`, idem `_legacy/pronoia*.py`). Cancella shim. | runtime/pronoia.py, runtime/pronoia_classify_fail.py | full test suite (deve passare: zero callers) | 30min |
| 7 | **Unifica writer aporia**: `engine.terminator._record_lacuna` adotta schema esatto `_legacy/aporia` (stessi nomi colonna). Admin route invariata. | `engine/terminator.py` (10 righe DDL) | apri /admin/aporiae dopo terminator hit | 1h |
| 8 | **Disabilita scheduler jobs morti** (`praxis_cluster_merge`, `praxis_template_refresh`): rimuove registrazione in `builtin_callbacks.py` finché non li portiamo su autopath. | `runtime/scheduler_v2/builtin_callbacks.py` (commenta 4 blocchi) | scheduler start log | 15min |
| 9 | **Sposta cluster jobs su engine.cluster** o accetta debt: opzione A re-implementa cluster_merge usando autopath schema (3h); opzione B accetta che cluster_merge non gira finché rebuild. **Raccomando B** durante consolidamento. | — | — | 0 (skip) |
| 10 | **Cleanup praxis.sqlite orfano**: rinomina a `praxis.sqlite.deprecated_<ts>` per chiarezza. | — (rename manuale) | smoke | 5min |

Tempo totale: **6.5 h** (step 9 escluso). Step 1+2 sbloccano il path osservazione→promozione subito (impatto utente immediato).

---

## §7. Primo modulo da rebuildare

**Candidati e metriche**:

| Modulo | LOC engine + legacy in-prod | Valore osservato | Bug/debt aperti | Standalone? |
|--------|------------------------------|------------------|-----------------|-------------|
| Mētis (Proposer) | 243+135 = 378 (+ MetisProposer 268 opt-in) | LLM full ogni miss = costo principale | dipende da `praxis_propose.GRAMMAR`, prompt non typed | parziale |
| Noûs (Executor) | 759+746 = 1505 (parallel impl) | core di OGNI turno | doppia impl, filler/from_step duplicato | sì |
| Praxis (Cache/autopath) | 382+1196 = 1578 (parallel) | 0% skills in prod = zero valore attuale | wiring break + schema drift + 2 DB | sì |
| Pronoia | 0 | morta | dormant | n/a |
| Aporia | 260 + 144 engine = 404 | basso (registry lacune) | 2 writer schemi divergenti | sì |

**Raccomandazione: Praxis (autopath)** primo.

**Rationale (200 parole)**:

Praxis è l'unico modulo che, una volta wirato correttamente (§6 step 1-4), porta beneficio immediato all'utente: oggi 0% coverage, target realistico 30-50% post-fix wiring (le 336 observations esistenti diventano candidate skill). Il rebuild di Noûs/Mētis senza Praxis funzionante NON cambia il costo medio per turn perché ogni query continua a invocare Proposer LLM.

Inoltre Praxis ha la massima entropia di codice morto: 1578 LOC totali con 2 DB e schema drift. Rebuild standalone è fattibile (autopath già ha API pulita, basta finire il wiring e dismettere praxis.py legacy).

**Design proposal**: mantenere `engine/autopath.py` come unico writer. API target: `Praxis.observe(turn_id, intent, framework, query, latency)`, `Praxis.lookup(query, intent)`, `Praxis.feedback(turn_id, verdict)`, `Praxis.demote(intent, fhash)`. Schema sqlite single-table `skills` + `observations` + `anti_skills` (già presente). Intent_sig formula unica e dichiarata in `engine/types.py::Intent.signature()`. Cluster_id via `engine/cluster` (più snello). Jobs scheduler (refresh/merge) re-implementati ex-novo solo se metric "skill_count" cresce post-wiring fix (decisione data-driven, non a-priori).

Noûs rebuild segue (executor è troppo accoppiato a runtime placeholder/filler/from_step per essere safe ora). Mētis è LLM-bound, rebuild = prompt tuning, può attendere.
