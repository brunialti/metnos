"""engine/cluster.py — utility BGE-M3 embedding + cosine + cluster assign.

Riusa BGE-M3 ONNX int8 già nel runtime (ADR 0134). Usato da:
  - fastpath.py: semantic match query → fastpath stored
  - autopath.py: cluster_id emergente per skill promotion

Cache embedding su disco: ~/.cache/metnos/affinity_emb/<sha16>.npz (riusato
dal sistema esistente).

§7.9 deterministic: embed + cosine puri. LLM judge solo zona grigia
(0.75-0.90 cosine) per cluster_id assignment, opzionale.
"""
from __future__ import annotations

import hashlib
import logging
import os
import struct
from typing import Optional

log = logging.getLogger(__name__)

# Soglie cosine canoniche (env tunable)
COSINE_HIGH = float(os.environ.get("METNOS_CLUSTER_COSINE_HIGH", "0.90"))
COSINE_LOW = float(os.environ.get("METNOS_CLUSTER_COSINE_LOW", "0.75"))
K_NEIGHBORS = int(os.environ.get("METNOS_CLUSTER_K", "10"))


def embed(query: str) -> Optional[bytes]:
    """Ritorna embedding BGE-M3 di query come bytes (float32 packed).

    Fallback graceful: se BGE-M3 non disponibile → None. Caller decide
    se procedere con hash-only o fallire.
    """
    if not query or not query.strip():
        return None
    try:
        # affinity_semantic non espone `embed_query` a modulo: il vero
        # embedder è BGEEmbeddingService (bge_embedding.py). Riusa il
        # singleton lazy `_get_embedder()` (degrade graceful → None).
        from affinity_semantic import _get_embedder
        emb = _get_embedder()
        if emb is None:
            return None
        vec = emb.embed_query(query)  # ndarray (1024,) L2-normalized
        if vec is None or len(vec) == 0:
            return None
        # Pack come float32 raw bytes (compatibile con praxis_cluster legacy)
        return b"".join(struct.pack("<f", float(x)) for x in vec)
    except Exception as ex:
        log.debug("cluster.embed: BGE-M3 unavailable (%r)", ex)
        return None


def _unpack(eb: bytes) -> list[float]:
    n = len(eb) // 4
    return list(struct.unpack(f"<{n}f", eb))


def cosine(a: bytes, b: bytes) -> float:
    """Cosine similarity fra due embedding packed. 0.0 se incompatibili."""
    if not a or not b or len(a) != len(b):
        return 0.0
    av = _unpack(a)
    bv = _unpack(b)
    if not av:
        return 0.0
    dot = sum(x * y for x, y in zip(av, bv))
    na = sum(x * x for x in av) ** 0.5
    nb = sum(x * x for x in bv) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def new_cluster_id() -> str:
    """Genera nuovo cluster_id univoco."""
    import secrets
    return f"cl_{secrets.token_hex(6)}"


def normalize_query(q: str) -> str:
    """Normalizza per hash lookup deterministico Layer 0a.

    Lowercase, collapse whitespace, strip punctuation, rimuovi stopword
    fissa multi-lingua.
    """
    if not q:
        return ""
    import re
    s = q.lower().strip()
    # Strip trailing punctuation + collapse whitespace
    s = re.sub(r"[.?!,;:'\"]+$", "", s)
    s = re.sub(r"\s+", " ", s)
    # Stopword fissa (chiusa, mai espansa con LLM)
    stop = {
        # IT
        "il", "lo", "la", "i", "gli", "le", "un", "una", "uno",
        "di", "del", "della", "dei", "dello", "delle", "degli",
        "dimmi", "dimi", "mostrami", "mostra", "fammi", "fai",
        "quali", "sono", "che", "cosa", "cos", "c'e", "ce",
        # EN
        "the", "a", "an", "of", "for", "to", "in", "on",
        "show", "tell", "give", "list", "what", "which", "is", "are",
        "do", "does",
    }
    tokens = [t for t in s.split() if t not in stop]
    return " ".join(tokens)


def normalize_hash(q: str) -> str:
    """SHA256 troncato della query normalizzata."""
    return hashlib.sha256(normalize_query(q).encode("utf-8")).hexdigest()[:16]
