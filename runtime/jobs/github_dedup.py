"""github_dedup — embedding helper BGE-M3 per gli executor issue.

Conteneva la logica di dedup/4-AND/classify del watcher GitHub (Fase D),
RITIRATA: il flusso vive ora negli executor `write/read/find_issues` + comandi
`run_user_query` schedulati (vedi internal/reports/github_maintenance_flow.html).

Resta solo `embed_query`: wrapper BGE-M3 1024d L2-normalized (riuso singleton
`affinity_semantic._get_embedder`, ADR 0134), usato da `find_issues`/`write_issues`
per il dedup semantico nello store. Determinismo §7.9: nessun LLM.
"""
from __future__ import annotations

import logging

import numpy as np  # noqa: F401 — type hint di embed_query

_LOG = logging.getLogger(__name__)


def embed_query(text: str) -> "np.ndarray | None":
    """Embed BGE-M3 1024d L2-normalized. Ritorna None se BGE non disponibile
    (degrade silenzioso: il caller fa fallback). Riusa il singleton di
    `affinity_semantic._get_embedder()` (ADR 0134, regola del 3 §7.2)."""
    if not text or not text.strip():
        return None
    try:
        from affinity_semantic import _get_embedder  # type: ignore
        emb = _get_embedder()
        if emb is None:
            return None
        return emb.embed_query(text)
    except Exception as e:
        _LOG.info("github_dedup: embed_query fail (%r)", e)
        return None
