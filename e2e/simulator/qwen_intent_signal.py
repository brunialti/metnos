"""qwen_intent_signal.py — Qwen3-Emb FT v1 as ranker signal.

Lazy-load runtime/intent_classifier. Cache prediction per query in process.

API:
- predict_object(query: str) → str | None
- score_boost(first_tool: str, query: str) → float
    Returns:
      +0.7 if first_tool contains predicted object
      -0.3 if first_tool has DIFFERENT canonical object
       0   if model unavailable or first_tool has no object
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Optional

_PRED_CACHE: dict[str, Optional[str]] = {}
_CLASSIFY = None
_OBJECTS = set()


def _lazy_init():
    global _CLASSIFY, _OBJECTS
    if _CLASSIFY is not None:
        return _CLASSIFY
    # Add metnos root for import
    metnos_root = "/opt/metnos"
    if metnos_root not in sys.path:
        sys.path.insert(0, metnos_root)
    try:
        from runtime.intent_classifier import classify_query_object, OBJECTS
        _CLASSIFY = classify_query_object
        _OBJECTS = set(OBJECTS)
    except Exception as e:
        if os.environ.get("SIM_QWEN_DEBUG"):
            print(f"qwen_intent_signal init fail: {e}", file=sys.stderr)
        _CLASSIFY = False
    return _CLASSIFY


def predict_object(query: str) -> Optional[str]:
    if not query:
        return None
    if query in _PRED_CACHE:
        return _PRED_CACHE[query]
    cls = _lazy_init()
    if not cls:
        _PRED_CACHE[query] = None
        return None
    try:
        pred = cls(query, lang="it")
        _PRED_CACHE[query] = pred
        return pred
    except Exception:
        _PRED_CACHE[query] = None
        return None


def predict_object_with_score(query: str) -> tuple[Optional[str], float]:
    """Return (predicted_object, cosine_score) — for confidence threshold."""
    if not query:
        return None, 0.0
    _lazy_init()
    if not _CLASSIFY:
        return None, 0.0
    try:
        # Use loader internals to get score
        sys.path.insert(0, "/opt/metnos")
        from runtime.intent_classifier.loader import (
            _load_model, _get_anchor_embeddings,
        )
        from runtime.intent_classifier.anchors import OBJECTS as _OBJ
        model = _load_model()
        anchor_emb = _get_anchor_embeddings("it")
        if model is None or anchor_emb is None:
            return None, 0.0
        q_emb = model.encode(query, convert_to_tensor=True, normalize_embeddings=True)
        scores = anchor_emb @ q_emb
        idx = int(scores.argmax())
        return _OBJ[idx], float(scores[idx])
    except Exception:
        return None, 0.0


def first_tool_object(first_tool: str) -> Optional[str]:
    if not first_tool or "_" not in first_tool:
        return None
    parts = first_tool.lower().split("_")
    if len(parts) < 2:
        return None
    cand = parts[1]
    if cand in _OBJECTS:
        return cand
    return None


def score_boost(first_tool: str, query: str, weight: float = 0.7) -> float:
    """Compute boost/penalty based on Qwen prediction vs first_tool object."""
    predicted = predict_object(query)
    if not predicted:
        return 0.0
    first_obj = first_tool_object(first_tool)
    if not first_obj:
        return 0.0
    if first_obj == predicted:
        return weight
    return -weight * 0.4  # penalty 40% of boost for mismatch
