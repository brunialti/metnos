# SPDX-License-Identifier: AGPL-3.0-only
"""runtime/ai_backend — SHIM del backend AI (asse 1 del rilascio pubblico).

Astrae il layer modelli/servizi così che la STESSA codebase giri in due varianti
senza fork (vedi [[project-public-release-initiative]]):

- **esercizio** (`.33`): `SuprastructureBackend` → usa `suprastructure.*` (hub
  servizi AI di Roberto), comportamento identico ad oggi.
- **pubblico** (BYO): `LocalOnnxBackend` → usa i wrapper ONNX standalone di
  Metnos (`bge_embedding`/`clip_embedding`/`face_embedding`) + un endpoint LLM
  e un geocoder portati dall'utente.

Selezione via env `METNOS_AI_BACKEND` ∈ {auto(default), suprastructure, local}.
`auto` = prova suprastructure (se importabile), altrimenti ONNX locale.

NB superficie reale (audit 5/6): l'UNICO import hard di suprastructure a runtime
era l'embedding di `find_urls` (deep_search). LLM = HTTP a llama-server (endpoint
config in `llm_router.DEFAULT_TIERS`), geo = Photon REST (`photon_client`): già
pluggabili via config, non importano suprastructure → qui per ora si astrae
l'EMBEDDING; LLM/geo restano config-driven (documentati, da formalizzare).

Contract version: `runtime.__version__.AI_BACKEND_API`.
"""
from __future__ import annotations

import os

try:
    from __version__ import AI_BACKEND_API  # noqa: F401  (re-export)
except Exception:  # pragma: no cover
    AI_BACKEND_API = 1


def _pref() -> str:
    return (os.environ.get("METNOS_AI_BACKEND") or "auto").strip().lower()


def _supra_embedding():
    """suprastructure.EmbeddingService caricata (o None se non disponibile)."""
    from suprastructure.embedding.onnx_embedding import EmbeddingService
    emb = EmbeddingService()
    emb._ensure_loaded()
    return emb


def _local_embedding():
    """BGE-M3 ONNX standalone di Metnos (drop-in, embed_texts(list[str]))."""
    from bge_embedding import BGEEmbeddingService
    return BGEEmbeddingService()


def embedding_service():
    """Servizio di embedding TESTO (BGE-M3). API: `embed_texts(list[str])`.

    Ritorna None in modo GRAZIOSO se nessun backend è disponibile (§2.8: i
    chiamanti — es. find_urls deep_search — degradano senza esplodere). La
    selezione rispetta `METNOS_AI_BACKEND`; `auto` preferisce suprastructure
    (esercizio) e ripiega su ONNX locale (pubblico)."""
    pref = _pref()
    if pref in ("suprastructure", "supra"):
        try:
            return _supra_embedding()
        except Exception:
            return None
    if pref in ("local", "onnx", "byo"):
        try:
            return _local_embedding()
        except Exception:
            return None
    # auto: suprastructure se c'è, altrimenti ONNX locale.
    try:
        return _supra_embedding()
    except Exception:
        pass
    try:
        return _local_embedding()
    except Exception:
        return None
