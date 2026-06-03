"""runtime.intent_classifier — Qwen3-Embedding-0.6B fine-tuned per query→canonical_object.

Engine intent classification (rimpiazzo hardcoded+affinity).

Usage:
    from runtime.intent_classifier import classify_query_object, is_available

    obj = classify_query_object("appuntamenti di domani")  # → "events"

API:
- `classify_query_object(query, lang='it', threshold=None)` → str | None
- `is_available()` → bool (modello fine-tuned presente)
- `warmup(lang='it')` → bool (eager load)

Storage:
- Model: `~/.local/share/metnos/intent_classifier/v<N>/` (LWW max version).
- Audit: `~/.local/share/metnos/intent_classifier/retrain_audit.jsonl`.
- Seed dataset: `runtime/intent_classifier/seed_pairs.jsonl` (bundled).

Fallback ladder (loader.py):
1. Fine-tuned `v<N>/` se presente (~97% acc locale-trained, ~22ms latency).
2. Qwen3-Embedding-0.6B base ZERO-SHOT (~74% acc universale multilingue 100+ lingue).
3. None (caller deve gestire — opzionale `hardcoded+affinity` come 3° tier IT/EN).

NON usare hardcoded+affinity come fallback universale: solo IT/EN.
Per public installer GitHub multilingue: Qwen zero-shot è il default.

Re-training:
- Auto daily@04:15 via `runtime/jobs/intent_retrain.py` (scheduler v2).
- Manual: `python -m runtime.intent_classifier.train`.
"""
from runtime.intent_classifier.loader import (
    classify_query_object,
    is_available,
    warmup,
)
from runtime.intent_classifier.anchors import OBJECTS, ANCHORS_IT, ANCHORS_EN

__all__ = [
    "classify_query_object",
    "is_available",
    "warmup",
    "OBJECTS",
    "ANCHORS_IT",
    "ANCHORS_EN",
]
