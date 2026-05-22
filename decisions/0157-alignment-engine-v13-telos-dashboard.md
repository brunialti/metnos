---
id: 0157
title: AlignmentEngine formula v1.3 (α·top + γ·rest) + rimozione t.coltivazione_strumenti + Dashboard /admin/proposals/telos
date: 2026-05-22
status: accepted
area: telos-engine
related:
  - 0156  # telos engine fase 1 + 10 lenti
  - 0078  # admin HTTP routes + base.html nav
related_doc:
  - docs/it/architecture/telos.html  # cap.3, cap.4, cap.5 aggiornati
  - docs/en/architecture/telos.html
complements:
  - 0156
---

## Context

Telos engine fase 1 (ADR 0156, 21/5/2026) wirata in produzione: 10 lenti
generano proposte introspettive notturne, 481 accumulate al 22/5/2026
mattina in `~/.local/share/metnos/telos_proposals.jsonl`. AlignmentEngine
(fase 2) implementato il 22/5 mattina con formula sum-product canonica
(`docs/it/architecture/telos.html` cap.4 originale):

```
expected_alignment = ( Σ peso_i · gate(fit_i, soglia_i) ) · urgency · confidence - bother_cost
```

Backfill LLM judge Gemma 4 26B locale (~9s/call, 71 min totali) ha
prodotto distribuzione `expected_alignment` con caratteristiche
problematiche:

- min 0.077 / max 0.569 / mean 0.302 / **stdev 0.075** / ratio 7.4x
- 6 proposte > 0.5 (top 1%), 238 > 0.3 (sopra media)
- Top 5 dominato da "documenti scadenza → calendar" (5 lenti diverse
  concordano: scoperta robusta)

**Diagnosi cardine** (cosa NON tornava):
Il Giudice teleologico discriminava bene per-telos (mean(max(fit)) = 0.87,
mean(stdev fit per proposta) = 0.32 — distribuzione bimodale, LLM "sa"
quali telos servono), ma l'aggregazione sum-product diluiva il segnale:
una proposta con fit=1.0 PERFETTO su `t.parsimonia` (peso 0.08) otteneva
contributo solo 0.08, mentre un tuttofare medio (fit≈0.5 su tutti i 7
telos) sommava ~0.5 → batteva regolarmente lo specialista.

Premio dato al "blando-su-molti", penalizzazione dello specialista
perfetto. Stile manageriale, non d'artigianato — opposto del segnale
voluto da Roberto: "in linea di massima una proposta che soddisfa
ampiamente un telos è importante".

## Decision

**Tre cambi coesi** applicati in pomeriggio 22/5/2026:

### 1. Rimozione `t.coltivazione_strumenti` (TELOS.md v1.2)

Backfill ha mostrato che questo "telos" si comportava da clausola di
runtime universale: qualunque proposta di estensione capacita' lo
soddisfaceva almeno parzialmente (soglia 0.10, la piu' bassa del set),
contribuendo rumore moderato su quasi tutte le 481 proposte. Mean ea
per generante t.coltivazione_strumenti = 0.312 (terzultimo dei 7),
ma fit > 0.10 osservato 478/481 volte come secondary contribution.

Decisione: rimuoverlo dal modello dei telos. La semantica anti-rinuncia
(esaurire la cascata synt prima di "non posso") rimane come **policy
deterministica** del runtime (synt_multistage), non come telos pesato.

Pesi ridistribuiti proporzionalmente sui 6 rimanenti:

| telos          | v1.1 (7 telos) | v1.2 (6 telos) |
|----------------|----------------|----------------|
| t.tempo        | 0.22           | **0.25**       |
| t.puntualita   | 0.18           | **0.20**       |
| t.protezione   | 0.18           | **0.20**       |
| t.ordine       | 0.13           | **0.15**       |
| t.coltivazione | 0.12           | (rimosso)      |
| t.discrezione  | 0.09           | **0.10**       |
| t.parsimonia   | 0.08           | **0.10**       |

### 2. Formula compose v1.0 → v1.3 (top + γ·rest con boost α)

Iterazioni di taratura:

**v1.2 (intermedia, 0.3·rest):**
```
ea_base = top + 0.3 · rest    dove top=max(contrib), rest=sum-top
```
Ordering corretto (specialista > tuttofare medio) ma stdev crollato
a 0.039 (vs v1.0 0.075). Causa: top1 limitato a [0.10, 0.25] = 5 valori
discreti dai pesi v1.2; persa dispersione utile.

**v1.3 (finale, α·top + γ·rest con α=2, γ=0.5):**
```
contrib_i = peso_i · gate(fit_i, soglia_i)
top = max(contrib_i)
rest = Σ contrib_i - top
expected_alignment = (α · top + γ · rest) · urgency · confidence - bother_cost
```

Vincolo critico **α > 3γ**: derivato algebricamente garantendo
specialista perfetto su t.tempo (peso 0.25, fit=1) > tuttofare medio
(fit=0.5 uniforme). Con (2.0, 0.5) margine di sicurezza 33%.

### 3. Dashboard `/admin/proposals/telos` (triage umano-in-the-loop)

Prerequisito per il "free-run" del telos engine notturno (ADR 0156
schedule daily@02:30): senza triage, 481+ proposte/giorno diventano
rumore. Stack: aiohttp + Jinja2 + htmx (ADR 0078 coerente).

**Componenti:**
- `runtime/telos_proposals_store.py`: load+filtri+stats su JSONL,
  persistenza decisioni in `~/.local/share/metnos/telos_decisions.jsonl`
  (append-only, LWW per `prop_id` = timestamp microsecondo).
- `runtime/http_routes_admin.py`: `admin_telos_proposals` GET +
  `admin_telos_proposal_action` POST con action ∈
  {accept, reject, stage}.
- `runtime/templates/proposals_telos.html`: 6 colonne (ea, origine,
  proposta, esempio applicabile, impatto, azioni) con buttons htmx.
- Link in `base.html` subnav + card riassuntiva in `dashboard.html`.

**Enrichment per riga** (deterministico, no LLM):
- `n_observed`: estratto da regex sul rationale ("N volte"/"N occorrenze").
- `example_query` + `current_path` + `current_latency_ms`: lookup nel
  turn log (14gg, mtime-cache) per il turno piu' rilevante che
  contiene il target executor (e idealmente tutti i tool menzionati
  nella proposta).
- `pipeline_observed`: chip a 3 stati: verde "pipeline osservata"
  (tutti i tool della proposta sono insieme in un turno reale),
  giallo "match parziale" (solo il target), grigio "speculativa"
  (target mai usato).
- `new_path_estimated`: euristica di collasso (sostituisce i tool
  menzionati con singolo step `target`).
- `latency_saved_ms_est`: `(len(current) - len(new)) × 5500ms`
  (mediana per-step osservata in produzione).

## Consequences

**Distribuzione 481 proposte recomposed con v1.3 (deterministico, no LLM):**

| Metrica | v1.0 sum-product | v1.2 top+0.3·rest | **v1.3 2·top+0.5·rest** |
|---------|------------------|-------------------|--------------------------|
| min     | 0.077            | 0.072             | **0.144** |
| max     | 0.569            | 0.278             | **0.524** |
| mean    | 0.302            | 0.176             | **0.338** |
| stdev   | 0.075            | 0.039             | **0.073** |
| ordering | ❌ invertito   | ✓ corretto       | ✓ corretto |

**Cutoff dashboard empirici v1.3:**
- > 0.50: 3 proposte (top 0.6%)
- > 0.45: **23** (5%, soglia review consigliata)
- > 0.40: 88 (top decile, 18%)
- > 0.30: 349 (top quartile, 73%)

**Top 6 stabili** (deadline → calendar, 4 lenti indipendenti concordano):
1. 0.524 scamper → create_events (combina get_files_metadata)
2. 0.514 scamper → consult_frontier (metadati documenti)
3. 0.512 scamper → compute_entries (log scadenze)
4. 0.500 scamper → create_events (consult_frontier upstream)
5. 0.496 endgame_book → create_events (pattern documento_scadenza)
6. 0.496 scamper → create_events (parametro accounts)

Cluster: il telos engine ha scoperto **autonomamente** un'opportunita'
robusta (4 lenti concordano) di automazione "documenti con scadenza →
eventi calendar". Da convertire in feature.

**File modificati questa decisione:**
- `workspace/TELOS.md` (v1.1 → v1.2)
- `runtime/alignment_engine.py` (docstring + `_ALPHA`/`_GAMMA` + compose
  v1.3 + CLI `--recompose`)
- `runtime/telos_proposals_store.py` (nuovo, 235 righe)
- `runtime/http_routes_admin.py` (+2 handler, +2 ROUTES, +1 import)
- `runtime/templates/proposals_telos.html` (nuovo)
- `runtime/templates/base.html` (+1 link nav)
- `runtime/templates/dashboard.html` (+1 card)
- `runtime/tests/test_alignment_engine.py` (22/22 PASS, ricalibrato +
  classe `FormulaOrderingTests` con 3 test qualitativi)
- `runtime/tests/test_telos_proposals_store.py` (nuovo, 19/19 PASS)
- `docs/{it,en}/architecture/telos.html` cap.3+4+5

**Backfill output:**
`~/.local/share/metnos/telos_proposals.rescored.recomposed.jsonl`
(input `.rescored.jsonl` mantenuto intatto pre-v1.3).

## Alternatives considered

- **Aggiungere telos discriminanti** (suggerimento iniziale Roberto):
  rifiutato dopo diagnosi. I telos discriminano gia' bene per-proposta
  (max(fit) media 0.87, stdev 0.32). Il problema era nell'aggregazione,
  non nel set telos.
- **Top-K weighted sum** (Σ top-2 contributi): non risolve specialista
  vs tuttofare per pesi piccoli (es. t.parsimonia 0.10).
- **Max-pool puro** (`max(peso·fit) - bother_cost`): perde completamente
  il segnale multi-telos.
- **Normalizzazione per peso massimo**: invertirebbe l'ordering (tuttofare
  guadagnerebbe sempre).
- **Geometric mean √(top × sum)**: ordering corretto ma controintuitivo
  e meno calibrabile.
- **γ = 0.5 senza α boost**: tuttofare batte specialista (margine
  insufficiente con pesi compressi v1.2).
- **Dashboard senza enrichment turn log**: ridurrebbe il triage a
  "ranking secco", perdendo i 3 chip (osservata/parziale/speculativa)
  che sono il segnale piu' utile per Roberto.

## Open

- Cutoff `θ` di default (oggi 0.30 nella UI): da affinare osservando
  cosa Roberto accepta/rejecta nei primi 30gg di triage.
- Reverse pattern delle decisioni "accept": una volta che una proposta
  e' accettata, come si traduce in modifica al catalog/synt? Per ora
  e' "decisione registrata" senza pipeline di promozione automatica.
- Auto-approve prudenziale (suggerito in [[project-telos-dashboard-need]]):
  rinviato a dopo i primi 30gg di feedback umano.
- Garbage collection di decisioni "orphan" (prop_id non corrispondenti
  a proposte attuali): non urgente, append-only e' robusto a basso
  volume.
