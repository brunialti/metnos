---
id: 0158
title: Unified change-intent lifecycle — un solo oggetto per tutte le proposte di cambiamento del sistema
date: 2026-05-22
status: accepted   # Roberto ha scelto opzione A (full migration) 22/5/2026
area: architecture/lifecycle
related:
  - 0157  # AlignmentEngine + dashboard proposals
  - 0156  # Naming Authority + 10 lenti telos
  - 0078  # Admin HTTP routes + base.html
  - 0114  # Synth admission policy (4 layers)
  - 0122  # Proposal auto-evaluator
related_doc:
  - docs/it/architecture/lifecycle.html
---

# Sommario per il sopravvissuto

> «Voglio capire cosa sta cambiando nel mio sistema. Oggi ho 5 tab, 7 schemi, 9 file e l'utente vede 481 oggetti al primo giro.»

Questo doc propone di **fondere** le 6 sorgenti di "proposte" e i 4 lifecycle separati in **un solo oggetto** (`change_intent`) con **un solo state machine** e **una sola UI** (`/admin/changes`).

Il modello mentale unico è: **proposta → accettata → applicata → osservata → consolidata** (o rollbackata). Tutto il resto sono dettagli interni che non finiscono mai sulla pagina dell'utente.

---

# 1. Stato attuale — sei sorgenti, nove storage, sette state machine

## 1.1 Le sorgenti di "proposte"

Oggi nel sistema, **sei moduli diversi** producono cose che chiamiamo "proposte" o "promozioni". Sono:

| # | Sorgente | Cosa propone | Esempio |
|---|----------|--------------|---------|
| 1 | **Telos engine** (10 lenti notturne) | Modifiche/estensioni del pool di executor | scamper propone "combina `get_files_metadata + create_events` in una pipeline deadline-to-calendar" |
| 2 | **Introvertiva** (3 kind: dedupe/generalize/specialize) | Razionalizzazioni del catalog | dedupe: "`list_processes` e `get_processes` sono duplicati, unificali"; specialize: "`find_files` con `time_window='last-7d'` è usato 156 volte, materializza" |
| 3 | **Synt multistage** (request_new_executor) | Nuovo executor da generare | utente chiede "manda PDF firmato" → no executor adatto → synt genera `sign_files_pdf` |
| 4 | **Multi-tool paths** (L2 chain cache) | Pipeline ricorrenti da memorizzare | "questa catena `find→filter→write` è stata usata 49 volte, candidate per fast-path" |
| 5 | **Canonical query log** (L1 single-tool) | Query → tool mapping veloce | "`controllare temperatura cpu gpu` → get_processes, vista 59 volte" |
| 6 | **Feedback utente** (✓/✗) | Validazioni o rifiuti di pattern già visti | "ho premuto ✗ su questa pipeline, non riproporla" |
| 7 | (futuro) **Skill importer** | Import executor da agentskills.io | wrapper per skill esterna `mock-up_anything` |

## 1.2 Storage — dove vive ogni cosa

| Sorgente | File / DB | Tipo | Records oggi |
|----------|-----------|------|--------------|
| Telos | `~/.local/share/metnos/telos_proposals.jsonl` | JSONL append | **481** |
| Introvertiva | `~/.local/state/metnos/proposals_state.db` | sqlite | 58 (1+5+52) |
| Synt | `~/.local/share/metnos/synt_proposals/*.json` | file-per-proposal | **253** |
| Multi-tool L2 | `~/.local/share/metnos/multi_tool_paths.sqlite` | sqlite | 22 |
| Canonical L1 | `/opt/metnos/workspace/.mnestoma/mnest.sqlite::canonical_query_log` | sqlite | 73 |
| Feedback utente | `~/.local/share/metnos/turn_feedback.jsonl` | JSONL append | 28 |
| Marker post-accept | `~/.local/share/metnos/proposal_accepts/{synt,change,pipeline}_pending/` | file-per-marker | (in costruzione) |
| Promotion lifecycle | `~/.local/state/metnos/executor_stats.db::executor_history` | sqlite append | 194 events |
| Decisioni utente telos | `~/.local/share/metnos/telos_decisions.jsonl` | JSONL append | varia |

**Totale**: 9 storage diversi. Schema diverso. Naming diverso. Lifecycle diverso.

## 1.3 Le state machine — sette grammar diverse

```
proposals_state (introvertiva)  : pending → applied | dormant | rejected | blocked
multi_tool_paths (L2)          : candidate → shadow → active → demoted
canonical_query_log (L1)       : candidate → active → demoted
synt_proposals (synth)         : in-progress → installed | rejected | rejected_semantic_drift
executor_history (promotion)   : created → first_used → deprecated | archived | undeprecated | auto_applied
turn_feedback (user)           : (none — append-only)
telos + dashboard              : pending → accept | reject | stage
```

**Ognuno dei 7 ha un suo "completed", un suo "abandoned", un suo "in vita"**. Nessuno parla la stessa lingua.

## 1.4 Naming — sette identificatori per la stessa cosa

| Sorgente | ID del "qualcosa che propone un cambio" |
|----------|------------------------------------------|
| Telos | `prop_id` (timestamp microsecondo) |
| Introvertiva | `sig_key` (JSON list serializzata) |
| Synt | `expected_name` + `path_hash` |
| Multi-tool L2 | `id` (autoincrement) + `path_shape_hash` |
| Canonical L1 | `(canonical_query, tool_name, args_shape)` chiave composita |
| Feedback | `turn_id` |
| Promotion | `name` (executor name) |

L'utente che vede "find_recipes" nella pagina /admin/proposals non sa che lo stesso `find_recipes`, dopo accept, diventa un record in `synt_proposals/`, poi un row in `executor_stats`, poi un evento in `executor_history`. Vede 4 oggetti diversi che sono in realtà **lo stesso oggetto in 4 fasi della sua vita**.

## 1.5 UI attuale — 3 pagine, mental model frammentato

```
/admin/proposals           → telos (481) + introvertiva (58) = 539 oggetti
/admin/proposals/telos     → telos only (deprecated, da rimuovere)
/admin/proposals/introvertiva → introvertiva only (deprecated, da rimuovere)
/admin/promotions          → executor lifecycle (grace/finalized/rolled_back)
/admin/synth-proposals/{id}/evaluate → synt evaluator (one-off per id)
```

L'utente:
- Vede 539 oggetti nella prima pagina
- Sa che "promotions" esiste ma non sa quando/perché ci finisce
- Non vede MAI gli oggetti synt_proposals (sono accessibili solo per evaluate ad-hoc)
- Non vede multi_tool_paths / canonical_query_log (sono cache interne)

**Conseguenza pratica**: «accetto una proposta. Cosa succede dopo? Boh, sparisce.»

---

# 2. Le asimmetrie che fanno male

## 2.1 Asimmetria di osservabilità post-accept

**Tre destini diversi a seconda della sorgente**:

```
Sorgente             post-accept                visible?
─────────────────────────────────────────────────────────
telos new_valid    → synt_pending marker     →  NO (file in cartella)
telos existing_*   → change/pipeline marker  →  NO (file in cartella)
introvertiva       → proposals_state.applied →  /admin/proposals only
synt request       → executor installed      →  /admin/promotions
```

Un utente che accetta 4 proposte di tipo diverso vede 4 destini diversi.

## 2.2 Asimmetria di ranking

| Sorgente | Score | Range tipico |
|----------|-------|--------------|
| Telos | `expected_alignment` (formula α·top + γ·rest) | 0.14 - 0.52 |
| Introvertiva | `n_seen * 0.05 + last_uses * 0.005` | 0.0 - 1.0 |
| Synt | `judge_score + cost_ratio + ...` (multi-componente) | 0.0 - 1.0 |
| Multi-tool L2 | `uses` (integer) | 1 - 100+ |
| Canonical L1 | `uses` (integer) | 1 - 59 |

Cross-source comparison impossibile. La dashboard unified li sorta per "ranking_score" ma stai mescolando mele e arance.

## 2.3 Asimmetria di lifecycle

| Sorgente | Fase di osservazione post-accept |
|----------|-----------------------------------|
| Telos new_valid | NESSUNA (marker file, niente tracking) |
| Telos existing_parametric | NESSUNA (marker file) |
| Telos existing_pipeline | NESSUNA (marker file) |
| Synt | Sì — `promotion: grace → finalized` |
| Multi-tool L2 | Sì — `candidate → active → demoted` |

**Buco**: per le proposte telos "existing_parametric" (es. "aggiungi arg X a executor Y"), dopo accept non sai mai se la modifica è stata applicata, se funziona, se va rollbackata.

## 2.4 Asimmetria semantica

Le sorgenti dicono cose DIVERSE ma le mostriamo uguali:

```
telos:scamper          → "rimagina executor X usando metafora Y" (creativo)
telos:endgame_book     → "se accade evento E, esegui cascata C" (pattern temporale)
introvertiva:dedupe    → "X e Y sono duplicati" (cleanup)
introvertiva:specialize → "executor X con args Y usato N volte" (materializzazione)
synt request           → "manca capacità X" (dichiarazione di assenza)
multi_tool L2 chain    → "questa catena ricorre, memoizzala" (cache promotion)
```

Mostrarli tutti nello stesso elenco confonde. Non sono comparabili 1:1.

---

# 3. La logica comune che NON stiamo sfruttando

Tutti gli oggetti delle 6 sorgenti rappresentano UN'UNICA cosa:

> **«Una richiesta di cambio al sistema, in qualche stato del suo ciclo di vita.»**

Le fasi sono le stesse per tutti:

```
   ┌─────────────────────────────────────────────────────────┐
   │  PROPOSED → ACCEPTED → APPLIED → OBSERVED → FINALIZED   │
   │       ↓        ↓          ↓        ↓                    │
   │   REJECTED  REJECTED   FAILED  ROLLED_BACK              │
   │       ↓                                                 │
   │    STAGED                                               │
   └─────────────────────────────────────────────────────────┘
```

Cosa cambia tra le sorgenti? Solo:
- **CHI propone** (origin)
- **COSA propone** (intent: create / extend / use / cache / dedupe)
- **CON QUALE EVIDENZA** (n_observations, ea, uses, ecc.)

Tutto il resto — accept, decisione, applicazione, monitoraggio, rollback — è **identico**.

---

# 4. Proposta — un solo oggetto: `change_intent`

## 4.1 Schema unico

```python
@dataclass
class ChangeIntent:
    # --- identità ---
    id: str                          # UUID stabile cross-sorgente
    
    # --- chi propone ---
    origin: dict                     # {family, module, source_id, discovered_at}
    # family ∈ {telos, introvertiva, synt, user, observation}
    # module = nome specifico (scamper, dedupe, request_new_executor, ...)
    # source_id = ID nel sistema originario (per backward compat)
    
    # --- cosa propone ---
    intent: dict                     # {kind, target_name, summary, rationale, body}
    # kind ∈ {create_executor, extend_executor, dedupe_executors,
    #          materialize_pipeline, cache_pattern, reject_pattern}
    # target_name = str  (executor o pattern coinvolto)
    # summary = 1 frase user-facing
    # rationale = paragrafo motivazione
    # body = dict campi specifici per kind
    
    # --- ranking normalizzato cross-source ---
    ranking: dict                    # {score: 0-1, confidence: 0-1, convergence: int}
    # score = mappato da ea/uses/n_seen → 0-1 con scaler per family
    # convergence = n. di altre change_intent con stesso fingerprint semantico
    
    # --- stato e decisione utente ---
    state: str                       # vedi state machine
    decision: dict | None            # {by, ts, action, reason}
    
    # --- effetto post-accept ---
    effect: dict | None              # {applied_at, executor_name, promotion_id, observed_metrics}
```

## 4.2 State machine unico

```
┌─────────────┐
│  PROPOSED   │  ← appena nato (da qualsiasi sorgente)
└─────────────┘
       │
   ┌───┴───┐
   │       │
   ▼       ▼
ACCEPT  STAGE     REJECT
   │      (in attesa, decay naturale)
   ▼
┌──────────┐
│ ACCEPTED │  ← utente ha detto sì
└──────────┘
       │
       ▼  (cron / daemon applica)
┌──────────┐
│ APPLIED  │  ← cambio fisicamente avvenuto (executor creato / arg aggiunto / pipeline cached)
└──────────┘                                              
       │
       ▼  (grace period, monitoring)
┌──────────┐                              ┌──────────┐
│ OBSERVED │ ──── nessun problema in N ──▶│FINALIZED │
└──────────┘     (7gg default)            └──────────┘
       │
       ▼  (errore / regressione)
┌────────────┐
│ROLLED_BACK │
└────────────┘
```

**Sei stati che bastano per tutto**. Compreso il vecchio `dormant` (= STAGED nel nuovo), il vecchio `grace` (= OBSERVED), il vecchio `archived` (= ROLLED_BACK).

## 4.3 Esempio "for dummies" — find_recipes

### Atto 1 — Proposed

Il telos engine notturno gira la lente SCAMPER. Lo SCAMPER suggerisce:

> «Modifica `find_files` aggiungendo `kind=recipe` per matchare file `.md` in `~/Documents/Recipes`.»

Sistema crea:

```yaml
change_intent:
  id: "ci_b1a2c3"
  origin:
    family: telos
    module: scamper
    source_id: "1779382036.195886"
    discovered_at: 2026-05-22T02:31:14
  intent:
    kind: extend_executor
    target_name: find_files
    summary: "Estendi find_files con kind=recipe (filtra .md in ~/Documents/Recipes)"
    rationale: "L'utente cerca ricette settimanalmente. Aggiungere kind=recipe riduce 3 step a 1."
    body:
      arg_to_add: kind
      arg_value_example: "recipe"
  ranking:
    score: 0.52
    confidence: 0.8
    convergence: 1   # solo scamper l'ha proposto
  state: PROPOSED
  decision: null
  effect: null
```

Lo vedi nella pagina `/admin/changes`, tab «📋 Proposte», con score 0.52.

### Atto 2 — Accepted

Tu clicchi ✓. Sistema scrive:

```yaml
decision:
  by: roberto
  ts: 2026-05-22T18:35:00
  action: accept
  reason: "ho cercato ricette 4 volte questa settimana"
state: ACCEPTED
```

L'oggetto si sposta nel tab «⏳ Da Applicare».

### Atto 3 — Applied

Daemon notturno legge gli ACCEPTED, applica:
- Modifica `find_files` manifest aggiungendo arg `kind` (con enum: recipe, photo, ...)
- Aggiorna codice e re-firma
- Riavvia servizio
- Aggiorna change_intent:

```yaml
state: APPLIED
effect:
  applied_at: 2026-05-22T22:00:00
  executor_name: find_files
  diff_summary: "+ args.properties.kind (enum: recipe, photo)"
  rollback_blob: "/var/lib/metnos/rollback/find_files_pre_b1a2c3.toml"
```

Vedi nel tab «🔧 Applicate (in osservazione)».

### Atto 4 — Observed

Per 7 giorni il sistema monitora:
- `find_files(kind=recipe)` viene chiamato 5 volte → tutte successo → 0 fail
- Nessun pattern di regression

```yaml
state: OBSERVED
effect:
  ...
  observed_metrics:
    calls_with_new_arg: 5
    success_rate: 1.0
    observation_window_days: 7
```

### Atto 5 — Finalized

Dopo 7gg senza problemi:

```yaml
state: FINALIZED
```

L'oggetto si sposta nel tab «✅ Consolidate». Non lo vedi più di default (ma è cercabile per audit).

### Atto alternativo — Rolled-back

Se in fase OBSERVED qualcosa rompe (es. fail_rate > 0.3 o utente preme ✗ sul cambio):

```yaml
state: ROLLED_BACK
effect:
  ...
  rolled_back_at: 2026-05-25T10:15:00
  rolled_back_reason: "utente fb negativo: 'kind=recipe non funziona su .md case-insensitive'"
```

Rollback fisico: ripristina `find_files` dal `rollback_blob`, re-firma, restart.

## 4.4 UI unica — `/admin/changes`

```
┌─────────────────────────────────────────────────────────────────────┐
│ Cambiamenti al sistema                                              │
│                                                                     │
│ [📋 Proposte 30]  [⏳ Da Applicare 5]  [🔧 In Osservazione 12]      │
│ [✅ Consolidate 47]  [❌ Scartate 8]    [👁️ Tutte (audit)]          │
│                                                                     │
│ Filtri: origin▾  intent.kind▾  min_score▾                           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ score │ proposta (1 riga)                       │ origin   │ azioni │
├───────┼─────────────────────────────────────────┼──────────┼────────┤
│  0.52 │ Estendi find_files con kind=recipe      │ scamper  │ ✓ ✗ ⏸  │
│  0.48 │ Unifica list_processes → get_processes  │ dedupe   │ ✓ ✗ ⏸  │
│  0.45 │ Crea sign_files_pdf (manca capability)  │ synt     │ ✓ ✗ ⏸  │
│  0.43 │ Cache pipeline find→filter→write (49x)  │ L2 chain │ ✓ ✗ ⏸  │
└─────────────────────────────────────────────────────────────────────┘
```

**3 colonne base**. Tutto il resto in dettaglio espandibile su click. Default = solo top 30 in PROPOSED.

## 4.5 Le 6 sorgenti restano interne (cache, log)

Le sorgenti attuali (telos_proposals.jsonl, proposals_state.db, ecc.) **restano come "input feed"** ma vengono normalizzate in `change_intent` da un livello di compat.

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ telos engine │  │ introvertiva │  │ synt request │   ...
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       ▼                 ▼                 ▼
   adapter_telos    adapter_intr      adapter_synt
       │                 │                 │
       └────────┬────────┴────────┬────────┘
                ▼                 ▼
        ┌─────────────────────────────┐
        │   change_intents (sqlite)   │  ← single source of truth
        │   un solo schema, un solo   │
        │   state machine             │
        └─────────────────────────────┘
                       │
                       ▼
                 /admin/changes (UI)
```

L'utente vede SOLO `change_intent`. Le sorgenti diventano dettagli implementativi del feed.

---

# 5. Migrazione — piano in 3 fasi

## Fase 1 (1-2 giorni) — Adapter layer

Implementa adapter read-only: legge dai 6 storage attuali e proietta in vista virtuale `change_intent`. Niente migrazione dati fisica, solo presentazione.

UI nuova `/admin/changes` mostra la vista. Le vecchie pagine restano per compat.

## Fase 2 (2-3 giorni) — Decisione e applicazione unificate

`apply_decision_unified` scrive un record `change_intent` nello storage canonico (nuova sqlite) e propaga al sorgente originale (idempotente).

Daemon `change_applier` legge gli ACCEPTED e applica fisicamente:
- create_executor → invoca `synt_multistage.run_full` (no più chiamata diretta da request_new_executor)
- extend_executor → modifica manifest in place + rollback_blob + re-sign
- dedupe_executors → alias setup
- materialize_pipeline → registra in multi_tool_paths come `active`
- cache_pattern → registra in canonical_query_log come `active`

## Fase 3 (2-3 giorni) — Lifecycle observer

Daemon `change_observer` monitora APPLIED:
- Per executor synth: tracking calls + success_rate via executor_stats
- Per extend_executor: tracking calls con arg nuovo
- Per materialize_pipeline: tracking hit rate fast-path

Dopo 7gg senza problemi → FINALIZED. Se fail_rate > soglia o feedback negativo → ROLLED_BACK con ripristino.

Le vecchie pagine `/admin/proposals` e `/admin/promotions` deprecate (redirect a `/admin/changes`).

---

# 6. Costo/beneficio

## Costi

- ~6-8 giorni dev + test
- ADR aggiornamento doc 0157, 0078
- 1 migrazione storage (ma adapter prima — rollback facile)
- Coordinamento con telos engine, synt, multi_tool: niente refactor del LORO codice, solo adapter

## Benefici

- **1 modello mentale** invece di 7
- **1 UI** invece di 3
- **30 oggetti default** invece di 539
- **Lifecycle visibile end-to-end** (oggi: extend_executor invisibile post-accept)
- **Cross-source ranking** comparabile (score normalizzato 0-1)
- **Rollback prima classe** (oggi: solo per synth)
- **Convergence cross-source visibile**: se 3 sorgenti diverse propongono cose equivalenti, lo vedi come `convergence: 3` invece di 3 oggetti distinti

## Rischi

- Schema unico = perdita di dettaglio specifico → mitigato da `intent.body` come dict aperto
- Adapter rotti se cambia uno storage upstream → mitigato da test per ogni adapter
- Daemon applier su extend_executor è chirurgico (modifica manifest + re-sign) → richiede testing approfondito su executor critici

---

# 7. Decisione richiesta

Tre opzioni per Roberto:

**Opzione A — Andiamo (full migration)**: 6-8gg dev, lifecycle unificato end-to-end. Massimo beneficio mentale.

**Opzione B — Solo Fase 1 (adapter)**: 1-2gg, UI unica `/admin/changes` ma backend resta com'è. Beneficio UX immediato, niente rischio backend.

**Opzione C — Niente, status quo**: tieni 3 UI separate, accetti la complessità.

Mia raccomandazione: **B prima** (UX win immediato, basso rischio), poi **A** se l'esperienza mostra benefici concreti.

---

# 8. Implementazione (22/5/2026 v1.0)

Roberto ha scelto **opzione A — full migration**. Implementazione completata in giornata.

## 8.1 File-map

```
runtime/change_intents.py                      → schema + storage sqlite + state machine
runtime/change_intent_adapters/                → proiezione dei 6 storage legacy
  ├── __init__.py                              → iter_all() concat 6 sorgenti
  ├── _base.py                                 → score normalization helpers
  ├── telos.py                                 → telos_proposals.jsonl → ChangeIntent
  ├── introvertiva.py                          → proposals_state.db → ChangeIntent
  ├── synt.py                                  → synt_proposals/*.json → ChangeIntent
  ├── multi_tool.py                            → multi_tool_paths.sqlite → ChangeIntent
  ├── canonical.py                             → canonical_query_log → ChangeIntent
  └── user_feedback.py                         → turn_feedback.jsonl → ChangeIntent
runtime/change_intents_i18n.py                 → UI_CHANGE_* i18n bootstrap
runtime/change_applier.py                     → daemon ACCEPTED → APPLIED
runtime/change_applier_extend.py               → extend_executor handler (manifest patch + re-sign)
runtime/change_observer.py                     → daemon APPLIED → FINALIZED|ROLLED_BACK
runtime/change_rollback.py                     → rollback fisico per kind
runtime/jobs/change_intent_materialize.py      → cron daily materializer
runtime/templates/changes.html                 → UI /admin/changes
runtime/http_routes_admin.py                  → handler admin_changes + admin_change_action
runtime/config.py                              → DB_CHANGE_INTENTS
runtime/scheduler_v2/builtin_callbacks.py     → 3 entry: materialize, applier, observer
tests/runtime/learning/test_change_intents.py           → 15 test schema/state-machine
tests/runtime/learning/test_change_intent_adapters.py   → 10 test adapter
tests/runtime/learning/test_change_intents_ui.py        → 8 test UI handler
tests/runtime/learning/test_change_applier.py           → 7 test applier
tests/runtime/learning/test_change_applier_extend.py    → 7 test extend
tests/runtime/learning/test_change_observer.py          → 8 test observer + rollback
```

## 8.2 Scheduler triggers

| Job                          | Trigger     | Callback                       |
|------------------------------|-------------|--------------------------------|
| `change_intent_materialize`  | daily@01:00 | `task_change_intent_materialize` |
| `change_applier`             | every_10m   | `task_change_applier`           |
| `change_observer`            | daily@03:15 | `task_change_observer`          |

## 8.3 Misure su sistema reale (22/5/2026 baseline)

Prima esecuzione materializer (post-deploy):

- 866 ChangeIntent **yielded** dagli adapter
- 153 ChangeIntent **unici** post-dedup (82% dedup rate cross-source)
- by_state_real: 97 PROPOSED · 28 STAGED · 26 ROLLED_BACK · 2 FINALIZED
- Top 5 per score: `canonical:get_processes` 0.98, `user:reject_pattern×2` 0.90, `telos:scamper:create_events` 0.52, `telos:scamper:consult_frontier` 0.51, ...

## 8.4 Decisioni minori durante implementazione

- **Fingerprint dedup cross-source**: NON include `origin_family` → due sorgenti che propongono cose equivalenti coalescono in 1 record, con `convergence` bumpato.
- **Stati legacy mapping**: introvertiva `pending→PROPOSED`, `applied→FINALIZED`, `dormant→STAGED`, `rejected/blocked→REJECTED`. Multi-tool/canonical `candidate→PROPOSED`, `shadow→ACCEPTED`, `active→OBSERVED`, `demoted→ROLLED_BACK`. Synt `installed→FINALIZED`, `rejected*/abandoned*→ROLLED_BACK`.
- **Score normalization per family**: telos ea già 0-1; uses (multi_tool/canonical) via funzione log-saturante mid=20; n_seen (introvertiva) formula storica `n*0.05 + last_uses*0.005`; synt `installed=0.8 / abandoned=0.3 / rejected=0.05`; reject_pattern `min(0.9, 0.3 + 0.15*n_rejections)`.
- **TOML patch strategia**: append della sezione `[args.properties.<arg>]` in fondo al manifest (TOML 1.0 ammette sezioni nuove in qualunque ordine). Backup full pre-modifica in `rollback_blobs/<sha8>-<name>.toml`. Re-sign via `sign.sign_executor`.
- **Grace period default 7gg**: configurabile via env `METNOS_CHANGE_GRACE_DAYS`. Test usa 0 per finalize immediato.
- **Soft deprecation legacy UI**: `/admin/proposals` e `/admin/promotions` restano funzionanti ma mostrano banner giallo di redirect a `/admin/changes`. Rimozione fisica in PR successiva quando l'utente conferma usabilita' della nuova vista.

## 8.5 Cosa NON e' incluso (deferred)

- `apply_create_executor`: invoca `synth_request.handle_synth_request` esistente (~150s wall). Il daemon ok ma latenza alta.
- Auto-restart `metnos-http.service` dopo extend_executor: il loader non ricarica manifest hot. Restart manuale richiesto.
- Rollback `create_executor`: archive synth dir, ma il loader puo' tenere ancora l'executor in memoria fino al restart.
- Test integrazione end-to-end con LLM live (synth_request reale): skipped per evitare 150s/test.
- Migration "hard delete" delle 6 storage legacy: per ora restano come input feed. Si rimuoveranno quando il sistema sara' rodato.

## 8.6 Note operative

- Smoke test prima del deploy: `python3 -m pytest tests/runtime/test_change_*.py -v` (55 test).
- Materializer locale dry-run: `python3 -m runtime.jobs.change_intent_materialize` (idempotente).
- Inspezione DB: `sqlite3 ~/.local/state/metnos/change_intents.sqlite ".dump"`.
- Audit JSONL: `~/.local/share/metnos/audit/change_{intent_materialize,applier,observer}.jsonl`.

---

**Esito**: vista unificata `/admin/changes` operativa. Roberto vede UN solo elenco invece di 539 oggetti distribuiti su 3 UI. Lifecycle visibile end-to-end (proposed → finalized) con rollback fisico per kind. 55 test verdi.
