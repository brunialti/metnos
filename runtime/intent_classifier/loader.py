"""loader.py — Lazy loader Qwen3-Embedding-0.6B fine-tuned + anchor cache.

Caricamento:
- Modello da `~/.local/share/metnos/intent_classifier/v<N>/` (LWW max version).
- Fallback HF base se nessuna versione fine-tuned (zero-shot).
- Anchor embedding caching (computed once per process).

Determinismo (§7.9):
- Cosine match contro anchor canonici → predizione canonical_object.
- Soglia confidence configurable via env `METNOS_INTENT_CLASSIFIER_THRESHOLD`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from runtime.intent_classifier.anchors import for_lang as anchors_for_lang, OBJECTS

_MODEL = None
_ANCHOR_EMB = None
_ANCHOR_LANG = None


def _model_dir() -> Path:
    """Resolve current intent classifier model dir, LWW max version."""
    base = Path.home() / ".local" / "share" / "metnos" / "intent_classifier"
    if not base.exists():
        return base / "v0_missing"
    versions = sorted(base.glob("v*"), key=lambda p: p.name)
    if not versions:
        return base / "v0_missing"
    return versions[-1]


def is_available() -> bool:
    """True se modello fine-tuned esiste su disco."""
    return _model_dir().exists() and any(_model_dir().iterdir())


def _load_model():
    """Lazy load model from disk (FT version) or HF (zero-shot fallback)."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    md = _model_dir()
    if md.exists() and any(md.iterdir()):
        _MODEL = SentenceTransformer(str(md))
    else:
        # Fallback zero-shot
        try:
            _MODEL = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
        except Exception:
            _MODEL = None
    return _MODEL


def _get_anchor_embeddings(lang: str = "it"):
    """Get cached anchor embeddings for language."""
    global _ANCHOR_EMB, _ANCHOR_LANG
    if _ANCHOR_EMB is not None and _ANCHOR_LANG == lang:
        return _ANCHOR_EMB
    model = _load_model()
    if model is None:
        return None
    anchors = anchors_for_lang(lang)
    texts = [anchors[o] for o in OBJECTS]
    _ANCHOR_EMB = model.encode(
        texts, convert_to_tensor=True, normalize_embeddings=True
    )
    _ANCHOR_LANG = lang
    return _ANCHOR_EMB


def classify_query_object(query: str, lang: str = "it",
                           threshold: Optional[float] = None) -> Optional[str]:
    """Map query string to canonical object via fine-tuned embedding cosine match.

    Args:
        query: full user query (non-tokenized).
        lang: locale ("it" | "en").
        threshold: minimum cosine; None = no threshold (always return best).

    Returns:
        Canonical object string (one of OBJECTS) or None if model unavailable.
    """
    if not query or not query.strip():
        return None
    model = _load_model()
    if model is None:
        return None
    anchor_emb = _get_anchor_embeddings(lang)
    if anchor_emb is None:
        return None
    q_emb = model.encode(
        query, convert_to_tensor=True, normalize_embeddings=True
    )
    scores = anchor_emb @ q_emb
    idx = int(scores.argmax())
    score = float(scores[idx])
    if threshold is not None and score < threshold:
        return None
    if threshold is None:
        env_th = os.environ.get("METNOS_INTENT_CLASSIFIER_THRESHOLD")
        if env_th:
            try:
                if score < float(env_th):
                    return None
            except ValueError:
                pass
    return OBJECTS[idx]


def warmup(lang: str = "it") -> bool:
    """Eager load + anchor embed (per startup health check)."""
    m = _load_model()
    if m is None:
        return False
    return _get_anchor_embeddings(lang) is not None
