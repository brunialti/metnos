---
id: 0150
title: Multi-tool fast-path — path memoization as middle tier before synthesis
date: 2026-05-18
status: retired (11/6/2026 — playback mai abilitato in prod, recording+promote
  rimossi; il ruolo è coperto da engine/fastpath L0, ADR 0164 ext, che fa
  cache query→piano multi-step con aging/morte/promozione proprie)
area: runtime | fast-path | mnestoma | synthesis
related:
  - 0066  # synth_request: synthesis of new executors from recurring patterns
  - 0094  # fast_path: deterministic short-circuit for trivial queries
  - 0122  # path_shape_hash: synth proposal enrichment
  - 0149  # query normalization (single-tool fast-path)
---

## Context

Gerarchia attuale di promozione mnestoma:

```
NEW QUERY
  ↓
L0 planner LLM (Gemma 26B, ~12 s)
  ↓
N executor calls (~300 ms ciascuno via subprocess)
  ↓
mnestoma logs pattern
  ↓
uses ≥ K_synth (~5 conferme stabili) → synth_request crea nuovo executor
```

ADR 0149 introduce un **livello single-tool** che cattura le query
triviali (time, where, status) tramite `canonical_query` + BGE matcher.

**Gap rilevato:** fra il planner LLM (12 s) e la synthesis di un nuovo
executor (operazione complessa, ~150 s wall, richiede skill_codegen + LLM
synthesis + admission policy) non c'è nessun livello intermedio per
i **path multi-executor ripetuti** che ancora non meritano synthesis ma
sono comunque costosi al planner.

Esempio: `"scarica https://x.com" → [get_urls, describe_entries]` —
pipeline standard, 2 executor, deterministica una volta appresa.
Attualmente paga sempre il planner (12 s) finche' la synthesis non
crea un executor unificato `get_and_describe_url` (che peraltro non è
detto avvenga: il path potrebbe essere usato 20 volte senza raggiungere
il threshold synthesis).

## Decision

Introdurre il **tier L2 multi-tool fast-path** come *path memoization*
deterministica:

```
NEW QUERY
  ↓
L0 normalize deterministico
  ↓
L1 single-tool fast-path (ADR 0149)        — cosine + uses ≥ 5
  ↓ miss
L2 multi-tool fast-path                    — cosine + uses ≥ 5 — ★ QUI ★
   playback deterministico della sequenza:
     execute tools[0]
     pipe output → from_step → execute tools[1]
     ...
     execute tools[N-1]
   ZERO planner LLM. N IPC executor.
  ↓ miss
L3 planner Gemma 26B                       — by-product canonical_query
  ↓
mnestoma logs (canonical_query, tools_sequence, args_shape_per_step)
  ↓
promotion:
  uses ≥ K_path (=5)   → entry L2 (sequence memoization)
  uses ≥ K_synth (=50) → request_new_executor (synthesis L3 esistente)
```

### Distinzione tier vs synth

| | L2 path memoization | L3 synth executor |
|---|---|---|
| Cosa produce | entry in `multi_tool_paths.sqlite` | nuovo `.py` + manifest signed |
| Costo creazione | scrittura DB (~ms) | synth_request (~150 s) |
| LLM in creazione | nessuno | sì (skill_codegen) |
| Reversibile | DELETE entry | rimuovi dir executor |
| Risk profile | basso (replica pattern osservato) | medio (nuovo codice) |
| Threshold | K_path = 5 | K_synth = 50 |
| IPC overhead | N executor calls | 1 executor call |

L2 e L3 sono **complementari, non alternativi**: L2 attiva subito con
pochi uses, raccoglie evidenze fino a K_synth, poi L3 (synth) prende
il sopravvento se vale.

## Schema di storage

Tabella `multi_tool_paths` (sqlite, sotto `~/.local/share/metnos/`):

```sql
CREATE TABLE multi_tool_paths (
  id              INTEGER PRIMARY KEY,
  canonical_query TEXT NOT NULL,
  tools_sequence  TEXT NOT NULL,   -- JSON array di tool names
  args_shape      TEXT NOT NULL,   -- JSON array di args templates per step
  uses            INTEGER NOT NULL DEFAULT 0,
  last_seen       INTEGER,
  weight          REAL,            -- decay come mnest, ADR 0117
  status          TEXT NOT NULL,   -- active|shadow|demoted|promoted_to_synth
  cosine_embed    BLOB,            -- BGE-M3 embed(canonical_query)
  UNIQUE(canonical_query, tools_sequence, args_shape)
);
```

`args_shape` cattura il PATTERN, non i valori concreti. Esempio:
```json
[
  {"url": "<STRING>"},                              // get_urls step
  {"from_step": 1, "lang": "<LANG_CODE>"}           // describe_entries step
]
```
Placeholder `<STRING>`, `<INT>`, `<LANG_CODE>`, `<URL>`, `<PATH>` etc.
Vengono sostituiti al playback dal valore osservato nella query corrente.

## Playback semantics

`playback(entry, new_query) -> turn_result`:

1. Per ogni step `i` in `tools_sequence`:
   - Resolve args dal template `args_shape[i]`:
     - Placeholder concreti (`<URL>`) → estrai dalla `new_query` via
       regex deterministica
     - `from_step: N` → riusa observation di `steps[N-1]` come da
       runtime esistente
   - Invoca executor `tools[i]` con args risolti
   - Se `observation.ok == False` → abort playback, fallback a planner
     (no harm, query rieseguita normalmente)
2. Final answer composta da template deterministico simile a L1, oppure
   dalla `summary` dell'ultimo executor.

Se durante il playback un placeholder non si risolve (es. query non
contiene URL ma il template lo richiede) → fallback a planner.

## Reliability constraints

Come per ADR 0149:

- **Shadow mode** per K turni dopo promozione: log della scelta planner
  reale per la stessa canonical_query, attivazione L2 solo se concordanza
  sequenza > 95%.
- **Demote on rejection**: se l'output del playback viene seguito da
  `undo_last_turn` o user retry → demote entry, fallback a planner.
- **Type-shape verification**: prima di salvare entry, verifica che
  `tools[i+1]` accetti il tipo di output di `tools[i]` (consumer_precursor
  check già esistente in `loader.py`).

## Costi

| Voce | Valore |
|---|---|
| Latency cache-hit | N × ~300 ms IPC executor (es. 600 ms per 2 tools) vs 12 s planner — -95% |
| VRAM extra | 0 |
| Storage extra | sqlite `multi_tool_paths.sqlite` (~MB per anno) |
| Servizi systemd | 0 |
| Spesa $$ | 0 |
| Manutenzione | bench periodico hit-rate + concordance |

## Implementation phases

Dipendente da ADR 0149 step 2b (mnestoma logs `canonical_query`).

| Step | File | Effort |
|---|---|---|
| 3a Schema sqlite `multi_tool_paths` | `runtime/multi_tool_paths.py` | 1 h |
| 3b Promotion job mnestoma → L2 (K_path=5) | `runtime/scheduler_v2/builtin_callbacks.py` | 2 h |
| 3c Playback engine | `runtime/multi_tool_paths.py::playback` | 3 h |
| 3d Type-shape verifier | `runtime/loader.py` (reuse) | 1 h |
| 3e Cosine matcher L2 in fast_path | `runtime/fast_path.py` | 1 h |
| 3v Verification suite | nuovi test scripts | 2 h |

Totale: ~10 h, **sessione successiva** dopo che ADR 0149 step 2b
ha raccolto sufficienti `canonical_query` in mnestoma da fare seed.

## Out of scope per questo ADR

- L1 single-tool fast-path: già coperto da ADR 0149.
- L3 synth executor: già coperto da ADR 0066 + 0122. Cambia solo
  threshold (K_synth da 5 a 50 ora che L2 cattura il low-volume).
- Multi-tool con branching (if/else, loop): out of scope. Playback è
  lineare per costruzione.
- Multi-tool con argomenti dinamici dipendenti dal contesto utente
  (es. `--actor-default`): rivalutabile post-MVP.

## Open questions

- K_path = 5 ottimale? Calibrazione bench.
- K_synth = 50 ottimale? Bench storico mnestoma.
- Strategia placeholder resolver: regex deterministica vs LLM extraction?
  Iniziamo con regex (URL/PATH/INT/EMAIL) + fallback planner su non-match.

---

## Update v4 — 19/5/2026 (implementation)

Implementato in `runtime/multi_tool_paths.py` (modulo principale),
`runtime/jobs/multi_tool_promote.py` (bridge L2→L3), `runtime/agent_runtime.py`
(wire-in fast-path + recording in TurnLog.write).

### Spec changes vs ADR originale

**TTL active-days (override user 19/5 v4)**

Sostituisce il decay weight wall-clock con una scadenza basata sui giorni di
*attivita' effettiva* del sistema:

```sql
CREATE TABLE system_active_days (
  date      TEXT PRIMARY KEY,
  n_turns   INTEGER NOT NULL DEFAULT 1,
  day_rank  INTEGER NOT NULL UNIQUE  -- counter monotono
);
```

Ogni turn: UPSERT della riga `date=today` con n_turns += 1. Il `day_rank` viene
assegnato monotono al primo turn della giornata (1, 2, 3, ...).

Per ogni entry `multi_tool_paths`, il campo `last_used_active_day` memorizza il
day_rank al momento dell'ultima osservazione.

Scadenza: `expire_stale_paths(ttl_active_days=N)` cancella entry con
`current_active_day - last_used_active_day > N`.

Razionale: se l'utente sta in ferie 60 giorni con 0 turn, le entry NON scadono:
servono di nuovo al ritorno. Wall-clock TTL le butterebbe via.

**Threshold uses (override user 19/5 v4)**

- `K_path` (promozione L2 candidate → active): default **3** (era 5).
  Simmetria con `canonical_matcher` ADR 0149 v2. Env override
  `METNOS_MTP_MIN_USES`.
- `K_synth` (promozione L2 → L3 proto-mnest): default **50**.
  Env override `METNOS_MTP_K_SYNTH`.
- `ttl_active_days`: default **30**. Env override
  `METNOS_MTP_TTL_ACTIVE_DAYS`.
- Cosine threshold: default **0.88** (era 0.93). Multi-tool pattern hanno
  piu' varianti lessicali della stessa intent. Env override
  `METNOS_MTP_THRESHOLD`.

### Composability fast-path-of-fast-path (user feedback 19/5 v4)

`TurnLog.write` registra in `multi_tool_paths` anche quando uno o piu' step
sono stati eseguiti dai layer fast-path (`fast_path` o `multi_tool_fast_path`
flag su step). Razionale: la decisione storica e' gia' approvata per ciascun
step; comporne piu' insieme non aggiunge rischio, solo determinismo.

Cosi' due (o piu') fast-path adiacenti possono dare origine a una pipeline L2
piu' lunga, ricorsivamente.

### Chaining fast-path → PLANNER (user feedback 19/5 v4)

Env opt-in `METNOS_MULTI_TOOL_FAST_PATH_CHAIN=1` (default OFF). Quando un L2
hit succede MA `_query_has_continuation(query)` rileva multi-verbo, il
runtime NON termina: assegna `resume_with_scratchpad` in-memory con i
fast-path step e cede il controllo al PLANNER per gli step rimanenti.

Esempio:
- Query: `"scarica https://x.com e mandami il riassunto via email"`
- L2 hit su prefisso `"scarica + descrivi"` → 2 step eseguiti.
- `_query_has_continuation` rileva `send_messages` verb residuo.
- PLANNER continua con `send_messages(to=..., from_step=2)` step 3.

Riusa il meccanismo `resume_with_scratchpad` esistente (ADR 0099 seed_step,
post-dialog resume). Niente duplicazione.

### Promotion bridge L2 → L3 (user feedback 19/5 v4)

`runtime/jobs/multi_tool_promote.py::task_multi_tool_promote`. Callback
scheduler v2 registrato in `builtin_callbacks.py`. Trigger raccomandato
`daily@04:30` (dopo i18n_translate_pending @02:00, prima di promoter @04:45).

Per ogni entry con `uses >= K_synth` e `state != promoted_to_synth`:

1. Costruisce `desired_signature` dict con summary human-readable,
   inputs derivati dai placeholder del primo step, outputs hint dall'ultimo
   step, pipeline completa, args_shape, canonical_query.
2. Chiama `mnestoma.record_passing(dst_exists=False, ...)` per creare un
   proto-mnest con `src=tools[0]`, `dst=<derived_name>`,
   `desired_signature=sig_dict` (dict completo, non DesiredSignature object,
   per preservare i campi extra).
3. UPDATE `multi_tool_paths.state = 'promoted_to_synth'` (idempotente).
4. La pipeline esistente `mnestoma.recurring_protos` + `synt.react()` su
   `metnos-scheduler` o ad-hoc CLI pickup il proto-mnest e tenta sintesi.

Naming derivato: `<verb_last>_<obj_first>`. Esempio:
- `[get_urls, describe_entries]` → `describe_urls`
- `[read_messages, classify_entries]` → `classify_messages`
- `[find_files, filter_files, move_files]` → `move_files` (gia' canonical)

### Files

| File | Purpose |
|---|---|
| `runtime/multi_tool_paths.py` | DB + matcher + record + expire + helpers placeholder |
| `runtime/jobs/multi_tool_promote.py` | Bridge L2→L3 (proto-mnest creation) |
| `runtime/agent_runtime.py` | Wire-in L2 fast-path + TurnLog.write recording |
| `runtime/scheduler_v2/builtin_callbacks.py` | Registrazione callback `multi_tool_promote` |
| `runtime/config.py` | `DB_MULTI_TOOL_PATHS` path (PATH_USER_DATA) |

### Env flags

| Flag | Default | Effetto |
|---|---|---|
| `METNOS_MULTI_TOOL_FAST_PATH` | `0` | Abilita L2 lookup pre-PLANNER |
| `METNOS_MULTI_TOOL_FAST_PATH_CHAIN` | `0` | Abilita chain fast-path → PLANNER |
| `METNOS_MTP_DB` | `~/.local/share/metnos/multi_tool_paths.sqlite` | DB path override |
| `METNOS_MTP_THRESHOLD` | `0.88` | Cosine threshold |
| `METNOS_MTP_MIN_USES` | `3` | Uses minimi per match |
| `METNOS_MTP_TTL_ACTIVE_DAYS` | `30` | TTL in active-days |
| `METNOS_MTP_K_SYNTH` | `50` | Soglia promozione L2→L3 |

### Deferred (Fase 13b o post-MVP)

- Hook `introvertiva.analyze_multi_tool_paths()` per generare proposte UI
  admin (deferred, user feedback 19/5 v4: "3 skip per ora").
- Shadow mode per K turni post-promozione (gating concordance > 95%).
- Type-shape verification con consumer_precursor check del loader.
- Auto-merge di adjacent L2 entries (es. due pipeline overlap).

## Update v7 — 20/5/2026 (args query-derived single source via args_extractor)

### Problema

Il fix iniziale 20/5 mattina della regressione mail task ("verifica le
mail importanti delle ultime 24 ore" ricevette `time_window: today` da una
query memoizzata precedente) introdusse un set hardcoded di "args volatili":

```python
_VOLATILE = {"time_window", "window", "since", "before", "range",
             "from", "date", "day", "when", "on_date"}
```

Strippati questi prima della memoization. Roberto ha bocciato: «sempre e
per tutti? hardcoded enum e' il pattern che hai gia' rifiutato in altre
sessioni».

### Generalizzazione

L'autorita' di "questo arg e' query-derived" diventa **`args_extractor.regex_extract`** applicato allo
schema dell'executor. Single source of truth: cio' che `args_extractor`
sa ri-estrarre dalla query corrente per un dato schema-arg E' per
definizione query-derived. Cio' che non sa estrarre E' planner-choice
(literal/default/scope) e va memoizzato fedelmente.

Applicato in due posti speculari:

1. **`runtime/multi_tool_paths.py::derive_args_shape`** (ADR 0150 v4-v5):
   ogni arg che `args_extractor(query, schema_step_k).keys()` ridenomina
   diventa `<DYNAMIC:array>` o `<DYNAMIC:string>` (type hint per shape
   preservation). Al playback, `resolve_args_from_shape` ri-estrae il
   valore dalla query corrente.

2. **`runtime/agent_runtime.py::TurnLog.write` &rarr; record_canonical_query**:
   `args_observed` esclude gli args che `args_extractor(self.user_query,
   schema_step_1).keys()` saprebbe ri-estrarre.

### Wire-in

`derive_args_shape` accetta nuovo parametro `schemas_per_step: list[dict]`.
Il caller in `TurnLog.write` lo popola via lookup catalog:

```python
_schemas_per_step = []
for _tname in tools_seq:
    _ex = (_cat.executors.get(_tname) if _cat else None)
    _schemas_per_step.append(getattr(_ex, "args_schema", None) or {})
shape = derive_args_shape(self.user_query or "", raw_args_per,
                           schemas_per_step=_schemas_per_step or None)
```

### Effetti

- Query "verifica mail ultime 24 ore" &rarr; `time_window` riconosciuto
  da `args_extractor` come query-derived (via `_extract_time_window`) &rarr;
  `<DYNAMIC:string>` placeholder &rarr; al playback ri-estratto a
  `"last-24h"`.
- `account: "all"` &rarr; NON estraibile da args_extractor (e' policy
  default, non query) &rarr; memoizzato literal.
- Aggiungere supporto a un nuovo arg query-derived (es. `lang`,
  `format`) richiede solo aggiungere la branch in `args_extractor.regex_extract`
  &mdash; nessun update a `_VOLATILE`-style enum.

### Proprieta'

- **Lang-agnostic**: `args_extractor` usa keyword IT+EN gia' bilingui
  (ADR 0149) + canonical OBJECT slug. Nuova lingua = aggiunta in
  `args_extractor`, automatica nella memoization.
- **Future-proof**: nuovo executor con schema personalizzato &rarr;
  classificazione query-derived automatica via schema introspection.
- **Anti-pattern eliminato**: rimossa la const hardcoded `_VOLATILE`
  (10+ righe). Test verifica: `args_observed = {"account": "all"}`
  per la mail query, `time_window` strippato come voluto.

Commit: `f50e8ca fix(memoization): args query-derived sempre placeholder
(single source: args_extractor)`. Test integrato: turno `ccd720f7` mail
regression chiusura verde, multi_tool_paths shape contiene
`"time_window": "<DYNAMIC:string>"`.
