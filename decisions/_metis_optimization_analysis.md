# Mētis / Praxis Engine — analisi onesta + piano intervento

**Status**: DRAFT (read-only, non committato). Scope: valutare se l'architettura cognitiva Praxis Engine vale la complessità accumulata, o se un design più semplice raggiunge accuracy comparabile.

**Author**: agente, su richiesta Roberto, 2026-05-28.

**Non-goals**: training pipeline ML retrain (escluso esplicito). Misurazioni end-to-end nuove (richiederebbero restart fuori scope). Modifiche al codice attivo.

---

## §1 Stato — componenti + bench + LOC

### 1.1 Inventario codice attivo

Due architetture coesistono in produzione:

**A. Legacy Praxis (ADR 0161/0162/0163), invocata come fallback se `METNOS_ENGINE_V2=0`:**

| File | LOC | Ruolo |
|---|---:|---|
| `runtime/_legacy/praxis.py` | 1196 | Store sqlite skills/observations/anti_skills/filler_cache, LWW feedback, promote/demote, dispatch ✓✗↻ |
| `runtime/_legacy/praxis_executor.py` | 746 | Noûs: deterministic executor — from_step, ${stepN.field}, ${FILLER:}, ${RUNTIME:}, vaglio gate, branching, recovery |
| `runtime/_legacy/praxis_propose.py` | 271 | Mētis: 1-shot LLM framework propose + GBNF + filler resolve |
| `runtime/_legacy/praxis_cluster.py` | 404 | BGE-M3 embed + cosine + cluster assign + champion/challenger composite + skill_versions audit |
| `runtime/_legacy/praxis_constants.py` | 130 | 30+ env-tunable constants (soglie/pesi/TTL) |
| `runtime/_legacy/pronoia.py` | 391 | Recovery: classify_error 4 classi + re-propose escluding failed_tool |
| `runtime/_legacy/pronoia_classify_fail.py` | 151 | LLM judge {format/args/pipeline}_fail post-✗ |
| `runtime/_legacy/aporia.py` | 327 | Vicolo cieco onesto: classify root_cause + suggest_action + sqlite |
| `runtime/_legacy/fast_path.py` | 452 | Pattern-match deterministico pre-cascata (ADR 0094) |
| `runtime/_legacy/multi_tool_paths.py` | 1041 | Cache TTL active-days + BGE cosine match (ADR 0150, marcato deprecato) |
| **Sub-total legacy** | **5109** | |

Plus prompts: `runtime/_legacy/prompts/{it,en}/praxis_propose.j2` (~280 righe ciascuno), `pronoia_recovery.j2` (~30 ciascuno).

**B. Engine v2 (ADR 0164), default `METNOS_ENGINE_V2=1`:**

| File | LOC | Ruolo |
|---|---:|---|
| `runtime/engine/dispatch.py` | 224 | Orchestrator 4 layer (fastpath → autopath → engine → recovery → terminator) |
| `runtime/engine/executor.py` | 759 | Deterministic execute (riscrittura di Noûs + magic resolvers) |
| `runtime/engine/proposer.py` | 243 | `SimpleProposer` 1-shot wise + factory swappable (metis/frontier) |
| `runtime/engine/proposer_metis.py` | 268 | Variante β multi-strategia |
| `runtime/engine/autopath.py` | 382 | Skill cache sqlite (cluster_id + intent_hash) + feedback |
| `runtime/engine/fastpath.py` | 210 | User-approved fastpath (hash + cosine BGE) |
| `runtime/engine/recovery.py` | 109 | Classify + 1-retry via Proposer escludendo failed_hash |
| `runtime/engine/recovery_metis.py` | 94 | Variante β |
| `runtime/engine/terminator.py` | 144 | Honest fail templates + lacuna sqlite |
| `runtime/engine/terminator_metis.py` | 141 | Variante β |
| `runtime/engine/cluster.py` | 107 | BGE embed + cosine + normalize (riscrittura di praxis_cluster) |
| `runtime/engine/validator.py` | 121 | Typecheck framework pre-execute (opt-in) |
| `runtime/engine/types.py` | 129 | Intent/Framework/StepSpec/RunResult dataclass |
| **Sub-total engine v2** | **2931** | |

**Dipendenze trasversali (entrambe cascate):**

| File | LOC | Ruolo |
|---|---:|---|
| `runtime/agent_runtime.py` | 9333 | run_turn orchestrator + tutti i wire-in + PLANNER legacy (parking) |
| `runtime/prefilter.py` | 1139 | rank_with_intent + 7 funzioni rank_adaptive + intent helpers |
| `runtime/prefilter_strategies/` | 1745 | 14 strategie alternative env-selectable |
| `runtime/tool_grammar.py` | 916 | GBNF gen + pool filter (provider + verb-aware) |
| `runtime/naming_grammar.py` | 404 | Vocab parse + GBNF fragment + validator |
| `runtime/loop_detect.py` | 93 | repeat-failure detection |
| `runtime/intent_extractor.py` | ~400 | Verb+object+keywords da query (LLM fast) |
| `runtime/compound_decomposer.py` | ~600 | Decomposer query con ≥2 verbi (bypassa Mētis) |
| `runtime/args_extractor.py` | ~600 | Regex+LLM fallback per filler args |
| **Sub-total dipendenze** | **~15230** | |

**Totale agente tool-selection + execution: ~23270 LOC (esclusi prompts, builtin executor handler, dispatcher safety/credentials).**

### 1.2 Bench misurati (onesti)

**Bench `bench_praxis_coverage` 26/5 (95 query stratificate, 8 domini)** — file `runtime/bench_praxis_coverage_1779824902.json`:

- coverage_total: **98.9%** (94/95 answer + 1/95 ask)
- praxis_coverage: **0.0%** (tutto andato a `handler=planner`, NON praxis)
- planner_coverage: 98.9%
- fast_path_coverage: 0.0%
- mean latency: 12.4s, p50: 10.2s, p95: 31.4s

**Lettura onesta**: questo bench, nonostante nome "praxis_coverage", non ha attraversato Praxis. È PLANNER legacy in piena attività. Il claim "33/35 94% Praxis" da ADR 0161 è su set diverso di 35 query curate, non riproducibile da bench attuale.

**Bench proposer 446 FROZEN (28/5)** — `memory/project_bench_proposer_final_28_5.md`:
- F_gemma_grammar (config produzione attuale): **first_acc top-1 46%**, parse 100%, p50 6.3s, wall 25min
- A_gemma (no grammar, think=True): first_acc 48.7%, p50 53.1s
- C_qwen 9B: first_acc 42.4%, p50 4.5s

Stima Roberto post-hardening = ~**65%** top-1 (estimated dopo +6pp prefilter rules + verb filter).

**Verdict bench**: l'unico dato robusto è ~50% first-tool-accuracy del Proposer, ~65% stimato post-hardening. Il 94% Praxis ADR 0161 è su 35 query curate cherry-picked, NON rappresenta carico reale.

### 1.3 Cascata effettiva (da `agent_runtime.py:5727-5980`)

```
turn → fast_path L1 (deterministic regex + render)        [skip se miss]
     → compound_decomposer (se ≥2 verbi)                  [bypassa engine]
     → engine v2 dispatch (default METNOS_ENGINE_V2=1):
         ├─ L0 fastpath  (user-approved hash + BGE cosine)
         ├─ L1 autopath  (skill cache cluster+intent_hash)
         ├─ L2 validator (typecheck opt-in)
         ├─ L3 proposer  (SimpleProposer Gemma 26B GBNF)
         ├─ recovery     (1-retry escluding failed_hash)
         └─ terminator   (honest fail + lacuna log)
     → praxis legacy (se METNOS_ENGINE_V2=0 AND METNOS_PRAXIS=1)
     → planner legacy (se METNOS_PLANNER_LEGACY=1, default 0)
```

### 1.4 Storage runtime

5 sqlite distinti, parzialmente sovrapposti:

- `~/.local/share/metnos/praxis.sqlite` — legacy skills/observations/anti_skills/filler_cache/skill_versions (ADR 0161/0162)
- `~/.local/share/metnos/autopath.sqlite` — engine v2 skills/observations/anti_skills (rename di praxis)
- `~/.local/share/metnos/fastpaths.sqlite` — engine v2 fastpath user-approved
- `~/.local/share/metnos/aporiae.sqlite` — legacy lacune
- `~/.local/share/metnos/terminator_log.sqlite` — engine v2 lacune (rename di aporiae)

Schema legacy vs v2 sono ~85% identici. Nessuna migrazione: doppia scrittura non implementata, ogni cascata persiste nel proprio DB.

---

## §2 Mappa valore — componente per componente

| Componente | LOC | Valore unico | Overlap con altri | Costo manutenzione |
|---|---:|---|---|---|
| `fast_path.py` (L1) | 452 | Pattern triviali zero-LLM ("che ora è", "chi sono io") | engine fastpath duplica parzialmente | Bassa, deterministic |
| `intent_extractor.py` | 400 | Verb+object+keywords da query (LLM fast ~370ms) | Usato sia da Praxis che engine v2 | Media, dipende prompt |
| `praxis_propose.py` Mētis | 271 | LLM 1-shot framework JSON + GBNF | engine `proposer.SimpleProposer` duplica al 95% | **Alta**: 280-riga prompt complesso, hot-spot bug |
| `praxis_executor.py` Noûs | 746 | Resolve from_step + ${stepN.field} + ${FILLER:} + ${RUNTIME:} + projection map + magic @count + branching `if_prev_*` | engine `executor.py` (759 LOC) è riscrittura quasi 1:1 | **Alta**: ogni placeholder semantic richiede 2 manutenzioni |
| `praxis.py` PraxisStore | 1196 | sqlite skill cache + try_match (cluster+exact+jaccard fuzzy) + LWW feedback + promote/demote + filler cache + retry_on_repeat | engine `autopath.py` (382 LOC) implementa subset (no fuzzy, no retry_on_repeat) | **Alta**: hotspot logica feedback |
| `praxis_cluster.py` ClusterLLM | 404 | BGE embed + cosine + LLM judge zona grigia + champion/challenger composite + skill_versions audit | engine `cluster.py` (107 LOC) ha embed+cosine ma niente champion/judge | Media |
| `pronoia.py` recovery | 391 | Classify error 4 classi + re-propose escluding failed_tool, tier override frontier, trace JSONL | engine `recovery.py` (109 LOC) classify + 1-retry. NO trace JSONL, NO frontier tier | Media |
| `pronoia_classify_fail.py` | 151 | LLM judge post-✗ in {format/args/pipeline}_fail per dispatch differenziato | Nessun duplicato | Media |
| `aporia.py` | 327 | sqlite lacune + classify root_cause 4 ortogonali + suggest_action template | engine `terminator.py` (144 LOC) ha 4 template ma classify più povero | Bassa |
| `multi_tool_paths.py` | 1041 | Cache TTL active-days + BGE cosine + L2→L3 promote scheduler | Subsumed da praxis/autopath. Marcato ADR 0150 deprecato | **Cruft**: nessuno usa effettivamente |
| `prefilter.py` rank_with_intent | 1139 | Token+verb+object scoring → top-K catalog | Punto unico di selezione catalog per il Proposer | Alta, hot-path |
| `prefilter_strategies/` | 1745 | 14 strategie env-selectable (bloom/fts5/trie/rrf/semantic/...) | Solo `token_flat_v2` attivo (default) | **Cruft**: 13 strategie dormienti |
| `tool_grammar.py` | 916 | GBNF per ogni step + pool filter provider-aware + verb-aware filter | Vive accanto a `naming_grammar.py`. GBNF marcato ADR 0161 deprecato | **Alta**: bug-prone, 2 generatori GBNF separati |
| `naming_grammar.py` | 404 | Vocab parse + GBNF fragment + name validator | Sovrapposto con `tool_grammar.py` per GBNF | Media |
| `args_extractor.py` | 600 | Regex+LLM fallback per filler args + memoization | Usato da agent_runtime via `remediate_args_cb` | Media |
| `compound_decomposer.py` | 600 | Decomposer query con ≥2 verbi, bypassa Mētis | Path parallelo alla cascata principale | Media |
| `loop_detect.py` | 93 | Detect 2× ripetizione tool+args | Cap_same già in executor. Usato pochissimo | Bassa |
| `engine/validator.py` | 121 | Typecheck pre-execute (tool exists, args type, requires_one_of) | Opt-in `METNOS_VALIDATOR=1`. Logica simile a executor remediation | Bassa |
| `engine/proposer_metis.py` etc | 503 | Varianti β multi-strategia + frontier terminator | NON nel default (`METNOS_ENGINE=simple`) | **Cruft**: scaffolding non in produzione |

**Componenti "cruft" identificati**: ~6500 LOC con valore marginale o duplicato (multi_tool_paths, 13 prefilter_strategies dormienti, proposer_metis/recovery_metis/terminator_metis, legacy praxis duplicato di engine v2).

---

## §3 Redundancies + cruft

### 3.1 Doppia implementazione legacy + engine v2

**Sostanziale duplicazione**:
- `praxis.py` ↔ `autopath.py` (skill cache)
- `praxis_executor.py` ↔ `engine/executor.py` (deterministic execute)
- `praxis_propose.py` ↔ `engine/proposer.py` (LLM 1-shot)
- `praxis_cluster.py` ↔ `engine/cluster.py` (BGE + cosine)
- `pronoia.py` ↔ `engine/recovery.py` (recovery)
- `aporia.py` ↔ `engine/terminator.py` (honest fail)
- `praxis.sqlite` ↔ `autopath.sqlite` (storage)
- `aporiae.sqlite` ↔ `terminator_log.sqlite` (storage)

Stato: legacy resta perché `engine/proposer.py` riusa `GRAMMAR_FRAMEWORK` da `_legacy/praxis_propose.py` (path hack `sys.path.insert`). Migrazione iniziata 28/5 (vedi git status `R runtime/praxis.py -> runtime/_legacy/praxis.py`) ma incompleta.

**Impatto**: ogni bug fix richiede 2 patch. Ogni nuova feature 2 implementazioni. Test si moltiplicano. Storage doppio = inconsistenza inevitabile.

### 3.2 ADR superseded ancora attivi

- **ADR 0150 multi_tool_paths**: marcato `# DEPRECATED-PRAXIS` ma 1041 LOC ancora in `_legacy/`. Schedulato per rimozione "post-MVP" mai effettuata.
- **ADR 0151 planner_split**: marcato deprecato, già rimosso da `runtime/` ma logica simile sopravvive in tool_grammar pool filter.
- **ADR 0133 tool_grammar.py** GBNF step-by-step: ADR 0161 lo dichiara superseded da framework GBNF in praxis_propose, ma `tool_grammar.py` resta 916 LOC ancora attivo per pool filter (provider/verb).
- **ADR 0094 fast_path.py** L1: 452 LOC. Engine v2 ha `engine/fastpath.py` (210 LOC) che è un'altra cosa (user-approved). Sovrappongono solo nel nome.

### 3.3 Env flags che sono di fatto fissi

Da `runtime/_legacy/praxis_constants.py` + boot:

| Env | Default | Stato osservato |
|---|---|---|
| `METNOS_ENGINE_V2` | 1 | Sempre on in produzione |
| `METNOS_PRAXIS` | 1 | Dead path (engine v2 vince) |
| `METNOS_PLANNER_LEGACY` | 0 | Sempre off, ~3000 LOC parking |
| `METNOS_PRAXIS_FALLBACK` | 1 | Dead path (engine v2 mai cede a praxis) |
| `METNOS_PRONOIA` | 1 | Sempre on |
| `METNOS_PRONOIA_CLASSIFY_FAIL` | 1 | Sempre on |
| `METNOS_REEXECUTE_ON_REPEAT` | 0 | Sempre off |
| `METNOS_EXPLORATION_EPSILON` | 0.0 | Sempre off (pure-exploit) |
| `METNOS_CLUSTER_MERGE_ENABLED` | 1 | Daily job, validato funzionante |
| `METNOS_PROPOSER_GRAMMAR` | 1 (post hardening 28/5) | Sempre on |
| `METNOS_PROPOSER_FAST_CONFIDENCE` | 0.70 | Tunato 28/5, stabile |
| `METNOS_PROPOSER_VERB_FILTER` | 1 | Sempre on |
| `METNOS_PREFILTER` | token_flat_v2 | Default, mai cambiato in prod |
| `METNOS_PREFILTER_RULES` | 1 | Sempre on post 28/5 |

**~12 env flag sempre on/off**: ognuno aggiunge ramo `if` + test path che non viene mai esercitato. Configurabilità teorica, ridondanza pratica.

### 3.4 Componenti scaffold mai entrati in produzione

- `engine/proposer_metis.py`, `engine/recovery_metis.py`, `engine/terminator_metis.py`: 503 LOC totali. Selettore `METNOS_ENGINE=metis` mai attivato. Scaffold per "β" mai promosso.
- `engine/proposer_frontier.py`: referenziato da factory ma file inesistente.
- 13 di 14 `prefilter_strategies/`: solo `token_flat_v2` in produzione. Le altre (bloom, fts5, trie/trie_v2, rrf_ensemble, selective_semantic_v1/v2, hybrid_cascade, length_adaptive, constraint, verb_first, cached_token_flat) sono benchmark code. ~1500 LOC dormienti.

### 3.5 Magic resolver sovra-ingegnerizzati

`praxis_executor.py` + `engine/executor.py` ognuno implementa indipendentemente:
- `${stepN.field}` dot-path
- `${stepN.entries.0.x}` index list
- `${stepN.entries.*.x}` projection map
- `${FILLER:name}` LLM fast-tier
- `${RUNTIME:actor|lang|channel}` runtime injection
- `@count` cascade magic
- `@fmt:X.Y` formatted dict
- `from_step: int` piping
- `if_prev_entries_nonempty` branching
- field_synonyms fallback (`image_path` → `path`)

10 sintassi di template diverse. Ogni bug semantico va corretto 2 volte. Mētis (LLM Gemma 26B) deve essere prompt-engineered per emetterle correttamente: il prompt `praxis_propose.j2` è 280 righe.

### 3.6 Hardcode di compatibilità

`runtime/engine/proposer.py:192-197`:
```
_legacy = Path("/opt/metnos/runtime/_legacy")
if str(_legacy) not in sys.path:
    sys.path.insert(0, str(_legacy))
from praxis_propose import GRAMMAR_FRAMEWORK
```

Engine v2 importa il grammar dal legacy via path hack. Viola §7.11 (no path assoluti). Indica migrazione interrotta.

---

## §4 Industry best practices 2026

### 4.1 Tool selection a scala 100+

**Anthropic** (`platform.claude.com/docs`, `anthropic.com/engineering/advanced-tool-use`):
> "Instead of sending all 100+ tools to the model, filter to the most relevant ~15 tools using semantic search or keyword matching."

Anthropic Tool Search Tool (BM25 / regex / embedding): definizioni con `defer_loading: true`, il modello chiama `tool_search_tool` per discovery on-demand. Bench interni: Opus 4 49% → 74%, Opus 4.5 79.5% → 88.1% sull'accuracy di selezione. Token saving 134K → ~10K.

**Implicazione per Metnos**: prefilter top-K (già fatto via `rank_with_intent k=12` in `engine/dispatch.py`) è il pattern industria-standard. Né Praxis né cluster sono richiesti per accuracy.

### 4.2 Plan caching (proposer-executor + cache)

**Agentic Plan Caching (Zhang et al., 2025, arxiv 2506.14852)**: cache tool-use plans keyed by extracted keywords. Mētis-like ma molto più semplice: keyword → plan, non cluster semantico + champion/challenger.

**GPTCache** (open source, integra con LangChain/LlamaIndex): cache semantica con embedding, 2-10× speedup su hit. Pattern: query → embed → cosine top-1 → cache hit. Single layer.

**Implicazione**: la cache Praxis (cluster + champion + LWW + filler + skill_versions audit + retry_on_repeat) è ~10× più complessa del pattern industria. Stessi benefici si ottengono con cache cosine LRU + TTL.

### 4.3 GBNF vs structured outputs (xgrammar, vLLM)

**xgrammar** (default in vLLM/SGLang/TRT-LLM da marzo 2026): <40µs/token, near-zero overhead, FSM cache. Bench settembre 2025: xgrammar > llguidance > Outlines su scenari ripetuti.

**OpenAI/Anthropic/Gemini**: tutti supportano structured output JSON Schema nativo. Convergenza ecosystem.

**Implicazione**: `tool_grammar.py` (916 LOC) e `GRAMMAR_FRAMEWORK` custom per Gemma sono lavoro che il llama.cpp + vLLM ecosystem già fanno meglio. JSON Schema + llama-server native grammar coverage ~100%.

### 4.4 Plan-and-execute pattern (LangGraph)

**Standard LangGraph plan-and-execute**:
1. Planner (LLM grande) → step list
2. Executor (per-step, LLM piccolo o tool diretto)
3. Validator (scoring opzionale)
4. Router (pure Python, decide next)

Variante PEV (Plan-Execute-Validate) aggiunge retry automatico + MCP integration. Reference: `dev.to/manjunathgovindaraju/building-a-reliable-langgraph-workflow-plan-execute-validate-pev`.

**Implicazione**: Praxis è plan-and-execute con cache aggressiva + 5 mitologie. Il pattern base è 3 nodi (planner + executor + validator), non 5 divinità + 10 sintassi di template.

### 4.5 Procedural memory (Letta/MemGPT)

**Letta skill learning**: agenti imparano da feedback testuale + trajectory. Skill = (situation → action) callable. Improvement 36.8% over baseline. Procedural memory non sostituisce planner, lo aumenta.

Pattern: skill ACTIVATED da retriever (BM25/embed), eseguita deterministic, fallback a LLM se skill non match.

**Implicazione**: Praxis auto-promote (2 obs + 100% ok) → ACTIVE è un riff su Letta. Ma Letta non ha champion/challenger né cluster semantic merge: un retriever standard basta.

### 4.6 Local LLM tool calling state (Gemma 4 / Qwen 3.6)

Bench InsiderLLM maggio 2026: Gemma 4 27B best general-purpose per 24GB VRAM, Qwen3 32B fallback, Llama 3.3 70B il più accurato (~97%) ma richiede 48GB+.

Con grammar constraints (Ollama format o llama.cpp `--json`): "il modello di qualità conta meno, i constraint garantiscono output valido". Cita: function calling accuracy matches cloud APIs per single tool calls.

**Implicazione per Metnos (Gemma 4 26B locale)**: la base è sufficiente. Il proposer attuale fa 46% top-1 sul bench 446q FROZEN → margine di miglioramento non viene da modello più grande, viene da prompt + prefilter + grammar costruiti correttamente.

---

## §5 Simple baseline comparison

Tre baseline "simple" da confrontare con stato corrente Praxis Engine.

### 5.1 Simple A — direct LLM tool call

Pattern: per ogni turn, system prompt contiene full pool 80 tool + descrizioni. `chat_with_tools()` nativo Gemma con `tools=[...]` parametro. Output `tool_calls` strutturati. Step-by-step (ReAct classico).

- LOC necessari: ~500 (agent_runtime + tool_grammar minimal + intent_extractor)
- Latency: 5-10 LLM call/turn × 3-5s = **15-50s** (worst case)
- Accuracy: cap by token budget (134K → degraded selection, vedi Anthropic)
- Storage: nessuno
- Fallback: nessuno → ogni fail = retry brute

**Verdict**: troppo costoso in token + latency. Validato da Anthropic doc: "always filter".

### 5.2 Simple B — prefilter top-K + LLM tool call (ReAct)

Pattern: prefilter `rank_with_intent k=15` → system prompt con 15 tool → LLM step-by-step. Nessuna cache, nessun Mētis-Noûs split.

- LOC necessari: ~800 (prefilter rules + agent_runtime + tool_grammar minimal)
- Latency: 5-7 LLM call × 3-5s = **15-35s** mean
- Accuracy: ~46% top-1 osservato ≈ Mētis attuale (proposer è stesso step di selezione)
- Storage: nessuno
- Fallback: nessuno

**Verdict**: equivalente per accuracy a Mētis attuale ma 3-5× più lento (multi-call invece di 1-shot framework). NON conveniente vs stato attuale.

### 5.3 Simple C — prefilter + LRU semantic cache + 1-shot framework

Pattern:
1. `rank_with_intent k=12` → top-K tool
2. Cache lookup: cosine BGE query → top-1 cached plan. Se sim ≥ 0.90 → riusa plan deterministic.
3. Cache miss → 1-shot LLM emette framework JSON (Anthropic structured output, no GBNF custom)
4. Deterministic executor (riusa engine/executor.py)
5. Feedback ✓ → cache put. Feedback ✗ → cache evict (no anti-skill TTL elaborato)

- LOC necessari: ~1500 (prefilter + cache + 1-shot proposer + executor + intent_extractor)
- Latency:
    - cache hit: **<2s** (no LLM, just deterministic execute)
    - cache miss: **6-15s** (1 wise call + execute)
- Accuracy: stessa di Mētis attuale (proposer step uguale)
- Storage: 1 sqlite LRU `cached_plans(query_hash, embedding, plan_json, n_uses, last_used)`
- Fallback: cache miss → propose; propose fail → terminator

**Verdict**: copre 80% del valore di Praxis Engine in 1500 LOC vs 5100 LOC legacy + 2900 LOC engine v2 = ~8000 LOC eliminati. **Mantiene plan caching, deterministic execute, recovery via re-propose**. Perde: champion/challenger, anti_skill TTL, classify_fail granulare, exploration epsilon, skill_versions audit.

---

## §6 Trade-off matrix

| Metrica | Praxis legacy attuale | Engine v2 attuale | Simple A | Simple B | **Simple C** |
|---|---|---|---|---|---|
| LOC core | 5100 | 2900 (+ riusa 1500 legacy) | 500 | 800 | **1500** |
| LOC dipendenze | 15000 | 15000 | 2000 | 4000 | **4000** |
| LOC totale | ~23K | ~21K | ~2.5K | ~5K | **~6K** |
| Latency cache hit | 8-15s | 8-15s | n/a | n/a | **<2s** |
| Latency cache miss | 12-25s | 12-25s | 15-50s | 15-35s | 6-15s |
| Accuracy first-tool | ~46% (bench 446) | ~46% | <30% (token saturation) | ~46% | ~46% |
| Accuracy con learning | claim 94% (35q curate, non validato 446) | non misurato | nessun learning | nessun learning | ~70-80% atteso (cache hit dopo 1 ✓) |
| Storage sqlite | 5 DB sovrapposti | 3 DB | 0 | 0 | **1 DB** |
| Env flags | 30+ | 15+ | 0-3 | 3-5 | **3-5** |
| Fallback paths | 5 (fastpath → engine → praxis → planner → terminator) | 4 | 0 | 1 | **2 (cache → propose → terminator)** |
| Componenti separati da capire | 13 (5 Praxis + 4 engine + intent + grammar + cluster + recovery + aporia) | 8 (engine v2) | 2 | 3 | **4 (prefilter, cache, propose, executor)** |
| Tempo debug failure end-to-end | 30-60min (capire quale layer + quale DB + quale env) | 20-40min | 5min | 10min | **10-15min** |
| Tempo onboarding nuovo contributore | 8h | 4h | 1h | 2h | **2-3h** |
| Pattern industry-standard? | NO (Mētis mitologia custom) | parziale | sì | sì | **sì (Anthropic + GPTCache + LangGraph PEV)** |

**Punto chiave**: il differenziale di accuracy first-tool fra Praxis e Simple C è ~0%. Il differenziale di accuracy con feedback learning è speculativo (claim 94% Praxis non validato out-of-the-box su 446 query reali). Il differenziale di latency cache hit è dove Simple C **vince** (cache LRU semantica veloce, nessun cluster lookup complesso).

---

## §7 Opzioni a/b/c

### Opzione (a) — Iterate: pulizia chirurgica senza rebuild

**Cosa fare**:
1. Eliminare `runtime/_legacy/` interamente (~5100 LOC). Engine v2 è già il path attivo.
2. Rimuovere `multi_tool_paths.py` (1041 LOC), `compound_decomposer.py` se non usato in produzione (verificare bench).
3. Rimuovere 13 `prefilter_strategies/` dormienti, mantenere solo `token_flat_v2`.
4. Rimuovere `proposer_metis.py`, `recovery_metis.py`, `terminator_metis.py` (503 LOC). Mētis come variante β scaffolding mai promosso.
5. Consolidare GBNF: spostare `GRAMMAR_FRAMEWORK` da `_legacy/praxis_propose.py` a `engine/proposer.py` (eliminare path hack), eliminare `tool_grammar.py` step-by-step GBNF (916 LOC) tenendo solo pool filter (~200 LOC).
6. Unificare sqlite: migrazione `praxis.sqlite` + `autopath.sqlite` → singolo schema engine v2. Aporia + terminator_log idem.
7. Rimuovere env flag sempre on/off → costanti hardcoded con commento.
8. Marker `# DEPRECATED-*` rimossi insieme al codice.

**Tempo**: 3-5 giorni di lavoro tecnico, basso rischio.

**Pro**: -8000 LOC, debug più veloce, storage unificato, no path hack. Architettura `engine/` resta come riferimento.

**Contro**: l'architettura di base (5 layer + multi-template + magic resolvers) resta complessa. La complessità non viene tagliata, viene compattata.

**Rischi**: regressioni su edge case che vivono solo nel legacy (es. fuzzy match Jaccard 0.9 di `praxis.try_match` step 2). Mitigazione: snapshot bench 35q curate + bench 95q domain prima/dopo.

**Validazione**: bench `bench_praxis_coverage` deve rimanere ≥98% coverage, accuracy invariata, latency invariata o migliorata.

### Opzione (b) — Selective rebuild: mantenere 2-3 componenti, rebuild il resto

**Componenti che meritano sopravvivenza** (analisi onesta):
1. **Deterministic executor** con magic resolvers (`engine/executor.py` 759 LOC). Valore unico: il piping `from_step` + `${stepN.field}` + projection `*` + `${FILLER:}` è la spina dorsale e funziona. NON è sostituibile da framework esterni senza perdere semantica.
2. **Intent extractor** (`intent_extractor.py` ~400 LOC). LLM fast 370ms, 100/100 su test. Spina dorsale.
3. **Prefilter rank_with_intent** (`prefilter.py` rank_with_intent + nuove rules ~600 LOC core). Top-K selection è industry-standard, già funziona.

**Cosa rimpiazzare** con design semplice:
- Mētis (proposer) → 1-shot LLM nativo (Gemma `chat_with_tools` con JSON Schema, no GBNF custom). 100 LOC.
- Praxis store + Pronoia recovery + Aporia + cluster + champion/challenger → cache LRU semantica (cosine BGE) + 1 retry su fail + terminator template. 400 LOC totali.
- Engine v2 fastpath/autopath/validator → assorbiti dalla nuova cache.

**Risultato atteso**: ~2000 LOC core (executor 759 + intent 400 + prefilter 600 + cache+propose+terminator 400). Dipendenze rimangono (BGE, llm_router, prompt_loader) ma vocabolario chiuso §2.2 + vaglio + dialog_pending restano intatti.

**Tempo**: 2-3 settimane (1 settimana design + porting executor, 1 sett rebuild Mētis sostituto + cache, 1 sett bench + validation + dismissione legacy).

**Pro**: salvi il pattern che funziona (executor deterministic, intent, prefilter). Sostituisci la torre Mētis-mitologica con cache standard. -7000 LOC stimato.

**Contro**: 2-3 settimane di lavoro. Discontinuità per i feedback ✓✗↻ già accumulati (migrazione dati possibile ma serve schema mapping). Test E2E vanno aggiornati.

**Rischi**: l'executor v2 (759 LOC) ha bug stratificati dai 6 mesi di Praxis. Rebuild conservativo conviene a tagliare 200 LOC sui `_resolve_*_with_synonyms` fallback non più necessari.

**Validazione**: bench 95q + bench 446q FROZEN side-by-side. Accuracy entro ±2pp del corrente, latency uguale o migliore.

### Opzione (c) — Full rebuild simpler

**Cosa fare**:
1. Branch `metnos-rebuild/` separato. Codice live continua a funzionare.
2. Design vincolato a:
   - Anthropic tool_search pattern (defer loading 100 tool, top-15 via embedding)
   - 1-shot framework propose con JSON Schema nativo (NO grammar custom)
   - Executor deterministic minimal (from_step + ${stepN.field} + ${FILLER:} + final_message template, 5 placeholder max)
   - Cache LRU semantica sqlite (cosine BGE, evict ts_last_used, TTL 30gg)
   - Recovery: 1 retry escluding failed tool, poi terminator template per classe errore (4 classi)
3. Mantieni vocabolario §2.2 + executor manifest TOML + vaglio + dialog_pending + i18n: non parte dell'agente, non rebuildare.
4. Bench parallelo settimanale: nuovo branch vs prod.
5. Switch quando: bench 446q ±2pp + ≥3 settimane di test interno.

**Target dimensione**: ~2000 LOC totali per il core "agente" (proposer + executor + cache + recovery + terminator + intent + prefilter rank_with_intent). Da 23K → 2K = -91% LOC.

**Tempo**: 4-6 settimane wall-clock (~3 settimane lavoro effettivo).

**Pro**: design pulito, segue best practice 2026 (Anthropic + GPTCache + LangGraph PEV), zero cruft. Onboarding 1h.

**Contro**: investimento 1+ mese. Rischio di re-implementare bug che il sistema attuale ha già risolto. Migrazione feedback storico va pianificata.

**Rischi**: 
- "Second system effect" — il rebuild può crescere imitando feature che servono raramente. Mitigazione: vincolo LOC hard cap 2500.
- Discontinuità UX se cache non si popola velocemente. Mitigazione: bootstrap cache da `observations` storiche.
- Test bench cherry-picked nel branch nuovo. Mitigazione: comparison side-by-side weekly su bench FROZEN 446q.

**Validazione**: 
- Bench 95q domain coverage ≥98% (parità)
- Bench 446q proposer accuracy ≥45% top-1 (parità o meglio)
- Latency p50 cache hit ≤2s, miss ≤15s
- 2 settimane live test + zero regressioni segnalate da Roberto

---

## §8 Raccomandazione finale

**Rifare from-zero ne vale la pena? Risposta onesta: SÌ, ma incrementalmente — opzione (b)**.

### Ragionamento

1. **L'accuracy non viene da Praxis**. Il 46% first-tool del Proposer è figlio di prefilter + Gemma + GBNF, non di cluster/champion/anti_skill/Pronoia. Il claim 94% Praxis è su 35 query curate non riproducibile. Smantellare la cache complessa non degrada accuracy in modo misurabile.

2. **La complessità non è giustificata dai bench**. 5 mitologie, 5 sqlite, 10 sintassi template, 30+ env flag, 2 implementazioni (legacy + engine v2) parallele. Industria-standard 2026 (Anthropic doc, LangGraph PEV, GPTCache) raggiunge lo stesso outcome con 3 nodi + 1 cache. Metnos ha 13 componenti.

3. **L'opzione (a) "pulizia"** è troppo poco: i ~5000 LOC eliminabili sono cruft, ma la torre architetturale resta. Stesso debug time, stessa mental load, stessi 4 livelli di cascata.

4. **L'opzione (c) "full rebuild"** è ambiziosa ma rischia second-system effect e 1 mese di lavoro. Inoltre la conoscenza accumulata in `engine/executor.py` (759 LOC di magic resolver testati) ha valore reale: il rebuild lo riscriverebbe perdendo i fix subtle.

5. **L'opzione (b) "selective rebuild"** è il punto medio onesto:
   - Conserva 3 componenti che funzionano (executor, intent, prefilter)
   - Sostituisce la torre Praxis (~3500 LOC) con cache LRU semantica + propose + recovery + terminator (~500 LOC)
   - Tempo: 2-3 settimane
   - Risk-adjusted return: tagli 7000 LOC mantenendo accuracy e migliorando latency cache-hit

### Prima azione concreta (questa settimana)

Eseguire la **§7 opzione (a) come step preparatorio**:
1. Rimuovere `runtime/_legacy/` (~5100 LOC) confermando che `engine v2` regge tutta la cascata (test set 95q + 446q FROZEN).
2. Rimuovere 13 prefilter_strategies dormienti, scaffolding metis variants, multi_tool_paths.
3. Output: -8000 LOC, 1 sqlite invece di 5, zero path hack.

Questo NON è un rebuild — è la pre-condizione necessaria per misurare onestamente cosa l'opzione (b) deve sostituire. Dopo la pulizia (a), il decidere fra (b) e status quo si misura con criteri puliti:
- Se post-pulizia il sistema gira con ≤3000 LOC core e tempo-debug accettabile → fermarsi qui.
- Se le complessità residue (5 layer engine, magic resolver, validator opt-in, schema autopath) ancora pesano → procedere con (b).

### Modo per prova/disprova del piano

**Test 1 — pulizia (a) non degrada**: bench 95q `bench_praxis_coverage` pre/post-pulizia. Coverage e latency entro ±1%. Tempo: 1 ora di run.

**Test 2 — Simple C fattibile**: implementare standalone Simple C in ~500 LOC su branch separato, eseguire bench 446q FROZEN. Se accuracy ≥40% top-1 (entro 6pp del corrente) → conferma fattibilità (b). Tempo: 1 settimana di scaffolding.

**Test 3 — feedback learning preservato**: simulare 20 query ripetute con ✓ feedback. Verificare cache hit dopo 1ª ✓. Se hit-rate ≥80% dopo 2 turni → conferma che cache semplice basta (no champion/anti_skill TTL). Tempo: 1 giorno.

Se Test 1+2+3 passano → (b) è validata, procedere. Se uno fallisce → review.

### Cosa NON fare

- NON eliminare l'intent_extractor (370ms, 100/100 test corpus, spina dorsale).
- NON eliminare prefilter rank_with_intent (top-K è industry-standard, accuracy lever principale).
- NON rimuovere la deterministic execution di engine/executor.py (semantica from_step + magic resolver è il vero asset).
- NON tagliare vaglio + dialog_pending + dispatcher safety: sono ortogonali all'agente.
- NON rimuovere telemetria turn log (serve per validare (a) e (b)).

---

## Sources

- [Anthropic Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use)
- [Claude Tool Search Tool Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- [Anthropic Writing Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [LangGraph Plan-and-Execute](https://www.langchain.com/blog/planning-agents)
- [LangGraph Plan-Execute-Validate template](https://dev.to/manjunathgovindaraju/building-a-reliable-langgraph-workflow-plan-execute-validate-pev-automated-retries-and-mcp-1pik)
- [Agentic Plan Caching (arxiv 2506.14852)](https://arxiv.org/html/2506.14852v2)
- [Why Agent Caching Fails (arxiv 2602.18922)](https://arxiv.org/html/2602.18922v1)
- [GPTCache + LangChain semantic caching](https://www.getmaxim.ai/articles/top-semantic-caching-solutions-for-ai-apps-in-2026/)
- [xgrammar / vLLM constrained decoding 2026](https://www.aidancooper.co.uk/constrained-decoding/)
- [Structured outputs guide 2026](https://logic.inc/resources/structured-outputs-guide)
- [Letta skill learning + procedural memory](https://www.letta.com/blog/skill-learning)
- [Mem0 vs Letta vs MemGPT 2026](https://tokenmix.ai/blog/ai-agent-memory-mem0-vs-letta-vs-memgpt-2026)
- [Local LLM tool calling Gemma 4 / Qwen 3 (InsiderLLM, May 2026)](https://insiderllm.com/guides/function-calling-local-llms/)
- [Best local models for tool calling 2026](https://www.promptquorum.com/power-local-llm/best-local-models-tool-calling-2026)
- [Agentic Design Patterns 2026 (Augment Code)](https://www.augmentcode.com/guides/agentic-design-patterns)
