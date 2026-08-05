# Audit architettura test Metnos — analisi + proposta unitaria (22/7/2026)

> **STATO: COMPLETO — 7/7 cluster analizzati** (executor+builtin, engine/routing, change/telos/aging, sites/
> backend/i18n, promoter, infra http/scheduler, layer e2e/simulator/bench). Analisi deterministica dell'intero
> panorama + revisione adversariale per cluster. Vincoli utente applicati: **equipotenza** (nessuna perdita di
> copertura; §8.4/§8.5/§8.1 preservate, verifica meccanica `pytest --collect-only` before==after) + **scelte
> semplici e lineari** (§7.2). Solo report — nulla modificato. Sintesi in §7 (totali) + §3 (architettura) + §8 (bug prodotto).

---

## 1. Panorama deterministico (completo)

**443 file `test_*.py`, ~90k LOC** (escludendo il worktree stantìo `.claude/worktrees/agent-a2dfb0f3c3d0f720c/` che duplica l'intero repo → **cleanup ambientale a parte**).

| Area | File | LOC | Natura |
|---|---|---|---|
| `tests/runtime/` | 393 | ~86.000 | Unit — **PIATTA** (solo sottodir `data/`), nessuna struttura di dominio |
| `e2e/scenarios/` | 29 | 3.879 | Turni reali §8.5 (server vero via `e2e/conftest.py`) |
| `e2e/simulator/` | 37 (.py) | — | Tooling/ricerca sperimentale (query builders, graph_search[_v2], qwen_finetune, fasttext…) |
| `tests/runtime/scheduler_v2/` | 17 | — | Co-locati col modulo (buon pattern) |
| `runtime/stress/` | 14 (.py) | — | Stress routing/latency |
| `runtime/bench_archive/` | 13 (bench_*.py) | — | Bench «archiviati» (nome) |
| `runtime/poc/` | 8 (.py) | — | Proof-of-concept |
| `runtime/testing/` | 5 (.py) | — | Infra di test |
| `tests/internal/` | 3 | — | Release-gate: conformance, release_gate, stack_live |
| smoke | 3 | 952 | `smoke.py` (561) + `smoke_en` (189) + `smoke_imports` (202) |

**Distribuzione dimensioni tests/runtime**: 34 micro (<60 righe), 138 piccoli (60-150), 145 medi
(150-300), 77 grandi (>300). → **172 file sotto 150 righe** = alta frammentazione.

**Clustering per prefisso (struttura di dominio implicita, già presente):** executor 31, get 13, sites 8,
http 8, skill 7, find 7, change 7, delete 6, admin 6, telos 5, promoter 5, planner 5, google 5, engine 5,
dialog 5, describe 5, telegram 4, playwright 4, manifest 4, install 4…

## 2. Reperti confermati (deterministico + cluster executor/builtin)

**Orfani / codice morto** — ⚠️ CORREZIONE (verifica cluster learning): l'ipotesi orfani `proposals_unified`
è **FALSA**. Il modulo E il suo test sono **già cancellati** (grep=0); `test_fastpath_promote.py` importa il
modulo **VIVO** `engine.fastpath_promote` (non `proposals_unified`) → NON è orfano, tenere. Il mio scan
deterministico iniziale (che li elencava) era stantìo. **Nel cluster learning: 0 test morti.** Restano da
verificare i moduli admin morti (i18n_migrate_v2/manifests) trovati dal cluster sites, e `bench_archive/`.

**Mal collocati:**
- `tests/runtime/e2e_google_backend.py`, `e2e_google_photos.py` → sono e2e, stanno in `tests/` → spostare in `e2e/`.

**Falsi allarmi verificati (NON toccare):**
- `test_engine_v2.py` **non è morto**: la docstring dice «testa il nuovo `runtime/engine/`» — nome fuorviante, copertura viva. (Rinominare a `test_engine.py`.)
- Riferimenti a executor ritirati (`fetch_urls`, `list_processes`…) nei test = fixture legittime di orphan-handling, non test di codice morto.

**Debito STRUTTURALE (cluster executor/builtin — 50 file, verificato):** la suite è **sana** (nessun test
tautologico di massa, nessuna sussunzione unit⊆e2e reale, 475 test con assert sostanziali). Il debito è
boilerplate ripetuto:
- `sys.path.insert` in **43 file** con 4 varianti divergenti → 1 riga in `conftest`.
- fixture `catalog` (carica 84 manifest) duplicata in **14 file** → 1 fixture session-scoped (**−13 caricamenti catalogo per run** = suite più veloce).
- `*_paraphrases_remain_routable` in **17 funzioni / 8 file** (tutte in `executor_standard_*`) → 1 `test_executor_standard_routing.py` parametrizzato (le query restano tutte).
- invariante «root non-oggetto → ERR_ARG_INVALID» in **11 file** → 1 parametrize cross-dominio.
- **Bilancio cluster**: ~−650 LOC (−7%), −9 file, run più veloce. Tagli secchi: 2-3 test tautologici.
- Split 22-file `executor_standard`: **coerente**, ma nomi confusi (`files_domain`/`filesystem_batch`/
  `readers`/`pure_batch`) → rinominare ai domini reali o elencare in testa gli executor coperti.

**Copertura**: 225 moduli runtime/ top-level, **55 senza test** (misto: alcuni critici — `approval_registry`,
`nightly_orchestrator`, `host_throttle`, `location_request`; altri triviali — `hashutil`, `logging_setup`,
`llm_pricing`). Gap da colmare selettivamente (non tutti meritano test).

## 3. Proposta di architettura unitaria (semplice, lineare, equipotente)

**Principio**: le directory dei test **rispecchiano i package di `runtime/`** che gli sviluppatori già
conoscono — nessuna tassonomia nuova da imparare. Ogni test sta accanto-per-dominio al codice che prova.

```
tests/runtime/
  conftest.py            # UNICO: sys.path + fixture catalog session-scoped + zero-pollution (già c'è)
  engine/                # test_engine, planner, routing, prefilter, autopath, fastpath, cache, coerce, validator, compound
  executors/             # test_executor_standard_* (rinominati), get_*, find_*, delete_*, write_*, read_*
  entries/               # describe, extract, classify, filter, group, sort, compute, dialog, get_inputs
  learning/              # change_*, telos_*, promoter_*, mnestoma, aging, alignment, proposals
  backends/              # google_*, backend_*, email/messages, geo/places, datastore
  sites/                 # sites, playwright_sidecar
  channels/              # telegram, pairing, dialog_pending, location
  skill/                 # skill_*, sign, manifest, install, admission
  safety/                # vaglio, capabilities, sandbox, credential
  remote/                # device_shim, invocation, remote
  i18n/                  # i18n_*
  http/                  # http_*, admin
  data/                  # (invariato — fixture dati)
tests/runtime/scheduler_v2/   # invariato (già co-locato, buon pattern)
e2e/
  scenarios/             # invariato — turni reali §8.5 (+ i 2 e2e_google_* spostati da tests/runtime)
  conftest.py, driver/, corpus/   # invariato
  tooling/               # ex-e2e/simulator: SOLO i vivi; gli sperimentali _v2/one-off → _archive/ o git
tests/internal/          # invariato — release-gate
tools/bench/             # ex runtime/bench_archive + stress + poc: bench VIVI; il resto archiviato
```

**Perché è equipotente**: nessun file di test con copertura unica viene eliminato — vengono solo (a)
**spostati** in cartelle di dominio (stessa esecuzione, `pytest tests/runtime` li raccoglie comunque), (b)
**fusi** dove sono copie letterali (le query/asserzioni restano tutte, come parametrize), (c) **rimossi**
solo gli orfani su codice morto e i 2-3 tautologici. Le fasce §8.4 diventano **marcatori pytest**
(`@pytest.mark.smoke/sanity/full`) invece di 3 script scollegati — un solo runner `pytest -m smoke`, stessa
potenza, meno entry-point. Gli e2e §8.5 e il protocollo iterate-test-cluster §8.1 restano identici.

**Perché è semplice/lineare (§7.2)**: mappa 1:1 coi package di `runtime/` (niente astrazioni nuove);
il consolidamento è meccanico (sposta + parametrizza + conftest), non riscrive la logica dei test; un solo
`conftest` per il boilerplate; i layer (unit / e2e / bench / tooling) separati per cartella, non per
framework. Migliora l'attuale su tre assi misurabili: **−9+ file**, **run più veloce** (fixture catalog
condivisa), **navigabilità** (dal file di codice al suo test in un passo).

**Basi condivise in `conftest` — il debito ricorrente di OGNI cluster (il vero intervento a più alto ritorno).**
Ogni cluster ha ritrovato lo stesso boilerplate copiato: è la conseguenza diretta della cartella piatta senza
un posto dove mettere l'infrastruttura comune. Quattro basi chiudono la gran parte del debito unit:
1. `sys.path` (ripetuto in **43+ file**, 4-5 varianti divergenti) → una riga in `conftest`.
2. fixture `catalog`/`standard_catalog` session-scoped (ricaricata in **14 file**, 84 manifest ×14/run) → una fixture.
3. base `AioHTTPTestCase` condivisa (`make_app`+reload replicato in **5 file** con drift) → una base http.
4. `_FastpathDbCase`/DB-isolato + factory `_fw()`/proposta-5-stadi (triplicati in fastpath, change-*, promoter) → helper unici.
Effetto: −centinaia di LOC, suite più veloce (13 caricamenti catalogo in meno), e **fine della deriva** fra le copie.

**Co-locazione: il modello sano già in casa.** `tests/runtime/scheduler_v2/` (co-locato col modulo, conftest locale,
fixture derivate dalla SoT) è unanimemente indicato come il pattern giusto. Due strade, entrambe semplici/lineari:
(A) **sotto-cartelle di dominio dentro `tests/runtime/`** (un solo `git mv`, un solo albero conftest) — la scelta
minima; (B) **co-locare** i test in `runtime/<package>/tests/` come scheduler_v2 — più pura ma più churn. Raccomando
(A) ora + estendere il pattern scheduler_v2 ai package nuovi; entrambe raccolgono lo stesso set con pytest.

**I 4 layer, separati per CARTELLA (non per framework):**
1. **unit** = `tests/runtime/` (sotto-cartelle di dominio) + `scheduler_v2/tests/` + i **5 test rimpatriati** da e2e
   (`test_relevance_gate_dedup`, `test_quality_gate`, i 3 lint statici) e i 2 `e2e_google_*` che stanno in tests/.
   Fasce §8.4 = marker `@pytest.mark.{smoke,sanity,full}`.
2. **e2e** = `e2e/scenarios/` SOLO turni reali §8.5 + `driver/`+`corpus/` + `tests/internal/` (release-gate). `smoke.py`
   (+`smoke_imports`) = battery pre-deploy viva.
3. **bench** = UNA cartella coi soli 3 gate vivi (`intent_accuracy`, `routing_subset`, `compound_scaling`).
4. **research/archive** = `e2e/simulator` + `poc` + `bench_archive` + `stress` → **fuori dal working tree** (git tag
   `research-archive-2026-06`, recuperabili da history). `runtime/testing/` idem, **ma dietro decisione §8.1**.

**Costo/rischio**: `git mv` di massa (storia preservata) + import via il conftest unico; l'archiviazione è la mossa a
rischio più basso (i file non sono collected oggi). Da fare a suite verde, in commit separati (1: basi conftest; 2:
archivio layer; 3: sotto-cartelle dominio; 4: rimpatrio misplaced). Equipotenza verificata meccanicamente a ogni
passo: **`pytest --collect-only` before == after**.

## 4. Cluttering ambientale collegato (dal cleanup 22/7)
- Worktree stantìo `.claude/worktrees/agent-a2dfb0f3c3d0f720c/` duplica tests/runtime → rimuovere.
- `e2e/tmp/` (5 GB, 17 run-dir) + `e2e/simulator/parse_cache*` → rigenerabili (vedi `cleanup_ambiente_22_7.md`).

## 5. Sintesi provvisoria
La codebase di test è **più sana del codice di prodotto** (gli audit codice/manifest hanno trovato più bug
reali). Il problema non è copertura sbagliata ma **cluttering strutturale**: 393 file piatti, boilerplate
ripetuto, fasce d'esecuzione come script separati, tooling sperimentale mescolato agli e2e. La ristrutturazione
proposta è a **rischio basso ed equipotenza verificabile** (`--collect-only` before/after).

## 6. Reperti per cluster (completati: 3/8)

### 6.a — sites / backend / i18n / skill / device / safety (~55 file campionati)
- **[SICUREZZA-TEST] conftest zero-pollution è opt-in, non default-deny** (`conftest.py:256`): la docstring
  promette «default-deny + opt-out `_REAL_HOME_TESTS`» ma il codice è un'allowlist (`if not in _POLLUTING_TESTS:
  yield`), e `_REAL_HOME_TESTS` **non esiste**. Un test nuovo che scrive via `Path.home()` NON è protetto —
  è l'incidente dell'8/5 (67667 foto cancellate) ancora aperto. **Priorità alta**: invertire in default-deny reale.
- **[necessità] `_POLLUTING_TESTS`: 10/12 entry puntano a file CANCELLATI** → potare la lista.
- **[necessità/codice-morto] `admin/i18n_migrate_v2.py` (126 LOC)** — unico caller è il suo test; la migrazione
  è già inline+idempotente in `i18n.py::_open`. Eliminare modulo + `TestI18nMigrationV2` (~200 LOC). Tenere il
  resto di `test_i18n_db_v2.py` (vivo). **`admin/i18n_migrate_manifests.py` (113 LOC)**: zero caller, zero test → morto.
- **[dup] `test_seed_i18n_gate_keys` + `test_i18n_catalog_hygiene`** → fondere (stesso seed DB, guard data-driven).
- **[necessità §7.1] migrazioni one-shot già eseguite in prod** (fastpath v1, shard skill_audit) → ritiro
  congiunto codice+test (~140 LOC) previa conferma DB prod migrato.
- **[consolida] `test_executor_standard_*`**: `_manifest()`/`_catalog()` copie verbatim in ~10 file (conferma il
  cluster executor). **`test_sites_security.py` = 5184 LOC / 159 test in UN file, 8 soggetti** → split per soggetto.
- **[watch] `test_sandbox_skill_backed` (5 segnali provider)**: vivi solo finché esistono executor legacy (ADR 0193)
  → marcare scadenza. **Modelli SANI**: 3 strati sites (unit-mock→sim opt-in→e2e reale), famiglia credentials per-ADR, drift-guard rigenera-e-confronta.
- **Bilancio cluster**: ~4 file e ~700 LOC secchi + ~130 boilerplate; nessun test vivo perde copertura.

### 6.b — promoter (5 file test)
- **[BUG DI PRODOTTO scoperto, non di test]** `channels/daemon.py:1096` invia via Telegram l'URL
  `/admin/promotions/review` — **route rimossa il 13/6**; col digest aggregato di default (≥3 pending) l'admin
  riceve SOLO il bottone verso l'URL morto → **il ciclo di review umana del promoter è amputato in prod**. Si
  somma al doppio-lifecycle promoter(opt-out) vs change_intents(opt-in) già rilevato nell'audit codice [T4/LG-1].
- **[dup/falsi] `test_promoter_daemon.py`**: 3 test falsi/duplicati (rollback su route morta mai colpita;
  callback rollback ×3 identici; split callback-data tautologico) + 2 test misplaced (target `promoter_example`) → ~120 LOC.
- **[consolida] `test_promoter_v2_commentary` + `_perf_savings`** (stesso target `promoter_example`) → 1 file;
  fixture proposta-5-stadi duplicata 3× nel cluster.
- **[semi-orfano] `test_promoter_v2_review_form`**: target `promotions_review.py`, superficie HTTP rimossa →
  decisione a monte (reintegrare route vs ritirare il form ~760 LOC), non un fix di test.
- **[rename] `test_count_cap_promote.py`** è engine/dispatch (collisione lessicale «promote») → `test_dispatch_count_cap.py`.

### 6.c — executor + builtin entries (già in §2): sana, debito strutturale, ~−650 LOC/−9 file.

### 6.d — change / telos / aging / mnestoma / alignment / proposte (29 file)
- **0 test morti** (orfani proposals_unified DISPROVATI, vedi §2). Tutti i moduli hanno importer vivi.
- **[consolida] 3 file `test_align_*`** (`framework_objects`/`foreign_object`/`foreign_producers_v3`) testano tutti
  `dispatch._align_framework_objects` con harness `_Cat`/`_align` triplicato → 1 file, 3 classi (tenere tutti i test).
- **[dup] fixture ripetute**: scaffold change-* (`multi_tool_paths`+`canonical_query_log` in applier+observer),
  builder telos-proposal in 3 file, `_make_proposal` 5-stadi (condiviso col promoter) → helper condivisi (~150 LOC, 0 file).
- **[misfiled] 2 falsi positivi da collisione di nome**: `test_change_files_format_undo.py` (è executor-undo),
  `test_url_reader_jit_alignment.py` (è routing engine) → fuori cluster, non toccare la logica.
- **[necessità-OK] tenere** i test che asseriscono i RITIRI (specialize/generalize, ADR 0180 anti-regressione) e il
  killer `layer_overlap` (vivo). **Modelli SANI**: alignment_engine, proposal_evaluator (11 killer), fastpath_promote, learning_loop.
- **Bilancio**: 0 tagli per deadness; ~3 file/~150 LOC di sola consolidazione.

### 6.e — engine / routing / planner / prefilter (30 file, 7.644 LOC)
- **Sano, 0 test morti.** `test_engine_v2.py` testa il package vivo `runtime/engine/` (rename → `test_engine_core.py`).
  Il ramo non-v3 (metis) è ancora nel sorgente (ADR 0177, ~3000 LOC morte non ancora rimosse) → `test_v2_untouched_keeps_phantom`
  resta valido finché il ramo esiste. Prefilter `*_v2`: registrate ma prod usa `legacy` → **nessun test le esercita** (superficie A/B sorgente, non test morto).
- **[dup] `_FastpathDbCase` + `_fw()` triplicati** nei 3 file fastpath → helper condiviso (~55 LOC).
- **[sussunzione] `test_engine_v2::TestFastpathRoundTrip`** ⊆ `test_fastpath_lifecycle` → elimina (~33 LOC).
- **[consolida] cacheability** sparsa su 3 file (`is_query_specific`/`_should_cache_plan`) → 1 `test_cacheability.py`.
- **[consolida] 2 file `test_align_foreign_*`** → merge (stessa fn, rami diversi) = i 3 align di 6.d.
- **[FRAGILE — segnalare] test che leggono il SORGENTE/PROMPT come testo**: `test_planner_routing_unified` (assert
  substring letterali in `photos.j2` → rompe a ogni edit Fable), `test_routing_pool::test_dispatch_e_bench_condividono_la_pool_build`
  (grep su `dispatch.py`) → riscrivere comportamentali. **[boundary] `test_planner_routing_composition`** carica catalog+prefilter reali = mini-bench come unit (l'analogo di natural_paraphrases qui) — confine unit/bench sfumato.
- **Bilancio**: ~1 file/~150-170 LOC; il guadagno è coesione (rename engine_v2, home unico cacheability/alignment), non LOC.

### 6.f — LAYER oltre-unit: e2e / simulator / stress / smoke / bench / poc / testing  ⭐ **il grosso del cluttering**
**~78 file / ~21.300 LOC archiviabili a COPERTURA INVARIATA** (verificato: nessuno pytest-collected, nessuno gatea
il deploy, nessuno importato da prod; liveness dipende solo da `run.sh`/`deploy.sh`/chiamata manuale — nessuna CI/cron).

| Blocco | File | LOC | Stato (con prova) | Azione |
|---|---|---|---|---|
| `e2e/simulator/` | 37 | 9.354 | MORTO/storico (congelati 3-5/6, `typing_cache/` VUOTA, port regole→prod chiuso) | archivia (git tag) |
| `runtime/testing/` | 5 | 4.670 | SUSSUNTO da pytest (0 importer, `tests.db` fermo 19/5; `populate_cases.py`=3.974 LOC seed hardcoded §7.3) | **decisione §8.1** poi ritira |
| `runtime/bench_archive/` | 13 | 3.285 | MORTO auto-dichiarato (il README lo dice; 0 import prod) | archivia/storia |
| `runtime/stress/` | 14 | 2.771 | storico (milestone synt 35q/50q chiusa, v1-accanto-v2) | archivia |
| `runtime/poc/` | 8 | 1.060 | MORTO («product-dead, inert», audit 29/6) | elimina |
| `runtime/smoke_en.py` | 1 | 189 | DUP di smoke.py (reimplementa l'harness) | fondi → `smoke.py --lang en` |

- **VIVI, intoccabili**: `e2e/scenarios/` (turni reali §8.5) + `driver/`+`corpus/`+`conftest`; `smoke.py`+`smoke_imports.py`
  (gate pre-deploy ADR 0114/0159); `tests/internal/` (release-gate); i **3 bench-gate vivi** (`intent_accuracy`, `routing_subset`, `compound_scaling`).
- **[misplaced da rimpatriare in `tests/runtime/`]**: `e2e/scenarios/test_relevance_gate_dedup.py` (importa un executor runtime
  = unit, viola la garanzia zero-import di e2e), `test_quality_gate.py` (unit dello strumento), e 3 lint statici
  (`test_prompts_frontmatter`/`_symmetry`, `test_universal_path_audit`) — sono lo specchio dei 2 `e2e_google_*` che stanno in tests/.
- **[cluttering] 5 sedi di bench** (`bench/` + bench_archive + stress + poc + simulator) misurano tutte routing/latency →
  consolidare in UNA con i soli gate vivi; il resto = storia git.
- ⚠️ **Nodo che richiede DECISIONE (non taglio meccanico)**: `runtime/testing/` è ancora citato da §8.1 / ADR 0023/0029
  come protocollo «aggiorna DB test → module+cluster». Ritirarlo richiede aggiornare §8.1 contestualmente, non un delete silenzioso.

### 6.g — infra: http / admin / telegram / pairing / scheduler_v2 (22 + 20 file)
- **[necessità] test su route RIMOSSE** (13/6): `test_http_server::test_admin_proposal_action_404/_invalid_action`
  colpiscono `/admin/proposals*` (morta) e passano solo via 404-default → rimuovere; `e2e/.../test_admin_pages_render`
  ha 4 voci morte (`/admin/proposals*`, `/admin/promotions`) nel ramo «404 ammesso» → sfoltire. (Convergono col bug promoter §6.b.)
- **[dup] `scheduler_v2/tests/test_dst.py` ⊆ `test_parser.py`** (stesse fixture DST) → fondi i 2 casi unici, −1 file.
- **[consolida — il pezzo mancante] harness HTTP replicato** (`setUpClass`+`make_app`+reload, ~35 LOC/file **con drift
  nel set di reload**) in 5 file → base `AioHTTPTestCase` condivisa in conftest. È la duplicazione che nasce PROPRIO
  dalla `tests/runtime/` piatta.
- **[sussunzione] unit admin-render ⊆ e2e** `test_admin_pages_render` (rende tutte le pagine su server reale) → negli
  unit tenere solo ETag/304 + auth-403 + asserzioni di contenuto, sfoltire i «200+render» ridondanti.
- **[necessità-condizionata] `test_migrate_v1`**: il test è FEDELE (`migrate()` gira a ogni boot); il ritiro è una
  decisione di PRODOTTO (modulo+boot-call+test insieme, se il cutover v1 è dichiarato chiuso) = +~556 LOC.
- **[MODELLO SANO da estendere] `scheduler_v2/tests/` co-locato**: conftest locale (`db_path`/`run_async`), fixture
  derivate dalla SoT (`_BUILTIN_JOBS`, immuni allo stale), layering per-modulo. I test `*idempotent` qui sono REALI (non tautologici).
- **Bilancio**: ~190 LOC/−1 file subito; +556 LOC/−1 file condizionati alla decisione migrate_v1.

## 7. Totali (7/7 cluster completati)

**Verdetto**: la suite unit è **sana** — nessun cluster ha trovato test morti significativi, e i pochi orfani del mio
scan iniziale erano **falsi positivi** (moduli già cancellati / nomi `_v2` che testano codice vivo). Il debito è di
DUE nature nette:

| Natura | Dove | Entità | Perdita copertura |
|---|---|---|---|
| **Cluttering di layer** (storia/morto/sussunto) | e2e/simulator, runtime/testing, bench_archive, stress, poc, smoke_en | **~78 file / ~21.300 LOC** | **ZERO** (nessuno pytest-collected né gate deploy) |
| **Debito strutturale unit** (boilerplate/fixture/copie) | tests/runtime piatta | **~18 file consolidati / ~2.300 LOC** | **ZERO** (tutti i test restano, come parametrize/fixture) |
| **Codice-morto di prodotto svelato dai test** | admin/i18n_migrate_v2+manifests; route /admin/proposals; migrate_v1 (cond.) | ~313 LOC + decisioni | n/a (non test) |
| **Test morti secchi** | promoter (falsi ×3), http (route morte ×2) | ~135 LOC | ZERO (codice non esiste) |

**Totale de-cluttering a copertura invariata: ~85+ file e ~24.000 LOC** (dominato dal layer oltre-unit).
**Equipotenza verificabile meccanicamente**: `pytest --collect-only` prima==dopo (gli archiviati non sono collected
oggi; i consolidati mantengono lo stesso set di test).

## 8. Bug di prodotto scoperti DALL'analisi dei test (da riportare negli audit codice)
- **URL promoter morto** (`channels/daemon.py:1096` → `/admin/promotions/review`, rimossa 13/6): con digest aggregato
  di default (≥3 pending) il ciclo di review umana del promoter è **amputato in prod**. Converge con [T4/LG-1] (doppio lifecycle).
- **conftest zero-pollution è opt-in, non default-deny** (`conftest.py:256`): la docstring promette un default-deny e
  una `_REAL_HOME_TESTS` che **non esistono** → l'incidente 8/5 (67667 foto cancellate) resta riaperto per ogni test nuovo.
- Moduli admin morti (`i18n_migrate_v2.py`, `i18n_migrate_manifests.py`) e 4 route admin rimosse ancora referenziate.

## 8. Nota trasversale emersa: bug di prodotto scoperti DALL'analisi dei test
Due bug reali di prodotto sono emersi guardando i test (non erano l'obiettivo): l'URL promoter morto
(`daemon.py:1096`) e il default-deny mancante nel conftest. Da riportare negli audit codice, non solo qui.
