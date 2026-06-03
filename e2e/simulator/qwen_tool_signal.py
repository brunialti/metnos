"""qwen_tool_signal.py — Qwen3-Emb FT v1 (tool classifier) as ranker signal.

Clone di `qwen_intent_signal.py` adattato a tool labels (multi-class ~50 attivi).

Lazy-load del modello fine-tuned da /tmp/qwen_ft_tool_classifier_v1 (default)
oppure path via env METNOS_QWEN_TOOL_CLASSIFIER_DIR. Anchor map persistita in
`metnos_tool_classifier_meta.json` (scritto dal trainer).

API:
- predict_tool(query: str) → str | None         # top-1
- predict_tool_with_score(query: str) → list[(tool, score)]  # top-3
- score_boost(first_tool: str, query: str, weight=0.7) → float
    Returns:
      +weight        if first_tool == top-1
      +weight*0.5    if first_tool in top-3 (not top-1)
      -weight*0.4    se first_tool predetto fra le label ma DIVERSO da top-1
       0             se modello assente o tool non risolto

§7.9: deterministico (cosine match contro anchor cache).
§7.11: paths via env + Path(__file__).resolve().parents.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Optional

# Repo layout: /opt/metnos/e2e/simulator/<this file>
_SIM_DIR = Path(__file__).resolve().parent
_METNOS_ROOT = _SIM_DIR.parents[1]
sys.path.insert(0, str(_METNOS_ROOT))

_DEFAULT_MODEL_DIR = os.environ.get(
    "METNOS_QWEN_TOOL_CLASSIFIER_DIR",
    "/tmp/qwen_ft_tool_classifier_v1",
)
_META_FILE = "metnos_tool_classifier_meta.json"

# Process-level caches
_PRED_CACHE: dict[str, list[tuple[str, float]]] = {}
_MODEL = None
_ANCHOR_EMB = None
_LABELS: list[str] = []
_INIT_DONE = False
_INIT_OK = False


def _lazy_init() -> bool:
    """Load model + anchor cache once per process."""
    global _MODEL, _ANCHOR_EMB, _LABELS, _INIT_DONE, _INIT_OK
    if _INIT_DONE:
        return _INIT_OK
    _INIT_DONE = True

    model_dir = Path(_DEFAULT_MODEL_DIR)
    meta_path = model_dir / _META_FILE
    if not model_dir.exists() or not meta_path.exists():
        if os.environ.get("SIM_QWEN_DEBUG"):
            print(f"[qwen_tool_signal] missing model_dir or meta: "
                  f"{model_dir} / {meta_path}", file=sys.stderr)
        return False

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        if os.environ.get("SIM_QWEN_DEBUG"):
            print(f"[qwen_tool_signal] sentence_transformers missing: {e}",
                  file=sys.stderr)
        return False

    try:
        meta = json.loads(meta_path.read_text())
        labels = meta.get("labels") or []
        anchors = meta.get("anchors") or {}
        if not labels or not anchors:
            return False
        anchor_texts = [anchors[t] for t in labels if t in anchors]
        labels_resolved = [t for t in labels if t in anchors]

        _MODEL = SentenceTransformer(str(model_dir))
        _ANCHOR_EMB = _MODEL.encode(
            anchor_texts, convert_to_tensor=True, normalize_embeddings=True
        )
        _LABELS = labels_resolved
        _INIT_OK = True
        return True
    except Exception as e:
        if os.environ.get("SIM_QWEN_DEBUG"):
            print(f"[qwen_tool_signal] init fail: {e}", file=sys.stderr)
        return False


def is_available() -> bool:
    """True se modello caricabile (controllo cheap)."""
    return _lazy_init()


def predict_tool_with_score(query: str, top_k: int = 3) -> list[tuple[str, float]]:
    """Top-k (tool_name, cosine_score) per la query. [] se modello assente."""
    if not query or not query.strip():
        return []
    cache_key = f"{query}|{top_k}"
    if cache_key in _PRED_CACHE:
        return _PRED_CACHE[cache_key]
    if not _lazy_init():
        _PRED_CACHE[cache_key] = []
        return []
    try:
        q_emb = _MODEL.encode(
            query, convert_to_tensor=True, normalize_embeddings=True
        )
        scores = _ANCHOR_EMB @ q_emb  # [N_labels]
        k = min(top_k, scores.shape[0])
        topk = scores.topk(k)
        idxs = topk.indices.tolist()
        vals = topk.values.tolist()
        result = [(_LABELS[i], float(v)) for i, v in zip(idxs, vals)]
        _PRED_CACHE[cache_key] = result
        return result
    except Exception as e:
        if os.environ.get("SIM_QWEN_DEBUG"):
            print(f"[qwen_tool_signal] predict fail: {e}", file=sys.stderr)
        _PRED_CACHE[cache_key] = []
        return []


def predict_tool(query: str) -> Optional[str]:
    """Top-1 tool (or None)."""
    pred = predict_tool_with_score(query, top_k=1)
    return pred[0][0] if pred else None


def score_boost(first_tool: str, query: str, weight: float = 0.7) -> float:
    """Boost/penalty for prefilter ranker, mirrors qwen_intent_signal style.

    +weight       if first_tool == top-1
    +weight*0.5   if first_tool in top-3 (not top-1)
    -weight*0.4   if first_tool is in our label set but NOT in top-3
     0            otherwise (model unavailable, tool unknown)
    """
    if not first_tool:
        return 0.0
    top3 = predict_tool_with_score(query, top_k=3)
    if not top3:
        return 0.0
    names = [t for t, _ in top3]
    if first_tool == names[0]:
        return weight
    if first_tool in names[1:]:
        return weight * 0.5
    if first_tool in _LABELS:
        return -weight * 0.4
    return 0.0


# ---------- CLI smoke ----------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="*", help="query/queries to classify")
    ap.add_argument("-k", "--top-k", type=int, default=3)
    args = ap.parse_args()

    if not args.query:
        # Default smoke
        args.query = [
            "che ora è a Tokyo?",
            "cerca foto a roma",
            "leggi le mail di oggi",
            "sposta /tmp/a.txt in /tmp/archivio/",
            "calcola hash sha256 di /tmp/x.txt",
        ]

    if not is_available():
        print("[smoke] model NOT available — set METNOS_QWEN_TOOL_CLASSIFIER_DIR "
              "or train first via qwen_finetune_tool_classifier.py")
        sys.exit(1)

    for q in args.query:
        preds = predict_tool_with_score(q, top_k=args.top_k)
        print(f"\nQ: {q}")
        for tool, score in preds:
            print(f"  {score:.3f}  {tool}")
