# ADR 0162 — ClusterLLM + Pronoia classify_fail + retry on repeat

**Date**: 2026-05-26
**Status**: accepted
**Supersedes**: estende ADR 0161 (Praxis Engine)
**Related**: ADR 0134 (BGE-M3 semantic fallback), ADR 0157 (Alignment Engine)

## Context

Praxis Engine (ADR 0161) usa `intent_hash = sha256(verb|object|keywords)` come bucket di skill. In live (25-26/5/2026) si manifestano 3 problemi:

1. **Bucket frammentati**: `intent_hash` include keywords lessicali. Query equivalenti ("quanti file" / "conta file" / "how many files") finiscono in bucket diversi (1 obs ciascuno) → promote mai (richiede N≥2 stesso bucket).

2. **Granularità feedback grossa**: click ✗ utente penalizza l'intera skill anche quando l'errore è solo formattazione finale (`final='file'` senza numero — executor + args sono corretti, solo template hardcoded sbagliato).

3. **Click ↻ inerte**: registra observation ma non scatena cambio framework. Utente che ripete vede stessa risposta sbagliata.

Inoltre la review architetturale ha rilevato:
- Magic constants hardcoded senza calibration
- `update_skill_metrics` mescola lookup-hit (`uses`) con outcome reale
- Mancanza audit trail per cambi skill (debug futuro difficile)

## Decision

### 1. ClusterLLM — bucket emergente via embedding multilingua

`runtime/praxis_cluster.py` sostituisce `intent_hash` discreto con `cluster_id` emergente:

```
assign_cluster(query, embedding):
  1. cosine top-1 ≥ COSINE_HIGH (0.90) → riusa cluster esistente     [no LLM]
  2. cosine top-1 < COSINE_LOW (0.75)  → nuovo cluster                [no LLM]
  3. zona grigia                       → LLM judge same/different    [~2-3s Gemma 26B]
```

**Embedding**: BGE-M3 ONNX int8 (ADR 0134 reused), ~15ms/query, multilingua nativo (100+ lingue).

**Pattern**: ClusterLLM (Zhang 2023) + GPTCache (Bang 2023) — embedding + cosine + LLM verifier in zona ambigua. Deterministic-first §7.9: LLM solo dove la cosine è incerta (statisticamente raro post-warmup).

**Promote** ora per `(cluster_id, framework_hash)` invece di `intent_hash`. Query equivalenti convergono nello stesso cluster → promote possibile dopo 2 ✓ totali.

### 2. Champion / challenger darwiniano

Più framework possono coesistere nello stesso `cluster_id`:
- **champion** (`champion=1`): selezionato per default lookup
- **challenger**: skill alternativi accumulano metriche

`composite_score = W_SUCCESS·success_rate + W_SPEED·speed + W_ALIGNMENT·alignment` (default 0.5/0.3/0.2, env-tunable).

`maybe_swap_champion`: se `swap_score = 2·Δsuccess + Δlat_norm > 0.15` AND `challenger.uses ≥ 1` → swap immediato. Selezione rapida, no exploration random (utente preferisce determinismo).

Reversibile: nuovo champion che peggiora viene deposto al prossimo swap.

### 3. Pronoia classify_fail — granularità feedback

`runtime/pronoia_classify_fail.py`: su click ✗, LLM Gemma 26B classifica la causa in {`format_fail`, `args_fail`, `pipeline_fail`}.

**Dispatch differenziato**:

| classe | semantica | effetto |
|---|---|---|
| `format_fail` | template buggy, executor/args ok | mark `template_issue=1` + Mētis refresh inline (~3s) |
| `args_fail` | filler invalido | `DELETE filler_cache for intent_hash`, skill resta active |
| `pipeline_fail` | pipeline strutturalmente sbagliata | `fail_count++` + `anti_skill TTL 30gg` (status quo) |

Latency: ~1.5-2s LLM (solo su ✗). Disabilitabile con `METNOS_PRONOIA_CLASSIFY_FAIL=0`.

### 4. Retry on repeat (↻)

Click ↻ → Mētis re-propose framework alternativo **INLINE** (~3-15s wise tier), salva su skill esistente. Soft `anti_skill TTL 1h` sul vecchio framework_hash con `reason='repeat_soft'`.

**Vincoli**:
- Non re-execute (evita side-effects su framework mutating: send/move/delete). Utente vede risultato nuovo solo al prossimo turn equivalente.
- Solo `update_skill_metrics(success=False)` (uses++, fail_count++) — counter separati da promote.

### 5. Counters separati `uses` vs `ok_count` vs `fail_count`

Pre-ADR: `_touch_skill` incrementava `uses` al lookup-hit, confondendo selezione con outcome.
Post-ADR: 
- `_touch_skill` aggiorna SOLO `ts_last_used` (no counter).
- `update_skill_metrics(success: bool)`: `uses++` e `ok_count++` o `fail_count++` separati.
- Composite_score ricomputato da `success_rate = ok_count / uses`.

### 6. Costanti centralizzate

`runtime/praxis_constants.py`: tutte le soglie/pesi in UN modulo (COSINE_HIGH/LOW, K_NEIGHBORS, SWAP_THR, W_SUCCESS/SPEED/ALIGNMENT, MAX_LATENCY_MS, EMA_ALPHA, MIN_OBS_*, TTL_*, PRONOIA_*). Env override per esperimenti runtime.

### 7. Audit trail `skill_versions`

Nuova tabella append-only `skill_versions(skill_id, ts, event, old_fw_hash, new_fw_hash, reason)`. Event in {`created`, `refresh_template`, `retry_repeat`, `champion_swap`}. Storia ricostruibile via SQL per debug.

### 8. Batch nightly refresh

`runtime/jobs/praxis_template_refresh.py` registrato in scheduler v2 daily@04:00. Safety net per skill con `template_issue=1` che non sono state rigenerate immediatamente (retry inline disabilitato o fallito).

### 9. Magic renderer `${stepN.@count}`

In `praxis_executor._render_final_message`, sintassi speciale che il renderer risolve in cascata:
```
available_total → ok_count → used → len(first list[dict] of result)
```
Elimina hardcoded scelta di campo nel template Mētis.

## Consequences

### Positive

- **Promote sblocca**: query semanticamente equivalenti in lingue diverse condividono cluster → skill emerge dopo 2 ✓ totali (vs mai prima)
- **Feedback granulare**: ✗ non penalizza unfairly skill quando l'errore è solo formato
- **↻ proattivo**: utente vede framework alternativo al prossimo turn senza dover modificare query
- **Determinismo §7.9**: LLM solo in zona grigia (~5-10% lookup) + classify_fail (raro) + retry (raro)
- **Backward compatible**: BGE-M3 non disponibile → degrade silent a intent_hash legacy
- **Test coverage**: 29/29 unit test green
- **Audit trail**: cambi skill ricostruibili via `skill_versions`

### Negative

- **Latency** zona grigia cluster: +2-3s (raro)
- **Latency** classify_fail: +1.5-2s su ✗ (raro)
- **Latency** retry on repeat: +3-15s su ↻ (raro)
- **Storage**: +4KB per observation (embedding BLOB)
- **Scaling**: `find_neighbors` O(N) linear. Limite pratico ~10k obs senza FAISS

### Neutral

- Magic constants ora documentate ma comunque arbitrarie (calibration empirica raccomandata per produzione)
- Re-execute on ↻ non implementato (deliberate: evita side-effects mutating)

## Open questions / future work

1. **FAISS index** per `find_neighbors` O(log N) — necessario oltre ~10k obs
2. **Exploration ε-greedy** in `select_skill_for_cluster` — oggi pure-exploit
3. **Cluster merge** daily batch via LLM (rivisita cluster singleton)
4. **Cluster split** on-demand quando 2 framework_hash coesistono con success simile
5. **Re-execute on ↻** per framework idempotent (helper `_is_framework_idempotent` esiste)
6. **Calibration soglie** via bench dedicato (varia COSINE_HIGH/LOW su corpus reale, misura precision/recall)
7. **UI** che mostra `cluster_id` + `template_issue` + audit log per debug user-side

## References

- Zhang et al. (2023). "ClusterLLM: Large Language Models as a Guide for Text Clustering"
- Bang et al. (2023). "GPTCache: An Open-Source Semantic Cache for LLM Applications" (Zilliz)
- Shinn et al. (2023). "Reflexion: Language Agents with Verbal Reinforcement Learning"
- Park et al. (2023). "Generative Agents: Interactive Simulacra of Human Behavior" (Stanford)
- ADR 0134 — BGE-M3 affinity_semantic fallback
- ADR 0157 — Alignment Engine formula v1.3
- ADR 0161 — Praxis Engine (parent decision)

## Files

```
runtime/praxis_cluster.py            (385 LOC, new)
runtime/praxis_constants.py          (105 LOC, new)
runtime/pronoia_classify_fail.py     (151 LOC, new)
runtime/jobs/praxis_template_refresh.py (157 LOC, new)
runtime/tests/test_praxis_cluster.py (262 LOC, new, 16 tests)
runtime/tests/test_pronoia_classify_fail.py (130 LOC, new, 13 tests)
runtime/praxis.py                    (+340 LOC: refactor record_feedback in 7 helper, retry_on_repeat, _refresh_template_on_format_fail)
runtime/praxis_executor.py           (+10 LOC: @count renderer magic)
runtime/turn_feedback.py             (+1 LOC: VALID_ACTIONS += "repeat")
runtime/http_routes_agent.py         (+1 LOC: action whitelist += "repeat")
runtime/agent_runtime.py             (+1 LOC: query propagation a record_observation)
runtime/scheduler_v2/builtin_callbacks.py (+22 LOC: schedule entry praxis_template_refresh daily@04:00)
runtime/prompts/it/praxis_propose.j2 (refactor sezioni con MATCH/NO_MATCH + @count magic)
decisions/0162-clusterllm-pronoia-classify-fail.md (THIS FILE)
```

## Schema delta

```sql
ALTER TABLE observations ADD COLUMN embedding BLOB;
ALTER TABLE observations ADD COLUMN cluster_id TEXT;
CREATE INDEX idx_obs_cluster ON observations(cluster_id);

ALTER TABLE skills ADD COLUMN cluster_id TEXT;
ALTER TABLE skills ADD COLUMN framework_hash TEXT;
ALTER TABLE skills ADD COLUMN latency_p50_ms INTEGER DEFAULT 0;
ALTER TABLE skills ADD COLUMN composite_score REAL DEFAULT 0.5;
ALTER TABLE skills ADD COLUMN champion INTEGER DEFAULT 1;
ALTER TABLE skills ADD COLUMN template_issue INTEGER DEFAULT 0;
CREATE INDEX idx_skills_cluster ON skills(cluster_id, champion DESC);
CREATE INDEX idx_skills_cluster_fw ON skills(cluster_id, framework_hash);

CREATE TABLE skill_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  event TEXT NOT NULL,  -- 'created'|'refresh_template'|'retry_repeat'|'champion_swap'
  old_fw_hash TEXT,
  new_fw_hash TEXT,
  reason TEXT,
  FOREIGN KEY (skill_id) REFERENCES skills(id)
);
CREATE INDEX idx_skill_versions_skill ON skill_versions(skill_id, ts DESC);
```

Migration idempotente via `praxis_cluster.ensure_schema(conn)`, chiamata al boot di `PraxisStore.__init__`.
