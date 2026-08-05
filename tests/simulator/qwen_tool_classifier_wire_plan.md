# Qwen Tool Classifier — Wire-in Plan (proposed, NOT applied)

## Status

Modello fine-tuned: `/tmp/qwen_ft_tool_classifier_v1/` (~1.2GB)
Inference: `/opt/metnos/tests/simulator/qwen_tool_signal.py`
Metadata: `/tmp/qwen_ft_tool_classifier_v1/metnos_tool_classifier_meta.json`

**Holdout (98 samples, frozen seed=42):**
- Top-1: 80/98 = **81.6%**
- Top-3: 88/98 = **89.8%**
- Top-3 misses: 10

**Analisi qualitativa dei 10 miss top-3**: 5+ sono label-noise nel test_set
(es. *"mostrami le foto di roma" → expected `find_files`, model predice
correttamente `find_images_indices`*; *"cerca mail da bookings" → expected
`read_tasks_history` (chiaramente sbagliato), model predice `read_messages`*).
Accuracy "vera" (post-cleanup label) probabilmente >=85% top-1 e ~95% top-3.
Train loss: 1.355 (v2 baseline) → 0.887 (10 epochs tool classifier).

## Razionale wire-in

Il classifier predice direttamente `first_tool` ∈ {45 labels} con score
cosine ∈ [0,1]. Pattern uguale al fallback `affinity_semantic.py` gia'
attivo nel prefilter: boost additivo sopra `affinity_score` quando il
modello esprime alta confidenza.

## Pattern proposto (mirroring affinity_semantic + qwen_intent_signal)

### Step 1 — Promozione del modello a path production

Spostare `/tmp/qwen_ft_tool_classifier_v1/` in:
```
~/.local/share/metnos/tool_classifier/v1/
```

Rispetta §7.11 e convention esistente per `intent_classifier/vN/`. Trainer
(`qwen_finetune_tool_classifier.py`) e weekly retrain (mirroring
`install/setup.sh` step [7/8]) scrivono in `vN+1/`. Loader LWW max version.

### Step 2 — Promozione del modulo di inference

Spostare `tests/simulator/qwen_tool_signal.py` in:
```
/opt/metnos/runtime/tool_classifier/loader.py
```

Pattern uguale a `runtime/intent_classifier/loader.py`:
- `_model_dir()` LWW max-version su `~/.local/share/metnos/tool_classifier/`
- `is_available()`, `predict_tool(query) -> str|None`
- `predict_tool_with_score(query, top_k=3) -> list[(tool, score)]`
- `warmup(lang="it") -> bool` per startup health check
- Cache process-level `_PRED_CACHE` (gia' presente nel modulo simulator)
- Soglia env `METNOS_TOOL_CLASSIFIER_THRESHOLD` (default 0.40)

### Step 3 — Helper di boost in `runtime/tool_classifier/boost.py`

Modulo nuovo, mirroring `affinity_semantic.py` + `qwen_intent_signal.score_boost`:

```python
import os

_ALPHA_DEFAULT = 4.0
_THRESHOLD_DEFAULT = 8

def is_enabled() -> bool:
    return os.environ.get("METNOS_TOOL_CLASSIFIER_BOOST", "0") == "1"  # OPT-IN

def alpha() -> float:
    try: return float(os.environ.get("METNOS_TOOL_CLASSIFIER_ALPHA", _ALPHA_DEFAULT))
    except ValueError: return _ALPHA_DEFAULT

def hard_threshold() -> int:
    try: return int(os.environ.get("METNOS_TOOL_CLASSIFIER_THRESHOLD", _THRESHOLD_DEFAULT))
    except ValueError: return _THRESHOLD_DEFAULT

def tool_score_map(query: str) -> dict[str, float]:
    from runtime.tool_classifier.loader import predict_tool_with_score
    top = predict_tool_with_score(query, top_k=5)
    return {t: s for t, s in top if s >= 0.30}
```

### Step 4 — Hook nel prefilter (3 callsite)

#### 4a) `_rank_adaptive_legacy` (fallback BoW path, linea ~1066)

DOPO il blocco `affinity_semantic` esistente, prima di `rel_cutoff`:

```python
try:
    from runtime.tool_classifier import boost as _tc
    if _tc.is_enabled() and top_score < _tc.hard_threshold():
        _tmap = _tc.tool_score_map(query)
        if _tmap:
            _a = _tc.alpha()
            scored = [(s + _a * _tmap.get(e.name, 0.0), e)
                      for s, e in scored]
            scored.sort(key=lambda p: p[0], reverse=True)
            scores = [s for s, _ in scored]
            top_score = scores[0] if scores else 0
            semantic_reason = (semantic_reason + "+tool_classifier"
                                if semantic_reason else "tool_classifier")
except Exception:
    pass
```

#### 4b) `rank_with_intent` (intent-driven path, linea ~704)

Dopo la `primary` list, riordinare con tool_classifier come tie-breaker
quando ci sono multipli candidate con stesso `verb`:

```python
try:
    from runtime.tool_classifier import boost as _tc
    if _tc.is_enabled() and len(primary) > 1:
        _tmap = _tc.tool_score_map(query)
        if _tmap:
            _a = _tc.alpha()
            primary = sorted(
                primary,
                key=lambda p: -(p[0] + _a * _tmap.get(p[1].name, 0.0))
            )
except Exception:
    pass
```

#### 4c) Eager warmup in HTTP server boot

In `runtime/metnos_http_server.py` startup hook:

```python
try:
    from runtime.tool_classifier.loader import warmup as _tc_warmup
    _tc_warmup(lang="it")
except Exception:
    pass
```

### Step 5 — Smoke test in `runtime/smoke.py`

Aggiungere 5-10 query da `test_set_FROZEN_v2.json` con assertion sul
boosted top-1.

## Misure / Rollout

1. **Bench A/B**: rieseguire `bench_prefilter_strategies.py` con/senza
   `METNOS_TOOL_CLASSIFIER_BOOST=1`. Soglia di accettazione: +5% top-1
   sulla baseline su query reali del 28/5.
2. **Latenza overhead**: predict cosine su 45 anchor + 1 query ~ 20-30ms
   CPU. Comparable a affinity_semantic (~25ms).
3. **Memoria**: il modello SentenceTransformer (~1.2GB safetensors).
4. **Opt-in default-OFF**: `METNOS_TOOL_CLASSIFIER_BOOST=1` finche' bench
   non conferma +5% accuracy senza regressioni latenza > +50ms p95.

## Open question (da chiarire con Roberto)

- (Q1) Soglia confidence: per il boost serve threshold sul cosine?
  Proposta: `score < 0.45` -> skip boost.
- (Q2) Coverage 45/84 labels: tool del catalog senza esempi non
  partecipano al boost. OK come additive signal, non come sostituto.
- (Q3) Retrain trigger: mirror `intent_classifier/v1` weekly cron, oppure
  trigger su delta_size >= 100 nuove query?
