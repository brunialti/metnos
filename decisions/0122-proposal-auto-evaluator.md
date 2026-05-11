---
id: 0122
title: Auto-evaluator delle proposte synth + ETA instrumentation forward
date: 2026-05-10
status: accepted
area: synt
related:
  - 0114  # Synth admission policy 4 layers (L2 affinity, L3 efficacy, L5 smoke, L6 LLM verifier)
  - 0076  # Synth_request short-circuit (already_in_catalog + redirect)
  - 0077  # Introvertiva quality filters
  - 0096  # Proposals cleanup
  - 0099  # Runtime perf (catalog cache)
complements:
  - 0114
---

## Context

A 8/5/2026 ADR 0114 ha introdotto quattro gate cumulativi contro synth
difettosi (L2 affinity overlap, L3 efficacy ager, L5 smoke battery con
expected_first_tool, L6 LLM semantic verifier). I quattro layer agiscono
**al catalog load** o **come daemon notturno**, con focus su rifiutare
synth gia' difettosi (catch-all affinity, naming hijack, drift semantico
description vs code).

Manca ancora un anello: **la valutazione diretta delle proposte**
prima che diventino synth installati. Oggi il flusso e' (a)
`synth_request.handle_synth_request` lancia la cascata multistage,
(b) se ok scrive proposal JSON in `~/.local/share/metnos/synt_proposals/`
+ installa con sign, (c) opzionale review umana via CLI/HTTP. Non c'e' un
verdetto deterministico per dire "questa proposta vale davvero la pena
di sostituire un percorso multi-step con UN nuovo executor?".

Le metriche piu' rilevanti — speedup vs path, frequenza d'uso storica,
decidability del PLANNER simulato, danni potenziali (affinity overlap
stretto, schema ambiguo) — non vengono raccolte ne' aggregate. Il
turn JSONL contiene `ts_start/ts_end` e `chosen_tool` per step ma
nessuno costruisce un fingerprint di "shape" su cui fare aggregazione
(p50/p95) e nessuno legge in stage 5 di synt il dato per riempire la
proposta.

Numeri al 9/5/2026:
- 22 file in `synt_proposals/`, di cui 16 con `final_state="synthesized"`
  e 6 con `expected_name` mai promosso a canonical alias.
- 30401 foto in `unified/` (ADR 0117), batch attivo `metnos-vlm-enrich`
  a ~6%.
- Suite 1057 PASS / 0 FAIL.

## Decision

Quattro componenti, tutti deterministici §7.9 (no LLM nell'evaluator):

### 1. `runtime/path_shape.py`

Fingerprint SHA-256(16 hex char) della sequenza di `chosen_tool`
produttivi. Esclude `final_answer`, `describe_entries`, `undo_last_turn`,
`request_new_executor`, `scratchpad_read`, `@uploaded`, e step in errore.
Accetta sia dict (turn JSONL) sia dataclass-like (`StepLog` runtime) per
usabilita' al call-site `synth_request`.

API: `path_shape_hash(steps)`, `extract_path_shape(turn)` =
`(hash, n_steps)`, `steps_to_tools(steps)`, `turn_total_ms(turn)`,
`is_shape_terminal(steps)`.

### 2. `runtime/proposals_eta_index.py`

SQLite store `~/.local/share/metnos/proposals_eta.sqlite`:

```sql
CREATE TABLE path_eta_index (
    path_hash TEXT PRIMARY KEY,
    sample_count INTEGER NOT NULL,
    p50_ms INTEGER NOT NULL,
    p95_ms INTEGER NOT NULL,
    last_seen REAL NOT NULL,
    sample_steps_json TEXT
);
```

API: `upsert_aggregate(hash, samples_ms, ...)`,
`lookup(hash)`, `aggregate_from_jsonls(since_ts, ...)`,
`count_shape_calls(hash, since_ts, ...)`.

`aggregate_from_jsonls` walk dei `~/.local/share/metnos/turns/*.jsonl`,
calcola path_shape_hash + total_ms per ogni turno con
`ts_start >= since_ts`, scrive aggregati. Idempotente (rewrite full
per shape — non incrementale per evitare drift di p50/p95).

### 3. Task scheduler v2 `proposals_eta_aggregate`

Trigger `daily@04:30`. Callback in
`runtime/scheduler_v2/builtin_callbacks.py::task_proposals_eta_aggregate`:
finestra rolling 7 giorni. Allineato 30 min prima di
`apply_executor_ager` (5:00) cosi' l'efficacy ager puo' usare i dati
freschi se serve.

### 4. `runtime/proposal_evaluator.py`

Punto di ingresso unico `evaluate_proposal(proposal_path) ->
EvaluationResult`. Sei **killer** (basta uno per REJECT):

| killer | fonte | trigger |
|---|---|---|
| inflation | `vocab.py` | verbo non in 22, oggetto non in 15, qualifier non in 3 famiglie |
| affinity_overlap | `loader.HANDCRAFTED_FAMILIES` + catalog | Jaccard ≥ 0.4 vs handcrafted o synth piu' vecchio |
| test_pass_rate | stage 3 + final_state | tests vuoti, falliti, o final_state ≠ "synthesized" |
| reversibility_parity | path_steps + catalog | path conteneva `reverse_pattern`, nuovo non lo dichiara |
| error_class_discriminability | stage 4 desc + stage 5 code | < 2 classi d'errore distinte |
| observation_schema_stability | §2.6 + stage 5 code | verbo trasformativo (move/delete/...) ritorna `entries` invece di `results` |

Soglia `affinity_overlap` 0.4 per evaluator vs 0.5 al catalog load
(`loader._check_affinity_overlap`): l'evaluator e' piu' conservativo
perche' opera **prima** del load, su una proposta nuova; un overlap
borderline 0.4-0.5 non scatta al catalog ma e' un segnale di
sovrapposizione semantica che la review dovrebbe cogliere.

Score weighted (quando nessun killer):

```python
score = (
    (+2 if eta_speedup>=2 else (-1 if eta_speedup<1.2 and eta_speedup>0 else 0))
    + (+1.5 if call_freq_60d>=30 else -0.5)
    + (+1 if decidability>=0.7 else (-1 if decidability<0.5 else 0))
    + (+1 if noising_top10_pct>=0.8 else 0)
    + (+1 if pipeline_terminal else 0)
    + (+1 if truncation_honest else 0)
    + (+1 if token_saving_pct>=30 else 0)
)
```

Verdict:
- `score >= 4` AND no killer → **ACCEPT**
- `-2 < score < 4` AND no killer → **GRAY** (review umana)
- `score <= -2` OR any killer → **REJECT**

**Decidability heuristic** (no LLM): per ciascuna delle 12
riformulazioni IT+EN della `user_query` originale, simula il PLANNER
con `_bow_intent_simple` (BoW deterministico, riusa il pattern di
`smoke._bow_intent_for_smoke`) + `prefilter.rank_with_intent`/`rank`,
verifica che `ranked[0].name == proposal.name`. `decidability_pct` =
pass / 12.

**Noising heuristic**: per le query del path storico (lookup da
`proposals_eta_index.sample_steps`), il nuovo executor deve risalire
nel top-10 del prefilter su >= 80% delle query. Fallback a
`user_query` come singolo campione se l'index non ha `path_queries`.

Audit JSONL append-only: `~/.local/share/metnos/synth_audit/proposal_evaluator.jsonl`.

### 5. Wiring `synth_request.py`

`handle_synth_request(args, *, ..., current_steps=None)` riceve gli
step gia' eseguiti del turno corrente. Calcola `path_hash` +
`path_steps` deterministicamente, fa lookup `proposals_eta_index`
per `path_eta_p50_ms`/`p95_ms`, conta le occorrenze 60d. Tutti questi
campi vengono scritti nel proposal JSON:

```json
{
  ...
  "path_hash": "ab12cd34ef560000",
  "path_steps": ["find_files", "filter_entries"],
  "path_n_steps": 2,
  "path_eta_p50_ms": 4500,
  "path_eta_p95_ms": 7200,
  "path_call_count_60d": 47,
  ...
}
```

`agent_runtime.py` passa `current_steps=list(log.steps)` al call site
`request_new_executor`.

### 6. CLI + HTTP

`python -m admin.proposals_cli evaluate <id>` invoca l'evaluator,
stampa l'`EvaluationResult` come JSON.

`python -m admin.proposals_cli aggregate-eta --days N` invoca
l'aggregator manualmente.

HTTP `GET/POST /admin/synth-proposals/{id}/evaluate` ritorna JSON
(o card HTMX se Accept: text/html).

## Alternatives considered

### A. Evaluator LLM-as-judge

Far giudicare la proposta a Gemma 4 26B con prompt strict JSON, simile
a stage 6 di synt (ADR 0114 L6). Rifiutato: §7.9 (codice deterministico
> LLM se equipotente). I sei killer + 7 signal sono tutti regex/lookup
deterministici, non c'e' valore aggiunto LLM e introduce non-idempotenza
+ costo.

### B. Solo ETA speedup, niente killer

Pipeline minimale: solo path_eta vs new_executor_latency. Rifiutato:
saremmo ciechi al caso `find_texts` 8/5 (synth con affinity catch-all),
gia' coperto da L2 al catalog load ma non dall'evaluator preventivo.
ADR 0114 L2 e questo si rinforzano: due punti di osservazione, due
soglie diverse (0.4 evaluator vs 0.5 catalog).

### C. Auto-apply ACCEPT (skip review)

Dopo `verdict=accept`, marcare `proposal.auto_promoted=true` e installarlo
nel pool senza review umana. Rifiutato per Phase 7: review esplicita
resta safety net contro killer non ancora codificati. Quando avremo
N >> 100 proposte ACCEPT senza regression, valutare auto-promotion.

### D. Decidability con intent_extractor LLM-based

Usare il vero intent_extractor (Gemma 4 26B middle, ~370ms/query) invece
di BoW. Rifiutato: latenza × 12 riformulazioni × N proposte =
costo proibitivo per cron. BoW deterministico e' equipotente per il
80% dei verbi/oggetti coperti dalle query reali (smoke battery dimostra
12/12 corretti). I casi grigi BoW (verbi non mappati) cadono a 0% pass
e l'evaluator emette GRAY → review umana.

## Consequences

Cosa diventa piu' facile:
- Review batch dei backlog `synt_proposals/` con un comando.
- Decisione ACCEPT/GRAY/REJECT con motivazione tracciabile (rationale
  + signals dump nell'audit JSONL).
- Aggregare l'efficienza dei multi-step path: il turn JSONL diventa
  fonte di verita' per il "tempo medio del path".
- Telemetria forward dei nuovi executor: il proposal JSON ora porta
  evidenza quantitativa al posto di intuizione.

Cosa diventa piu' caro:
- ~250 LOC nuove (`proposal_evaluator.py`) + ~220 (`proposals_eta_index.py`)
  + ~140 (`path_shape.py`).
- Daily aggregator legge tutti i turn JSONL ultimi 7 giorni (~10MB su
  disk, walk + parse ~1-2s su SSD) — trascurabile vs `apply_executor_ager`.

Doors that close:
- Niente piu' "valutazione tacita" al review. L'evaluator emette un
  verdetto esplicito e l'admin lo accetta o lo discute.
- Synth con `error_class_discriminability` < 2 sono REJECT
  immediato — incentivo per stage 5 prompt synt a forzarlo.

Doors that open:
- Se la suite ACCEPT cresce con regression nulle, possiamo passare a
  auto-promotion (alternativa C).
- L'index `proposals_eta_index` puo' essere riusato per altri lavori:
  detection di path "lenti" candidati a refactor, BFS prioritario nel
  pianificatore basato su shape ETA noti.

Work items spawned:
- Wirare il task scheduler v2 al boot (gia' in `_BUILTIN_JOBS`,
  install via `install_default_jobs` idempotente).
- Estendere stage 5 di synt per prediligere description con
  `error_class` esplicito (oggi alcuni synth dichiarano 0 classi
  → REJECT garantito).
- Bench live `new_executor_latency_p50_ms`: oggi default 1500ms,
  estendibile a misurazione reale durante stage 3 tests.
