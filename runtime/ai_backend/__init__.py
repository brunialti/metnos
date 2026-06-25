# SPDX-License-Identifier: AGPL-3.0-only
"""runtime/ai_backend — alias di compatibilità per l'embedding TESTO.

Superato dalla virtualizzazione segregata `virt/` (25/6): `embedding_service()`
è ora un sottile delega a `virt.get_embedder("text")` (config
`embedding_tiers.toml`, default BGE-M3 locale, in-process). Il ramo
`suprastructure` è RIMOSSO — Metnos è autonomo per l'embedding. Mantenuto solo
perché alcuni chiamanti (es. `find_urls` deep_search) usano `embedding_service()`.

Contract version: `runtime.__version__.AI_BACKEND_API`.
"""
from __future__ import annotations

try:
    from __version__ import AI_BACKEND_API  # noqa: F401  (re-export)
except Exception:  # pragma: no cover
    AI_BACKEND_API = 1


def embedding_service():
    """Servizio di embedding TESTO. API: `embed_texts(list[str])`.

    Delega alla virtualizzazione segregata `virt.get_embedder("text")` (config
    `embedding_tiers.toml`, default BGE-M3 locale). Ramo suprastructure RIMOSSO
    (25/6: Metnos autonomo, embedding in-process). Ritorna None in modo GRAZIOSO
    se l'embedder non è disponibile (§2.8: i chiamanti degradano senza esplodere)."""
    try:
        from virt import get_embedder
        return get_embedder("text")
    except Exception:
        return None
