---
id: 0146
title: Consolidamento tier LLM — fast/middle/wise unificati su Gemma 4 26B + drafter
date: 2026-05-18
status: accepted
area: runtime | llm | docs
related:
  - 0025  # three-tier LLM architecture (sempre valido)
  - 0044  # default qwen3:8b fast — SUPERSEDED-BY this ADR
  - 0106  # fast tier bench PROMOTE_FAIL (verdict empirico)
  - 0142  # consult_frontier tier-map (Haiku/Sonnet/Opus — unchanged)
  - 0144  # supranet consolidation .33
  - 0207  # fast-level logical vocabulary; binding centralization retained
supersedes:
  - 0044
---

## Context

Tre verità in conflitto rilevate durante la pubblicazione su GitHub
(18/5/2026):

1. **Codice**: `runtime/llm_router.py::DEFAULT_TIERS["fast"]` punta
   a `ollama:qwen3:8b @ localhost:11434` (ereditata da ADR 0044,
   aprile 2026).

2. **Bench formale ADR 0106** (7/5/2026, corpus reale n=50):
   - `intent_extractor` concordanza fast↔middle = 58% (soglia 90%)
   - `vaglio` LLM judge concordanza = 62% (soglia 95%)
   - **Speedup fast vs middle = 0.72×** (fast era PIÙ LENTO su intent
     extraction, perché Gemma 4 26B su llama-server con `--cache-prompt`
     + speculative decoding batte qwen3:8b su Ollama senza warmup).
   - Verdict overall: **PROMOTE_FAIL** — tier `middle` resta su
     entrambi i call site.

3. **Realtà operativa .33** (snapshot 18/5/2026):
   - **Un solo** processo llama-server attivo su `:8080`, main
     `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` + drafter
     `gemma-4-E2B-it-Q4_K_M.gguf` (speculative decoding via `-md`,
     `--spec-draft-ngl 999 --spec-draft-n-max 4 --spec-draft-p-min 0.75`),
     `--parallel 2` (slot 0 giorgio2, slot 1 Metnos).
   - `ollama.service` `inactive + disabled` — `qwen3:8b` non gira.
   - Ogni caller `tier="fast"` rimasto nel codice cadrebbe su un
     Ollama spento. Funzionante in pratica solo perché ADR 0106 ha
     già spostato `intent_extractor` e `vaglio` su `middle`.

L'inconsistenza è un debito tecnico. Documenti diversi raccontano
storie diverse a seconda di quale aprivi:
- `runtime/llm_router.py` → qwen3:8b
- ADR 0044 → qwen3:8b
- ADR 0106 → qwen3:8b *bocciato* per intent+vaglio
- ADR 0142 → Haiku (ma è la mappa consult_frontier, distinta)
- CLAUDE.md §11 → qwen3:8b
- Realtà → solo Gemma

## Decision

Consolidare la verità su **un singolo modello + drafter speculative
decoding**, esposto su un singolo endpoint. Le sezioni che seguono
sono **la fonte unica**; ogni altra documentazione punta qui.

### Tier mapping canonico

```python
# runtime/llm_router.py — DEFAULT_TIERS (post-ADR 0146)
DEFAULT_TIERS = {
    "fast": {
        "provider": "llamacpp",
        "model":    "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        "endpoint": "http://127.0.0.1:8080",
        "think":    False,
        "num_predict": 400,
    },
    "middle": {
        "provider": "llamacpp",
        "model":    "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        "endpoint": "http://127.0.0.1:8080",
        # think default true via llama-server config (--reasoning-budget 1024)
    },
    "wise": {
        "provider": "llamacpp",
        "model":    "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        "endpoint": "http://127.0.0.1:8080",
    },
    "frontier": {
        "provider": "anthropic",
        "model":    "claude-opus-4-7",
        # OpenAI GPT-5 fallback in [[frontier.fallback]] del file utente
    },
}
```

Tutti e tre i tier locali (fast/middle/wise) puntano **allo stesso
processo llama-server**. La differenza è esclusivamente nei parametri
per-call:

| Tier | `think` | `num_predict` | Quando |
|------|---------|---------------|--------|
| fast | False | 400 | call procedurali (eventuale futuro promote se bench cambia) |
| middle | True (default server) | dinamico (`--reasoning-budget 1024` server-side) | ReAct planner |
| wise | True | uguale a middle, con cap esteso se synth stage 5 | synt + heavy planning |

### Architettura runtime LLM

- **Un processo** `llama-server` su `:8080`
- **Main**: Gemma 4 26B (`gemma-4-26B-A4B-it-UD-Q4_K_M.gguf`)
- **Drafter**: Gemma 4 E2B (`gemma-4-E2B-it-Q4_K_M.gguf`) caricato
  nello stesso processo via `-md`, vocab condiviso con il main →
  speculative decoding automatico per ogni chiamata, su tutti i tier
- **Parametri canonical** in `/home/user/llamacpp.env` (derivato
  da `/opt/suprastructure/config/models.yaml` via `supra-model`)
- `--parallel 2`: due slot indipendenti, slot 0 giorgio2, slot 1 Metnos

### Mappa `consult_frontier` (invariata)

`consult_frontier` (ADR 0142) mantiene la propria tier-map separata
in `~/.config/metnos/llm_tiers.toml`:

```toml
[fast]      { provider = "anthropic", model = "claude-haiku-4-5" }
[middle]    { provider = "anthropic", model = "claude-sonnet-4-6" }
[wise]      { provider = "anthropic", model = "claude-opus-4-7"   }
[frontier]  { provider = "anthropic", model = "claude-opus-4-7"   }
```

Questa NON è la mappa dei tier dell'agente: è la mappa che
`consult_frontier` usa quando l'utente delega esplicitamente al cloud.
Convivono perché servono casi diversi.

## Centralization rule

**Da oggi**:

1. **Codice = SoT**: `runtime/llm_router.py::DEFAULT_TIERS` è la
   dichiarazione canonica. Ogni cambio passa qui per primo.

2. **Doc canonica umana**: questa ADR (0146).

3. **CLAUDE.md §11**: una riga che punta a questa ADR + a
   `DEFAULT_TIERS`. Niente inlining di nomi modello.

4. **Tutti gli altri doc** (public `docs/LLM_TIERS.md`, README,
   installer phase 2, etc.): rinviano qui, **non duplicano** i
   valori. Quando duplicano, è esempio illustrativo con etichetta
   "current default — see DEFAULT_TIERS for the truth".

5. **Bench passati** (ADR 0106 e altre) restano in archivio con i
   loro numeri storici, ma sono interpretati alla luce di questa ADR:
   il fast tier oggi *non* è qwen3:8b — è un profilo parametri sullo
   stesso Gemma — quindi il "fast vs middle" della 0106 non si
   applica al setup attuale.

## Migration

Codice e doc da aggiornare in questa sessione:

- [x] Nuova ADR 0146 (questa)
- [ ] `runtime/llm_router.py::DEFAULT_TIERS` → struttura sopra
- [ ] `runtime/llm_router.py` docstring + commento "Default baked-in"
- [ ] CLAUDE.md §11 "LLM tier" → riga che punta qui
- [ ] ADR 0044 `status: accepted` → `status: superseded` + breadcrumb
  a 0146
- [ ] `docs/it/internal/infrastructure_inventory.html` sezione B
  (LLM backend) → menzione drafter E2B
- [ ] Public stub:
  - [ ] `docs/LLM_TIERS.md` → tabella tier corretta (Gemma su tutti
    e tre i locali)
  - [ ] `README.md` → short version
  - [ ] `install/phases/phase2_infra.py` → fast tier NON downloada
    qwen3-8b, eredita il config llamacpp di middle

Non toccati:
- ADR 0025 (three-tier architecture): il concetto resta valido, solo
  i riferimenti `qwen3:8b` sono storici. Non si supersede.
- ADR 0106 (bench): il verdict empirico (qwen3:8b non promosso) resta
  valido. Non si supersede; si interpreta in chiave 0146.
- ADR 0142 (consult_frontier): la mappa Haiku/Sonnet/Opus è distinta
  e non tocca i tier dell'agente.
- Bench JSONL storici sotto `tests/stress/synt/`: data records,
  immutabili.

## Consequences

+ **Centralizzazione**: chi vuole sapere "che modello uso per tier X"
  legge `DEFAULT_TIERS` o questa ADR, basta. Niente più drift.
+ **Coerenza con realtà operativa**: `.33` non ha più una
  discrepanza fra config in codice e processo running.
+ **Caller `tier="fast"` non rotti più**: erano latenti su Ollama
  spento; ora puntano al Gemma vivo.
+ **Setup utente nuovo più semplice**: un solo modello locale da
  scaricare (~15 GB Gemma 4 26B + ~2 GB drafter E2B), non un secondo
  download qwen3:8b sostanzialmente inutile.
- **Perdita teorica di un tier fast economy**: se in futuro emerge un
  caller che beneficia di vero `think=false` rapido, si rivaluta.
  Per ora nessun caller lo esercita; ADR 0106 ha già dimostrato che
  qwen3:8b non porta benefici reali.

## Note di tuning ereditate da `.33`

I parametri canonical di llama-server su `.33` (da preservare in
`/opt/suprastructure/config/models.yaml`):

| Param | Valore | Razionale |
|---|---|---|
| `--cache-type-k`/`-v` | `f16` | +40% short-prompt, +110% long-prompt vs q4_0 (rebench 9/5/2026) |
| `-c` | `131072` | enrichment Metnos saturava 32k slot; rebench KV f16 ~200 KiB/tok → 131k ≈ 26 GB su 124 GB UMA |
| `-b 4096 -ub 256` | tuning Vulkan Strix Halo (gfx1151 MoE), regression con `-ub > 512` (issue ggml-org/llama.cpp#18725) |
| `--threads 8` | UMA memory-bandwidth-bound, oltre 8 regredisce |
| `--cache-reuse 256` | incremental KV reuse multi-turn |
| `--spec-draft-n-max 4` | sweet spot Strix Halo |
| `--spec-draft-p-min 0.75` | sweet spot bench 9/5/2026 |

Per setup utente diverso (NVIDIA, distro non-Linux, GPU diversa) i
parametri si rivalutano. Questa ADR documenta il default `.33`; la
public release lascerà i tuning a un override editabile.
