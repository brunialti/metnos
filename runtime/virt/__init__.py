"""virt — casa UNICA, segregata e minimale della virtualizzazione modelli.

Tre facciate config-driven, stile `llm_router` (factory, NIENTE registry/DI):

    from virt import get_embedder, get_llm, get_vlm
    get_embedder("text").embed_texts([...])   # BGE-M3 (o SigLIP "image", o http)
    get_llm("middle").chat(system, user).text  # delega a llm_router
    get_vlm()                                  # spec config del VLM :8081

Cambiare modello = editare `~/.config/metnos/{embedding,vlm}_tiers.toml` (il LLM
ha già `llm_tiers.toml`). Mai il codice. I default uguagliano la realtà attuale.
"""
from __future__ import annotations

from . import tiers
from .interfaces import (  # noqa: F401
    EmbeddingProvider, LLMProvider,
    EmbeddingUnavailableError, VLMUnavailableError, VirtError,
)

__all__ = [
    "get_embedder", "get_local_embedder", "get_llm", "get_vlm", "ensure_vlm_up",
    "EmbeddingProvider", "LLMProvider",
    "EmbeddingUnavailableError", "VLMUnavailableError", "VirtError",
]

# Default baked-in = realtà attuale (cutover behavior-preserving).
DEFAULT_EMBEDDERS = {
    "text":  {"provider": "bge"},      # bge_embedding.BGEEmbeddingService (1024)
    "image": {"provider": "siglip"},   # clip_embedding.ClipEngine (768, text+image)
}
DEFAULT_VLM = {
    "default": {
        "provider": "llamacpp", "model": "qwen3vl-2b",
        "base_url": "http://127.0.0.1:8081",
        "timeout_s": 60, "max_edge": 1024, "max_tokens": 512,
    },
}

_cache: dict = {}


def get_embedder(role: str = "text"):
    """EmbeddingProvider per ruolo: "text"=BGE-M3, "image"=SigLIP, o remoto
    ("http"). Istanza cachata/lazy. Le classi locali conformano già al Protocol."""
    ck = ("emb", role)
    if ck in _cache:
        return _cache[ck]
    s = tiers.spec("embedding", role, DEFAULT_EMBEDDERS)
    prov = (s.get("provider") or "bge").lower()
    if prov == "bge":
        from bge_embedding import BGEEmbeddingService
        obj = BGEEmbeddingService(s.get("model_dir"))
    elif prov == "siglip":
        from clip_embedding import get_clip_engine
        obj = get_clip_engine(s.get("model_dir"))
    elif prov in ("http", "openai", "remote"):
        from .providers import HttpEmbedder
        ep = s.get("endpoint") or s.get("base_url")
        if not ep:
            raise EmbeddingUnavailableError(f"embedding role {role!r}: manca base_url")
        obj = HttpEmbedder(ep, s.get("model", "local"), int(s.get("timeout_s", 30)))
    else:
        raise EmbeddingUnavailableError(f"provider embedding sconosciuto: {prov!r}")
    _cache[ck] = obj
    return obj


def get_local_embedder(role: str = "text"):
    """Return an in-process embedder, never an HTTP-configured backend.

    Read-only executors use this boundary when their signed contract declares
    local computation only.  Model-path options from a local ``bge`` or
    ``siglip`` tier are preserved; a remote tier is deliberately ignored
    instead of silently enlarging network authority.
    """
    ck = ("emb-local", role)
    if ck in _cache:
        return _cache[ck]
    spec = tiers.spec("embedding", role, DEFAULT_EMBEDDERS)
    if role == "text":
        from bge_embedding import BGEEmbeddingService
        obj = BGEEmbeddingService(
            spec.get("model_dir") if spec.get("provider") == "bge" else None,
        )
    elif role == "image":
        from clip_embedding import get_clip_engine
        obj = get_clip_engine(
            spec.get("model_dir") if spec.get("provider") == "siglip" else None,
        )
    else:
        raise EmbeddingUnavailableError(f"local embedding role sconosciuto: {role!r}")
    _cache[ck] = obj
    return obj


def get_llm(role: str = "middle"):
    """LLMProvider per tier ("fast"/"middle"/"wise"/"frontier"). Delega a
    `llm_router` — che È già la factory config-driven da `llm_tiers.toml`."""
    from llm_router import LLMRouter
    return LLMRouter().provider(role)


def get_vlm(role: str = "default") -> dict:
    """Spec config del VLM (provider, model, base_url, timeout_s, max_edge,
    max_tokens) da `vlm_tiers.toml`. Il calcolo immagine vive nell'executor
    immagini; qui si virtualizza la CONFIG (swap modello/endpoint senza codice)."""
    return tiers.spec("vlm", role, DEFAULT_VLM)


# Lifecycle VLM (lazy-start + health), una sola volta per processo. Centralizzata
# qui — non dentro un executor — cosi' OGNI consumatore del VLM la condivide
# (Metnos possiede l'up del modello, non un effetto collaterale di un executor).
_vlm_started: dict = {}


def ensure_vlm_up(role: str = "default", *, wait_s: int = 35) -> bool:
    """Avvia il server VLM via `scripts/vlm_server.sh` se non gia' in piedi e
    non gia' tentato in questo processo. Ritorna True se l'endpoint risponde
    /health entro `wait_s`, False altrimenti (il chiamante decide il fallback).

    Idempotente per (processo, role): un solo tentativo di start; le chiamate
    successive ritornano lo stato dell'health corrente. Endpoint e path-script
    sono config-driven: base_url da `get_vlm(role)`, override script via env
    `METNOS_VLM_SERVER_SH`. Deterministico, no LLM."""
    import os
    import time
    import urllib.error as _ue
    import urllib.request as _u
    from pathlib import Path

    spec = get_vlm(role)
    base_url = (spec.get("base_url") or "http://127.0.0.1:8081").rstrip("/")
    health_url = base_url + "/health"

    def _health_ok(timeout: float = 2.0) -> bool:
        try:
            with _u.urlopen(health_url, timeout=timeout) as h:
                return h.status == 200
        except (_ue.URLError, _ue.HTTPError, OSError, TimeoutError):
            return False

    # Gia' su: nessun start necessario.
    if _health_ok():
        return True
    # Gia' tentato in questo processo: non ritentare lo spawn (fallback hard).
    if _vlm_started.get(role):
        return _health_ok()
    _vlm_started[role] = True

    helper = os.environ.get("METNOS_VLM_SERVER_SH") or str(
        Path(__file__).resolve().parents[1].parent / "scripts" / "vlm_server.sh")
    if not os.path.exists(helper):
        return False
    import subprocess
    try:
        r = subprocess.run([helper, "start", "--auto-stop-idle", "600"],
                           timeout=45, capture_output=True, text=True)
        if r.returncode != 0:
            return False
    except (subprocess.TimeoutExpired, OSError):
        return False
    deadline = wait_s
    for _ in range(deadline):
        if _health_ok():
            return True
        time.sleep(1)
    return False
