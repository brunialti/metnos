"""sim_dense.py — BGE-M3 dense retrieval per executor selection §7.3.

Universal §7.3: nessun nome executor hardcoded. Embed manifest description+
affinity, cosine vs query embed. Cache disk per persistenza.

Layer 0 dense = retrieval semantico orthogonal al lexical match (Layer 1+).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

# Path ai manifests reali Metnos
_EXEC_ROOT = Path("/opt/metnos/executors")
_CACHE_PATH = Path(__file__).parent / "exec_embeddings.npz"
_QCACHE: dict[str, np.ndarray] = {}

_EMB = None


def _load_embedder():
    global _EMB
    if _EMB is not None:
        return _EMB
    try:
        sys.path.insert(0, "/opt/metnos/runtime")
        from bge_embedding import BGEEmbeddingService
        _EMB = BGEEmbeddingService()
        return _EMB
    except Exception as e:
        print(f"[sim_dense] BGE-M3 load FAILED: {e}", file=sys.stderr)
        return None


def _corpus_text(manifest_path: Path) -> str:
    """Estrai description IT + affinity tags dal manifest TOML."""
    try:
        import tomllib
        with open(manifest_path, "rb") as f:
            m = tomllib.load(f)
    except Exception:
        return ""
    desc_block = m.get("description", {})
    if isinstance(desc_block, dict):
        desc = desc_block.get("it", "") or desc_block.get("en", "")
    else:
        desc = str(desc_block or "")
    affinity = m.get("affinity", [])
    if isinstance(affinity, list):
        aff_str = " ".join(str(a) for a in affinity)
    else:
        aff_str = str(affinity or "")
    return f"{desc} {aff_str}".strip()


def build_or_load(executor_names: list[str]) -> dict | None:
    """Build catalog embeddings or load from cache. Returns {matrix, names}."""
    texts, names = [], []
    for n in executor_names:
        mp = _EXEC_ROOT / n / "manifest.toml"
        if not mp.exists():
            continue
        txt = _corpus_text(mp)
        if not txt:
            continue
        texts.append(txt)
        names.append(n)
    if not texts:
        return None
    key = hashlib.sha256(
        "|".join(f"{n}\t{t}" for n, t in zip(names, texts)).encode()
    ).hexdigest()[:16]
    if _CACHE_PATH.exists():
        try:
            d = np.load(_CACHE_PATH, allow_pickle=True)
            cached_key = str(d["key"]) if "key" in d.files else ""
            if cached_key == key:
                return {"matrix": d["matrix"], "names": list(d["names"])}
        except Exception:
            pass
    emb = _load_embedder()
    if emb is None:
        return None
    matrix = emb.embed_texts(texts)
    # L2 normalize per cosine = dot
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms
    try:
        np.savez_compressed(
            _CACHE_PATH, matrix=matrix, names=np.array(names), key=key)
    except Exception:
        pass
    return {"matrix": matrix, "names": names}


def query_topk(q: str, cache: dict, k: int = 10,
                min_score: float = 0.4) -> list[tuple[str, float]]:
    """Cosine top-K vs catalog. Returns [(name, score)]."""
    if not q or not cache:
        return []
    h = hashlib.sha256(q.encode()).hexdigest()
    if h not in _QCACHE:
        emb = _load_embedder()
        if emb is None:
            return []
        qv = emb.embed_query(q)
        qn = np.linalg.norm(qv)
        if qn > 0:
            qv = qv / qn
        _QCACHE[h] = qv
    qv = _QCACHE[h]
    cos = cache["matrix"] @ qv
    idx = np.argsort(-cos)[:k]
    out = [(cache["names"][i], float(cos[i])) for i in idx]
    return [(n, s) for n, s in out if s >= min_score]


def cosine(q: str, exec_name: str, cache: dict) -> float:
    """Cosine score between query and specific executor."""
    if not q or not cache or exec_name not in cache["names"]:
        return 0.0
    h = hashlib.sha256(q.encode()).hexdigest()
    if h not in _QCACHE:
        emb = _load_embedder()
        if emb is None:
            return 0.0
        qv = emb.embed_query(q)
        qn = np.linalg.norm(qv)
        if qn > 0:
            qv = qv / qn
        _QCACHE[h] = qv
    i = cache["names"].index(exec_name)
    return float(cache["matrix"][i] @ _QCACHE[h])
